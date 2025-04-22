from typing import Optional

from pipecat.observers.base_observer import BaseObserver 
from pipecat.frames.frames import BotInterruptionFrame, Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

class BotInterruptionObserver(BaseObserver):
	def __init__(self, transcript_handler):
		self._handler = transcript_handler

	async def on_push_frame(
		self,
		src: FrameProcessor,
		dst: FrameProcessor,
		frame: Frame,
		direction: FrameDirection,
		timestamp: int,
	):
		if isinstance(frame, BotInterruptionFrame):
			partial_text = frame.partial_text if hasattr(frame, 'partial_text') else ""

			if partial_text:
				await self._handler.on_bot_interrupted(partial_text)