"""
Сервисный слой для CS2 map veto / pick-ban.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sqlite3
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_connection
from razryad_arena_utils import (
    can_manage_tournament,
    format_utc_to_msk,
    get_bracket_match_by_id,
    get_team_members,
    get_tournament_by_id,
    get_tournament_manager_chat_ids,
    get_tournament_map_pool,
    get_user,
    get_user_by_id,
    is_captain,
    normalize_sport_name,
    parse_utc_storage_datetime,
    resolve_bracket_match_format,
)
from utils.cs2_maps import DEFAULT_CS2_MAP_POOL, get_cs2_map_name

logger = logging.getLogger(__name__)

STATUS_NOT_READY = "not_ready"
STATUS_READY = "ready_to_start"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

LAUNCH_AUTO = "auto_start"
LAUNCH_ADMIN = "admin_start"

START_KIND_SYSTEM = "system"
START_KIND_ADMIN = "admin"

START_SOURCE_AUTO = "auto"
START_SOURCE_NOTIFICATION = "notification"
START_SOURCE_PANEL = "panel"

ACTION_BAN = "ban"
ACTION_PICK = "pick"

FIXED_STEPS = {
    "bo3": [
        (1, ACTION_BAN),
        (2, ACTION_BAN),
        (1, ACTION_PICK),
        (2, ACTION_PICK),
        (1, ACTION_BAN),
        (2, ACTION_BAN),
    ],
    "bo5": [
        (1, ACTION_BAN),
        (2, ACTION_BAN),
        (1, ACTION_PICK),
        (2, ACTION_PICK),
        (1, ACTION_PICK),
        (2, ACTION_PICK),
    ],
}


@dataclass
class SessionResolution:
    status: str
    current_step_index: int | None
    current_team_id: int | None
    current_action_type: str | None


def _normalize_format(match_format: str | None) -> str:
    value = (match_format or "bo3").strip().lower()
    return value if value in {"bo1", "bo3", "bo5"} else "bo3"


def _is_cs2_veto_match(match: dict[str, Any]) -> bool:
    return (
        normalize_sport_name(match.get("tournament_sport")) == "CS2"
        and int(match.get("map_veto_enabled") or 0) == 1
        and bool(match.get("team1_id"))
        and bool(match.get("team2_id"))
    )


def _get_pool_rows_or_default(tournament_id: int) -> list[dict[str, Any]]:
    rows = [dict(row) for row in get_tournament_map_pool(tournament_id)]
    if rows:
        return rows

    default_rows: list[dict[str, Any]] = []
    for idx, map_key in enumerate(DEFAULT_CS2_MAP_POOL, 1):
        default_rows.append(
            {
                "tournament_id": tournament_id,
                "map_key": map_key,
                "map_name": get_cs2_map_name(map_key),
                "sort_order": idx,
            }
        )
    return default_rows


def validate_veto_pool(match_format: str, map_keys: list[str]) -> tuple[bool, str | None]:
    normalized = _normalize_format(match_format)
    unique = []
    seen: set[str] = set()
    for map_key in map_keys:
        key = (map_key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)

    if normalized == "bo1":
        if len(unique) < 3 or len(unique) % 2 == 0:
            return False, "Для BO1 нужен нечетный пул минимум из 3 карт."
        return True, None

    if len(unique) < 7:
        return False, "Для BO3/BO5 нужен пул минимум из 7 уникальных карт."
    return True, None


def _fetch_candidate_matches(tournament_id: int | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    sql = """
        SELECT
            b.*,
            t.name AS tournament_name,
            t.sport AS tournament_sport,
            t.map_veto_enabled,
            t.veto_launch_mode,
            tm1.name AS team1_name,
            tm2.name AS team2_name
        FROM tournament_brackets b
        JOIN tournaments t ON t.id = b.tournament_id
        LEFT JOIN teams tm1 ON tm1.id = b.team1_id
        LEFT JOIN teams tm2 ON tm2.id = b.team2_id
        WHERE b.status='pending'
          AND COALESCE(t.map_veto_enabled, 0)=1
          AND t.sport='CS2'
    """
    params: list[Any] = []
    if tournament_id is not None:
        sql += " AND b.tournament_id=?"
        params.append(tournament_id)
    sql += " ORDER BY b.tournament_id, b.round_number, b.match_number"
    cur.execute(sql, params)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _fetch_session(bracket_match_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM match_veto_sessions
        WHERE bracket_match_id=?
    """, (bracket_match_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _fetch_session_by_id(session_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM match_veto_sessions WHERE id=?", (session_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _fetch_actions(session_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM match_veto_actions
        WHERE session_id=?
        ORDER BY step_index, id
    """, (session_id,))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _fetch_series_maps(session_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM match_series_maps
        WHERE session_id=?
        ORDER BY map_order
    """, (session_id,))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _fetch_message_targets(session_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT session_id, chat_id, message_id
        FROM match_veto_message_targets
        WHERE session_id=?
    """, (session_id,))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def store_veto_message_target(bracket_match_id: int, chat_id: int, message_id: int):
    session = _fetch_session(bracket_match_id)
    if not session:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO match_veto_message_targets (
            session_id, chat_id, message_id, created_at, updated_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, chat_id) DO UPDATE SET
            message_id=excluded.message_id,
            updated_at=CURRENT_TIMESTAMP
    """, (session["id"], int(chat_id), int(message_id)))
    conn.commit()
    conn.close()


def _delete_veto_message_target(session_id: int, chat_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM match_veto_message_targets
        WHERE session_id=? AND chat_id=?
    """, (session_id, int(chat_id)))
    conn.commit()
    conn.close()


def get_completed_series_maps_for_match(bracket_match_id: int) -> list[dict[str, Any]]:
    session = _fetch_session(bracket_match_id)
    if not session or session["status"] != STATUS_COMPLETED:
        return []
    return _fetch_series_maps(session["id"])


def has_match_results_for_bracket_match(bracket_match_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM match_map_results
        WHERE match_source='bracket' AND match_id=?
        LIMIT 1
    """, (bracket_match_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def _remaining_pool(pool_rows: list[dict[str, Any]], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used = {
        row["map_key"]
        for row in actions
        if row["action_type"] in {ACTION_BAN, ACTION_PICK} and row.get("map_key")
    }
    return [row for row in pool_rows if row["map_key"] not in used]


def _picked_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in actions if row["action_type"] == ACTION_PICK]


def _resolve_session_state(session: dict[str, Any], pool_rows: list[dict[str, Any]], actions: list[dict[str, Any]]) -> SessionResolution:
    if session["status"] in {STATUS_COMPLETED, STATUS_CANCELLED}:
        return SessionResolution(session["status"], None, None, None)

    played = len([row for row in actions if row["action_type"] in {ACTION_BAN, ACTION_PICK}])
    remaining = _remaining_pool(pool_rows, actions)
    match_format = _normalize_format(session["match_format"])

    if match_format == "bo1":
        if len(remaining) <= 1:
            return SessionResolution(STATUS_COMPLETED, None, None, None)
        current_team_id = session["team1_id"] if played % 2 == 0 else session["team2_id"]
        return SessionResolution(STATUS_IN_PROGRESS, played + 1, current_team_id, ACTION_BAN)

    step_config = FIXED_STEPS[match_format]
    if played >= len(step_config) or len(remaining) <= 1:
        return SessionResolution(STATUS_COMPLETED, None, None, None)

    slot, action_type = step_config[played]
    current_team_id = session["team1_id"] if slot == 1 else session["team2_id"]
    return SessionResolution(STATUS_IN_PROGRESS, played + 1, current_team_id, action_type)


def _write_session_state(conn: sqlite3.Connection, session_id: int, resolution: SessionResolution):
    cur = conn.cursor()
    cur.execute("""
        UPDATE match_veto_sessions
        SET status=?,
            current_step_index=?,
            current_team_id=?,
            current_action_type=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (
        resolution.status,
        resolution.current_step_index,
        resolution.current_team_id,
        resolution.current_action_type,
        session_id,
    ))


def _team_captain_id(team_id: int | None) -> int | None:
    if not team_id:
        return None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT captain_id FROM teams WHERE id=?", (team_id,))
    row = cur.fetchone()
    conn.close()
    return row["captain_id"] if row else None


def get_match_veto_details(bracket_match_id: int) -> dict[str, Any] | None:
    match = get_bracket_match_by_id(bracket_match_id)
    if not match:
        return None
    match_payload = dict(match)
    tournament = get_tournament_by_id(match_payload["tournament_id"])
    if tournament:
        match_payload["match_format"] = resolve_bracket_match_format(bracket_match_id)
        match_payload["map_veto_enabled"] = tournament["map_veto_enabled"]
        match_payload["veto_launch_mode"] = tournament["veto_launch_mode"]

    session = _fetch_session(bracket_match_id)
    pool_rows = _get_pool_rows_or_default(match_payload["tournament_id"])
    actions = _fetch_actions(session["id"]) if session else []
    series_maps = _fetch_series_maps(session["id"]) if session else []

    return {
        "match": match_payload,
        "session": session,
        "actions": actions,
        "maps": series_maps,
        "pool": pool_rows,
        "available_maps": _remaining_pool(pool_rows, actions),
        "captain1": get_user_by_id(_team_captain_id(match_payload.get("team1_id"))) if match_payload.get("team1_id") else None,
        "captain2": get_user_by_id(_team_captain_id(match_payload.get("team2_id"))) if match_payload.get("team2_id") else None,
    }


def sync_veto_session_for_match(bracket_match_id: int) -> dict[str, Any] | None:
    match = get_bracket_match_by_id(bracket_match_id)
    if not match:
        return None
    match_payload = dict(match)
    tournament = get_tournament_by_id(match_payload["tournament_id"])
    if not tournament:
        return None
    match_payload["match_format"] = resolve_bracket_match_format(bracket_match_id)
    match_payload["map_veto_enabled"] = tournament["map_veto_enabled"]
    match_payload["veto_launch_mode"] = tournament["veto_launch_mode"]

    if not _is_cs2_veto_match(match_payload):
        return None

    pool_rows = _get_pool_rows_or_default(match_payload["tournament_id"])
    ok, _ = validate_veto_pool(match_payload["match_format"], [row["map_key"] for row in pool_rows])
    if not ok:
        return None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM match_veto_sessions
        WHERE bracket_match_id=?
    """, (bracket_match_id,))
    session = cur.fetchone()
    match_format = _normalize_format(match_payload["match_format"])

    if not session:
        cur.execute("""
            INSERT INTO match_veto_sessions (
                bracket_match_id, tournament_id, team1_id, team2_id, match_format,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            bracket_match_id,
            match_payload["tournament_id"],
            match_payload["team1_id"],
            match_payload["team2_id"],
            match_format,
            STATUS_NOT_READY,
        ))
        conn.commit()
        session_id = cur.lastrowid
        conn.close()
        return _fetch_session_by_id(session_id)

    session_payload = dict(session)
    if session_payload["status"] in {STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_CANCELLED}:
        conn.close()
        return session_payload

    cur.execute("""
        UPDATE match_veto_sessions
        SET tournament_id=?,
            team1_id=?,
            team2_id=?,
            match_format=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE bracket_match_id=?
    """, (
        match_payload["tournament_id"],
        match_payload["team1_id"],
        match_payload["team2_id"],
        match_format,
        bracket_match_id,
    ))
    conn.commit()
    conn.close()
    return _fetch_session(bracket_match_id)


def sync_veto_sessions_for_tournament(tournament_id: int) -> list[dict[str, Any]]:
    synced: list[dict[str, Any]] = []
    for match in _fetch_candidate_matches(tournament_id):
        session = sync_veto_session_for_match(match["id"])
        if session:
            synced.append(session)
    return synced


def list_tournament_veto_sessions(tournament_id: int, statuses: list[str] | None = None) -> list[dict[str, Any]]:
    sync_veto_sessions_for_tournament(tournament_id)

    conn = get_connection()
    cur = conn.cursor()
    sql = """
        SELECT
            s.*,
            b.round_number,
            b.round_name,
            b.match_number,
            b.scheduled_at_utc,
            b.location,
            t1.name AS team1_name,
            t2.name AS team2_name
        FROM match_veto_sessions s
        JOIN tournament_brackets b ON b.id = s.bracket_match_id
        LEFT JOIN teams t1 ON t1.id = s.team1_id
        LEFT JOIN teams t2 ON t2.id = s.team2_id
        WHERE s.tournament_id=?
    """
    params: list[Any] = [tournament_id]
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" AND s.status IN ({placeholders})"
        params.extend(statuses)
    sql += " ORDER BY b.round_number, b.match_number"
    cur.execute(sql, params)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _match_due(match: dict[str, Any]) -> bool:
    if not match.get("scheduled_at_utc") or not match.get("location"):
        return False
    scheduled_dt = parse_utc_storage_datetime(match.get("scheduled_at_utc"))
    if not scheduled_dt:
        return False
    return scheduled_dt <= datetime.now(timezone.utc)


def start_veto_session(
    bracket_match_id: int,
    *,
    actor_telegram_id: int | None = None,
    started_by_kind: str = START_KIND_ADMIN,
    start_source: str = START_SOURCE_PANEL,
) -> tuple[bool, str | None]:
    session = sync_veto_session_for_match(bracket_match_id)
    match = get_bracket_match_by_id(bracket_match_id)
    if not session or not match:
        return False, "Сессия veto недоступна для этого матча."
    match_payload = dict(match)
    due_reached = _match_due(match_payload)
    is_system_start = started_by_kind == START_KIND_SYSTEM
    if not due_reached and is_system_start:
        return False, "Нельзя запустить pick/ban до времени начала матча."
    if session["status"] == STATUS_COMPLETED:
        return False, "Pick/ban уже завершен."
    if session["status"] == STATUS_IN_PROGRESS:
        return True, None

    pool_rows = _get_pool_rows_or_default(session["tournament_id"])
    actions = _fetch_actions(session["id"])
    resolution = _resolve_session_state(session, pool_rows, actions)
    actor = get_user(actor_telegram_id) if actor_telegram_id else None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE match_veto_sessions
        SET status=?,
            ready_at=COALESCE(ready_at, CURRENT_TIMESTAMP),
            started_at=CURRENT_TIMESTAMP,
            completed_at=NULL,
            cancelled_at=NULL,
            started_by_user_id=?,
            started_by_kind=?,
            start_source=?,
            current_step_index=?,
            current_team_id=?,
            current_action_type=?,
            auto_start_consumed=1,
            updated_at=CURRENT_TIMESTAMP
        WHERE bracket_match_id=?
    """, (
        STATUS_IN_PROGRESS,
        actor["id"] if actor else None,
        started_by_kind,
        start_source,
        resolution.current_step_index,
        resolution.current_team_id,
        resolution.current_action_type,
        bracket_match_id,
    ))
    cur.execute("""
        INSERT INTO match_veto_actions (
            session_id, step_index, action_type, actor_user_id, actor_role,
            team_id, map_key, map_name
        )
        VALUES (?, 0, 'start', ?, ?, NULL, NULL, NULL)
    """, (
        session["id"],
        actor["id"] if actor else None,
        "system" if started_by_kind == START_KIND_SYSTEM else "admin",
    ))
    conn.commit()
    conn.close()
    return True, None


def mark_session_ready(bracket_match_id: int) -> bool:
    session = sync_veto_session_for_match(bracket_match_id)
    match = get_bracket_match_by_id(bracket_match_id)
    if not session or not match:
        return False
    if session["status"] in {STATUS_READY, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_CANCELLED}:
        return False
    if not _match_due(dict(match)):
        return False

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE match_veto_sessions
        SET status=?,
            ready_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        WHERE bracket_match_id=?
    """, (STATUS_READY, bracket_match_id))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def _store_series_maps(conn: sqlite3.Connection, session: dict[str, Any], actions: list[dict[str, Any]], pool_rows: list[dict[str, Any]]):
    cur = conn.cursor()
    cur.execute("DELETE FROM match_series_maps WHERE session_id=?", (session["id"],))
    picks = _picked_actions(actions)
    next_order = 1
    for action in picks:
        cur.execute("""
            INSERT INTO match_series_maps (
                session_id, map_order, map_key, map_name, selection_type,
                selected_by_team_id, source_action_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session["id"],
            next_order,
            action["map_key"],
            action["map_name"] or get_cs2_map_name(action["map_key"]),
            ACTION_PICK,
            action["team_id"],
            action["id"],
        ))
        next_order += 1

    remaining = _remaining_pool(pool_rows, actions)
    if remaining:
        decider = remaining[0]
        cur.execute("""
            INSERT INTO match_series_maps (
                session_id, map_order, map_key, map_name, selection_type,
                selected_by_team_id, source_action_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session["id"],
            next_order,
            decider["map_key"],
            decider["map_name"],
            "decider",
            None,
            None,
        ))


def perform_veto_action(bracket_match_id: int, actor_telegram_id: int, map_key: str) -> tuple[bool, str | None]:
    session = _fetch_session(bracket_match_id)
    match = get_bracket_match_by_id(bracket_match_id)
    if not session or not match:
        return False, "Сессия не найдена."
    if session["status"] != STATUS_IN_PROGRESS:
        return False, "Pick/ban еще не запущен."

    actor = get_user(actor_telegram_id)
    if not actor:
        return False, "Пользователь не найден."

    if not is_captain(actor["id"], session["current_team_id"]):
        return False, "Сейчас ход другого капитана."

    normalized_key = (map_key or "").strip()
    pool_rows = _get_pool_rows_or_default(session["tournament_id"])
    available = _remaining_pool(pool_rows, _fetch_actions(session["id"]))
    available_by_key = {row["map_key"]: row for row in available}
    if normalized_key not in available_by_key:
        return False, "Эта карта уже недоступна."

    step_index = session["current_step_index"] or 1
    action_type = session["current_action_type"] or ACTION_BAN

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO match_veto_actions (
            session_id, step_index, action_type, actor_user_id, actor_role,
            team_id, map_key, map_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session["id"],
        step_index,
        action_type,
        actor["id"],
        "captain",
        session["current_team_id"],
        normalized_key,
        available_by_key[normalized_key].get("map_name") or get_cs2_map_name(normalized_key),
    ))
    conn.commit()

    refreshed_actions = _fetch_actions(session["id"])
    resolution = _resolve_session_state(session, pool_rows, refreshed_actions)
    if resolution.status == STATUS_COMPLETED:
        _store_series_maps(conn, session, refreshed_actions, pool_rows)
        cur.execute("""
            UPDATE match_veto_sessions
            SET status=?,
                current_step_index=NULL,
                current_team_id=NULL,
                current_action_type=NULL,
                completed_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (STATUS_COMPLETED, session["id"]))
    else:
        _write_session_state(conn, session["id"], resolution)

    conn.commit()
    conn.close()
    return True, None


def cancel_veto_session(bracket_match_id: int, actor_telegram_id: int | None = None) -> tuple[bool, str | None]:
    session = _fetch_session(bracket_match_id)
    if not session:
        return False, "Сессия не найдена."
    if session["status"] == STATUS_COMPLETED:
        return False, "Нельзя отменить завершенный pick/ban."

    actor = get_user(actor_telegram_id) if actor_telegram_id else None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE match_veto_sessions
        SET status=?,
            current_step_index=NULL,
            current_team_id=NULL,
            current_action_type=NULL,
            cancelled_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        WHERE bracket_match_id=?
    """, (STATUS_CANCELLED, bracket_match_id))
    cur.execute("""
        INSERT INTO match_veto_actions (
            session_id, step_index, action_type, actor_user_id, actor_role,
            team_id, map_key, map_name
        )
        VALUES (?, 0, 'cancel', ?, 'admin', NULL, NULL, NULL)
    """, (session["id"], actor["id"] if actor else None))
    conn.commit()
    conn.close()
    return True, None


def reset_veto_session(bracket_match_id: int, actor_telegram_id: int | None = None) -> tuple[bool, str | None]:
    session = _fetch_session(bracket_match_id)
    match = get_bracket_match_by_id(bracket_match_id)
    if not session or not match:
        return False, "Сессия не найдена."
    if has_match_results_for_bracket_match(bracket_match_id):
        return False, "Нельзя сбросить pick/ban после сохранения результатов по картам."

    next_status = STATUS_READY if _match_due(dict(match)) else STATUS_NOT_READY

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM match_series_maps WHERE session_id=?", (session["id"],))
    cur.execute("DELETE FROM match_veto_actions WHERE session_id=?", (session["id"],))
    cur.execute("""
        UPDATE match_veto_sessions
        SET status=?,
            ready_at=CASE WHEN ? = ? THEN CURRENT_TIMESTAMP ELSE NULL END,
            started_at=NULL,
            completed_at=NULL,
            cancelled_at=NULL,
            started_by_user_id=NULL,
            started_by_kind=NULL,
            start_source=NULL,
            current_step_index=NULL,
            current_team_id=NULL,
            current_action_type=NULL,
            admin_notified_at=NULL,
            captains_notified_at=NULL,
            updated_at=CURRENT_TIMESTAMP
        WHERE bracket_match_id=?
    """, (next_status, next_status, STATUS_READY, bracket_match_id))
    actor = get_user(actor_telegram_id) if actor_telegram_id else None
    cur.execute("""
        INSERT INTO match_veto_actions (
            session_id, step_index, action_type, actor_user_id, actor_role,
            team_id, map_key, map_name
        )
        VALUES (?, 0, 'reset', ?, 'admin', NULL, NULL, NULL)
    """, (session["id"], actor["id"] if actor else None))
    conn.commit()
    conn.close()
    return True, None


def list_due_sessions_for_dispatch() -> list[dict[str, Any]]:
    for match in _fetch_candidate_matches():
        sync_veto_session_for_match(match["id"])

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            s.*,
            b.scheduled_at_utc,
            b.location,
            b.round_number,
            b.round_name,
            b.match_number,
            t.name AS tournament_name,
            t.veto_launch_mode,
            t1.name AS team1_name,
            t2.name AS team2_name
        FROM match_veto_sessions s
        JOIN tournament_brackets b ON b.id = s.bracket_match_id
        JOIN tournaments t ON t.id = s.tournament_id
        LEFT JOIN teams t1 ON t1.id = s.team1_id
        LEFT JOIN teams t2 ON t2.id = s.team2_id
        WHERE s.status IN (?, ?)
        ORDER BY b.scheduled_at_utc, s.id
    """, (STATUS_NOT_READY, STATUS_READY))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _manager_chat_ids_for_tournament(tournament_id: int) -> list[int]:
    tournament = get_tournament_by_id(tournament_id)
    chat_ids = set(get_tournament_manager_chat_ids(tournament_id))
    created_by = tournament["created_by"] if tournament else None
    creator = get_user(created_by) if created_by else None
    if creator and creator["telegram_id"]:
        chat_ids.add(int(creator["telegram_id"]))
    elif created_by:
        maybe_user = get_user_by_id(created_by)
        if maybe_user and maybe_user["telegram_id"]:
            chat_ids.add(int(maybe_user["telegram_id"]))
        elif isinstance(created_by, int):
            chat_ids.add(int(created_by))
    return list(chat_ids)


def _captain_chat_ids(team1_id: int | None, team2_id: int | None) -> list[int]:
    chat_ids: set[int] = set()
    for team_id in (team1_id, team2_id):
        captain_id = _team_captain_id(team_id)
        if not captain_id:
            continue
        user = get_user_by_id(captain_id)
        if user and user["telegram_id"]:
            chat_ids.add(int(user["telegram_id"]))
    return list(chat_ids)


def _all_team_member_chat_ids(team1_id: int | None, team2_id: int | None) -> list[int]:
    chat_ids: set[int] = set()
    for team_id in (team1_id, team2_id):
        if not team_id:
            continue
        for player in get_team_members(team_id):
            telegram_id = player.get("telegram_id") if isinstance(player, dict) else (player["telegram_id"] if "telegram_id" in player.keys() else None)
            if telegram_id:
                chat_ids.add(int(telegram_id))
    return list(chat_ids)


def get_veto_viewer_context(viewer_telegram_id: int, details: dict[str, Any]) -> tuple[bool, int | None]:
    match = details["match"]
    is_manager = can_manage_tournament(viewer_telegram_id, match["tournament_id"])
    user = get_user(viewer_telegram_id)
    if not user:
        return is_manager, None
    if match.get("team1_id") and is_captain(user["id"], match["team1_id"]):
        return is_manager, match["team1_id"]
    if match.get("team2_id") and is_captain(user["id"], match["team2_id"]):
        return is_manager, match["team2_id"]
    return is_manager, None


def format_veto_text(summary: dict[str, Any]) -> str:
    details = summary["details"]
    match = details["match"]
    session = details.get("session")
    maps = details.get("maps", [])
    available = summary["available_rows"]
    banned = summary["banned"]
    picks = summary["picks"]

    lines = [
        "Map pick / ban",
        "",
        f"Турнир: {match.get('tournament_name') or 'Турнир'}",
        f"Раунд: {match.get('round_name') or match.get('round_number') or '-'}",
        f"Матч: {match.get('team1_name') or 'Команда 1'} vs {match.get('team2_name') or 'Команда 2'}",
    ]

    if session:
        lines.append(f"Формат: {str(session.get('match_format') or 'bo3').upper()}")
        lines.append(f"Статус: {get_veto_status_label(session.get('status'))}")
        if session.get("started_at"):
            lines.append(f"Запуск: {'авто' if session.get('started_by_kind') == START_KIND_SYSTEM else 'ручной'}")
    else:
        lines.append("Статус: сессия еще не создана")

    if session and session.get("status") == STATUS_IN_PROGRESS:
        team_name = match.get("team1_name") if session.get("current_team_id") == match.get("team1_id") else match.get("team2_name")
        action_label = "бан" if session.get("current_action_type") == ACTION_BAN else "пик"
        lines.extend(["", f"Сейчас ход: {team_name}", f"Действие: {action_label}"])

    if available:
        lines.extend(["", "Доступные карты: " + ", ".join(row["map_name"] for row in available)])
    if banned:
        lines.extend(["", "Забанено: " + ", ".join(row["map_name"] for row in banned)])
    if picks:
        lines.extend(["", "Пики: " + ", ".join(row["map_name"] for row in picks)])
    if maps:
        lines.append("")
        lines.append("Итоговая серия:")
        for row in maps:
            suffix = ""
            if row["selection_type"] == "decider":
                suffix = " (decider)"
            elif row["selection_type"] == "pick":
                suffix = " (pick)"
            lines.append(f"{row['map_order']}. {row['map_name']}{suffix}")
    return "\n".join(lines)


def build_veto_keyboard_for_user(viewer_telegram_id: int, summary: dict[str, Any]) -> InlineKeyboardMarkup:
    details = summary["details"]
    match = details["match"]
    session = details.get("session")
    is_manager, captain_team_id = get_veto_viewer_context(viewer_telegram_id, details)
    builder = InlineKeyboardBuilder()

    if session and session.get("status") in {STATUS_NOT_READY, STATUS_READY} and is_manager:
        builder.button(text="Запустить пик карт", callback_data=f"veto_admin_start_panel_{match['id']}")

    if session and session.get("status") == STATUS_IN_PROGRESS and captain_team_id and captain_team_id == summary["current_turn_team"]:
        for row in summary["available_rows"]:
            builder.button(text=row["map_name"], callback_data=f"veto_map_{match['id']}_{row['map_key']}")
        builder.adjust(2)

    if session and is_manager:
        if session.get("status") == STATUS_IN_PROGRESS:
            builder.button(text="Отменить пик", callback_data=f"veto_admin_cancel_{match['id']}")
        if session.get("status") in {STATUS_READY, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_IN_PROGRESS}:
            builder.button(text="Сбросить пик", callback_data=f"veto_admin_reset_{match['id']}")

    builder.button(text="Обновить", callback_data=f"veto_open_{match['id']}")
    if is_manager:
        builder.button(text="К матчу", callback_data=f"bracket_match_{match['id']}_{match['tournament_id']}")
    builder.adjust(1)
    return builder.as_markup()


def build_manager_ready_keyboard(bracket_match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Запустить пик карт", callback_data=f"veto_admin_start_notify_{bracket_match_id}")],
        [InlineKeyboardButton(text="Открыть управление", callback_data=f"veto_admin_open_{bracket_match_id}")],
    ])


