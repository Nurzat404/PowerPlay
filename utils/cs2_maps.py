"""
Константы и helpers для пула карт CS2.
"""

from __future__ import annotations

import re
from typing import Iterable


CS2_MAPS = [
    {"key": "de_anubis", "name": "Anubis", "default_enabled": True},
    {"key": "de_ancient", "name": "Ancient", "default_enabled": True},
    {"key": "de_dust2", "name": "Dust2", "default_enabled": True},
    {"key": "de_inferno", "name": "Inferno", "default_enabled": True},
    {"key": "de_mirage", "name": "Mirage", "default_enabled": True},
    {"key": "de_nuke", "name": "Nuke", "default_enabled": True},
    {"key": "de_overpass", "name": "Overpass", "default_enabled": True},
    {"key": "de_train", "name": "Train", "default_enabled": False},
]

CS2_MAPS_BY_KEY = {item["key"]: item for item in CS2_MAPS}
DEFAULT_CS2_MAP_POOL = [item["key"] for item in CS2_MAPS if item["default_enabled"]]


def _normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


STANDARD_MAP_LOOKUP: dict[str, dict] = {}
for item in CS2_MAPS:
    aliases = {
        item["key"],
        item["name"],
        item["key"].replace("de_", ""),
        item["name"].replace(" ", ""),
        item["name"].replace("-", ""),
    }
    if item["key"] == "de_dust2":
        aliases.update({"dust 2", "dust-2"})
    for alias in aliases:
        STANDARD_MAP_LOOKUP[_normalize_alias(alias)] = item


def get_cs2_map_name(map_key: str) -> str:
    item = CS2_MAPS_BY_KEY.get((map_key or "").strip())
    return item["name"] if item else (map_key or "").strip()


def normalize_cs2_map_keys(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in keys:
        key = (raw or "").strip()
        if not key or key not in CS2_MAPS_BY_KEY or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


def resolve_map_catalog_entry(raw_value: str):
    if not raw_value:
        return None
    return STANDARD_MAP_LOOKUP.get(_normalize_alias(raw_value))


def map_entry_from_value(raw_value: str, used_keys: set[str] | None = None) -> tuple[str, str] | None:
    raw_name = (raw_value or "").strip()
    if not raw_name:
        return None

    resolved = resolve_map_catalog_entry(raw_name)
    if resolved:
        return resolved["key"], resolved["name"]

    used = used_keys if used_keys is not None else set()
    slug = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")
    slug = slug or "map"
    candidate = f"custom_{slug}"
    suffix = 2
    while candidate in used:
        candidate = f"custom_{slug}_{suffix}"
        suffix += 1
    return candidate, raw_name


def parse_map_pool_text(text: str) -> list[tuple[str, str]]:
    used_keys: set[str] = set()
    entries: list[tuple[str, str]] = []
    raw_items = re.split(r"[\n,;]+", text or "")
    for raw_item in raw_items:
        item = map_entry_from_value(raw_item, used_keys)
        if not item or item[0] in used_keys:
            continue
        used_keys.add(item[0])
        entries.append(item)
    return entries


def default_cs2_map_entries() -> list[tuple[str, str]]:
    return [(item["key"], item["name"]) for item in CS2_MAPS if item["default_enabled"]]
