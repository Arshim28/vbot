import os
import argparse
from typing import Optional, Tuple

import aiohttp

from pipecat.transports.services.helpers.daily_rest import DailyRESTHelper


async def configure(aiohttp_session: aiohttp.ClientSession):
    """Simple version for backward compatibility"""
    (url, token, _, _) = await configure_with_args(aiohttp_session)
    return (url, token)


async def configure_with_args(
    aiohttp_session: aiohttp.ClientSession, parser: Optional[argparse.ArgumentParser] = None
) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Configure Daily and parse arguments including call_id and client_id"""
    if not parser:
        parser = argparse.ArgumentParser(description="Daily AI SDK Bot Sample")
        
    parser.add_argument(
        "-u", "--url", type=str, required=False, help="URL of the Daily room to join"
    )
    parser.add_argument(
        "-t", "--token", type=str, required=False, help="Token for the Daily room"
    )
    parser.add_argument(
        "-k",
        "--apikey",
        type=str,
        required=False,
        help="Daily API Key (needed to create an owner token for the room)",
    )
    parser.add_argument(
        "--call_id",
        type=str,
        required=False,
        help="Call ID for this conversation"
    )
    parser.add_argument(
        "--client_id",
        type=str,
        required=False,
        help="Client ID for this conversation"
    )

    args, unknown = parser.parse_known_args()

    url = args.url or os.getenv("DAILY_SAMPLE_ROOM_URL")
    key = args.apikey or os.getenv("DAILY_API_KEY")
    call_id = args.call_id
    client_id = args.client_id

    if not url:
        raise Exception(
            "No Daily room specified. use the -u/--url option from the command line, or set DAILY_SAMPLE_ROOM_URL in your environment to specify a Daily room URL."
        )

    if not key:
        raise Exception(
            "No Daily API key specified. use the -k/--apikey option from the command line, or set DAILY_API_KEY in your environment to specify a Daily API key, available from https://dashboard.daily.co/developers."
        )

    daily_rest_helper = DailyRESTHelper(
        daily_api_key=key,
        daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
        aiohttp_session=aiohttp_session,
    )

    expiry_time: float = 60 * 60

    token = await daily_rest_helper.get_token(url, expiry_time)

    return (url, token, call_id, client_id)