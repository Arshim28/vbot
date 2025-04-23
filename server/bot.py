import os
import sys
import uuid
import asyncio
import argparse
from pathlib import Path
from typing import List, Optional

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TranscriptionMessage, TranscriptionUpdateFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIProcessor
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.transcriptions.language import Language
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

# Transport
from pipecat.transports.services.daily import DailyParams, DailyTransport

# STT
from pipecat.services.deepgram.stt import DeepgramSTTService

# LLM
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.google.rtvi import GoogleRTVIObserver

# TTS
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.cartesia.tts import CartesiaTTSService

sys.path.append(str(Path(__file__).parent.parent))

from runner import configure_with_args
from interruption_observer import BotInterruptionObserver

load_dotenv(dotenv_path='.env')

logger.remove(0)
logger.add(sys.stderr, level="INFO")

SYSTEM_INSTRUCTION_FILE = Path(__file__).parent.parent / "prompts" / "bot_system_prompt.txt"
with open(SYSTEM_INSTRUCTION_FILE, "r") as f:
    SYSTEM_INSTRUCTION = f.read()

class TranscriptHandler:
    def __init__(self, call_id: Optional[str]=None, client_id: Optional[str]=None, output_dir: Optional[str]=None):
        self.messages: List[TranscriptionMessage] = []
        self.call_id = call_id or str(uuid.uuid4())  # Generate a UUID if call_id not provided
        self.client_id = client_id
        self.output_dir = output_dir or Path(__file__).parent.parent / "logs"
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Always use the call_id_transcript_log.txt format
        self.output_file = self.output_dir / f"{self.call_id}_transcript_log.txt"
            
        self.current_partial: dict = {}
        logger.debug(
            f"TranscriptHandler initialized with call_id={self.call_id}, client_id={client_id}, output_file={self.output_file}"
        )

    async def save_message(self, message: TranscriptionMessage):
        timestamp = f"[{message.timestamp}] " if message.timestamp else ""
        line = f"{timestamp}{message.role}: {message.content}"

        logger.info(f"Transcript: {line}")

        try:
            with open(self.output_file, "a", encoding="utf-8") as f:
                f.write(line + '\n')
        except Exception as e:
            logger.error(f"Error saving transcript message to file: {e}")

    async def on_transcript_update(
        self, processor: TranscriptProcessor, frame: TranscriptionUpdateFrame
    ):
        logger.debug(f"Received transcript update with {len(frame.messages)} new messages")

        for msg in frame.messages:
            self.messages.append(msg)
            await self.save_message(msg)

    async def on_bot_interrupted(self, partial_text: str):
        if not partial_text:
            return

        import datetime
        timestamp = datetime.datetime.now().isoformat()

        interrupted_msg = TranscriptionMessage(
            role='assistant',
            content=f"{partial_text} [interrupted]",
            timestamp=timestamp,
            final=True
        )

        self.messages.append(interrupted_msg)
        await self.save_message(interrupted_msg)

        logger.info(f"Bot interrupted with partial text: {partial_text}")
        self.current_partial.pop('assistant', None)

def load_expert_suggestions(client_id: Optional[str]=None):
    """Load expert suggestions for the client if available"""
    if client_id:
        expert_opinion_dir = Path(__file__).parent.parent / "expert_opinion"
        expert_suggestion_file = expert_opinion_dir / f"{client_id}_exp_opinion.txt"
        
        if expert_suggestion_file.exists():
            try:
                with open(expert_suggestion_file, "r") as f:
                    expert_suggestions = f.read().strip()
                if expert_suggestions:
                    logger.info(f"Loaded expert suggestions for client {client_id} from {expert_suggestion_file}")
                    return f"{SYSTEM_INSTRUCTION}\n\nADDITIONAL CONTEXT ABOUT THIS CLIENT:\n{expert_suggestions}"
            except Exception as e:
                logger.error(f"Error loading expert suggestions: {e}")
    
    logger.info("Using default system prompt")
    return SYSTEM_INSTRUCTION

async def main():
    # Set up parser for args
    parser = argparse.ArgumentParser(description="BFSI Sales Agent Bot")
    parser.add_argument("-u", "--url", type=str, required=True, help="URL of the Daily room to join")
    parser.add_argument("-t", "--token", type=str, required=True, help="Token for the Daily room")
    parser.add_argument("--call_id", type=str, help="Call ID")
    parser.add_argument("--client_id", type=str, help="Client ID")
    
    args, unknown = parser.parse_known_args()
    
    url = args.url
    token = args.token
    call_id = args.call_id
    client_id = args.client_id
    
    # If call_id is not provided, generate one
    if not call_id:
        call_id = str(uuid.uuid4())
    
    logger.info(f"Starting bot with room URL: {url}, call_id: {call_id}, client_id: {client_id}")
    
    # Create expert_opinion directory if it doesn't exist
    expert_opinion_dir = Path(__file__).parent.parent / "expert_opinion"
    os.makedirs(expert_opinion_dir, exist_ok=True)
    
    # Load expert suggestions for this client (if available)
    system_prompt = load_expert_suggestions(client_id)
    
    async with aiohttp.ClientSession() as session:
        transport = DailyTransport(
            url,
            token,
            "BFSI Sales Agent",
            DailyParams(
                audio_out_enabled=True,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                vad_audio_passthrough=True,
            ),
        )

        stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))

        tts = DeepgramTTSService(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            voice="aura-helios-en",
            sample_rate=24000,
        )

        llm = GoogleLLMService(
            api_key=os.getenv("GOOGLE_API_KEY"),
            model="gemini-2.0-flash",
            system_instruction=system_prompt,
            streaming=True,
            tools=[],
        )

        context = OpenAILLMContext(
            [
                {
                    "role": "user",
                    "content": "Begin by greeting the user. Proceed with your instructions.",
                }
            ]
        )

        context_aggregator = llm.create_context_aggregator(context)

        rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

        transcript = TranscriptProcessor()
        transcript_handler = TranscriptHandler(call_id=call_id, client_id=client_id)
        interrupt_observer = BotInterruptionObserver(transcript_handler)

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                rtvi,
                transcript.user(),
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                transcript.assistant(),
                context_aggregator.assistant(),
            ]
        )

        task = PipelineTask(
            pipeline, 
            params=PipelineParams(allow_interruptions=True), 
            observers=[GoogleRTVIObserver(rtvi), interrupt_observer]
        )

        @rtvi.event_handler("on_client_ready")
        async def on_client_ready(rtvi):
            await rtvi.set_bot_ready()
            await task.queue_frames([context_aggregator.user().get_context_frame()])

        @transport.event_handler("on_first_participant_joined")
        async def on_first_participant_joined(transport, participant):
            logger.info(f"First participant joined: {participant['id']}")
            await transport.capture_participant_transcription(participant["id"])

        @transcript.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            await transcript_handler.on_transcript_update(processor, frame)

        @transport.event_handler("on_participant_left")
        async def on_participant_left(transport, participant, reason):
            logger.info(f"Participant left: {participant}")
            await task.cancel()

        runner = PipelineRunner()
        await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main())