import os
import sys
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Literal

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

from pipecat.transports.services.daily import DailyParams, DailyTransport
from pipecat.services.deepgram.stt import DeepgramSTTService

from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.google.rtvi import GoogleRTVIObserver
from pipecat.services.groq.llm import GroqLLMService

from pipecat.services.cartesia.tts import CartesiaTTSService

sys.path.append(str(Path(__file__).parent.parent))

from runner import configure
from interruption_observer import BotInterruptionObserver

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

logger.remove(0)
logger.add(sys.stderr, level="INFO")

PERSONA_FILE = Path(__file__).parent.parent / "prompts" / "bot_persona.txt"
KNOWLEDGE_BASE_FILE = Path(__file__).parent.parent / "prompts" / "bot_knowledge.txt"
CONVERSATION_STRATEGY_FILE = Path(__file__).parent.parent / "prompts" / "conversation_strategy.txt"
NEW_CLIENT_GREETING_FILE = Path(__file__).parent.parent / "prompts" / "new_client_greeting.txt"
RETURNING_CLIENT_GREETING_FILE = Path(__file__).parent.parent / "prompts" / "returning_client_greeting.txt"

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

def build_system_prompt(client_id, llm_type: str, client_name=None, is_returning_client=False, initial_greeting=None):
    """Build a structured system prompt with the requested sections"""
    call_highlight = load_call_highlight(client_id)
    expert_suggestion = load_expert_suggestions(client_id)
    is_returning_client = is_returning_client or bool(call_highlight or expert_suggestion)
    
    # Always use the same persona content regardless of llm_type
    persona_content = PERSONA
    
    # Start building prompt parts
    prompt_parts = [persona_content]
    
    # Add client name if available
    if client_name:
        prompt_parts.append(f"\n\n# CLIENT INFORMATION\nClient name: {client_name}")
    
    if call_highlight:
        prompt_parts.append("\n\n# PREVIOUS CALL HIGHLIGHT\n" + call_highlight)
    
    if expert_suggestion:
        prompt_parts.append("\n\n# EXPERT SUGGESTIONS\n" + expert_suggestion)
    
    # Append Conversation Strategy and Knowledge Base
    if CONVERSATION_STRATEGY:
        prompt_parts.append("\n\n# CONVERSATION STRATEGY\n" + CONVERSATION_STRATEGY)
    else:
         logger.warning(f"Conversation strategy file not found at {CONVERSATION_STRATEGY_FILE}, skipping.")

    if KNOWLEDGE_BASE:
        prompt_parts.append("\n\n# KNOWLEDGE BASE\n" + KNOWLEDGE_BASE)
    else:
        logger.warning(f"Knowledge base file not found at {KNOWLEDGE_BASE_FILE}, skipping.")
    
    # Add the initial greeting to use
    if initial_greeting:
        prompt_parts.append(f"\n\n# INITIAL GREETING - MANDATORY\nYou MUST use this EXACT greeting to start the conversation: \"{initial_greeting}\"\nDo not modify or rephrase this greeting in any way. Use it exactly as provided.\nYOUR FIRST RESPONSE MUST START WITH THIS EXACT GREETING.\n\nIMPORTANT FOR RETURNING CLIENTS: Always acknowledge you are calling them again and reference the previous conversation summary if provided.")
    
    final_prompt = "\n".join(prompt_parts)
    return final_prompt

def get_llm_service(llm_type: Literal["gemini", "groq"], model_name: str, system_prompt: str):
    """
    Returns the appropriate LLM service based on the type and model name.
    
    Args:
        llm_type: The type of LLM service to use ('gemini' or 'groq')
        model_name: The model name to use
        system_prompt: The system prompt to use
        
    Returns:
        The initialized LLM service
    """
    if llm_type == "gemini":
        return GoogleLLMService(
            api_key=os.getenv("GOOGLE_API_KEY"),
            model=model_name,
            system_instruction=system_prompt,
            streaming=True,
            tools=[],
        )
    elif llm_type == "groq":
        # Groq doesn't handle system_instruction the same way as Gemini
        return GroqLLMService(
            api_key=os.getenv("GROQ_API_KEY"),
            model=model_name,
            # No system_instruction parameter for Groq
            streaming=True,
            tools=[],
        )
    else:
        raise ValueError(f"Unsupported LLM type: {llm_type}")

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

