import os
import sys
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
from pipecat.adapters.schemas.tool_schema import ToolsSchema
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

#Transport
from pipecat.transports.services.daily import DailyParams, DailyTransport

#STT
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.services.ultravox.stt import UltravoxSTTService

#LLM
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.google.llm import GoogleLLMService, LLMSearchResponseFrame
from pipecat.services.google.rtvi import GoogleRTVIObserver

#TTS
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.cartesia.tts import CartesiaTTSService

load_dotenv(dotenv_path='.env')

sys.path.append(str(Path(__file__).parent.parent))
from runner import configure

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

SYSTEM_INSTRUCTION_FILE = Path(__file__).parent.parent / "prompts" / "bot_system_prompt.txt"
with open(SYSTEM_INSTRUCTION_FILE, "r") as f:
	SYSTEM_INSTRUCTION = f.read()

TRANSCRIPT_LOGFILE = Path(__file__).parent.parent / "logs" / "transcript_log.txt"

class TranscriptHandler:
	def __init__(self, output_file: Optional[str]=None):
		self.messages: List[TranscriptionMessage] = []
		self.output_file: Optional[str] = output_file
		logger.debug(
			f"TranscriptHandler initialized {'with output file=' + output_file if output_file else 'with log output only'}"
		)

	async def save_message(self, message: TranscriptionMessage):
		timestamp = f"[{message.timestamp}]" if message.timestamp else ""
		line = f"{timestamp}{message.role}: {message.content}"

		logger.info(f"Transcript: {line}")

		if self.output_file:
			try:
				with open(self.output_file, "a", encoding="utf-8") as f:
					f.write(line + '\n')

			except Exception as e:
				logger.error(f"Error saving transcripts message to the file: {e}")

	async def on_transcript_update(
		self, processor: TranscriptProcessor, frame: TranscriptionUpdateFrame
	):
		logger.debug(f"Received	transcript update with {len(frame.messages)} new messages")

		for msg in frame.messages:
			self.messages.append(msg)
			await self.save_message(msg)

async def main():
	async with aiohttp.clientSession() as session:
		(room_url, token) = await configure(session)

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

		tts = DeepgramTTSService(
			api_key=os.getenv("DEEPGRAM_API_KEY"),
			voice="aura-helios-en",
			sample_rate=24000,
		)

		llm = GoogleLLMService(
			api_key=os.getenv("GOOGLE_API_KEY"),
			model="gemini-2.0-flash",
			system_instruction=SYSTEM_INSTRUCTION,
			tools=[],
		)

		context = OpenAILLMContext(
			[
				{
					"role": "user",
					"content": "Begin by greeting the user. Proceed with your instructions",
				}

			]
		)

		context_aggregator = llm.create_context_aggregator(context)

		rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

		transcript = TranscriptProcessor()
		transcript_handler = TranscriptHandler(output_file=TRANSCRIPT_LOGFILE)

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

		task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True), observers=[GoogleRTVIObserver(rtvi)])


		@rtvi.event_handler("on_client_ready")
		async def on_client_ready(rtvi):
			await rtvi.set_bot_ready()
			await task.queue_frames([context_aggregator.user().get_context_frame()])

		@transport.event_handler("on_first_participant_joined")
		async def on_first_participant_joined(transport, participant):
			logger.debug("First participant joined: {}", participant["id"])
			await transport.capture_participant_transcription(participant["id"])

		@transcript.event_handler("on_transcript_update")
		async def on_transcript_update(processor, frame):
			await transcript_handler.on_transcript_update(processor, frame)

		@transport.event_handler("on_participant_left")
		async def on_participant_left(transport, participant, reason):
			print("Participant left:" {participant})
			await task.cancel()

		runner = PipelineRunner()
		await runner.run(task)

if __name__ == "__main__":
	asyncio.run(main())

