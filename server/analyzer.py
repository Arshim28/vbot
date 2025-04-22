import os
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from google import genai
from google.genai import types

load_dotenv(dotenv_path='.env')

INSTRUCTION_FILE = Path(__file__).parent.parent / "prompts" / "analyst_system_prompt.txt"
with open(INSTRUCTION_FILE, "r") as f:
	INSTRUCTION = f.read()

TRANSCRIPT_LOGFILE = Path(__file__).parent.parent / "logs" / "transcript_log.txt"
with open(TRANSCRIPT_LOGFILE, "a") as f:
	TRANSCRIPT = f.read()

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

