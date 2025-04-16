import argparse
import asyncio
import os
import sys
import json
from pathlib import Path
from enum import Enum
from typing import Dict, Any, Optional

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService, LLMSearchResponseFrame
from pipecat.services.google.rtvi import GoogleRTVIObserver
from pipecat.services.groq import GroqLLMService, GroqSTTService
from pipecat.transports.services.daily import DailyParams, DailyTransport
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter
from pipecat.transcriptions.language import Language

# Import configuration helper
sys.path.append(str(Path(__file__).parent.parent))
from runner import configure

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

# Service type enums
class STTServiceType(str, Enum):
    DEEPGRAM = "deepgram"
    GROQ = "groq"

class LLMServiceType(str, Enum):
    GEMINI = "gemini"
    GROQ = "groq"

class TTSServiceType(str, Enum):
    CARTESIA = "cartesia"
    ELEVENLABS = "elevenlabs"

# Model configurations
STT_MODELS = {
    "deepgram": {
        "nova-3": "Latest Deepgram model with best accuracy",
        "nova-2": "General purpose transcription model",
        "nova-2-general": "Enhanced general purpose model",
        "nova-2-telephony": "Optimized for telephony audio",
        "nova-2-meeting": "Specialized for meeting transcription",
        "nova-2-phonecall": "Optimized for phone call audio"
    },
    "groq": {
        "whisper-large-v3": "High accuracy multilingual model (10.3% WER)",
        "whisper-large-v3-turbo": "Fast multilingual model (12% WER)",
        "distil-whisper-large-v3-en": "Fastest English-only model (13% WER)"
    }
}

LLM_MODELS = {
    "gemini": {
        "gemini-2.0-flash": "Latest Gemini model - multimodal capabilities",
        "gemini-2.0-flash-lite": "Cost efficient and low latency model",
        "gemini-2.0-flash-live-001": "Optimized for real-time interaction",
        "gemini-1.5-flash": "Fastest Gemini 1.5 model",
        "gemini-1.5-pro": "Balanced speed and quality model",
        "gemini-1.5-ultra": "Most powerful Gemini 1.5 model"
    },
    "groq": {
        "meta-llama/llama-4-scout-17b-16e-instruct": "Meta's Llama 4 Scout model (17B)",
        "meta-llama/llama-4-maverick-17b-128e-instruct": "Meta's Llama 4 Maverick model (17B, 128k context)",
        "llama-3.1-8b-instant": "Ultra-fast lightweight model",
        "llama-3.3-70b-versatile": "Powerful balanced model",
        "llama-3.2-11b-vision-preview": "Medium-size vision model",
        "llama-3.2-90b-vision-preview": "Large-size vision model",
        "mistral-saba-24b": "Mistral's SABA model (24B)",
        "qwen-2.5-32b": "Alibaba's Qwen 2.5 model (32B)"
    }
}

TTS_MODELS = {
    "cartesia": {
        "71a7ad14-091c-4e8e-a314-022ece01c121": "British Reading Lady",
        "b98e4dfe-a8ab-4e14-8cb5-a9a0abe1fd2b": "Default Male Voice", 
        "9e184750-08cd-427c-9d11-50cdf523848a": "Alternative Female Voice"
    },
    "elevenlabs": {
        "11Labs-v1/Adam": "Adam - Male, versatile",
        "11Labs-v1/Antoni": "Antoni - Male, deep with slight accent",
        "11Labs-v1/Bella": "Bella - Female, soft and warm",
        "11Labs-v1/Dorothy": "Dorothy - Female, older, warm",
        "11Labs-v1/Josh": "Josh - Male, young American",
        "11Labs-v1/Rachel": "Rachel - Female, expressive American",
        "11Labs-v1/Sam": "Sam - Male, raspy and rough"
    }
}

# LLM logger for debug purposes
class LLMSearchLoggerProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMSearchResponseFrame):
            print(f"LLMSearchLoggerProcessor: {frame}")

        await self.push_frame(frame)

