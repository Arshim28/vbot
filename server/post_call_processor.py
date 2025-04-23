import os
import sys
import json
import asyncio
from typing import Dict, Any, Optional
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from google import genai
from google.genai import types

from firestore_db import VoiceAgentDB 
load_dotenv(dotenv_path='.env')

class PostCallProcessor:
    def __init__(self, db_path: Optional[str] = 'serviceAccountKey.json'):
        self.db = VoiceAgentDB(service_account_path=db_path)
        self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        
    async def process_call(self, customer_id: str, transcript_path: str, call_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            transcript = await self._read_transcript(transcript_path)
            analysis = await self._analyze_transcript(transcript)
            await self._update_database(customer_id, analysis, call_id)
            
        except Exception as e:
            logger.error(f"Error processing call: {e}")
            raise

    async def _read_transcript(self, transcript_path: str) -> str:
        try:
            with open(transcript_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading transcript file: {e}")
            raise

    async def _analyze_transcript(self, transcript: str) -> Dict[str, Any]:
        
        config = types.GenerateContentConfig(
            temperature=0.1,
            top_p=0.95,
            top_k=40,
            max_output_tokens=2048,
            response_mime_type="application/json",
        )
        
        try:
            with open("prompts/post_call_prompt.txt", "r") as f:
                system_prompt = f.read()

            response = await self.client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            f"{system_prompt}\n\nTRANSCRIPT:\n{transcript}"
                        ]
                    }
                ],
                config=config,
            )
            
            json_str = response.text
            return json.loads(json_str)
            
        except Exception as e:
            logger.error(f"Error analyzing transcript with LLM: {e}")
            raise

    async def _update_database(self, customer_id: str, analysis: Dict[str, Any], call_id: Optional[str] = None):
        try:
            profile_data = {
                'clientType': analysis.get('clientType'),
                'understandsCreditFunds': analysis.get('understandsCreditFunds'),
                'hasMinimumInvestment': analysis.get('hasMinimumInvestment'),
                'knowsManeesh': analysis.get('knowsManeesh'),
                'investorSophistication': analysis.get('investorSophistication'),
                'attitudeTowardsOffering': analysis.get('attitudeTowardsOffering'),
                'wantsZoomCall': analysis.get('wantsZoomCall'),
                'shouldCallAgain': analysis.get('shouldCallAgain'),
                'interestedInSalesContact': analysis.get('interestedInSalesContact'),
                'languagePreference': analysis.get('languagePreference', 'English'),
                'notes': analysis.get('notes', '')
            }
            
            self.db.update_client_profile(customer_id, profile_data)
            
            if call_id:
                self.db.end_call(
                    call_id,
                    summary=analysis.get('callSummary'),
                    tags=analysis.get('tags', [])
                )
        
                self.db.add_call_note(
                    call_id,
                    f"Automated analysis: {analysis.get('notes', '')}"
                )
            
            logger.info(f"Successfully updated database for customer {customer_id}")
            
        except Exception as e:
            logger.error(f"Error updating database: {e}")
            raise

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Process call transcript and update database")
    parser.add_argument("--customer_id", type=str, required=True, help="Customer ID")
    parser.add_argument("--transcript", type=str, required=True, help="Path to transcript file")
    parser.add_argument("--call_id", type=str, help="Call ID (optional)")
    parser.add_argument("--db_path", type=str, default="serviceAccountKey.json", help="Path to Firebase service account key")
    
    args = parser.parse_args()
    
    processor = PostCallProcessor(db_path=args.db_path)
    result = await processor.process_call(args.customer_id, args.transcript, args.call_id)
    
    print(f"Analysis completed:")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())