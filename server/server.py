import os
import argparse
import subprocess
from typing import Any, Dict
from contextlib import asynccontextmanager

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pipecat.transports.service.helpers.daily_rest import DailyRESTHelper, DailyRoomParams

load_dotenv(dotenv_path='.env')

bot_procs = {}
daily_helpers = {}

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

async def create_room_and_token() -> tuple[str, str]:
	room = await daily_helpers["rest"].create_room(DailyRoomParams())
	if not room.url:
		raise HTTPException(status_code=500, detail="Failed to create room")

	token = await daily_helpers["rest"].get_token(room.url)
	if not token:
		raise HTTPException(status_code=500, detail=f"Failed to obtain token for {room.url}")

	return room.url, token

@app.post("/connect")
async def bot_connect(request: Request) -> Dict[Any, Any]:
	print("Creating room for RTVI connection")
	room_url, token = await create_room_and_token()
	print(f"Room URL: {room_url}")

	try:
		bot_file = "bot"
		proc = subprocess.Popen(
			[f"uv run -m {bot_file} - u {room_url} -t {token}"],
			shell=True,
			bufsize=1,
			cwd=os.path.dirname(os.path.abspath(__file__)),
		)
		bot_procs[proc.pid] = (proc, room_url)

	except Exception as e:
		raise HTTPException(status_code=500, detail=f"Failed to start subprocess: {e}")

	return {"room_url": room_url, "token": token}

if __name__ == "__main__":
	import uvicorn

	default_host = os.getenv("HOST", "0.0.0.0")
	default_port = int(os.getenv("FAST_API_PORT", "7860"))

	parser = argparse.ArgumentParser(description="Daily-FastAPI Server")
	parser.add_argument("--host", type=str, default=default_host, help="Host address")
	parser.add_argument("--port", type=str, default=default_port, help="Default port")
	parser.add_argument("--reload", action="store_true", help="Reload code on change")

	config = parser.parse_args()

	uvicorn.run(
		"server:app",
		host=config.host,
		port=config.port,
		reload=config.reload,
	)