async def main(call_id, client_id, llm_type="gemini", model_name="gemini-2.0-flash", 
               client_name=None, returning_client=False, previous_summary=""):
    # Create initial greeting based on client status
    initial_greeting = ""
    first_name = None
    if client_name and client_name.strip():
        name_parts = client_name.strip().split()
        if name_parts:
            first_name = name_parts[0]

    # Check for call highlights if summary not provided
    if returning_client and not previous_summary:
        call_highlight = load_call_highlight(client_id)
        if call_highlight:
            # Extract a brief summary from call highlights
            highlight_lines = call_highlight.split('\n')
            for line in highlight_lines:
                if line.strip() and not line.startswith('#'):
                    # Use first substantive line as a fallback summary
                    previous_summary = line.strip()[:100]
                    logger.info(f"Using extracted call highlight as summary: {previous_summary}")
                    break
    
    if returning_client:
        # Construct returning client greeting with summary
        greeting_start = f"Hi {first_name}," if first_name else "Hi there,"
        
        # Always include some form of previous conversation reference
        if previous_summary:
            # Ensure summary is concise - truncate if needed
            summary_snippet = previous_summary[:80] + ("..." if len(previous_summary) > 80 else "")
            initial_greeting = f"{greeting_start} it's Neha from Mosaic Asset Management. Last time we spoke about: {summary_snippet}. How have you been?"
        else:
            # Alternative greeting that still acknowledges prior conversation even without details
            initial_greeting = f"{greeting_start} it's Neha from Mosaic Asset Management calling you back. I hope things have been going well since we last spoke. How have you been?"
            logger.warning("No previous summary found for returning client, using generic returning client greeting")
    else:
        # New client greeting
        greeting_start = f"Hello {first_name}," if first_name else "Hello,"
        initial_greeting = f"{greeting_start} I'm Neha from Mosaic Asset Management, and I'm excited to connect with you today to discuss our exclusive alternate investment solutions. How are you doing today?"
    
    # Pass the initial greeting to build_system_prompt so it's part of the system prompt
    system_prompt = build_system_prompt(client_id, llm_type, client_name, returning_client, initial_greeting)
    
    transcript_logfile = os.path.join(TRANSCRIPT_LOGDIR, f"{call_id}.txt")
    async with aiohttp.ClientSession() as session:
        room_url, token = await configure(session)
    
    logger.info(f"Room URL from configure: {room_url}")
    logger.info(f"Token obtained: {bool(token)}")
    logger.info(f"Using LLM type: {llm_type}, model: {model_name}")
    logger.info(f"Client name: {client_name}")
    logger.info(f"Returning client: {returning_client}")
    logger.info(f"Previous summary: {previous_summary}")
    logger.info(f"Initial greeting: {initial_greeting}")
    
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
        voice_id="3b554273-4299-48b9-9aaf-eefd438e3941", 
        model="sonic-2-2025-04-16",
        params=CartesiaTTSService.InputParams(
            language=Language.EN,          
            speed=-0.3,                # fixed rate
            emotion=["positivity", "curiosity"]                     # no dynamic emotion
        ),
        output_format={
            "container": "mp3",
            "sample_rate": 24000
        }
    )

    # Determine if client is returning or new
    is_returning_client = returning_client # Use the flag passed from server
    
    # Get the appropriate LLM service based on the type and model
    llm = get_llm_service(llm_type, model_name, system_prompt)

    if llm_type == "groq":
        context = OpenAILLMContext([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Begin the conversation."}
        ])
        logger.info("Using Groq-specific context with system message in OpenAILLMContext")

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
        
        logger.info(f"Using initial greeting: {initial_greeting}")
        
        # Add the specific greeting message to context with stronger instruction
        initial_context = context_aggregator.user().get_context_frame()
        
        # Create a strong instruction emphasizing the mandatory greeting
        is_returning = "returning" if returning_client else "new"
        instruction = f"""MANDATORY INSTRUCTION: BEGIN YOUR FIRST RESPONSE WITH THIS EXACT GREETING WITHOUT ANY MODIFICATION:

"{initial_greeting}"

This is a {is_returning} client outbound call. YOU MUST USE THE EXACT GREETING ABOVE as your first words.
"""
        
        initial_context.content = instruction
        
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
    parser = argparse.ArgumentParser(description="BFSI Sales Agent Bot")
    parser.add_argument("--call_id", help="The ID of the call")
    parser.add_argument("--client_id", help="The ID of the client")
    parser.add_argument("--llm_type", choices=["gemini", "groq"], default="gemini", 
                        help="The type of LLM to use (gemini or groq)")
    parser.add_argument("--model_name", default="gemini-2.0-flash", 
                        help="The model name to use")
    parser.add_argument("--client_name", default=None,
                        help="The name of the client")
    parser.add_argument("--returning_client", type=int, default=0,
                        help="Whether this is a returning client (0 or 1)")
    parser.add_argument("--previous_summary", default="",
                        help="Summary of the previous call")
    args = parser.parse_args()
    
    # Validate model name based on LLM type
    valid_gemini_models = ["gemini-2.5-flash-preview-04-17", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
    valid_groq_models = ["meta-llama/llama-4-maverick-17b-128e-instruct", 
                         "meta-llama/llama-4-scout-17b-16e-instruct",
                         "llama-3.3-70b-versatile"]
    
    if args.llm_type == "gemini" and args.model_name not in valid_gemini_models:
        logger.warning(f"Invalid Gemini model: {args.model_name}. Using default: gemini-2.0-flash")
        args.model_name = "gemini-2.0-flash"
    elif args.llm_type == "groq" and args.model_name not in valid_groq_models:
        logger.warning(f"Invalid Groq model: {args.model_name}. Using default: llama-3.3-70b-versatile")
        args.model_name = "llama-3.3-70b-versatile"
    
    asyncio.run(main(args.call_id, args.client_id, args.llm_type, args.model_name, 
                     args.client_name, bool(args.returning_client), args.previous_summary))