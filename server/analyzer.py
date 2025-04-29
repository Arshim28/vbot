import os
import sys
import argparse
import asyncio
from typing import Dict, List, Optional
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from google import genai
from google.genai import types

from firestore_db import VoiceAgentDB

# Load .env from project root
load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

logger.remove(0)
logger.add(sys.stderr, level="INFO")

INSTRUCTION_FILE = Path(__file__).parent.parent / "prompts" / "analyst_system_prompt.txt"
HIGHLIGHT_INSTRUCTION_FILE = Path(__file__).parent.parent / "prompts" / "call_highlight_prompt.txt"

with open(INSTRUCTION_FILE, "r") as f:
    INSTRUCTION = f.read()

# Create call_highlight_prompt.txt if it doesn't exist
if not HIGHLIGHT_INSTRUCTION_FILE.exists():
    with open(HIGHLIGHT_INSTRUCTION_FILE, "w") as f:
        f.write("""You are analyzing a sales call transcript. Your task is to extract key information that would be valuable for future conversations with this client.

Focus on extracting:
1. Client's specific investment goals and priorities
2. Key concerns or objections raised
3. Personal details mentioned (family, career, etc.)
4. Investment history or preferences
5. Timeline for decision making

Format this as a clear, concise summary that can be quickly referenced by an agent before speaking with this client again.

Keep your response under 200 words, focusing on the most relevant and actionable insights.
""")
    logger.info(f"Created call highlight prompt at {HIGHLIGHT_INSTRUCTION_FILE}")
else:
    with open(HIGHLIGHT_INSTRUCTION_FILE, "r") as f:
        HIGHLIGHT_INSTRUCTION = f.read()

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
db = VoiceAgentDB()

async def read_transcript(call_id: str) -> str:
    try:
        transcript_file = Path(__file__).parent.parent / "logs" / f"{call_id}.txt"
        if not transcript_file.exists():
            logger.warning(f"Transcript file not found: {transcript_file}")
            return ""
        
        with open(transcript_file, "r") as f:
            transcript = f.read()
        
        logger.info(f"Read transcript with {len(transcript.splitlines())} lines for call {call_id}")
        return transcript
    except Exception as e:
        logger.error(f"Error reading transcript: {e}")
        return ""

async def get_previous_calls_data(client_id: str, max_calls: int = 3) -> str:
    """Fetch previous calls data from the database"""
    try:
        # Get previous calls for this client
        previous_calls = db.get_call_history(client_id, limit=max_calls)
        
        if not previous_calls:
            logger.info(f"No previous calls found for client {client_id}")
            return ""
        
        previous_data = []
        for call in previous_calls:
            call_id = call.get('callId')
            if not call_id:
                continue
                
            # Get transcript for this call
            transcript = db.get_call_transcript(call_id)
            if not transcript:
                continue
                
            # Format transcript for analysis
            formatted_transcript = "\n".join([
                f"[{entry.get('timestamp', '')}] {entry.get('speaker', '')}: {entry.get('content', '')}"
                for entry in transcript
            ])
            
            call_date = call.get('startTime', 'unknown date')
            previous_data.append(f"--- PREVIOUS CALL ({call_date}) ---\n{formatted_transcript}\n\n")
        
        if previous_data:
            return "\n\n=== PREVIOUS CALL HISTORY ===\n\n" + "\n".join(previous_data)
        return ""
        
    except Exception as e:
        logger.error(f"Error fetching previous calls: {e}")
        return ""

async def read_previous_expert_suggestion(client_id: str) -> str:
    """Read previous expert suggestion for this client if it exists"""
    try:
        expert_suggestion_file = Path(__file__).parent.parent / "expert_opinion" / f"{client_id}_exp_opinion.txt"
        
        if not expert_suggestion_file.exists():
            logger.info(f"No previous expert suggestion found for client {client_id}")
            return ""
        
        with open(expert_suggestion_file, "r") as f:
            suggestion = f.read()
            
        if suggestion and suggestion != "No transcript data available for analysis.":
            logger.info(f"Found previous expert suggestion for client {client_id}")
            return f"\n\n=== PREVIOUS EXPERT SUGGESTION ===\n\n{suggestion}"
        return ""
    except Exception as e:
        logger.error(f"Error reading previous expert suggestion: {e}")
        return ""

