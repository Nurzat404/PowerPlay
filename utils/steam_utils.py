"""Utilities for SteamID parsing and lookup."""

import logging
import os
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _steam_api_key() -> str:
    return (os.getenv("STEAM_API_KEY") or "").strip()


def validate_steam_id64(steam_id: str) -> bool:
    """Return True if value looks like SteamID64."""
    if not steam_id:
        return False
    return bool(re.match(r'^7656\d{13}$', steam_id.strip()))


def parse_steam_link(link: str) -> Optional[str]:
    """Parse profile URL/custom URL/ID and return SteamID64 if possible."""
    if not link:
        return None

    link = link.strip()
    if validate_steam_id64(link):
        return link

    match = re.match(r'https?://steamcommunity\.com/profiles/(\d+)', link)
    if match:
        steam_id = match.group(1)
        if validate_steam_id64(steam_id):
            return steam_id

    match = re.match(r'https?://steamcommunity\.com/id/([^/\s?]+)', link)
    if match:
        return get_steam_id64_from_custom_url(match.group(1))

    return None


def get_steam_id64_from_custom_url(custom_url: str) -> Optional[str]:
    """Resolve custom Steam URL to SteamID64 via Steam Web API."""
    steam_api_key = _steam_api_key()
    if not steam_api_key:
        logger.warning("STEAM_API_KEY is not configured")
        return None

    try:
        response = requests.get(
            "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/",
            params={"key": steam_api_key, "vanityurl": custom_url},
            timeout=5,
        )
        data = response.json()

        if data.get('response', {}).get('success') == 1:
            steam_id = data['response'].get('steamid')
            if steam_id and validate_steam_id64(steam_id):
                return steam_id

        return None
    except Exception:
        logger.exception("ResolveVanityURL request failed")
        return None


def get_steam_profile_url(steam_id: str) -> str:
    return f"https://steamcommunity.com/profiles/{steam_id}"


def get_steam_id_instructions() -> str:
    return (
        "How to find SteamID64:\n\n"
        "1) Open Steam profile\n"
        "2) Copy profile URL\n"
        "3) Send URL to bot\n\n"
        "Alternative:\n"
        "- Open https://steamid.io/\n"
        "- Paste profile URL\n"
        "- Copy SteamID64 (starts with 7656...)"
    )