def create_stt_service(service_type: STTServiceType, model: str, optimize_for_latency: bool = False) -> Any:
    """Factory function to create the selected STT service with specified model."""
    if service_type == STTServiceType.DEEPGRAM:
        return DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            model=model,
            interim_results=optimize_for_latency,
            endpointing=50 if optimize_for_latency else 500,
        )
    elif service_type == STTServiceType.GROQ:
        return GroqSTTService(
            api_key=os.getenv("GROQ_API_KEY"),
            model=model,
            temperature=0.0,  # Lower temperature for deterministic results
            language=Language.EN,
        )
    else:
        raise ValueError(f"Unsupported STT service: {service_type}")

def create_llm_service(
    service_type: LLMServiceType, 
    model: str,
    enable_search: bool = False, 
    optimize_for_latency: bool = False
) -> Any:
    """Factory function to create the selected LLM service with specified model."""
    
    system_instruction = """
    You are an expert at providing the most recent news from any place. Your responses will be converted to audio, 
    so ensure they are formatted in plain text without special characters (e.g., *, _, -) or overly complex formatting.

    Guidelines:
    - Always deliver accurate and concise responses.
    - Ensure all responses are clear, using plain text only. Avoid any special characters or symbols.
    - If optimizing for latency, keep responses shorter and more direct.

    Start every interaction by asking how you can assist the user.
    """
    
    # Google Gemini with optional search capabilities
    if service_type == LLMServiceType.GEMINI:
        # Configure search tool if enabled
        tools = []
        if enable_search:
            search_tool = {
                "google_search_retrieval": {
                    "dynamic_retrieval_config": {
                        "mode": "MODE_DYNAMIC",
                        "dynamic_threshold": 0,  # Always ground with search
                    }
                }
            }
            tools.append(search_tool)
        
        return GoogleLLMService(
            api_key=os.getenv("GOOGLE_API_KEY"),
            model=model,
            system_instruction=system_instruction,
            tools=tools if tools else None,
        )
    
    # Groq LLM service
    elif service_type == LLMServiceType.GROQ:
        return GroqLLMService(
            api_key=os.getenv("GROQ_API_KEY"),
            model=model,
            params=GroqLLMService.InputParams(
                temperature=0.5,
                max_tokens=800 if optimize_for_latency else 2000,
            )
        )
    else:
        raise ValueError(f"Unsupported LLM service: {service_type}")

def create_tts_service(service_type: TTSServiceType, model: str, optimize_for_latency: bool = False) -> Any:
    """Factory function to create the selected TTS service with specified model."""
    
    if service_type == TTSServiceType.CARTESIA:
        return CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY"),
            voice_id=model,
            text_filters=[MarkdownTextFilter()],
        )
    
    elif service_type == TTSServiceType.ELEVENLABS:
        return ElevenLabsTTSService(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            voice_id=model,
            model="eleven_monolingual_v1" if optimize_for_latency else "eleven_multilingual_v2",
            optimize_streaming_latency=optimize_for_latency,
        )
    
    else:
        raise ValueError(f"Unsupported TTS service: {service_type}")

def get_default_model(service_type: str, service_name: str) -> str:
    """Get the default model for a service."""
    if service_name == "stt":
        models = STT_MODELS.get(service_type, {})
    elif service_name == "llm":
        models = LLM_MODELS.get(service_type, {})
    elif service_name == "tts":
        models = TTS_MODELS.get(service_type, {})
    else:
        return ""
        
    return next(iter(models.keys())) if models else ""