def build_captain_open_keyboard(bracket_match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть пик карт", callback_data=f"veto_open_{bracket_match_id}")],
    ])


async def _safe_send(bot: Bot, chat_ids: list[int], text: str, reply_markup: InlineKeyboardMarkup | None = None):
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception as exc:
            logger.warning("Не удалось отправить veto-уведомление chat_id=%s: %s", chat_id, exc)


async def _send_and_track_veto_message(bot: Bot, bracket_match_id: int, chat_id: int):
    summary = get_veto_session_summary(bracket_match_id)
    if not summary:
        return
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=format_veto_text(summary),
            reply_markup=build_veto_keyboard_for_user(chat_id, summary),
        )
        store_veto_message_target(bracket_match_id, chat_id, sent.message_id)
    except Exception as exc:
        logger.warning("Не удалось отправить live veto message chat_id=%s: %s", chat_id, exc)


async def refresh_veto_messages(bot: Bot, bracket_match_id: int, exclude_chat_ids: set[int] | None = None):
    details = get_match_veto_details(bracket_match_id)
    session = details.get("session") if details else None
    if not details or not session:
        return
    excluded = exclude_chat_ids or set()
    summary = get_veto_session_summary(bracket_match_id)
    if not summary:
        return
    for row in _fetch_message_targets(session["id"]):
        chat_id = int(row["chat_id"])
        if chat_id in excluded:
            continue
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(row["message_id"]),
                text=format_veto_text(summary),
                reply_markup=build_veto_keyboard_for_user(chat_id, summary),
            )
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if "message is not modified" in message:
                continue
            if "message to edit not found" in message or "message can't be edited" in message:
                _delete_veto_message_target(session["id"], chat_id)
                continue
            raise
        except Exception as exc:
            logger.warning("Не удалось обновить veto message chat_id=%s: %s", chat_id, exc)


