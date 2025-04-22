import os
import sys
import argparse
import subprocess
from typing import Any, Dict, Tuple
from contextlib import asynccontextmanager

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper, DailyRoomParams

load_dotenv(dotenv_path='.env')

# Dictionary to track bot processes: {pid: (process, room_url)}
bot_procs = {}
daily_helpers = {}

def cleanup():
    """Cleanup function to terminate all bot processes."""
    for entry in bot_procs.values():
        proc = entry[0]
        proc.terminate()
        proc.wait()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan manager that handles startup and shutdown tasks."""
    aiohttp_session = aiohttp.ClientSession()
    daily_helpers["rest"] = DailyRESTHelper(
        daily_api_key=os.getenv("DAILY_API_KEY", ""),
        daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
        aiohttp_session=aiohttp_session,
    )
    yield
    await aiohttp_session.close()
    cleanup()

# Initialize FastAPI app with lifespan manager
app = FastAPI(lifespan=lifespan)

# Configure CORS to allow requests from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def create_room_and_token() -> Tuple[str, str]:
    """Helper function to create a Daily room and generate an access token."""
    room = await daily_helpers["rest"].create_room(DailyRoomParams())
    if not room.url:
        raise HTTPException(status_code=500, detail="Failed to create room")

    token = await daily_helpers["rest"].get_token(room.url)
    if not token:
        raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room.url}")

    return room.url, token

@app.post("/connect")
async def bot_connect(request: Request) -> Dict[Any, Any]:
    """Connect endpoint that creates a room and returns connection credentials."""
    print("Creating room for RTVI connection")
    room_url, token = await create_room_and_token()
    print(f"Room URL: {room_url}")

    # Start the bot process
    try:
        bot_file = "bot"
        proc = subprocess.Popen(
            [f"uv run -m {bot_file} -u {room_url} -t {token}"],
            shell=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        bot_procs[proc.pid] = (proc, room_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")

    # Return the authentication bundle in format expected by DailyTransport
    return {"room_url": room_url, "token": token}

@app.post("/analyze")
async def analyze_transcript() -> Dict[str, str]:
    """Endpoint to trigger transcript analysis."""
    try:
        analyzer_file = "analyzer"
        subprocess.run(
            [f"uv run -m {analyzer_file}"],
            shell=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return {"status": "success", "message": "Transcript analysis completed successfully"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed with exit code {e.returncode}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run analysis: {str(e)}")

if __name__ == "__main__":
    import uvicorn

    # Parse command line arguments for server configuration
    default_host = os.getenv("HOST", "0.0.0.0")
    default_port = int(os.getenv("FAST_API_PORT", "7860"))

    parser = argparse.ArgumentParser(description="BFSI Sales Agent FastAPI server")
    parser.add_argument("--host", type=str, default=default_host, help="Host address")
    parser.add_argument("--port", type=int, default=default_port, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Reload code on change")

    config = parser.parse_args()

    # Start the FastAPI server
    uvicorn.run(
        "server:app",
        host=config.host,
        port=config.port,
        reload=config.reload,
    )