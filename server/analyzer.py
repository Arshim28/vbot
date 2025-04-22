import os
import sys
import asyncio
from typing import Dict, List, Optional
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from google import genai
from google.genai import types

load_dotenv(dotenv_path='.env')

logger.remove(0)
logger.add(sys.stderr, level="INFO")

INSTRUCTION_FILE = Path(__file__).parent.parent / "prompts" / "analyst_system_prompt.txt"
with open(INSTRUCTION_FILE, "r") as f:
    INSTRUCTION = f.read()

TRANSCRIPT_LOGFILE = Path(__file__).parent.parent / "logs" / "transcript_log.txt"
EXPERT_SUGGESTION_FILE = Path(__file__).parent.parent / "prompts" / "expert_suggestion.txt"

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

async def read_transcript() -> str:
    try:
        if not Path(TRANSCRIPT_LOGFILE).exists():
            logger.warning(f"Transcript file not found: {TRANSCRIPT_LOGFILE}")
            return ""
        
        with open(TRANSCRIPT_LOGFILE, "r") as f:
            transcript = f.read()
        
        logger.info(f"Read transcript with {len(transcript.splitlines())} lines")
        return transcript
    except Exception as e:
        logger.error(f"Error reading transcript: {e}")
        return ""

async def analyze_conversation(transcript: str) -> str:
    if not transcript:
        return "No transcript data available for analysis."
    
    logger.info("Analyzing conversation transcript")
    
    prompt = f"""
{INSTRUCTION}

Based on the following conversation transcript between our sales agent and a potential client, 
please analyze the client's profile and provide recommendations for future interactions.

Please analyze for the following parameters:
1. Is the client a distributor or investor?
2. Does the client understand credit fund investing?
3. Does the client have 1 crore to invest?
4. Does the client know Maneesh Dangi?
5. Is the client a sophisticated or novice investor?
6. Is the client optimistic or skeptical about our offering?
7. Does the client want to have a Zoom call?
8. Should we call this client again?
9. Is the client interested in talking to our sales executive?
10. Is the client proficient in English or comfortable in another language?

Format your response as specific, concise points that can be used to tailor our approach in future conversations.

TRANSCRIPT:
{transcript}
"""

    config = types.GenerateContentConfig(
        temperature=0.2,
        top_p=0.95,
        top_k=40,
        max_output_tokens=2048,
    )
    
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.5-pro-exp-03-25",
            contents=prompt,
            config=config,
        )
        analysis = response.text
        logger.info("Conversation analysis completed")
        return analysis
    except Exception as e:
        logger.error(f"Error analyzing conversation: {e}")
        return f"Error analyzing conversation: {str(e)}"

async def write_analysis(analysis: str) -> None:
    try:
        os.makedirs(os.path.dirname(EXPERT_SUGGESTION_FILE), exist_ok=True)
        with open(EXPERT_SUGGESTION_FILE, "w") as f:
            f.write(analysis)
        logger.info(f"Analysis written to {EXPERT_SUGGESTION_FILE}")
    except Exception as e:
        logger.error(f"Error writing analysis: {e}")

async def main() -> None:
    logger.info("Starting conversation analysis")

    transcript = await read_transcript()    
    analysis = await analyze_conversation(transcript)    
    await write_analysis(analysis)
    
    logger.info("Conversation analysis completed")

if __name__ == "__main__":
    asyncio.run(main())