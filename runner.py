import argparse
import os
from typing import Optional, Tuple, Any

import aiohttp

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper


async def configure(aiohttp_session: aiohttp.ClientSession) -> Tuple[str, str]:
    (url, token, _) = await configure_with_args(aiohttp_session)
    return (url, token)


async def configure_with_args(
    aiohttp_session: aiohttp.ClientSession, 
    parser: Optional[argparse.ArgumentParser] = None
) -> Tuple[str, str, Any]:

    if not parser:
        parser = argparse.ArgumentParser(description="Configurable AI Voice Agent")
    
    parser.add_argument(
        "-u", "--url", 
        type=str, 
        required=False, 
        help="URL of the Daily room to join"
    )
    parser.add_argument(
        "-k", "--apikey",
        type=str,
        required=False,
        help="Daily API Key (needed to create an owner token for the room)",
    )
    parser.add_argument(
        "-t", "--token",
        type=str,
        required=False,
        help="Daily room token (if already created)",
    )

    args, unknown = parser.parse_known_args()

    url = args.url or os.getenv("DAILY_SAMPLE_ROOM_URL")
    key = args.apikey or os.getenv("DAILY_API_KEY")

    if not url:
        if not key:
            raise Exception(
                "No Daily API key specified. Use the -k/--apikey option from the command line, "
                "or set DAILY_API_KEY in your environment to specify a Daily API key, "
                "available from https://dashboard.daily.co/developers."
            )
        
        daily_rest_helper = DailyRESTHelper(
            daily_api_key=key,
            daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
            aiohttp_session=aiohttp_session,
        )
        
        room = await daily_rest_helper.create_room()
        if not room or not room.url:
            raise Exception("Failed to create Daily room")
        
        url = room.url
        print(f"Created new Daily room: {url}")
    
    token = args.token
    
    if not token:
        if not key:
            raise Exception(
                "No Daily API key specified. Use the -k/--apikey option from the command line, "
                "or set DAILY_API_KEY in your environment to specify a Daily API key, "
                "available from https://dashboard.daily.co/developers."
            )
        
        # Initialize the REST helper if not already done
        if 'daily_rest_helper' not in locals():
            daily_rest_helper = DailyRESTHelper(
                daily_api_key=key,
                daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
                aiohttp_session=aiohttp_session,
            )
        
        # Create a meeting token with 1 hour expiry
        expiry_time: float = 60 * 60
        token = await daily_rest_helper.get_token(url, expiry_time)
        
        if not token:
            raise Exception(f"Failed to create token for room: {url}")

    return (url, token, args)


async def create_room_with_token(aiohttp_session: aiohttp.ClientSession) -> Tuple[str, str]:
    """
    Create a new Daily room and token without command-line arguments.
    
    Args:
        aiohttp_session: Active aiohttp client session
        
    Returns:
        Tuple containing (room_url, token)
    """
    key = os.getenv("DAILY_API_KEY")
    
    if not key:
        raise Exception("DAILY_API_KEY environment variable is required")
    
    # Initialize the REST helper
    daily_rest_helper = DailyRESTHelper(
        daily_api_key=key,
        daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
        aiohttp_session=aiohttp_session,
    )
    
    # Create a new room
    room = await daily_rest_helper.create_room()
    if not room or not room.url:
        raise Exception("Failed to create Daily room")
    
    url = room.url
    
    # Create a token with 1 hour expiry
    expiry_time: float = 60 * 60
    token = await daily_rest_helper.get_token(url, expiry_time)
    
    if not token:
        raise Exception(f"Failed to create token for room: {url}")
    
    return (url, token)