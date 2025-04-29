from pipecat.frames.frames import BotInterruptionFrame
from pipecat.observers.base_observer import BaseObserver


class BotInterruptionObserver(BaseObserver):
	def __init__(self, transcript_handler):
		self.transcript_handler = transcript_handler

	async def on_push_frame(self, frame, **kwargs):
		if isinstance(frame, BotInterruptionFrame):
			partial_text = frame.partial_text if hasattr(frame, 'partial_text') else None
			await self.transcript_handler.on_bot_interrupted(partial_text)