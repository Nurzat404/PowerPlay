from __future__ import annotations

import math

ENTITY_PLAYER = "player"
ENTITY_TEAM = "team"

SCOPE_OVERALL = "overall"
SCOPE_SEASONAL = "seasonal"

FORMAT_GENERAL = "general"

SOURCE_MANUAL = "manual"
SOURCE_TOURNAMENT = "tournament"
SOURCE_BRACKET_MATCH = "bracket_match"
SOURCE_LEGACY_MATCH = "legacy_match"
SOURCE_REFERRAL = "referral"

STATUS_SEASON_ACTIVE = "active"
STATUS_SEASON_COMPLETED = "completed"
STATUS_SEASON_UPCOMING = "upcoming"

PLAYER_RATING_RULES = {
    "participation": 2,
    "match_win": 5,
    "first_place": 20,
    "second_place": 15,
    "third_place": 10,
    "match_mvp": 1,
    "tournament_mvp": 3,
    "mvp_cap": 4,
}

TEAM_RATING_RULES = {
    "match_win": 5,
    "match_draw": 1,
    "first_place": 20,
    "second_place": 15,
    "third_place": 10,
}

CS2_FORMAT_MULTIPLIERS = {
    "1x1": 1.0,
    "2x2": 0.8,
    "5x5": 0.6,
}

SPORT_ALIASES = {
    "cs2": "CS2",
    "counter-strike 2": "CS2",
    "basketball": "Basketball",
    "баскетбол": "Basketball",
    "football": "Football",
    "футбол": "Football",
    "volleyball": "Volleyball",
    "волейбол": "Volleyball",
}


def normalize_sport_key(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in {"CS2", "Basketball", "Football", "Volleyball"}:
        return raw
    return SPORT_ALIASES.get(raw.lower(), raw)


def sport_supports_formats(sport_key: str | None) -> bool:
    return normalize_sport_key(sport_key) == "CS2"


def normalize_format_key(format_key: str | None, sport_key: str | None) -> str | None:
    if not sport_supports_formats(sport_key):
        return None
    raw = str(format_key or "").strip().lower()
    if not raw or raw in {FORMAT_GENERAL, "overall", "all", "sport", "none"}:
        return None
    if raw in CS2_FORMAT_MULTIPLIERS:
        return raw
    return None


def get_format_options_for_sport(sport_key: str | None) -> list[tuple[str, str]]:
    if not sport_supports_formats(sport_key):
        return []
    return [
        (FORMAT_GENERAL, "Общий"),
        ("1x1", "1x1"),
        ("2x2", "2x2"),
        ("5x5", "5x5"),
    ]


def resolve_cs2_format_from_team_size(sport_key: str | None, required_team_size: int | None) -> str | None:
    if not sport_supports_formats(sport_key):
        return None
    size = int(required_team_size or 0)
    if size == 1:
        return "1x1"
    if size == 2:
        return "2x2"
    if size == 5:
        return "5x5"
    return None


def get_rating_multiplier(sport_key: str | None, format_key: str | None) -> float:
    normalized_sport = normalize_sport_key(sport_key)
    normalized_format = normalize_format_key(format_key, normalized_sport)
    if normalized_sport == "CS2" and normalized_format:
        return CS2_FORMAT_MULTIPLIERS.get(normalized_format, 1.0)
    return 1.0


def round_rating_points(value: float) -> int:
    if value <= 0:
        return 0
    return int(math.floor(value + 0.5))
