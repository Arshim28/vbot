import os
import sys
import argparse
import subprocess
from typing import Any, Dict, Tuple, Optional
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper, DailyRoomParams
from firestore_db import VoiceAgentDB
from sqlite_db import SQLiteVoiceAgentDB

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

firestore_db = VoiceAgentDB()
sqlite_db = SQLiteVoiceAgentDB()

bot_procs = {}
daily_helpers = {}

current_call_id = None
current_client_id = None
current_client_name = None  

# Valid LLM models
VALID_GEMINI_MODELS = ["gemini-2.5-flash-preview-04-17", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
VALID_GROQ_MODELS = ["llama-4-maverick-17b-128e-instruct", 
                     "llama-4-scout-17b-16e-instruct", 
                     "llama-3.3-70b-versatile"]

def cleanup():
    for entry in bot_procs.values():
        proc = entry[0]
        proc.terminate()
        proc.wait()

@asynccontextmanager
async def lifespan(app: FastAPI):
    aiohttp_session = aiohttp.ClientSession()
    daily_helpers["rest"] = DailyRESTHelper(
        daily_api_key=os.getenv("DAILY_API_KEY", ""),
        daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
        aiohttp_session=aiohttp_session,
    )
    yield
    await aiohttp_session.close()
    cleanup()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
    expose_headers=["*"], 
)

async def create_room_and_token() -> Tuple[str, str]:
    room = await daily_helpers["rest"].create_room(DailyRoomParams())
    if not room.url:
        raise HTTPException(status_code=500, detail="Failed to create room")

    token = await daily_helpers["rest"].get_token(room.url)
    if not token:
        raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room.url}")

    return room.url, token

def get_client_latest_call(client_id: str) -> Dict[str, Any]:
    """Get the latest call info for a client from both databases with priority to SQLite."""
    latest_call_info = {}
    
    try:
        sqlite_call = sqlite_db.get_latest_call(client_id)
        if sqlite_call:
            latest_call_info["timestamp"] = sqlite_call.get("timestamp")
            latest_call_info["has_transcript"] = bool(sqlite_call.get("transcript"))
            
            # Get summary from SQLite
            transcript = sqlite_call.get("transcript", "")
            if transcript:
                # Extract a simple summary from the transcript if available
                # Just use the first few lines as a basic summary for now
                lines = transcript.split('\n')
                relevant_lines = [line for line in lines if line.strip() and 'user:' in line]
                if relevant_lines:
                    # Use the first user message as a simple summary
                    latest_call_info["summary"] = relevant_lines[0].split('user:', 1)[1].strip()[:100]
    except Exception as e:
        print(f"SQLite get_latest_call error: {e}")
    
    # If we already have data from SQLite, return it
    if latest_call_info.get("summary"):
        return latest_call_info
        
    # Fallback to Firestore for summary only if SQLite doesn't have it
    try:
        firestore_call = firestore_db.get_latest_call_details(client_id) 
        if firestore_call and firestore_call.get("summary"):
            latest_call_info["summary"] = firestore_call.get("summary")
    except Exception as e:
        print(f"Firestore get_latest_call_details error: {e}")
        # Continue without Firestore data - don't let Firestore errors block the process
        
    return latest_call_info

def get_client_info(client_id: str) -> Dict[str, Any]:
    """Get client info from both databases."""
    client_info = {}
    
    # Try SQLite first
    try:
        sqlite_info = sqlite_db.get_customer_by_id(client_id)
        if sqlite_info:
            print(f"Found client info in SQLite: {sqlite_info.get('first_name')} {sqlite_info.get('last_name')}")
            return sqlite_info
    except Exception as e:
        print(f"Error retrieving client from SQLite: {e}")
    
    # If not found in SQLite, try Firestore
    try:
        firestore_info = firestore_db.get_customer(client_id)
        if firestore_info:
            # Convert Firestore format to match SQLite format
            client_info = {
                'id': client_id,
                'first_name': firestore_info.get('firstName', ''),
                'last_name': firestore_info.get('lastName', ''),
                'phone_number': firestore_info.get('phoneNumber', ''),
                'email': firestore_info.get('email', ''),
                'city': firestore_info.get('city', ''),
                'job_business': firestore_info.get('jobBusiness', '')
            }
            print(f"Found client info in Firestore: {client_info.get('first_name')} {client_info.get('last_name')}")
            return client_info
    except Exception as e:
        print(f"Error retrieving client from Firestore: {e}")
    
    # If we get here, client info wasn't found in either database
    print(f"WARNING: Client info not found in any database for ID: {client_id}")
    return client_info

