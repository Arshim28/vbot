import argparse
import os
import subprocess
import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper, DailyRoomParams

load_dotenv(override=True)

bot_procs = {}
daily_helpers = {}

STT_MODELS = {
    "deepgram": {
        "nova-3": "Latest Deepgram model with best accuracy",
        "nova-2": "General purpose transcription model",
        "nova-2-general": "Enhanced general purpose model",
        "nova-2-telephony": "Optimized for telephony audio",
        "nova-2-meeting": "Specialized for meeting transcription",
        "nova-2-phonecall": "Optimized for phone call audio"
    },
    "groq": {
        "whisper-large-v3": "High accuracy multilingual model (10.3% WER)",
        "whisper-large-v3-turbo": "Fast multilingual model (12% WER)",
        "distil-whisper-large-v3-en": "Fastest English-only model (13% WER)"
    }
}

LLM_MODELS = {
    "gemini": {
        "gemini-2.0-flash": "Latest Gemini model - multimodal capabilities",
        "gemini-2.0-flash-lite": "Cost efficient and low latency model",
        "gemini-2.0-flash-live-001": "Optimized for real-time interaction",
        "gemini-1.5-flash": "Fastest Gemini 1.5 model",
        "gemini-1.5-pro": "Balanced speed and quality model",
        "gemini-1.5-ultra": "Most powerful Gemini 1.5 model"
    },
    "groq": {
        "meta-llama/llama-4-scout-17b-16e-instruct": "Meta's Llama 4 Scout model (17B)",
        "meta-llama/llama-4-maverick-17b-128e-instruct": "Meta's Llama 4 Maverick model (17B, 128k context)",
        "llama-3.1-8b-instant": "Ultra-fast lightweight model",
        "llama-3.3-70b-versatile": "Powerful balanced model",
        "llama-3.2-11b-vision-preview": "Medium-size vision model",
        "llama-3.2-90b-vision-preview": "Large-size vision model",
        "mistral-saba-24b": "Mistral's SABA model (24B)",
        "qwen-2.5-32b": "Alibaba's Qwen 2.5 model (32B)"
    }
}

TTS_MODELS = {
    "cartesia": {
        "71a7ad14-091c-4e8e-a314-022ece01c121": "British Reading Lady",
        "b98e4dfe-a8ab-4e14-8cb5-a9a0abe1fd2b": "Default Male Voice", 
        "9e184750-08cd-427c-9d11-50cdf523848a": "Alternative Female Voice"
    },
    "elevenlabs": {
        "11Labs-v1/Adam": "Adam - Male, versatile",
        "11Labs-v1/Antoni": "Antoni - Male, deep with slight accent",
        "11Labs-v1/Bella": "Bella - Female, soft and warm",
        "11Labs-v1/Dorothy": "Dorothy - Female, older, warm",
        "11Labs-v1/Josh": "Josh - Male, young American",
        "11Labs-v1/Rachel": "Rachel - Female, expressive American",
        "11Labs-v1/Sam": "Sam - Male, raspy and rough"
    }
}

class ServiceOptions(BaseModel):
    stt: str = "deepgram"  # deepgram or groq
    stt_model: Optional[str] = None
    llm: str = "gemini"    # gemini or groq
    llm_model: Optional[str] = None
    tts: str = "cartesia"  # cartesia or elevenlabs
    tts_model: Optional[str] = None
    enable_search: bool = False
    optimize_latency: bool = False

class ModelInfo(BaseModel):
    models: Dict[str, Dict[str, str]]

def cleanup():
    """Cleanup function to terminate all bot processes.

    Called during server shutdown.
    """
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


async def create_room_and_token() -> tuple[str, str]:
    """Helper function to create a Daily room and generate an access token.

    Returns:
        tuple[str, str]: A tuple containing (room_url, token)

    Raises:
        HTTPException: If room creation or token generation fails
    """
    room = await daily_helpers["rest"].create_room(DailyRoomParams())
    if not room.url:
        raise HTTPException(status_code=500, detail="Failed to create room")

    token = await daily_helpers["rest"].get_token(room.url)
    if not token:
        raise HTTPException(status_code=500, detail=f"Failed to get token for room: {room.url}")

    return room.url, token


