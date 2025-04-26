import asyncio
import os
import json
import re
import pytz
import datetime
import argparse
from pathlib import Path
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from typing import Optional, Dict, Any

from firestore_db import VoiceAgentDB
from dotenv import load_dotenv
from loguru import logger

load_dotenv(dotenv_path='.env')

API_KEY = os.getenv("GOOGLE_API_KEY")
DEFAULT_MODEL_NAME = "gemini-2.0-flash"

logger.remove(0)
logger.add(
    os.sys.stderr,
    format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}",
    level="INFO",
)

class PostCallProcessor:
    def __init__(self, api_key: Optional[str] = API_KEY, model_name: str = DEFAULT_MODEL_NAME):
        if not api_key:
            raise ValueError("API key not found. Set GOOGLE_API_KEY environment variable.")
        self.api_key = api_key
        self.model_name = model_name
        self._configure_genai()
        self.db_conn = VoiceAgentDB()

    def _configure_genai(self):
        genai.configure(api_key=self.api_key)

    async def format_transcript(self, call_id: str):
        formatted_transcript = []
        transcript_path = Path(__file__).parent.parent / "logs" / f"{call_id}_transcript.txt"

        try:
            if not transcript_path.exists():
                logger.error(f"Transcript file not found: {transcript_path}")
                return []
                
            with open(transcript_path, 'r') as file:
                transcript_text = file.read()
            
            pattern = r'\[([^\]]+)\]\s+(user|assistant):\s+(.*?)(?=\n\[|$)'
            matches = re.findall(pattern, transcript_text, re.DOTALL)

            for match in matches:
                timestamp_str, speaker, content = match
                try:
                    timestamp = datetime.datetime.fromisoformat(timestamp_str.replace('T', ' ').replace('Z', '+00:00'))
                    india_tz = pytz.timezone('Asia/Kolkata')
                    timestamp_ist = timestamp.astimezone(india_tz)
                    formatted_timestamp = timestamp_ist.strftime("%B %d, %Y at %I:%M:%S %p UTC+5:30")
                except Exception as e:
                    logger.warning(f"Error parsing timestamp '{timestamp_str}': {e}")
                    formatted_timestamp = timestamp_str
                    
                content = content.strip()
                entry = {
                    "content": content,
                    "speaker": speaker,
                    "timestamp": formatted_timestamp
                }
                formatted_transcript.append(entry)

            logger.info(f"Formatted transcript with {len(formatted_transcript)} entries")
            return formatted_transcript

        except Exception as e:
            logger.error(f"Error formatting transcript: {e}")
            return []

    async def process(self, call_id: str, client_id: str):
        logger.info(f"Processing call {call_id} for client {client_id}")
        
        # Format the transcript
        transcript = await self.format_transcript(call_id)
        if not transcript:
            logger.error(f"No transcript found for call {call_id}")
            return
            
        # Store the formatted transcript in the database
        success = self.db_conn.add_call_transcript(call_id, transcript)
        if not success:
            logger.error(f"Failed to add transcript to database for call {call_id}")
        else:
            logger.info(f"Added transcript to database for call {call_id}")
        
        # Generate structured data from the transcript
        profile_data = await self.generate_structured_json_async(transcript)
        if not profile_data:
            logger.error("Failed to generate profile data from transcript")
            return
            
        # End the call in the database with summary and tags
        success = self.db_conn.end_call(
            call_id,
            profile_data.get("callSummary", "Call completed"),
            profile_data.get("tags", [])
        )
        if not success:
            logger.error(f"Failed to update call {call_id} as ended in database")
        else:
            logger.info(f"Call {call_id} marked as ended in database")
        
        # Update the client profile with new information
        success = self.db_conn.update_client_profile(client_id, profile_data)
        if not success:
            logger.error(f"Failed to update client profile for client {client_id}")
        else:
            logger.info(f"Updated client profile for client {client_id}")
        
        logger.info(f"Post-call processing completed for call {call_id}")
        
    async def generate_structured_json_async(self, transcript) -> Optional[Dict[str, Any]]:
        prompt_file = Path(__file__).parent.parent / "prompts" / "post_call_prompt.txt"
        
        if not prompt_file.exists():
            logger.error(f"Prompt file not found: {prompt_file}")
            return None
            
        with open(prompt_file, "r") as f:
            system_message = f.read()

        generation_config = GenerationConfig(
            response_mime_type="application/json",
        )

        model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_message,
            generation_config=generation_config,
        )

        try:        
            user_prompt = f"""
                TRANSCRIPT: 
                {transcript}
            """

            response = await model.generate_content_async(user_prompt)

            if response.parts:
                json_string = response.text
                try:
                    parsed_json = json.loads(json_string)
                    logger.info("Successfully generated JSON from transcript")
                    return parsed_json
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON: {e}\nReceived: {json_string}")
                    return None
            else:
                logger.warning(f"Warning: Empty or blocked response. Feedback: {response.prompt_feedback}")
                return None

        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            return None

async def main():
    print("#"*30, "POST PROCESSOR CALLED", "#"*30)
    parser = argparse.ArgumentParser(description="Post-call processing")
    parser.add_argument("--call_id", type=str, required=True, help="Call ID")
    parser.add_argument("--client_id", type=str, required=True, help="Client ID")
    
    args = parser.parse_args()
    
    call_id = args.call_id
    client_id = args.client_id
    
    logger.info(f"Starting post-call processing for call {call_id}, client {client_id}")
    
    processor = PostCallProcessor()
    await processor.process(call_id, client_id)
    
if __name__ == "__main__":
    asyncio.run(main())