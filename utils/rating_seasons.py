from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database import get_connection
from utils.rating_rules import (
    STATUS_SEASON_ACTIVE,
    STATUS_SEASON_COMPLETED,
    normalize_sport_key,
)


def _row_to_dict(row) -> dict[str, Any] | None:
    return dict(row) if row else None


def _current_date_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _get_sport_display_name(cur, sport_key: str) -> str:
    cur.execute("SELECT display_name FROM sports WHERE name=?", (sport_key,))
    row = cur.fetchone()
    if row and row["display_name"]:
        return str(row["display_name"])
    return sport_key


def _default_season_name(cur, sport_key: str, sequence_no: int) -> str:
    return f"{_get_sport_display_name(cur, sport_key)} Season {sequence_no}"


def ensure_active_rating_season(sport_key: str) -> dict[str, Any]:
    normalized_sport = normalize_sport_key(sport_key)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM rating_seasons
        WHERE sport_key=? AND status=?
        ORDER BY sequence_no DESC, id DESC
        LIMIT 1
        """,
        (normalized_sport, STATUS_SEASON_ACTIVE),
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        return dict(existing)

    cur.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) AS max_sequence FROM rating_seasons WHERE sport_key=?",
        (normalized_sport,),
    )
    sequence_no = int((cur.fetchone() or {"max_sequence": 0})["max_sequence"] or 0) + 1
    season_name = _default_season_name(cur, normalized_sport, sequence_no)
    cur.execute(
        """
        INSERT INTO rating_seasons (
            sport_key, name, sequence_no, start_date, end_date, status, created_at, completed_at
        )
        VALUES (?, ?, ?, ?, NULL, ?, CURRENT_TIMESTAMP, NULL)
        """,
        (normalized_sport, season_name, sequence_no, _current_date_iso(), STATUS_SEASON_ACTIVE),
    )
    season_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM rating_seasons WHERE id=?", (season_id,))
    created = cur.fetchone()
    conn.close()
    return dict(created)


def ensure_active_rating_seasons_for_all_sports() -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sports ORDER BY display_name, name")
    sports = [normalize_sport_key(row["name"]) for row in cur.fetchall()]
    conn.close()
    for sport_key in sports:
        ensure_active_rating_season(sport_key)


def get_active_rating_season(sport_key: str) -> dict[str, Any]:
    normalized_sport = normalize_sport_key(sport_key)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM rating_seasons
        WHERE sport_key=? AND status=?
        ORDER BY sequence_no DESC, id DESC
        LIMIT 1
        """,
        (normalized_sport, STATUS_SEASON_ACTIVE),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return ensure_active_rating_season(normalized_sport)


def list_rating_seasons(sport_key: str) -> list[dict[str, Any]]:
    normalized_sport = normalize_sport_key(sport_key)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM rating_seasons
        WHERE sport_key=?
        ORDER BY sequence_no DESC, id DESC
        """,
        (normalized_sport,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def get_rating_season_by_id(season_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rating_seasons WHERE id=?", (season_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)


def get_adjacent_season_ids(sport_key: str, season_id: int) -> tuple[int | None, int | None]:
    seasons = list_rating_seasons(sport_key)
    ids = [int(row["id"]) for row in seasons]
    if season_id not in ids:
        return None, None
    idx = ids.index(season_id)
    prev_id = ids[idx + 1] if idx + 1 < len(ids) else None
    next_id = ids[idx - 1] if idx - 1 >= 0 else None
    return prev_id, next_id


def advance_to_next_rating_season(sport_key: str) -> dict[str, Any]:
    normalized_sport = normalize_sport_key(sport_key)
    conn = get_connection()
    cur = conn.cursor()
    current = get_active_rating_season(normalized_sport)
    current_id = int(current["id"])
    current_sequence = int(current["sequence_no"] or 0)
    today = _current_date_iso()

    cur.execute(
        """
        UPDATE rating_seasons
        SET status=?, end_date=COALESCE(end_date, ?), completed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (STATUS_SEASON_COMPLETED, today, current_id),
    )
    next_sequence = current_sequence + 1
    season_name = _default_season_name(cur, normalized_sport, next_sequence)
    cur.execute(
        """
        INSERT INTO rating_seasons (
            sport_key, name, sequence_no, start_date, end_date, status, created_at, completed_at
        )
        VALUES (?, ?, ?, ?, NULL, ?, CURRENT_TIMESTAMP, NULL)
        """,
        (normalized_sport, season_name, next_sequence, today, STATUS_SEASON_ACTIVE),
    )
    new_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM rating_seasons WHERE id=?", (current_id,))
    previous = dict(cur.fetchone())
    cur.execute("SELECT * FROM rating_seasons WHERE id=?", (new_id,))
    current_new = dict(cur.fetchone())
    conn.close()
    return {"previous": previous, "current": current_new}