def get_default_model(service_type: str, service_name: str) -> str:
    """Get the default model for a service."""
    if service_name == "stt":
        models = STT_MODELS.get(service_type, {})
    elif service_name == "llm":
        models = LLM_MODELS.get(service_type, {})
    elif service_name == "tts":
        models = TTS_MODELS.get(service_type, {})
    else:
        return ""
        
    return next(iter(models.keys())) if models else ""


@app.get("/models/{service_type}")
async def get_models(service_type: str) -> ModelInfo:
    """Get available models for a specific service type."""
    if service_type == "stt":
        return ModelInfo(models=STT_MODELS)
    elif service_type == "llm":
        return ModelInfo(models=LLM_MODELS)
    elif service_type == "tts":
        return ModelInfo(models=TTS_MODELS)
    else:
        raise HTTPException(status_code=404, detail="Service type not found")


@app.post("/connect")
async def bot_connect(request: Request) -> Dict[Any, Any]:
    """Connect endpoint that creates a room and returns connection credentials.

    This endpoint is called by client to establish a connection.

    Returns:
        Dict[Any, Any]: Authentication bundle containing room_url and token

    Raises:
        HTTPException: If room creation, token generation, or bot startup fails
    """
    try:
        # Parse request body to get service configuration
        body = await request.json()
        service_options = ServiceOptions(**body) if body else ServiceOptions()
    except Exception:
        # Use default configuration if request parsing fails
        service_options = ServiceOptions()
    
    # Set default models if not provided
    if not service_options.stt_model:
        service_options.stt_model = get_default_model(service_options.stt, "stt")
    
    if not service_options.llm_model:
        service_options.llm_model = get_default_model(service_options.llm, "llm")
    
    if not service_options.tts_model:
        service_options.tts_model = get_default_model(service_options.tts, "tts")
    
    print(f"Using services - STT: {service_options.stt} ({service_options.stt_model})")
    print(f"LLM: {service_options.llm} ({service_options.llm_model})")
    print(f"TTS: {service_options.tts} ({service_options.tts_model})")
    print(f"Search enabled: {service_options.enable_search}, Low latency: {service_options.optimize_latency}")
    
    print("Creating room for RTVI connection")
    room_url, token = await create_room_and_token()
    print(f"Room URL: {room_url}")

    # Build command line arguments
    cmd_args = [
        f"--stt {service_options.stt}",
        f"--stt-model {service_options.stt_model}",
        f"--llm {service_options.llm}",
        f"--llm-model {service_options.llm_model}",
        f"--tts {service_options.tts}",
        f"--tts-model {service_options.tts_model}"
    ]
    
    if service_options.enable_search:
        cmd_args.append("--enable-search")
    
    if service_options.optimize_latency:
        cmd_args.append("--optimize-latency")
    
    cmd_args_str = " ".join(cmd_args)

    # Start the bot process
    try:
        bot_file = "configurable_voice_agent"
        cmd = f"python3 -m {bot_file} -u {room_url} -t {token} {cmd_args_str}"
        print(f"Starting bot with command: {cmd}")
        
        proc = subprocess.Popen(
            [cmd],
            shell=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        bot_procs[proc.pid] = (proc, room_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")

    # Return the authentication bundle in format expected by DailyTransport
    return {"room_url": room_url, "token": token}


if __name__ == "__main__":
    import uvicorn

    # Parse command line arguments for server configuration
    default_host = os.getenv("HOST", "0.0.0.0")
    default_port = int(os.getenv("FAST_API_PORT", "7860"))

    parser = argparse.ArgumentParser(description="Configurable AI Voice Agent FastAPI server")
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