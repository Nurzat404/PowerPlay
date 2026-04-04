"""Utilities for SteamID parsing and lookup."""

import logging
import os
import re
from typing import Optional
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

STEAM_PROFILES_URL_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?steamcommunity\.com/profiles/(\d{17,})(?:[/?#].*)?$',
    re.IGNORECASE,
)
STEAM_CUSTOM_URL_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?steamcommunity\.com/id/([^/?#\s]+)(?:[/?#].*)?$',
    re.IGNORECASE,
)


def _steam_api_key() -> str:
    return (os.getenv("STEAM_API_KEY") or "").strip()


def _steam_headers() -> dict:
    return {
        "User-Agent": "RazryadArenaBot/1.0",
        "Accept": "application/json, text/xml, application/xml, text/plain, */*",
    }


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

    match = STEAM_PROFILES_URL_RE.match(link)
    if match:
        steam_id = match.group(1)
        if validate_steam_id64(steam_id):
            return steam_id

    match = STEAM_CUSTOM_URL_RE.match(link)
    if match:
        return get_steam_id64_from_custom_url(match.group(1))

    return None


def get_steam_id64_from_custom_url(custom_url: str) -> Optional[str]:
    """Resolve custom Steam URL to SteamID64 via Steam Web API or profile XML fallback."""
    custom_url = (custom_url or "").strip().strip("/")
    if not custom_url:
        return None

    steam_api_key = _steam_api_key()

    if steam_api_key:
        try:
            response = requests.get(
                "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/",
                params={"key": steam_api_key, "vanityurl": custom_url},
                headers=_steam_headers(),
                timeout=5,
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                logger.warning(
                    "ResolveVanityURL returned non-JSON response: status=%s content_type=%s body=%r",
                    response.status_code,
                    response.headers.get("Content-Type"),
                    response.text[:200],
                )
            else:
                if data.get('response', {}).get('success') == 1:
                    steam_id = data['response'].get('steamid')
                    if steam_id and validate_steam_id64(steam_id):
                        return steam_id
        except Exception:
            logger.exception("ResolveVanityURL request failed")
    else:
        logger.warning("STEAM_API_KEY is not configured, using Steam community XML fallback")

    try:
        response = requests.get(
            f"https://steamcommunity.com/id/{custom_url}/?xml=1",
            headers=_steam_headers(),
            timeout=5,
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        steam_id = (root.findtext("steamID64") or "").strip()
        if validate_steam_id64(steam_id):
            return steam_id
    except Exception:
        logger.exception("Steam community XML fallback failed for custom URL %s", custom_url)

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
