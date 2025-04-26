import os
import sys
import argparse
import subprocess
from typing import Any, Dict, Tuple, Optional
from contextlib import asynccontextmanager
import time

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pathlib import Path

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper, DailyRoomParams
from firestore_db import VoiceAgentDB

load_dotenv(dotenv_path='.env')

bot_procs = {}
daily_helpers = {}
active_clients = {}  # Map client_id to call_id

# Initialize the database connection
db = VoiceAgentDB()

def cleanup():
    """Terminate all bot processes when server shuts down"""
    for proc, _, _, _ in bot_procs.values():
        try:
            proc.terminate()
        except:
            pass

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
)

@app.post("/login")
async def login(data: Dict[str, Any] = Body(...)):
    """Endpoint for user login using phone number"""
    phone_number = data.get("phoneNumber")
    
    if not phone_number:
        raise HTTPException(status_code=400, detail="Phone number is required")
    
    client_id, client_data = db.get_customer_by_phone(phone_number)
    
    if not client_id:
        return JSONResponse(
            status_code=404,
            content={"message": "User not found. Please register first."}
        )
    
    # End any active calls for this client
    if client_id in active_clients:
        call_id = active_clients[client_id]
        for pid, (proc, _, c_id, cl_id) in list(bot_procs.items()):
            if c_id == call_id:
                try:
                    proc.terminate()
                    del bot_procs[pid]
                except:
                    pass
        del active_clients[client_id]
    
    return {"clientId": client_id}

@app.post("/register")
async def register(data: Dict[str, Any] = Body(...)):
    """Endpoint for user registration"""
    phone_number = data.get("phoneNumber")
    first_name = data.get("firstName")
    last_name = data.get("lastName")
    email = data.get("email")
    city = data.get("city")
    job_business = data.get("jobBusiness")
    
    if not phone_number or not first_name or not last_name:
        raise HTTPException(status_code=400, detail="Required fields missing")
    
    # Check if user already exists
    client_id, _ = db.get_customer_by_phone(phone_number)
    
    if client_id:
        # End any active calls for this client
        if client_id in active_clients:
            call_id = active_clients[client_id]
            for pid, (proc, _, c_id, cl_id) in list(bot_procs.items()):
                if c_id == call_id:
                    try:
                        proc.terminate()
                        del bot_procs[pid]
                    except:
                        pass
            del active_clients[client_id]
            
        return {"clientId": client_id, "message": "Logged in with existing account"}
    
    # Create new user
    client_id = db.add_customer(
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number,
        email=email,
        city=city,
        job_business=job_business
    )
    
    return {"clientId": client_id}

async def create_room_and_token() -> Tuple[str, str]:
    """Helper function to create a Daily room and token"""
    room = await daily_helpers["rest"].create_room(DailyRoomParams())
    if not room.url:
        raise HTTPException(status_code=500, detail="Failed to create room")

    token = await daily_helpers["rest"].get_token(room.url)
    if not token:
        raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room.url}")

    return room.url, token

@app.post("/connect")
async def connect(request: Request) -> Dict[Any, Any]:
    """Endpoint to connect to the bot and start a call"""
    body = await request.json()
    client_id = body.get("clientId")
    
    if not client_id:
        raise HTTPException(status_code=400, detail="Client ID is required")
    
    # Verify client exists
    client_data = db.get_customer(client_id)
    if not client_data:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # End any active calls for this client
    if client_id in active_clients:
        old_call_id = active_clients[client_id]
        for pid, (proc, _, c_id, cl_id) in list(bot_procs.items()):
            if c_id == old_call_id:
                try:
                    proc.terminate()
                    del bot_procs[pid]
                except:
                    pass
        
        # Mark call as ended in database
        try:
            db.end_call(old_call_id, "Call ended due to new call start")
        except:
            pass
    
    # Create a new call in the database
    call_id = db.create_call(client_id)
    active_clients[client_id] = call_id
    
    # Create room for call
    print(f"Creating room for call {call_id} with client {client_id}")
    room_url, token = await create_room_and_token()
    print(f"Room URL: {room_url}")

    # Create log directory if it doesn't exist
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # Ensure transcript file exists and is prepared
    transcript_file = log_dir / f"{call_id}_transcript.txt"
    if not transcript_file.exists():
        transcript_file.touch()
    
    # Create expert_opinion directory if it doesn't exist
    expert_dir = Path(__file__).parent.parent / "expert_opinion"
    expert_dir.mkdir(exist_ok=True)
    
    # Create a default expert suggestion file if it doesn't exist yet
    expert_suggestion_file = expert_dir / f"{client_id}_exp_opinion.txt"
    if not expert_suggestion_file.exists():
        try:
            with open(expert_suggestion_file, "w") as f:
                f.write("No transcript data available for analysis.")
            print(f"Created default expert suggestion file for client {client_id}")
        except Exception as e:
            print(f"Error creating default expert suggestion file: {e}")

    try:
        bot_file = "bot"
        env_vars = f"CALL_ID={call_id} CLIENT_ID={client_id}"
        proc = subprocess.Popen(
            [f"{env_vars} uv run -m {bot_file} -u {room_url} -t {token}"],
            shell=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        bot_procs[proc.pid] = (proc, room_url, call_id, client_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")

    return {"room_url": room_url, "token": token, "callId": call_id}

@app.post("/analyze")
async def analyze(data: Dict[str, Any] = Body(...)):
    """Endpoint to end a call and analyze the transcript"""
    call_id = data.get("callId")
    client_id = data.get("clientId")
    
    if not call_id or not client_id:
        raise HTTPException(status_code=400, detail="Call ID and Client ID are required")
    
    # Validate that this call belongs to this client
    if client_id not in active_clients or active_clients[client_id] != call_id:
        return {"status": "success", "message": "Call already processed or not found"}
    
    # End the bot process for this call
    for pid, (proc, _, c_id, cl_id) in list(bot_procs.items()):
        if c_id == call_id:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                del bot_procs[pid]
            except Exception as e:
                print(f"Error terminating process: {e}")
    
    # Remove from active clients
    if client_id in active_clients:
        del active_clients[client_id]
    
    # Process call data
    try:
        # Run post-call processor
        subprocess.run(
            [f"uv run -m post_call_processor --call_id={call_id} --client_id={client_id}"],
            shell=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        
        # Run analyzer
        subprocess.run(
            [f"uv run -m analyzer --call_id={call_id} --client_id={client_id}"],
            shell=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        
        return {"status": "success", "message": "Call ended and processed successfully"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Call processing failed with exit code {e.returncode}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process call: {str(e)}")

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