def _action_team_name(action: dict[str, Any], match: dict[str, Any]) -> str:
    if action.get("team_id") == match.get("team1_id"):
        return match.get("team1_name") or "Команда 1"
    if action.get("team_id") == match.get("team2_id"):
        return match.get("team2_name") or "Команда 2"
    return "Команда"


def format_completed_veto_message(bracket_match_id: int) -> str | None:
    details = get_match_veto_details(bracket_match_id)
    if not details or not details.get("session") or details["session"].get("status") != STATUS_COMPLETED:
        return None
    match = details["match"]
    actions = details["actions"]
    maps = details["maps"]
    location = (match.get("location") or "не указано").strip()

    pick_action_by_map = {
        action["map_key"]: action
        for action in actions
        if action.get("action_type") == ACTION_PICK and action.get("map_key")
    }
    bans = [action for action in actions if action.get("action_type") == ACTION_BAN and action.get("map_key")]

    lines = [
        "⚔️ Матч",
        "",
        f"🏆 Турнир: {match.get('tournament_name') or 'Турнир'}",
        f"📍 Раунд: {match.get('round_name') or match.get('round_number') or '-'}",
        f"⚔️ {match.get('team1_name') or 'Команда 1'} vs {match.get('team2_name') or 'Команда 2'}",
        "",
        f"📌 Место: {location}",
    ]

    if maps:
        for row in maps:
            if row["selection_type"] == "pick":
                pick_action = pick_action_by_map.get(row["map_key"])
                team_name = _action_team_name(pick_action or {}, match)
                suffix = f"pick - {team_name}"
            else:
                suffix = "decider"
            lines.append(f"{row['map_order']}. {row['map_name']} ({suffix})")

    if bans:
        lines.extend(["", "Баны:"])
        for action in bans:
            lines.append(f"• {action['map_name']} (ban - {_action_team_name(action, match)})")
    return "\n".join(lines)


