import os
import sys
import asyncio
from pathlib import Path
from typing import List, Optional, Dict

import argparse

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

# TTS - Replace ElevenLabs with Cartesia
from pipecat.services.cartesia.tts import CartesiaTTSService

sys.path.append(str(Path(__file__).parent.parent))

from runner import configure
from interruption_observer import BotInterruptionObserver

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

logger.remove(0)
logger.add(sys.stderr, level="INFO")

PERSONA_FILE = Path(__file__).parent.parent / "prompts" / "bot_persona.txt"
KNOWLEDGE_BASE_FILE = Path(__file__).parent.parent / "prompts" / "bot_knowledge.txt"
SYSTEM_INSTRUCTION_FILE = Path(__file__).parent.parent / "prompts" / "bot_system_prompt.txt"
CONVERSATION_STRATEGY_FILE = Path(__file__).parent.parent / "prompts" / "conversation_strategy.txt"
NEW_CLIENT_GREETING_FILE = Path(__file__).parent.parent / "prompts" / "new_client_greeting.txt"
RETURNING_CLIENT_GREETING_FILE = Path(__file__).parent.parent / "prompts" / "returning_client_greeting.txt"

if SYSTEM_INSTRUCTION_FILE.exists() and (not PERSONA_FILE.exists() or not KNOWLEDGE_BASE_FILE.exists()):
    with open(SYSTEM_INSTRUCTION_FILE, "r") as f:
        full_prompt = f.read()
    
    parts = full_prompt.split("## INFORMATION")
    
    if len(parts) > 1:
        with open(PERSONA_FILE, "w") as f:
            f.write(parts[0].strip())
        logger.info(f"Created persona prompt at {PERSONA_FILE}")
        
        with open(KNOWLEDGE_BASE_FILE, "w") as f:
            f.write("## INFORMATION" + parts[1].strip())
        logger.info(f"Created knowledge base prompt at {KNOWLEDGE_BASE_FILE}")
    else:
        with open(PERSONA_FILE, "w") as f:
            f.write(full_prompt)
        logger.info(f"Created persona prompt at {PERSONA_FILE}")
        
        with open(KNOWLEDGE_BASE_FILE, "w") as f:
            f.write(full_prompt)
        logger.info(f"Created knowledge base prompt at {KNOWLEDGE_BASE_FILE}")

if PERSONA_FILE.exists():
    with open(PERSONA_FILE, "r") as f:
        PERSONA = f.read()
else:
    PERSONA = ""
    logger.warning(f"Persona file not found at {PERSONA_FILE}")

if KNOWLEDGE_BASE_FILE.exists():
    with open(KNOWLEDGE_BASE_FILE, "r") as f:
        KNOWLEDGE_BASE = f.read()
else:
    KNOWLEDGE_BASE = ""
    logger.warning(f"Knowledge base file not found at {KNOWLEDGE_BASE_FILE}")

if SYSTEM_INSTRUCTION_FILE.exists():
    with open(SYSTEM_INSTRUCTION_FILE, "r") as f:
        SYSTEM_INSTRUCTION = f.read()
else:
    SYSTEM_INSTRUCTION = PERSONA + "\n\n" + KNOWLEDGE_BASE
    logger.warning(f"System instruction file not found, using combined persona and knowledge base")

if CONVERSATION_STRATEGY_FILE.exists():
    with open(CONVERSATION_STRATEGY_FILE, "r") as f:
        CONVERSATION_STRATEGY = f.read()
else:
    CONVERSATION_STRATEGY = ""
    logger.warning(f"Conversation strategy file not found at {CONVERSATION_STRATEGY_FILE}")

if NEW_CLIENT_GREETING_FILE.exists():
    with open(NEW_CLIENT_GREETING_FILE, "r") as f:
        NEW_CLIENT_GREETING = f.read().strip()
else:
    NEW_CLIENT_GREETING = "Hello! I'm Neha from Mosaic Asset Management, and I'm excited to connect with you today to discuss our exclusive alternate investment solutions. How are you doing today?"
    logger.warning(f"New client greeting file not found at {NEW_CLIENT_GREETING_FILE}")

if RETURNING_CLIENT_GREETING_FILE.exists():
    with open(RETURNING_CLIENT_GREETING_FILE, "r") as f:
        RETURNING_CLIENT_GREETING = f.read().strip()
else:
    RETURNING_CLIENT_GREETING = "Welcome back! It's Neha from Mosaic Asset Management. It's great to speak with you again. I hope you've been well since our last conversation."
    logger.warning(f"Returning client greeting file not found at {RETURNING_CLIENT_GREETING_FILE}")

EXPERT_SUGGESTION_DIR = Path(__file__).parent.parent / "expert_opinion"
CALL_HIGHLIGHT_DIR = Path(__file__).parent.parent / "call_highlights"
TRANSCRIPT_LOGDIR = Path(__file__).parent.parent / "logs"

def load_call_highlight(client_id):
    os.makedirs(CALL_HIGHLIGHT_DIR, exist_ok=True)
    highlight_file = os.path.join(CALL_HIGHLIGHT_DIR, f"{client_id}_highlights.txt")
    
    if os.path.exists(highlight_file):
        try:
            with open(highlight_file, "r") as f:
                highlight = f.read().strip()
                if highlight:
                    logger.info("Loaded previous call highlight")
                    return highlight
        except Exception as e:
            logger.error(f"Error loading call highlight: {e}")
    
    logger.info("No previous call highlight found")
    return ""

