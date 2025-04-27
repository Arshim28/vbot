import os
import sys
import argparse
import subprocess
from typing import Any, Dict, Tuple
from contextlib import asynccontextmanager

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper, DailyRoomParams
from firestore_db import VoiceAgentDB

load_dotenv(dotenv_path='.env')

db = VoiceAgentDB()

bot_procs = {}
daily_helpers = {}

current_call_id = None
current_client_id = None

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

async def create_room_and_token() -> Tuple[str, str]:
    room = await daily_helpers["rest"].create_room(DailyRoomParams())
    if not room.url:
        raise HTTPException(status_code=500, detail="Failed to create room")

    token = await daily_helpers["rest"].get_token(room.url)
    if not token:
        raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room.url}")

    return room.url, token

@app.post("/login")
async def login(data: Dict[str, Any] = Body(...)):
    global current_client_id
    phone_number = data.get("phoneNumber")

    if not phone_number:
        raise HTTPException(status_code=400, details="Phone number is required")
    
    client_id, client_data = db.get_customer_by_phone(phone_number)
    current_client_id = client_id
    print(f"##################Client ID set to: {current_client_id}####################")

    if not client_id:
        return JSONResponse(
            status_code=404,
            content={
                "message": "User not found. Please register first."
            }
        )
    

@app.post("/register")
async def register(data: Dict[str, Any] = Body(...)):
    global current_client_id
    phone_number = data.get("phoneNumber")
    first_name = data.get("firstName")
    last_name = data.get("lastName")
    email = data.get("email")
    city = data.get("city")
    job_business = data.get("jobBusiness")

    if not phone_number or not first_name or not last_name:
        raise HTTPException(status_code=400, detail="Required fields missing")
    
    client_id, _ = db.get_customer_by_phone(phone_number)
    if client_id:
        current_client_id = client_id
        return {"message": "Logged in with existing account"}
    
    #createing new user 
    client_id = db.add_customer(
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number,
        email=email,
        city=city,
        job_business=job_business
    )
    current_client_id = client_id

@app.post("/connect")
async def bot_connect(request: Request) -> Dict[Any, Any]:
    global current_call_id, current_client_id
    print("#"*30, "Client ID", current_client_id)
    current_call_id = db.create_call(current_client_id)

    print("Creating room for RTVI connection")
    room_url, token = await create_room_and_token()
    print(f"Room URL: {room_url}")

    os.environ["DAILY_SAMPLE_ROOM_URL"] = room_url
    try:
        bot_file = "bot"
        proc = subprocess.Popen(
            [f"uv run -m {bot_file} --call_id {current_call_id} --client_id {current_client_id}"],
            shell=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        bot_procs[proc.pid] = (proc, room_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")

    return {"room_url": room_url, "token": token}

@app.post("/analyze")
async def analyze_transcript() -> Dict[str, str]:
    global current_client_id, current_call_id
    try:
        #analyzer
        analyzer_file = "analyzer"
        subprocess.run(
            [f"uv run -m {analyzer_file} --call_id={current_call_id} --client_id={current_client_id}"],
            shell=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        #post processor
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