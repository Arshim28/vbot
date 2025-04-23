import asyncio
import os
import json
import re
import pytz
import datetime
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from typing import Optional, Dict, Any

from firestore_db import VoiceAgentDB

API_KEY = os.getenv("GOOGLE_API_KEY")
DEFAULT_MODEL_NAME = "gemini-2.0-flash"

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

    async def format_transcript(self, transcript_path):
        formatted_transcript = []

        try:
            with open(transcript_path, 'r') as file:
                transcript_text = file.read()
            
            pattern = r'\[([^\]]+)\]\s+(user|assistant):\s+(.*?)(?=\n\[|$)'
            matches = re.findall(pattern, transcript_text, re.DOTALL)

            for match in matches:
                timestamp_str, speaker, content = match
                timestamp = datetime.datetime.fromisoformat(timestamp_str.replace('T', ' ').replace('Z', '+00:00'))
                india_tz = pytz.timezone('Asia/Kolkata')
                timestamp_ist = timestamp.astimezone(india_tz)
                formatted_timestamp = timestamp_ist.strftime("%B %d, %Y at %I:%M:%S %p UTC+5:30")
                content = content.strip()
                entry = {
                    "content": content,
                    "speaker": speaker,
                    "timestamp": formatted_timestamp
                }
                formatted_transcript.append(entry)

            return formatted_transcript

        except Exception as e:
            print(f"Error formatting transcript: {e}")
            return []

    async def process(self, transcript_path, call_id, customer_id):
        transcript = await self.format_transcript(transcript_path)
        self.db_conn.add_call_transcript(call_id, transcript)
        profile_data = await self.generate_structured_json_async(transcript)
        self.db_conn.end_call(call_id, profile_data.get("callSummary", ""), profile_data.get("tags", []))
        self.db_conn.update_client_profile(customer_id, profile_data)
        
        
    async def generate_structured_json_async(self, transcript) -> Optional[Dict[str, Any]]:
        generation_config = GenerationConfig(
            response_mime_type="application/json",
        )

        with open("prompts/post_call_prompt.txt", "r") as f:
            system_message = f.read()

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
                    return parsed_json
                except json.JSONDecodeError as e:
                    print(f"Error decoding JSON: {e}\nReceived: {json_string}")
                    return None
            else:
                print(f"Warning: Empty or blocked response. Feedback: {response.prompt_feedback}")
                return None

        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return None

async def main():
    obj = PostCallProcessor()
    await obj.process("/Users/sparsh/Desktop/vbot/logs/transcript_log.txt", "67a69b3d-94ab-49a0-994f-23931deec804", "waYg1V1EmGlsbPbskk38")
    
if __name__ == "__main__":
    asyncio.run(main())