async def notify_veto_completed(bot: Bot, bracket_match_id: int):
    text = format_completed_veto_message(bracket_match_id)
    details = get_match_veto_details(bracket_match_id)
    if not text or not details:
        return
    match = details["match"]
    chat_ids = set(_all_team_member_chat_ids(match.get("team1_id"), match.get("team2_id")))
    chat_ids.update(_manager_chat_ids_for_tournament(match["tournament_id"]))
    await _safe_send(bot, list(chat_ids), text)


async def _notify_managers_ready(bot: Bot, session: dict[str, Any]):
    chat_ids = _manager_chat_ids_for_tournament(session["tournament_id"])
    if not chat_ids:
        return
    text = (
        "Матч готов к запуску pick/ban.\n\n"
        f"Турнир: {session.get('tournament_name') or 'Турнир'}\n"
        f"Раунд: {session.get('round_name') or session.get('round_number') or '-'}\n"
        f"Матч: {session.get('team1_name') or 'Команда 1'} vs {session.get('team2_name') or 'Команда 2'}\n"
        f"Время: {format_utc_to_msk(session.get('scheduled_at_utc'))} (МСК)\n"
        "Режим запуска: admin_start"
    )
    await _safe_send(bot, chat_ids, text, build_manager_ready_keyboard(session["bracket_match_id"]))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE match_veto_sessions
        SET admin_notified_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (session["id"],))
    conn.commit()
    conn.close()