@app.get("/")
async def root():
    """
    Root endpoint for API health checks.
    """
    return {"status": "ok", "message": "Server is running"}

@app.post("/login")
async def login(data: Dict[str, Any] = Body(...)):
    global current_client_id, current_client_name
    try:
        print(f"Login attempt with data: {data}")
        phone_number = data.get("phoneNumber")

        if not phone_number:
            print("Login failed: Phone number required")
            return JSONResponse(
                status_code=400,
                content={"message": "Phone number is required"}
            )
        
        # Check in Firestore
        try:
            firestore_client_id, firestore_client_data = firestore_db.get_customer_by_phone(phone_number)
        except Exception as e:
            print(f"Firestore lookup error: {e}")
            firestore_client_id, firestore_client_data = None, None
        
        # Check in SQLite
        try:
            sqlite_client_id, sqlite_client_data = sqlite_db.get_customer_by_phone(phone_number)
        except Exception as e:
            print(f"SQLite lookup error: {e}")
            sqlite_client_id, sqlite_client_data = None, None
        
        # Determine which client ID to use (prefer Firestore if both exist)
        client_id = firestore_client_id or sqlite_client_id
        
        if client_id:
            current_client_id = client_id
            
            # Explicitly get client info from database using the ID
            client_info = sqlite_db.get_customer_by_id(client_id)
            if client_info:
                current_client_name = f"{client_info.get('first_name', '')} {client_info.get('last_name', '')}".strip()
                print(f"Retrieved client name from SQLite: {current_client_name}")
            else:
                # Fallback to Firestore if not in SQLite
                firestore_info = firestore_db.get_customer(client_id)
                if firestore_info:
                    current_client_name = f"{firestore_info.get('firstName', '')} {firestore_info.get('lastName', '')}".strip()
                    print(f"Retrieved client name from Firestore: {current_client_name}")
                else:
                    current_client_name = ""
                    print("Warning: Could not retrieve client name from either database")
            
            print(f"Client ID set to: {current_client_id}")
            print(f"Client Name set to: {current_client_name}")
            
            return JSONResponse(
                status_code=200,
                content={"status": "success", "message": "Logged in successfully"}
            )
        else:
            print(f"Login failed: User with phone number {phone_number} not found")
            return JSONResponse(
                status_code=404,
                content={"message": "User not found. Please register first."}
            )
    except Exception as e:
        print(f"Unexpected login error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"message": f"Server error: {str(e)}"}
        )

