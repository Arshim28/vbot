import os
import sys
import argparse
import subprocess
from typing import Any, Dict, Tuple, Optional
from contextlib import asynccontextmanager

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper, DailyRoomParams
from firestore_db import VoiceAgentDB

load_dotenv(dotenv_path='.env')

bot_procs = {}
daily_helpers = {}
active_calls = {}

# Pydantic models for request validation
class PhoneLoginRequest(BaseModel):
    phone_number: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class ConnectRequest(BaseModel):
    client_id: Optional[str] = None

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
)

async def create_room_and_token() -> Tuple[str, str, str]:
    room = await daily_helpers["rest"].create_room(DailyRoomParams())
    if not room.url:
        raise HTTPException(status_code=500, detail="Failed to create room")

    token = await daily_helpers["rest"].get_token(room.url)
    if not token:
        raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room.url}")

    return room.url, token, room.id

@app.post("/login")
async def login_signup(login_request: PhoneLoginRequest):
    """Handle login or signup via phone number"""
    db = VoiceAgentDB()
    
    # Check if user exists
    client_id, client_data = db.get_customer_by_phone(login_request.phone_number)
    
    # If client doesn't exist, create a new one
    if not client_id:
        if not login_request.first_name:
            raise HTTPException(status_code=400, detail="First name required for new users")
            
        client_id = db.add_customer(
            first_name=login_request.first_name,
            last_name=login_request.last_name or "",
            phone_number=login_request.phone_number
        )
        
        return {"status": "success", "message": "Account created successfully", "client_id": client_id}
    
    # Return existing client data
    return {"status": "success", "message": "Login successful", "client_id": client_id, "client_data": client_data}

@app.post("/connect")
async def bot_connect(request: Request, connect_request: ConnectRequest = None) -> Dict[Any, Any]:
    """Connect to bot with client_id (with backwards compatibility)"""
    
    # For backwards compatibility - handle direct calls without client_id
    client_id = None
    if connect_request and connect_request.client_id:
        client_id = connect_request.client_id
    
    room_url = None
    token = None
    room_id = None
    call_id = None
    
    # If client_id is provided, get customer data and maybe reuse room
    if client_id:
        db = VoiceAgentDB()
        
        # Get customer data
        client_data = db.get_customer(client_id)
        if not client_data:
            raise HTTPException(status_code=404, detail="Client not found")
        
        # Check if client already has a room
        room_url = client_data.get('RoomURL')
        room_id = client_data.get('RoomId')
        
        # Create a new room if client doesn't have one
        if not room_url or not room_id:
            room_url, token, room_id = await create_room_and_token()
            
            # Update client with new room details
            db.update_customer_room(client_id, room_id, room_url)
        else:
            # Use existing room
            token = await daily_helpers["rest"].get_token(room_url)
            if not token:
                raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room_url}")
        
        # Create a call in the database
        call_id = db.create_call(client_id)
        
        # Store the mapping of room_url to call_id and client_id for later use
        active_calls[room_url] = {"call_id": call_id, "client_id": client_id}
        
        print(f"Starting bot for client {client_id}, call {call_id}, room {room_url}")
    else:
        # No client_id provided - legacy mode, just create a room
        room_url, token, room_id = await create_room_and_token()
        print(f"Starting bot in legacy mode, room {room_url}")
    
    # Bot command arguments
    bot_args = f"uv run -m bot -u {room_url} -t {token}"
    
    # Add call_id and client_id if available
    if call_id and client_id:
        bot_args += f" --call_id {call_id} --client_id {client_id}"
    
    try:
        # Start the bot process
        proc = subprocess.Popen(
            [bot_args],
            shell=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        bot_procs[proc.pid] = (proc, room_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")

    # Response includes call_id if available
    response = {"room_url": room_url, "token": token}
    if call_id:
        response["call_id"] = call_id
    
    return response

@app.post("/analyze")
async def analyze_transcript(request: Request) -> Dict[str, str]:
    try:
        # Try to parse request body
        try:
            body = await request.json()
            room_url = body.get("room_url", None)
        except Exception:
            room_url = None
        
        # If room_url is provided and exists in active_calls, use it
        if room_url and room_url in active_calls:
            call_data = active_calls[room_url]
            call_id = call_data["call_id"]
            client_id = call_data["client_id"]
            
            # Run post-call processor
            subprocess.Popen(
                [f"uv run -m post_call_processor --call_id {call_id} --client_id {client_id}"],
                shell=True,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            
            # Run analyzer
            subprocess.Popen(
                [f"uv run -m analyzer --call_id {call_id} --client_id {client_id}"],
                shell=True,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            
            return {"status": "success", "message": f"Transcript analysis initiated for call {call_id}"}
        else:
            # Legacy mode - just run analyzer without client/call specific data
            subprocess.Popen(
                [f"uv run -m analyzer"],
                shell=True,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            return {"status": "success", "message": "Legacy transcript analysis initiated"}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run analysis: {str(e)}")

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