async def notify_captains_veto_started(bot: Bot, bracket_match_id: int):
    details = get_match_veto_details(bracket_match_id)
    if not details or not details.get("session"):
        return
    session = details["session"]
    chat_ids = _captain_chat_ids(session["team1_id"], session["team2_id"])
    if not chat_ids:
        return
    for chat_id in chat_ids:
        await _send_and_track_veto_message(bot, bracket_match_id, chat_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE match_veto_sessions
        SET captains_notified_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (session["id"],))
    conn.commit()
    conn.close()


async def dispatch_due_veto_sessions(bot: Bot) -> int:
    dispatched = 0
    for session in list_due_sessions_for_dispatch():
        if not _match_due(session):
            continue

        if session["status"] == STATUS_NOT_READY:
            mark_session_ready(session["bracket_match_id"])
            latest = _fetch_session(session["bracket_match_id"])
            if latest:
                session.update(latest)
                session["status"] = latest["status"]

        launch_mode = (session.get("veto_launch_mode") or LAUNCH_ADMIN).strip()
        if (
            launch_mode == LAUNCH_AUTO
            and int(session.get("auto_start_consumed") or 0) == 0
            and session["status"] in {STATUS_NOT_READY, STATUS_READY}
        ):
            ok, _ = start_veto_session(
                session["bracket_match_id"],
                actor_telegram_id=None,
                started_by_kind=START_KIND_SYSTEM,
                start_source=START_SOURCE_AUTO,
            )
            if ok:
                await notify_captains_veto_started(bot, session["bracket_match_id"])
                dispatched += 1
            continue

        latest = _fetch_session(session["bracket_match_id"])
        if (
            launch_mode == LAUNCH_ADMIN
            and latest
            and latest["status"] == STATUS_READY
            and not latest.get("admin_notified_at")
        ):
            merged = dict(session)
            merged.update(latest)
            await _notify_managers_ready(bot, merged)
            dispatched += 1
    return dispatched


def get_veto_status_label(status: str | None) -> str:
    return {
        STATUS_NOT_READY: "не готов",
        STATUS_READY: "готов к запуску",
        STATUS_IN_PROGRESS: "идет пик/бан",
        STATUS_COMPLETED: "завершен",
        STATUS_CANCELLED: "отменен",
    }.get((status or "").strip(), status or "—")


def get_veto_session_summary(bracket_match_id: int) -> dict[str, Any] | None:
    details = get_match_veto_details(bracket_match_id)
    if not details:
        return None

    session = details.get("session")
    actions = details["actions"]

    return {
        "details": details,
        "pool_rows": details["pool"],
        "available_rows": details["available_maps"],
        "banned": [row for row in actions if row["action_type"] == ACTION_BAN],
        "picks": [row for row in actions if row["action_type"] == ACTION_PICK],
        "current_turn_team": session.get("current_team_id") if session else None,
        "current_action": session.get("current_action_type") if session else None,
    }
