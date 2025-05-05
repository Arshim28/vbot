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
from sqlite_db import SQLiteVoiceAgentDB
from dotenv import load_dotenv
from loguru import logger

# Load .env from project root
load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

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
        
        # Initialize both databases
        self.firestore_db = VoiceAgentDB()
        self.sqlite_db = SQLiteVoiceAgentDB()

    def _configure_genai(self):
        genai.configure(api_key=self.api_key)

    async def format_transcript(self, call_id: str):
        formatted_transcript = []
        transcript_path = Path(__file__).parent.parent / "logs" / f"{call_id}.txt"

        try:
            if not transcript_path.exists():
                logger.error(f"Transcript file not found: {transcript_path}")
                return []
                
            with open(transcript_path, 'r') as file:
                transcript_text = file.read()
            
            # Save raw transcript to SQLite
            self.sqlite_db.update_call_transcript(call_id, transcript_text)
            logger.info(f"Saved raw transcript to SQLite database for call {call_id}")
            
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

    async def update_call_highlight(self, client_id: str, profile_data: Dict[str, Any]):
        """Update the call highlight based on profile data generated from the transcript"""
        try:
            call_highlight_dir = Path(__file__).parent.parent / "call_highlights"
            os.makedirs(call_highlight_dir, exist_ok=True)
            highlight_file = call_highlight_dir / f"{client_id}_highlights.txt"
            
            # Check if a highlight already exists
            existing_highlight = ""
            if highlight_file.exists():
                with open(highlight_file, "r") as f:
                    existing_highlight = f.read().strip()
            
            # Extract relevant data from profile data
            notes = profile_data.get("notes", "")
            summary = profile_data.get("callSummary", "")
            
            # Build a more comprehensive highlight
            client_type = f"Client Type: {profile_data.get('clientType', 'Unknown')}"
            investment_capacity = f"Minimum Investment Capacity: {'Yes' if profile_data.get('hasMinimumInvestment') is True else 'No' if profile_data.get('hasMinimumInvestment') is False else 'Unknown'}"
            sophistication = f"Investor Sophistication: {profile_data.get('investorSophistication', 'Unknown')}"
            attitude = f"Attitude: {profile_data.get('attitudeTowardsOffering', 'Unknown')}"
            
            # Include the transcript if available
            transcript_text = ""
            transcript = profile_data.get("transcript", [])
            if transcript:
                transcript_text = "\n\n# Full Conversation\n"
                for entry in transcript:
                    speaker = "Neha" if entry.get("speaker") == "assistant" else "Client"
                    timestamp = entry.get("timestamp", "")
                    content = entry.get("content", "")
                    transcript_text += f"[{timestamp}] {speaker}: {content}\n"
            
            # Create a new highlight that combines existing information with new insights
            new_highlight = f"""# Client Profile
{client_type}
{investment_capacity}
{sophistication}
{attitude}

# Latest Call Summary
{summary}

# Key Notes
{notes}
{transcript_text}
"""
            
            # If there's existing content, append it with a timestamp
            if existing_highlight and existing_highlight != "No transcript data available for highlights.":
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_highlight += f"\n# Previous Highlights ({timestamp})\n{existing_highlight}"
            
            # Write the updated highlight
            with open(highlight_file, "w") as f:
                f.write(new_highlight)
                
            logger.info(f"Updated call highlight for client {client_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating call highlight: {e}")
            return False

    async def process(self, call_id: str, client_id: str):
        logger.info(f"Processing call {call_id} for client {client_id}")
        
        # Format the transcript
        transcript = await self.format_transcript(call_id)
        if not transcript:
            logger.error(f"No transcript found for call {call_id}")
            return
            
        # Store the formatted transcript in both databases
        success_firestore = self.firestore_db.add_call_transcript(call_id, transcript)
        if not success_firestore:
            logger.error(f"Failed to add transcript to Firestore for call {call_id}")
        else:
            logger.info(f"Added transcript to Firestore for call {call_id}")
        
        # Generate structured data from the transcript
        profile_data = await self.generate_structured_json_async(transcript)
        if not profile_data:
            logger.error("Failed to generate profile data from transcript")
            return
        
        # Add the transcript to the profile data
        profile_data["transcript"] = transcript
        
        # Get the summary for updating SQLite
        summary = profile_data.get("callSummary", "Call completed")
        
        # Update the SQLite database with the summary
        try:
            # Store summary in SQLite - Update this to include a method to store the summary
            # First convert transcript to string format for SQLite storage
            transcript_text = ""
            for entry in transcript:
                speaker = entry.get("speaker", "")
                content = entry.get("content", "")
                timestamp = entry.get("timestamp", "")
                transcript_text += f"[{timestamp}] {speaker}: {content}\n"
            
            # Save both transcript and summary to SQLite
            self.sqlite_db.update_call_transcript(call_id, transcript_text)
            
            # Add a method to update summary in SQLite
            # Example: self.sqlite_db.update_call_summary(call_id, summary)
            # If this method doesn't exist yet, you'll need to implement it
            if hasattr(self.sqlite_db, 'update_call_summary'):
                self.sqlite_db.update_call_summary(call_id, summary)
                logger.info(f"Updated call summary in SQLite for call {call_id}")
            else:
                logger.warning("SQLite database doesn't have update_call_summary method. Summary not stored.")
                
            logger.info(f"Updated transcript in SQLite database for call {call_id}")
        except Exception as e:
            logger.error(f"Failed to update SQLite database: {e}")
            
        # End the call in the Firestore database with summary and tags
        success = self.firestore_db.end_call(
            call_id,
            summary,
            profile_data.get("tags", [])
        )
        if not success:
            logger.error(f"Failed to update call {call_id} as ended in Firestore")
        else:
            logger.info(f"Call {call_id} marked as ended in Firestore")
        
        # Update call highlight with profile data
        await self.update_call_highlight(client_id, profile_data)
        
        # Update the client profile in Firestore
        success = self.firestore_db.update_client_profile(client_id, profile_data)
        if not success:
            logger.error(f"Failed to update client profile in Firestore for client {client_id}")
        else:
            logger.info(f"Updated client profile in Firestore for client {client_id}")
        
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
    
    processor = PostCallProcessor()
    await processor.process(call_id, client_id)

if __name__ == "__main__":
    asyncio.run(main())