@app.post("/register")
async def register(data: Dict[str, Any] = Body(...)):
    global current_client_id, current_client_name
    try:
        print(f"Registration attempt with data: {data}")
        phone_number = data.get("phoneNumber")
        first_name = data.get("firstName")
        last_name = data.get("lastName")
        email = data.get("email")
        city = data.get("city")
        job_business = data.get("jobBusiness")

        if not phone_number or not first_name or not last_name:
            print("Registration failed: Required fields missing")
            return JSONResponse(
                status_code=400,
                content={"message": "Required fields missing"}
            )
        
        # Check if client exists in Firestore
        try:
            firestore_client_id, firestore_data = firestore_db.get_customer_by_phone(phone_number)
            if firestore_client_id:
                current_client_id = firestore_client_id
                
                # Ensure the client also exists in SQLite with the same ID
                sqlite_client_id, sqlite_data = sqlite_db.get_customer_by_phone(phone_number)
                if not sqlite_client_id:
                    # If client exists in Firestore but not SQLite, add to SQLite with same ID
                    print(f"User exists in Firestore but not SQLite. Adding to SQLite with ID: {firestore_client_id}")
                    sqlite_db.add_customer_with_id(
                        client_id=firestore_client_id,
                        first_name=first_name,
                        last_name=last_name,
                        phone_number=phone_number,
                        email=email,
                        city=city,
                        job_business=job_business
                    )
                
                current_client_name = f"{first_name} {last_name}"
                print(f"User already exists in Firestore: {firestore_client_id}")
                return JSONResponse(
                    status_code=200, 
                    content={"status": "success", "message": "Logged in with existing account"}
                )
        except Exception as e:
            print(f"Firestore check error: {e}")
            firestore_client_id = None
        
        # Check if client exists in SQLite
        try:
            sqlite_client_id, sqlite_data = sqlite_db.get_customer_by_phone(phone_number)
            if sqlite_client_id:
                current_client_id = sqlite_client_id
                
                # Ensure the client also exists in Firestore with the same ID
                if not firestore_client_id:
                    # If client exists in SQLite but not Firestore, add to Firestore with same ID
                    print(f"User exists in SQLite but not Firestore. Adding to Firestore with ID: {sqlite_client_id}")
                    firestore_db.add_customer_with_id(
                        client_id=sqlite_client_id,
                        first_name=first_name,
                        last_name=last_name,
                        phone_number=phone_number,
                        email=email,
                        city=city,
                        job_business=job_business
                    )
                
                current_client_name = f"{first_name} {last_name}"
                print(f"User already exists in SQLite: {sqlite_client_id}")
                return JSONResponse(
                    status_code=200,
                    content={"status": "success", "message": "Logged in with existing account"}
                )
        except Exception as e:
            print(f"SQLite check error: {e}")
            sqlite_client_id = None
        
        # Create new user in both databases with the same ID
        import uuid
        shared_client_id = str(uuid.uuid4())
        print(f"Generated new shared client ID: {shared_client_id}")
        
        # Add to Firestore with explicit ID
        try:
            firestore_db.add_customer_with_id(
                client_id=shared_client_id,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                email=email,
                city=city,
                job_business=job_business
            )
            print(f"User added to Firestore with ID: {shared_client_id}")
        except Exception as e:
            print(f"Failed to add user to Firestore: {e}")
        
        # Add to SQLite with explicit ID
        try:
            sqlite_db.add_customer_with_id(
                client_id=shared_client_id,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                email=email,
                city=city,
                job_business=job_business
            )
            print(f"User added to SQLite with ID: {shared_client_id}")
        except Exception as e:
            print(f"Failed to add user to SQLite: {e}")
        
        # Use the shared ID
        current_client_id = shared_client_id
        
        if not current_client_id:
            print("Registration failed: Could not create user in either database")
            return JSONResponse(
                status_code=500,
                content={"message": "Failed to create user account"}
            )
        
        # Set the client name using the provided first and last name
        current_client_name = f"{first_name} {last_name}".strip()
        print(f"Registration successful: {current_client_id}")
        print(f"Client name set to: {current_client_name}")
        
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Registration successful"}
        )
    except Exception as e:
        print(f"Unexpected registration error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"message": f"Server error: {str(e)}"}
        )