def load_expert_suggestions(client_id):
    os.makedirs(EXPERT_SUGGESTION_DIR, exist_ok=True)
    expert_suggestion_file = os.path.join(EXPERT_SUGGESTION_DIR, f"{client_id}_exp_opinion.txt")
    
    if os.path.exists(expert_suggestion_file):
        try:
            with open(expert_suggestion_file, "r") as f:
                expert_suggestions = f.read().strip()
                if expert_suggestions:
                    logger.info("Loaded expert suggestions")
                    return expert_suggestions
        except Exception as e:
            logger.error(f"Error loading expert suggestions: {e}")
    
    logger.info("No expert suggestions found")
    return ""

def build_system_prompt(client_id):
    """Build a structured system prompt with the requested sections"""
    call_highlight = load_call_highlight(client_id)
    expert_suggestion = load_expert_suggestions(client_id)
    is_returning_client = bool(call_highlight or expert_suggestion)
    
    if PERSONA_FILE.exists():
        with open(PERSONA_FILE, "r") as f:
            persona_content = f.read()
        prompt_parts = [persona_content]
    else:
        prompt_parts = [PERSONA]
    
    if call_highlight:
        prompt_parts.append("\n\n# PREVIOUS CALL HIGHLIGHT\n" + call_highlight)
    
    if expert_suggestion:
        prompt_parts.append("\n\n# EXPERT SUGGESTIONS\n" + expert_suggestion)
    
    prompt_parts.append("\n\n# CONVERSATION STRATEGY\n" + CONVERSATION_STRATEGY)
    prompt_parts.append("\n\n" + KNOWLEDGE_BASE)
    
    return "\n".join(prompt_parts)

class TranscriptHandler:
    def __init__(self, output_file: Optional[str]=None):
        self.messages: List[TranscriptionMessage] = []
        self.output_file: Optional[str] = output_file
        self.current_partial: Dict[str, str] = {}
        logger.debug(
            f"TranscriptHandler initialized {'with output file=' + str(output_file) if output_file else 'with log output only'}"
        )

    async def save_message(self, message: TranscriptionMessage):
        timestamp = f"[{message.timestamp}] " if message.timestamp else ""
        line = f"{timestamp}{message.role}: {message.content}"

        logger.info(f"Transcript: {line}")

        if self.output_file:
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

async def main(call_id, client_id):
    system_prompt = build_system_prompt(client_id)
    print("#"*30, "PROMPT", "#"*30)
    print(system_prompt)
    
    transcript_logfile = os.path.join(TRANSCRIPT_LOGDIR, f"{call_id}.txt")
    async with aiohttp.ClientSession() as session:
        room_url, token = await configure(session)
    
    logger.info(f"Room URL from configure: {room_url}")
    logger.info(f"Token obtained: {bool(token)}")
    
    try:
        if os.path.exists(transcript_logfile):
            with open(transcript_logfile, "w") as f:
                f.write("")
            logger.info(f"Cleared previous transcript file: {transcript_logfile}")
    except Exception as e:
        logger.error(f"Failed to clear transcript file: {e}")

    transport = DailyTransport(
        room_url,
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

    # Using Cartesia TTS with appropriate voice
    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id="3b554273-4299-48b9-9aaf-eefd438e3941",  # Indian Lady voice ID from Cartesia
        model="sonic-2",
        params=CartesiaTTSService.InputParams(
            language=Language.EN,          # fixed language
            speed="normal",                # fixed rate
            emotion=[]                     # no dynamic emotion
        ),
        output_format={
            "container": "mp3",
            "sample_rate": 24000
        }
    )

    # Determine if client is returning or new
    call_highlight = load_call_highlight(client_id)
    expert_suggestion = load_expert_suggestions(client_id)
    is_returning_client = bool(call_highlight or expert_suggestion)
    
    # Create initial greeting based on client status
    initial_greeting = RETURNING_CLIENT_GREETING if is_returning_client else NEW_CLIENT_GREETING
    
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
                "content": "Begin the conversation.",
            }
        ]
    )

    context_aggregator = llm.create_context_aggregator(context)
    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))
    transcript = TranscriptProcessor()
    transcript_handler = TranscriptHandler(output_file=transcript_logfile)
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
        params=PipelineParams(
            allow_interruptions=True
        ), 
        observers=[GoogleRTVIObserver(rtvi), interrupt_observer]
    )

    # Configure event handlers
    @rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("RTVI client ready, setting bot ready")
        await rtvi.set_bot_ready()
        
        # Set up initial greeting based on client type
        logger.info(f"Using {'returning' if is_returning_client else 'new'} client greeting")
        
        # Add a more specific greeting message to set the tone
        initial_context = context_aggregator.user().get_context_frame()
        initial_context.content = f"Use this exact greeting: {initial_greeting}"
        
        await task.queue_frames([initial_context])

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info(f"First participant joined: {participant['id']}")
        await transport.capture_participant_transcription(participant["id"])
        logger.info("Starting transcription capture")

    @transcript.event_handler("on_transcript_update")
    async def on_transcript_update(processor, frame):
        await transcript_handler.on_transcript_update(processor, frame)

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info(f"Participant left: {participant} with reason: {reason}")
        await task.cancel()
    
    
    runner = PipelineRunner()
    await runner.run(task)
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Your script description")
    parser.add_argument("--call_id", help="The ID of the call")
    parser.add_argument("--client_id", help="The ID of the client")
    args = parser.parse_args()
    asyncio.run(main(args.call_id, args.client_id))