async def generate_call_highlight(transcript: str, client_id: str) -> str:
    """Generate a concise highlight of key client information from the transcript"""
    if not transcript:
        return "No transcript data available for highlights."
    
    logger.info("Generating call highlights")
    
    config = types.GenerateContentConfig(
        temperature=0.2,
        top_p=0.95,
        top_k=40,
        max_output_tokens=1024,
    )
    
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"{HIGHLIGHT_INSTRUCTION}\n\nTRANSCRIPT:\n{transcript}",
            config=config,
        )
        highlights = response.text
        logger.info("Call highlights generated successfully")
        return highlights
    except Exception as e:
        logger.error(f"Error generating call highlights: {e}")
        return f"Error generating call highlights: {str(e)}"

async def write_call_highlight(highlight: str, client_id: str) -> None:
    """Write call highlights to a client-specific file"""
    try:
        # Ensure call_highlights directory exists
        highlight_dir = Path(__file__).parent.parent / "call_highlights"
        os.makedirs(highlight_dir, exist_ok=True)
        
        # Write to the client-specific highlight file
        highlight_file = highlight_dir / f"{client_id}_highlights.txt"
        with open(highlight_file, "w") as f:
            f.write(highlight)
        logger.info(f"Call highlights written to {highlight_file}")
    except Exception as e:
        logger.error(f"Error writing call highlights: {e}")

async def read_previous_call_highlight(client_id: str) -> str:
    """Read previous call highlight for this client if it exists"""
    try:
        highlight_file = Path(__file__).parent.parent / "call_highlights" / f"{client_id}_highlights.txt"
        
        if not highlight_file.exists():
            logger.info(f"No previous call highlight found for client {client_id}")
            return ""
        
        with open(highlight_file, "r") as f:
            highlight = f.read()
            
        if highlight and highlight != "No transcript data available for highlights.":
            logger.info(f"Found previous call highlight for client {client_id}")
            return f"\n\n=== PREVIOUS CALL HIGHLIGHT ===\n\n{highlight}"
        return ""
    except Exception as e:
        logger.error(f"Error reading previous call highlight: {e}")
        return ""

async def analyze_conversation(transcript: str, client_id: str, previous_data: str = "", previous_suggestion: str = "") -> str:
    if not transcript:
        return "No transcript data available for analysis."
    
    logger.info("Analyzing conversation transcript")
    
    # Combine current transcript with previous data and suggestions
    full_prompt = f"""
        {INSTRUCTION}
        
        {previous_suggestion}
        
        {previous_data}
        
        CURRENT CALL TRANSCRIPT:
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
            contents=full_prompt,
            config=config,
        )
        analysis = response.text
        logger.info("Conversation analysis completed")
        return analysis
    except Exception as e:
        logger.error(f"Error analyzing conversation: {e}")
        return f"Error analyzing conversation: {str(e)}"

async def write_analysis(analysis: str, client_id: str) -> None:
    try:
        # Ensure expert_opinion directory exists
        expert_opinion_dir = Path(__file__).parent.parent / "expert_opinion"
        os.makedirs(expert_opinion_dir, exist_ok=True)
        
        # Write directly to the client-specific expert opinion file
        expert_suggestion_file = expert_opinion_dir / f"{client_id}_exp_opinion.txt"
        with open(expert_suggestion_file, "w") as f:
            f.write(analysis)
        logger.info(f"Analysis written to {expert_suggestion_file}")
    except Exception as e:
        logger.error(f"Error writing analysis: {e}")

async def main() -> None:
    print("#"*30, "ANALYZER CALLED", "#"*30)
    parser = argparse.ArgumentParser(description="Analyze conversation transcript")
    parser.add_argument("--call_id", type=str, required=True, help="Call ID")
    parser.add_argument("--client_id", type=str, required=True, help="Client ID")
    
    args = parser.parse_args()
    
    call_id = args.call_id
    client_id = args.client_id
    
    logger.info(f"Starting conversation analysis for call {call_id}, client {client_id}")

    # Get current transcript
    transcript = await read_transcript(call_id)
    if not transcript:
        logger.error(f"No transcript found for call {call_id}")
        return
    
    # Get previous calls data
    previous_data = await get_previous_calls_data(client_id)
    
    # Get previous expert suggestion if available
    previous_suggestion = await read_previous_expert_suggestion(client_id)
    
    # Generate and save call highlights
    highlight = await generate_call_highlight(transcript, client_id)
    await write_call_highlight(highlight, client_id)
    
    # Analyze the conversation
    analysis = await analyze_conversation(transcript, client_id, previous_data, previous_suggestion)
    
    # Write the analysis to client-specific file
    await write_analysis(analysis, client_id)
    
    logger.info("Conversation analysis completed")

if __name__ == "__main__":
    asyncio.run(main())