@app.post("/connect")
async def bot_connect(
    request: Request,
    llm_type: str = Query("gemini", description="LLM type to use (gemini or groq)"),
    model_name: str = Query("gemini-2.0-flash", description="Model name to use")
) -> Dict[Any, Any]:
    global current_call_id, current_client_id, current_client_name
    print("#"*30, "Client ID", current_client_id)
    
    if not current_client_id:
         raise HTTPException(status_code=400, detail="Client ID not set. Please login or register first.")

    # Generate a shared call ID for both databases
    import uuid
    shared_call_id = str(uuid.uuid4())
    print(f"Generated shared call ID: {shared_call_id}")
    
    # Create call in both databases with the same ID
    try:
        sqlite_call_id = sqlite_db.create_call_with_id(current_client_id, shared_call_id)
        print(f"Call created in SQLite with ID: {sqlite_call_id}")
    except Exception as e:
        print(f"Error creating call in SQLite: {e}")
        sqlite_call_id = None
    
    try:
        firestore_call_id = firestore_db.create_call(current_client_id, call_id=shared_call_id)
        print(f"Call created in Firestore with ID: {firestore_call_id}")
    except Exception as e:
        print(f"Error creating call in Firestore: {e}")
        firestore_call_id = None
    
    # Use the shared call ID
    current_call_id = shared_call_id
    
    # Use the global client name that was set during login/registration
    client_name = current_client_name
    
    # If client_name is still empty/None, try one more time to get it
    if not client_name:
        print("Warning: client_name not set from login/registration, attempting to fetch from database")
        client_info = sqlite_db.get_customer_by_id(current_client_id)
        if client_info:
            client_name = f"{client_info.get('first_name', '')} {client_info.get('last_name', '')}".strip()
            current_client_name = client_name  # Update the global variable
            print(f"Retrieved client name from database: {client_name}")
        else:
            print(f"ERROR: Could not find client name for ID {current_client_id} in either database")
    
    # Get latest call info from SQLite
    latest_call = get_client_latest_call(current_client_id)
    previous_summary = latest_call.get("summary", "") # Get summary
    is_returning = bool(latest_call)
    
    # Validate LLM parameters
    if llm_type not in ["gemini", "groq"]:
        raise HTTPException(status_code=400, detail="Invalid LLM type. Must be 'gemini' or 'groq'")
    
    # Validate model name based on LLM type
    valid_models = VALID_GEMINI_MODELS if llm_type == "gemini" else VALID_GROQ_MODELS
    if model_name not in valid_models:
        default_model = valid_models[0]
        print(f"Warning: Invalid {llm_type} model: {model_name}. Using default: {default_model}")
        model_name = default_model

    print(f"Using LLM type: {llm_type}, model: {model_name}")
    print(f"Client Name: {client_name}")
    print(f"Is Returning Client: {is_returning}")
    print(f"Previous Summary: {previous_summary}")

    print("Creating room for RTVI connection")
    room_url, token = await create_room_and_token()
    print(f"Room URL: {room_url}")

    os.environ["DAILY_SAMPLE_ROOM_URL"] = room_url
    try:
        # Pass client name, returning status, and summary to bot
        bot_file = "bot"
        cmd = (f"uv run -m {bot_file} --call_id {current_call_id} "
              f"--client_id {current_client_id} --llm_type {llm_type} "
              f"--model_name \"{model_name}\" "
              f"--client_name \"{client_name}\" "
              f"--returning_client {1 if is_returning else 0} "
              f"--previous_summary \"{previous_summary}\"") # Pass summary
        
        print(f"Bot command: {cmd}")
        
        proc = subprocess.Popen(
            [cmd],
            shell=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        bot_procs[proc.pid] = (proc, room_url)
    except Exception as e:
        print(f"Failed to start subprocess: {e}") # Added print statement
        raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")

    return {"room_url": room_url, "token": token}

@app.get("/join")
async def join_call(
    llm_type: str = Query("gemini", description="LLM type to use (gemini or groq)"),
    model_name: str = Query("gemini-2.0-flash", description="Model name to use")
) -> Dict[Any, Any]:
    """
    Alternative endpoint for joining a call, better compatibility with the frontend.
    """
    return await bot_connect(
        Request(scope={"type": "http"}),
        llm_type=llm_type,
        model_name=model_name
    )

@app.post("/analyze")
async def analyze_transcript() -> Dict[str, str]:
    global current_client_id, current_call_id
    try:
        # Create a transcript file path to save the transcript
        transcript_file = Path(__file__).parent.parent / "logs" / f"{current_call_id}.txt"
        transcript_text = ""
        
        # Read the transcript file if it exists
        if transcript_file.exists():
            with open(transcript_file, "r") as f:
                transcript_text = f.read()
                
            # Update the transcript in the SQLite database
            sqlite_db.update_call_transcript(current_call_id, transcript_text)
            
            # Note: You would need to implement a similar method in Firestore
            # to store the transcript
        
        # Run the analyzer
        analyzer_file = "analyzer"
        subprocess.run(
            [f"uv run -m {analyzer_file} --call_id={current_call_id} --client_id={current_client_id}"],
            shell=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        # Run post processor
        subprocess.run(
            [f"uv run -m post_call_processor --call_id={current_call_id} --client_id={current_client_id}"],
            shell=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        return {"status": "success", "message": "Transcript analysis completed successfully"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed with exit code {e.returncode}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run analysis: {str(e)}")
    finally:
        current_client_id = None
        current_call_id = None
        current_client_name = None

if __name__ == "__main__":
    import uvicorn

    default_host = os.getenv("HOST", "0.0.0.0")
    default_port = int(os.getenv("FAST_API_PORT", "7860"))

    parser = argparse.ArgumentParser(description="BFSI Sales Agent FastAPI server")
    parser.add_argument("--host", type=str, default=default_host, help="Host address")
    parser.add_argument("--port", type=int, default=default_port, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Reload code on change")

    config = parser.parse_args()

    uvicorn.run(
        "server:app",
        host=config.host,
        port=config.port,
        reload=config.reload,
    )