async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Configurable AI Voice Agent")
    parser.add_argument(
        "--stt", 
        type=STTServiceType, 
        choices=list(STTServiceType), 
        default=STTServiceType.DEEPGRAM,
        help="Speech-to-text service to use"
    )
    parser.add_argument(
        "--stt-model", 
        type=str,
        help="Speech-to-text model to use"
    )
    parser.add_argument(
        "--llm", 
        type=LLMServiceType, 
        choices=list(LLMServiceType), 
        default=LLMServiceType.GEMINI,
        help="Large language model service to use"
    )
    parser.add_argument(
        "--llm-model", 
        type=str,
        help="Language model to use"
    )
    parser.add_argument(
        "--tts", 
        type=TTSServiceType, 
        choices=list(TTSServiceType), 
        default=TTSServiceType.CARTESIA,
        help="Text-to-speech service to use"
    )
    parser.add_argument(
        "--tts-model", 
        type=str,
        help="Text-to-speech model/voice to use"
    )
    parser.add_argument(
        "--enable-search", 
        action="store_true", 
        help="Enable search capabilities for Gemini"
    )
    parser.add_argument(
        "--optimize-latency", 
        action="store_true", 
        help="Optimize all services for low latency"
    )
    parser.add_argument(
        "-u", "--url", 
        type=str, 
        help="Daily room URL"
    )
    parser.add_argument(
        "-t", "--token", 
        type=str, 
        help="Daily room token"
    )
    
    args = parser.parse_args()
    
    # Set default models if not provided
    if not args.stt_model:
        args.stt_model = get_default_model(args.stt, "stt")
    
    if not args.llm_model:
        args.llm_model = get_default_model(args.llm, "llm")
    
    if not args.tts_model:
        args.tts_model = get_default_model(args.tts, "tts")
    
    # Print configuration
    print(f"Voice Agent Configuration:")
    print(f"STT Service: {args.stt}, Model: {args.stt_model}")
    print(f"LLM Service: {args.llm}, Model: {args.llm_model}")
    print(f"TTS Service: {args.tts}, Model: {args.tts_model}")
    print(f"Search Enabled: {args.enable_search}")
    print(f"Optimized for Latency: {args.optimize_latency}")
    
    async with aiohttp.ClientSession() as session:
        # Get room URL and token for Daily connection
        if args.url and args.token:
            room_url = args.url
            token = args.token
        else:
            (room_url, token) = await configure(session)
        
        # Create Daily transport
        transport = DailyTransport(
            room_url,
            token,
            "Configurable AI Voice Agent",
            DailyParams(
                audio_out_enabled=True,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                vad_audio_passthrough=True,
            ),
        )
        
        # Create services based on user selection
        stt = create_stt_service(args.stt, args.stt_model, args.optimize_latency)
        llm = create_llm_service(args.llm, args.llm_model, args.enable_search, args.optimize_latency)
        tts = create_tts_service(args.tts, args.tts_model, args.optimize_latency)
        
        # Set up LLM context
        context = OpenAILLMContext(
            [
                {
                    "role": "user",
                    "content": "Start by greeting the user warmly, introducing yourself, and mentioning the current day. Be friendly and engaging to set a positive tone for the interaction.",
                }
            ],
        )
        
        # Create appropriate context aggregator based on LLM type
        if args.llm == LLMServiceType.GEMINI:
            context_aggregator = llm.create_context_aggregator(context)
        else:  # For Groq and OpenAI-compatible LLMs
            context_aggregator = llm.create_context_aggregator(context)
        
        # Set up LLM search logger
        llm_search_logger = LLMSearchLoggerProcessor()
        
        # Set up RTVI processor for client UI events
        rtvi = RTVIProcessor(config=RTVIConfig(config=[]))
        
        # Assemble the pipeline
        pipeline_components = [
            transport.input(),
            stt,
            rtvi,
            context_aggregator.user(),
            llm,
        ]
        
        # Add search logger if search is enabled
        if args.enable_search and args.llm == LLMServiceType.GEMINI:
            pipeline_components.append(llm_search_logger)
            
        # Complete the pipeline
        pipeline_components.extend([
            tts, 
            transport.output(),
            context_aggregator.assistant(),
        ])
        
        # Create the pipeline
        pipeline = Pipeline(pipeline_components)
        
        # Set up task with observers if using Gemini
        observers = []
        if args.llm == LLMServiceType.GEMINI:
            observers.append(GoogleRTVIObserver(rtvi))
            
        task = PipelineTask(
            pipeline,
            params=PipelineParams(allow_interruptions=True),
            observers=observers,
        )
        
        # Set up event handlers
        @rtvi.event_handler("on_client_ready")
        async def on_client_ready(rtvi):
            await rtvi.set_bot_ready()

        @transport.event_handler("on_first_participant_joined")
        async def on_first_participant_joined(transport, participant):
            await task.queue_frames([context_aggregator.user().get_context_frame()])

        @transport.event_handler("on_participant_left")
        async def on_participant_left(transport, participant, reason):
            print(f"Participant left: {participant}")
            await task.cancel()
        
        # Run the pipeline
        runner = PipelineRunner()
        await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main()) 