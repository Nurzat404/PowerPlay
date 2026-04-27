import json
import os
import secrets
import sqlite3
from database import get_connection
import datetime
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import logging
from utils.rating_rules import ENTITY_TEAM, FORMAT_GENERAL, SCOPE_OVERALL
from utils.rating_service import (
    apply_manual_rating_adjustment,
    get_rating_leaderboard,
    replace_match_team_rating,
    replace_tournament_rating_awards,
)
from utils.referral_service import process_referral_application_approval

logger = logging.getLogger(__name__)

RUSSIAN_MONTHS = {
    'янв.': 1, 'февр.': 2, 'марта': 3, 'апр.': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'авг.': 8, 'сент.': 9, 'окт.': 10, 'нояб.': 11, 'дек.': 12
}

MSK_TZ = ZoneInfo("Europe/Moscow")
UTC_STORAGE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_user(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    user = cur.fetchone()
    conn.close()
    return user


def is_admin(telegram_id):
    user = get_user(telegram_id)
    return user and user['role'] == 'admin'


def get_all_admin_users():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM users
        WHERE role='admin' AND COALESCE(is_banned, 0)=0
        ORDER BY first_name, username, id
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_admin_chat_ids() -> list[int]:
    chat_ids: list[int] = []
    for user in get_all_admin_users():
        telegram_id = user["telegram_id"] if "telegram_id" in user.keys() else None
        if telegram_id:
            chat_ids.append(int(telegram_id))
    return chat_ids


def get_admin_tournament_notifications_enabled(user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT tournament_notifications_enabled
        FROM admin_notification_preferences
        WHERE user_id=?
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return True
    return int(row["tournament_notifications_enabled"] or 0) == 1


def set_admin_tournament_notifications_enabled(user_id: int, enabled: bool) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO admin_notification_preferences (
            user_id, tournament_notifications_enabled, created_at, updated_at
        )
        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            tournament_notifications_enabled=excluded.tournament_notifications_enabled,
            updated_at=CURRENT_TIMESTAMP
    """, (int(user_id), 1 if enabled else 0))
    conn.commit()
    conn.close()


def get_tournament_notification_override(tournament_id: int, user_id: int) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT notifications_mode
        FROM tournament_notification_overrides
        WHERE tournament_id=? AND user_id=?
    """, (int(tournament_id), int(user_id)))
    row = cur.fetchone()
    conn.close()
    return (row["notifications_mode"] if row else "inherit") or "inherit"


def set_tournament_notification_override(tournament_id: int, user_id: int, mode: str) -> None:
    normalized_mode = (mode or "inherit").strip().lower()
    if normalized_mode not in {"inherit", "on", "off"}:
        normalized_mode = "inherit"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tournament_notification_overrides (
            tournament_id, user_id, notifications_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(tournament_id, user_id) DO UPDATE SET
            notifications_mode=excluded.notifications_mode,
            updated_at=CURRENT_TIMESTAMP
    """, (int(tournament_id), int(user_id), normalized_mode))
    conn.commit()
    conn.close()


def is_tournament_notification_enabled_for_user(user_id: int, tournament_id: int) -> bool:
    override = get_tournament_notification_override(tournament_id, user_id)
    if override == "on":
        return True
    if override == "off":
        return False
    return get_admin_tournament_notifications_enabled(user_id)


def get_tournament_admin_notification_chat_ids(tournament_id: int) -> list[int]:
    chat_ids: set[int] = set()
    users_by_id: dict[int, sqlite3.Row] = {}

    for user in get_all_admin_users():
        users_by_id[int(user["id"])] = user

    tournament = get_tournament_by_id(tournament_id)
    created_by = int(tournament["created_by"]) if tournament and tournament["created_by"] else None
    if created_by:
        creator = get_user_by_id(created_by)
        if creator:
            users_by_id[int(creator["id"])] = creator

    for user in get_tournament_manager_users(tournament_id):
        users_by_id[int(user["id"])] = user

    for user_id, user in users_by_id.items():
        telegram_id = user["telegram_id"] if "telegram_id" in user.keys() else None
        if not telegram_id:
            continue
        if is_tournament_notification_enabled_for_user(user_id, tournament_id):
            chat_ids.add(int(telegram_id))
    return list(chat_ids)


def get_or_create_user(telegram_id, first_name, last_name, username):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    user = cur.fetchone()
    if not user:
        cur.execute("""
            INSERT INTO users (telegram_id, first_name, last_name, username, role)
            VALUES (?, ?, ?, ?, 'player')
        """, (telegram_id, first_name, last_name, username))
        conn.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        user = cur.fetchone()
    conn.close()
    return user


def update_user(telegram_id, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    set_clause = ", ".join([f"{k}=?" for k in kwargs])
    values = list(kwargs.values()) + [telegram_id]
    cur.execute(f"UPDATE users SET {set_clause} WHERE telegram_id=?", values)
    conn.commit()
    conn.close()


def update_user_by_id(user_id: int, **kwargs):
    if not kwargs:
        return
    conn = get_connection()
    cur = conn.cursor()
    set_clause = ", ".join([f"{k}=?" for k in kwargs])
    values = list(kwargs.values()) + [user_id]
    cur.execute(f"UPDATE users SET {set_clause} WHERE id=?", values)
    conn.commit()
    conn.close()


def get_user_teams(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.* FROM teams t
        JOIN team_members tm ON t.id = tm.team_id
        WHERE tm.user_id=?
    """, (user_id,))
    teams = cur.fetchall()
    conn.close()
    return teams


def is_captain(user_id, team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT captain_id FROM teams WHERE id=?", (team_id,))
    team = cur.fetchone()
    conn.close()
    return team and team['captain_id'] == user_id


def get_team_by_id(team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM teams WHERE id=?", (team_id,))
    team = cur.fetchone()
    conn.close()
    return team


def get_team_members(team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.* FROM users u
        JOIN team_members tm ON u.id = tm.user_id
        WHERE tm.team_id=?
    """, (team_id,))
    members = cur.fetchall()
    conn.close()
    return members


def update_team_fields(team_id: int, **kwargs):
    if not kwargs:
        return
    conn = get_connection()
    cur = conn.cursor()
    set_clause = ", ".join([f"{k}=?" for k in kwargs])
    values = list(kwargs.values()) + [team_id]
    cur.execute(f"UPDATE teams SET {set_clause} WHERE id=?", values)
    conn.commit()
    conn.close()


def set_team_city(team_id: int, city: str | None):
    normalized = (city or "").strip() or None
    update_team_fields(team_id, city=normalized)


def rename_team(team_id: int, new_name: str):
    update_team_fields(team_id, name=(new_name or "").strip())


def is_team_member_blocked(team_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM team_member_blocks WHERE team_id=? AND user_id=? LIMIT 1",
        (team_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row)


def get_team_member_blocks(team_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT b.team_id, b.user_id, b.blocked_by, b.reason, b.created_at,
               u.first_name, u.username
        FROM team_member_blocks b
        JOIN users u ON u.id = b.user_id
        WHERE b.team_id=?
        ORDER BY b.created_at DESC, b.user_id DESC
        """,
        (team_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def block_team_member(team_id: int, user_id: int, blocked_by: int | None = None, reason: str | None = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO team_member_blocks (team_id, user_id, blocked_by, reason, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(team_id, user_id) DO UPDATE SET
            blocked_by=excluded.blocked_by,
            reason=excluded.reason,
            created_at=CURRENT_TIMESTAMP
        """,
        (team_id, user_id, blocked_by, (reason or "").strip() or None),
    )
    conn.commit()
    conn.close()


def unblock_team_member(team_id: int, user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM team_member_blocks WHERE team_id=? AND user_id=?", (team_id, user_id))
    conn.commit()
    conn.close()


def remove_team_member_admin(team_id: int, user_id: int, replacement_captain_user_id: int | None = None):
    team = get_team_by_id(team_id)
    if not team:
        return {"ok": False, "reason": "team_not_found"}
    members = list(get_team_members(team_id))
    member_ids = [int(member["id"]) for member in members]
    if int(user_id) not in member_ids:
        return {"ok": False, "reason": "not_member"}
    if int(team["captain_id"] or 0) == int(user_id):
        remaining_ids = [member_id for member_id in member_ids if member_id != int(user_id)]
        if not remaining_ids:
            return {"ok": False, "reason": "last_captain"}
        if not replacement_captain_user_id or int(replacement_captain_user_id) not in remaining_ids:
            return {"ok": False, "reason": "replacement_captain_required", "choices": remaining_ids}
        update_team_fields(team_id, captain_id=int(replacement_captain_user_id))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id))
    cur.execute("DELETE FROM team_invites WHERE team_id=? AND user_id=?", (team_id, user_id))
    conn.commit()
    conn.close()
    return {"ok": True}


def add_team_member_admin(team_id: int, user_id: int, *, bypass_block: bool = False):
    team = get_team_by_id(team_id)
    if not team:
        return {"ok": False, "reason": "team_not_found"}
    if is_team_member(user_id, team_id):
        return {"ok": False, "reason": "already_member"}
    if is_team_member_blocked(team_id, user_id) and not bypass_block:
        return {"ok": False, "reason": "blocked"}
    current_count = get_team_members_count(team_id)
    max_members = get_team_max_members(team_id)
    if current_count >= max_members:
        return {"ok": False, "reason": "team_full"}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO team_members (team_id, user_id) VALUES (?, ?)", (team_id, user_id))
    cur.execute(
        """
        INSERT INTO team_invites (team_id, user_id, status, type)
        VALUES (?, ?, 'accepted', 'request')
        ON CONFLICT(team_id, user_id) DO UPDATE SET
            status='accepted',
            type='request'
        """,
        (team_id, user_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


def _tournament_team_roster_exists(tournament_id: int, team_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM tournament_team_rosters
        WHERE tournament_id=? AND team_id=?
        LIMIT 1
    """, (tournament_id, team_id))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def _get_tournament_roster_active_member_ids(conn: sqlite3.Connection, tournament_id: int, team_id: int) -> list[int]:
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id
        FROM tournament_team_rosters
        WHERE tournament_id=? AND team_id=? AND status='active'
        ORDER BY created_at, user_id
    """, (tournament_id, team_id))
    return [int(row["user_id"]) for row in cur.fetchall()]


def ensure_tournament_team_roster(tournament_id: int, team_id: int) -> bool:
    app = get_team_application(tournament_id, team_id)
    if not app or app["status"] != "approved":
        return False

    team = get_team_by_id(team_id)
    if not team:
        return False

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("""
            SELECT 1
            FROM tournament_team_rosters
            WHERE tournament_id=? AND team_id=?
            LIMIT 1
        """, (tournament_id, team_id))
        has_rows = bool(cur.fetchone())

        if not has_rows:
            members = list(get_team_members(team_id))
            for member in members:
                cur.execute("""
                    INSERT OR IGNORE INTO tournament_team_rosters (
                        tournament_id, team_id, user_id, status, added_by, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (tournament_id, team_id, member["id"]))

        active_ids = _get_tournament_roster_active_member_ids(conn, tournament_id, team_id)
        if active_ids:
            preferred_captain_id = int(team["captain_id"]) if team["captain_id"] in active_ids else active_ids[0]
            cur.execute("""
                INSERT INTO tournament_team_captains (tournament_id, team_id, user_id, assigned_by, created_at, updated_at)
                VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(tournament_id, team_id) DO NOTHING
            """, (tournament_id, team_id, preferred_captain_id))

        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_tournament_team_members(tournament_id: int, team_id: int):
    ensure_tournament_team_roster(tournament_id, team_id)
    if not _tournament_team_roster_exists(tournament_id, team_id):
        return get_team_members(team_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.*
        FROM tournament_team_rosters r
        JOIN users u ON u.id = r.user_id
        WHERE r.tournament_id=? AND r.team_id=? AND r.status='active'
        ORDER BY COALESCE(u.first_name, ''), COALESCE(u.username, ''), u.id
    """, (tournament_id, team_id))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_effective_tournament_captain_id(tournament_id: int, team_id: int) -> int | None:
    ensure_tournament_team_roster(tournament_id, team_id)
    team = get_team_by_id(team_id)
    if not team:
        return None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id
        FROM tournament_team_rosters
        WHERE tournament_id=? AND team_id=? AND status='active'
        ORDER BY created_at, user_id
    """, (tournament_id, team_id))
    active_ids = [int(row["user_id"]) for row in cur.fetchall()]

    cur.execute("""
        SELECT user_id
        FROM tournament_team_captains
        WHERE tournament_id=? AND team_id=?
    """, (tournament_id, team_id))
    captain_row = cur.fetchone()
    conn.close()

    if captain_row and int(captain_row["user_id"]) in active_ids:
        return int(captain_row["user_id"])
    if int(team["captain_id"]) in active_ids:
        return int(team["captain_id"])
    if active_ids:
        return active_ids[0]
    return int(team["captain_id"]) if team["captain_id"] else None


def is_tournament_captain(telegram_id: int, tournament_id: int, team_id: int) -> bool:
    user = get_user(telegram_id)
    if not user:
        return False
    return get_effective_tournament_captain_id(tournament_id, team_id) == user["id"]


def can_manage_tournament_team_roster(telegram_id: int, tournament_id: int, team_id: int) -> bool:
    return can_manage_tournament(telegram_id, tournament_id) or is_tournament_captain(telegram_id, tournament_id, team_id)


def get_user_tournament_captain_teams(tournament_id: int, telegram_id: int):
    user = get_user(telegram_id)
    if not user:
        return []
    teams = []
    for team in get_tournament_teams(tournament_id, status="approved"):
        team_id = team["id"] if isinstance(team, dict) else team["id"]
        if is_tournament_captain(telegram_id, tournament_id, team_id):
            teams.append(team)
    return teams


def _get_tournament_required_team_size(tournament_id: int) -> int:
    tournament = get_tournament_by_id(tournament_id)
    return int(tournament["required_team_size"] or 0) if tournament else 0


def _find_user_active_tournament_team(tournament_id: int, user_id: int, exclude_team_id: int | None = None) -> int | None:
    for team in get_tournament_teams(tournament_id, status="approved"):
        team_id = team["id"] if isinstance(team, dict) else team["id"]
        if exclude_team_id and team_id == exclude_team_id:
            continue
        members = get_tournament_team_members(tournament_id, team_id)
        member_ids = {int(member["id"]) for member in members}
        if user_id in member_ids:
            return team_id
    return None


def _validate_tournament_replacement(tournament_id: int, team_id: int, old_user_id: int, new_user_id: int):
    ensure_tournament_team_roster(tournament_id, team_id)
    user = get_user_by_id(new_user_id)
    if not user:
        return False, "Новый игрок не найден."
    if old_user_id == new_user_id:
        return False, "Нужно выбрать другого игрока для замены."
    if _find_user_active_tournament_team(tournament_id, new_user_id, exclude_team_id=team_id):
        return False, "Этот пользователь уже состоит в другой команде этого турнира."

    members = get_tournament_team_members(tournament_id, team_id)
    member_ids = {int(member["id"]) for member in members}
    if old_user_id not in member_ids:
        return False, "Игрок для замены не найден в активном составе."
    if new_user_id in member_ids:
        return False, "Этот пользователь уже находится в составе команды на турнире."
    return True, None


def assign_tournament_team_captain(tournament_id: int, team_id: int, new_captain_user_id: int, actor_user_id: int | None = None):
    ensure_tournament_team_roster(tournament_id, team_id)
    members = get_tournament_team_members(tournament_id, team_id)
    member_ids = {int(member["id"]) for member in members}
    if new_captain_user_id not in member_ids:
        return False, "Новый турнирный капитан должен входить в активный состав команды."

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tournament_team_captains (tournament_id, team_id, user_id, assigned_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(tournament_id, team_id) DO UPDATE SET
            user_id=excluded.user_id,
            assigned_by=excluded.assigned_by,
            updated_at=CURRENT_TIMESTAMP
    """, (tournament_id, team_id, new_captain_user_id, actor_user_id))
    conn.commit()
    conn.close()
    return True, None


def create_tournament_roster_change_request(
    tournament_id: int,
    team_id: int,
    old_user_id: int,
    new_user_id: int,
    actor_user_id: int | None = None,
):
    ok, error = _validate_tournament_replacement(tournament_id, team_id, old_user_id, new_user_id)
    if not ok:
        return None, error

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tournament_roster_change_requests (
            tournament_id, team_id, old_user_id, new_user_id, requested_by_user_id, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
    """, (tournament_id, team_id, old_user_id, new_user_id, actor_user_id))
    request_id = cur.lastrowid
    conn.commit()
    conn.close()
    return request_id, None


def get_tournament_roster_change_request(request_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.*,
               t.name AS tournament_name,
               tm.name AS team_name,
               old_u.first_name AS old_first_name,
               old_u.username AS old_username,
               new_u.first_name AS new_first_name,
               new_u.username AS new_username
        FROM tournament_roster_change_requests r
        JOIN tournaments t ON t.id = r.tournament_id
        JOIN teams tm ON tm.id = r.team_id
        LEFT JOIN users old_u ON old_u.id = r.old_user_id
        LEFT JOIN users new_u ON new_u.id = r.new_user_id
        WHERE r.id=?
    """, (request_id,))
    row = cur.fetchone()
    conn.close()
    return row


def update_tournament_roster_change_request_status(request_id: int, status: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tournament_roster_change_requests
        SET status=?, responded_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='pending'
    """, (status, request_id))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def accept_tournament_roster_change_request(request_id: int, responder_user_id: int):
    request_row = get_tournament_roster_change_request(request_id)
    if not request_row:
        return False, "Запрос не найден.", None
    if request_row["status"] != "pending":
        return False, "Запрос уже обработан.", request_row
    if int(request_row["new_user_id"]) != responder_user_id:
        return False, "Подтвердить замену может только приглашенный игрок.", request_row

    ok, error = replace_tournament_team_player(
        int(request_row["tournament_id"]),
        int(request_row["team_id"]),
        int(request_row["old_user_id"]),
        int(request_row["new_user_id"]),
        int(request_row["requested_by_user_id"]) if request_row["requested_by_user_id"] else None,
    )
    if not ok:
        update_tournament_roster_change_request_status(request_id, "cancelled")
        return False, error or "Не удалось выполнить замену.", get_tournament_roster_change_request(request_id)

    update_tournament_roster_change_request_status(request_id, "accepted")
    return True, None, get_tournament_roster_change_request(request_id)


def decline_tournament_roster_change_request(request_id: int, responder_user_id: int):
    request_row = get_tournament_roster_change_request(request_id)
    if not request_row:
        return False, "Запрос не найден.", None
    if request_row["status"] != "pending":
        return False, "Запрос уже обработан.", request_row
    if int(request_row["new_user_id"]) != responder_user_id:
        return False, "Отклонить замену может только приглашенный игрок.", request_row
    update_tournament_roster_change_request_status(request_id, "declined")
    return True, None, get_tournament_roster_change_request(request_id)


def add_tournament_team_member(tournament_id: int, team_id: int, user_id: int, actor_user_id: int | None = None):
    ensure_tournament_team_roster(tournament_id, team_id)
    user = get_user_by_id(user_id)
    if not user:
        return False, "Пользователь не найден."
    if _find_user_active_tournament_team(tournament_id, user_id, exclude_team_id=team_id):
        return False, "Этот пользователь уже состоит в другой команде этого турнира."

    members = get_tournament_team_members(tournament_id, team_id)
    member_ids = {int(member["id"]) for member in members}
    if user_id in member_ids:
        return False, "Этот пользователь уже находится в составе команды на турнире."

    required_size = _get_tournament_required_team_size(tournament_id)
    if required_size > 0 and len(member_ids) >= required_size:
        return False, "Состав уже заполнен до лимита турнира. Используйте замену игрока."

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tournament_team_rosters (
            tournament_id, team_id, user_id, status, added_by, removed_by, created_at, updated_at, removed_at
        )
        VALUES (?, ?, ?, 'active', ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
        ON CONFLICT(tournament_id, team_id, user_id) DO UPDATE SET
            status='active',
            added_by=excluded.added_by,
            removed_by=NULL,
            removed_at=NULL,
            updated_at=CURRENT_TIMESTAMP
    """, (tournament_id, team_id, user_id, actor_user_id))
    conn.commit()
    conn.close()
    return True, None


def remove_tournament_team_member(tournament_id: int, team_id: int, user_id: int, actor_user_id: int | None = None, replacement_captain_user_id: int | None = None):
    ensure_tournament_team_roster(tournament_id, team_id)
    members = get_tournament_team_members(tournament_id, team_id)
    member_ids = [int(member["id"]) for member in members]
    if user_id not in member_ids:
        return False, "Игрок не найден в активном составе турнира."
    if len(member_ids) <= 1:
        return False, "Нельзя убрать последнего активного участника команды."

    current_captain_id = get_effective_tournament_captain_id(tournament_id, team_id)
    if current_captain_id == user_id:
        if not replacement_captain_user_id or replacement_captain_user_id == user_id:
            return False, "Сначала назначьте нового турнирного капитана."
        if replacement_captain_user_id not in member_ids:
            return False, "Новый турнирный капитан должен быть в активном составе команды."

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tournament_team_rosters
        SET status='inactive',
            removed_by=?,
            removed_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        WHERE tournament_id=? AND team_id=? AND user_id=? AND status='active'
    """, (actor_user_id, tournament_id, team_id, user_id))
    if current_captain_id == user_id and replacement_captain_user_id:
        cur.execute("""
            INSERT INTO tournament_team_captains (tournament_id, team_id, user_id, assigned_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(tournament_id, team_id) DO UPDATE SET
                user_id=excluded.user_id,
                assigned_by=excluded.assigned_by,
                updated_at=CURRENT_TIMESTAMP
        """, (tournament_id, team_id, replacement_captain_user_id, actor_user_id))
    conn.commit()
    conn.close()
    return True, None


def replace_tournament_team_player(tournament_id: int, team_id: int, old_user_id: int, new_user_id: int, actor_user_id: int | None = None):
    ok, error = _validate_tournament_replacement(tournament_id, team_id, old_user_id, new_user_id)
    if not ok:
        return False, error

    current_captain_id = get_effective_tournament_captain_id(tournament_id, team_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tournament_team_rosters
        SET status='inactive',
            removed_by=?,
            removed_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        WHERE tournament_id=? AND team_id=? AND user_id=? AND status='active'
    """, (actor_user_id, tournament_id, team_id, old_user_id))
    cur.execute("""
        INSERT INTO tournament_team_rosters (
            tournament_id, team_id, user_id, status, added_by, removed_by, created_at, updated_at, removed_at
        )
        VALUES (?, ?, ?, 'active', ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
        ON CONFLICT(tournament_id, team_id, user_id) DO UPDATE SET
            status='active',
            added_by=excluded.added_by,
            removed_by=NULL,
            removed_at=NULL,
            updated_at=CURRENT_TIMESTAMP
    """, (tournament_id, team_id, new_user_id, actor_user_id))
    if current_captain_id == old_user_id:
        cur.execute("""
            INSERT INTO tournament_team_captains (tournament_id, team_id, user_id, assigned_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(tournament_id, team_id) DO UPDATE SET
                user_id=excluded.user_id,
                assigned_by=excluded.assigned_by,
                updated_at=CURRENT_TIMESTAMP
        """, (tournament_id, team_id, new_user_id, actor_user_id))
    conn.commit()
    conn.close()
    return True, None


def get_tournaments_by_sport(sport):
    """Возвращает турниры по виду спорта со всеми статусами."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM tournaments WHERE sport=? ORDER BY created_at DESC", (sport,))
    tournaments = cur.fetchall()
    conn.close()
    return tournaments


def get_tournament_by_id(tournament_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,))
    tournament = cur.fetchone()
    conn.close()
    return tournament


def format_tournament_date_range(
    start_date: str | None,
    end_date: str | None,
    *,
    empty_fallback: str,
) -> str:
    start_value = (start_date or "").strip()
    end_value = (end_date or "").strip()
    if start_value and end_value:
        return f"{start_value} - {end_value}"
    if start_value:
        return f"с {start_value}"
    if end_value:
        return f"до {end_value}"
    return empty_fallback


def format_tournament_registration_period(tournament) -> str:
    registration_start = tournament["registration_start_date"] if tournament and "registration_start_date" in tournament.keys() else None
    registration_end = tournament["registration_end_date"] if tournament and "registration_end_date" in tournament.keys() else None
    return format_tournament_date_range(
        registration_start,
        registration_end,
        empty_fallback="не указаны",
    )


def format_tournament_event_period(tournament) -> str:
    start_date = tournament["start_date"] if tournament and "start_date" in tournament.keys() else None
    end_date = tournament["end_date"] if tournament and "end_date" in tournament.keys() else None
    return format_tournament_date_range(
        start_date,
        end_date,
        empty_fallback="будут объявлены позже",
    )


def build_tournament_date_lines(tournament) -> list[str]:
    if not tournament:
        return []
    status = (tournament["status"] or "").strip().lower() if "status" in tournament.keys() else ""
    lines: list[str] = []
    if status == "registration":
        lines.append(f"Регистрация: {format_tournament_registration_period(tournament)}")
    lines.append(f"Проведение: {format_tournament_event_period(tournament)}")
    return lines


def list_tournaments_due_for_registration_deadline_notice():
    today = datetime.now(MSK_TZ).date()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM tournaments
        WHERE status='registration'
          AND registration_end_date IS NOT NULL
          AND TRIM(registration_end_date) <> ''
          AND registration_deadline_notified_at IS NULL
        ORDER BY id
    """)
    rows = cur.fetchall()
    conn.close()

    due_rows = []
    for row in rows:
        parsed = parse_russian_date(row["registration_end_date"])
        if not parsed:
            continue
        day, month = parsed
        if (month, day) <= (today.month, today.day):
            due_rows.append(row)
    return due_rows


def mark_tournament_registration_deadline_notified(tournament_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tournaments
        SET registration_deadline_notified_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (int(tournament_id),))
    conn.commit()
    conn.close()


def _resolve_internal_user_id(candidate_id):
    if candidate_id is None:
        return None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id=?", (candidate_id,))
    row = cur.fetchone()
    if row:
        conn.close()
        return row["id"]

    cur.execute("SELECT id FROM users WHERE telegram_id=?", (candidate_id,))
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None


def is_tournament_creator_or_global_admin(telegram_id, tournament_id):
    if is_admin(telegram_id):
        return True

    tournament = get_tournament_by_id(tournament_id)
    user = get_user(telegram_id)
    if not tournament or not user:
        return False

    created_by = tournament["created_by"]
    return created_by in (telegram_id, user["id"])


def get_tournament_manager_users(tournament_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.*
        FROM tournament_managers tm
        JOIN users u ON u.id = tm.user_id
        WHERE tm.tournament_id=?
        ORDER BY u.first_name, u.username, u.id
    """, (tournament_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_tournament_manager_chat_ids(tournament_id):
    chat_ids: list[int] = []
    for user in get_tournament_manager_users(tournament_id):
        if user["telegram_id"]:
            chat_ids.append(int(user["telegram_id"]))
    return chat_ids


def add_tournament_manager(tournament_id: int, user_id: int, assigned_by: int | None = None) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO tournament_managers (tournament_id, user_id, assigned_by)
        VALUES (?, ?, ?)
    """, (tournament_id, user_id, assigned_by))
    conn.commit()
    added = cur.rowcount > 0
    conn.close()
    return added


def remove_tournament_manager(tournament_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM tournament_managers
        WHERE tournament_id=? AND user_id=?
    """, (tournament_id, user_id))
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


def can_manage_tournament(telegram_id, tournament_id):
    if is_tournament_creator_or_global_admin(telegram_id, tournament_id):
        return True

    user = get_user(telegram_id)
    if not user:
        return False

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM tournament_managers
        WHERE tournament_id=? AND user_id=?
        LIMIT 1
    """, (tournament_id, user["id"]))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def can_manage_bracket_match(telegram_id, match_id):
    match = get_bracket_match_by_id(match_id)
    if not match:
        return False
    return can_manage_tournament(telegram_id, match["tournament_id"])


def get_tournament_map_pool(tournament_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT tournament_id, map_key, map_name, sort_order
        FROM tournament_map_pool
        WHERE tournament_id=?
        ORDER BY sort_order, map_name
    """, (tournament_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def replace_tournament_map_pool(tournament_id: int, maps: list[tuple[str, str]]):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tournament_map_pool WHERE tournament_id=?", (tournament_id,))
    for idx, (map_key, map_name) in enumerate(maps, 1):
        cur.execute("""
            INSERT INTO tournament_map_pool (tournament_id, map_key, map_name, sort_order)
            VALUES (?, ?, ?, ?)
        """, (tournament_id, map_key, map_name, idx))
    conn.commit()
    conn.close()


def _normalize_match_format_value(match_format: str | None) -> str:
    value = (match_format or "bo3").strip().lower()
    return value if value in {"bo1", "bo3", "bo5"} else "bo3"


def _calculate_total_rounds_for_bracket_size(num_teams: int | None) -> int:
    try:
        teams = int(num_teams or 0)
    except (TypeError, ValueError):
        teams = 0
    if teams <= 1:
        return 1
    bracket_size = 1
    rounds = 0
    while bracket_size < teams:
        bracket_size *= 2
        rounds += 1
    return max(rounds, 1)


def expand_stage_formats_to_round_rules(
    total_rounds: int,
    early_round_format: str,
    semifinal_format: str,
    final_format: str,
) -> list[tuple[int, str]]:
    total = max(int(total_rounds or 1), 1)
    early = _normalize_match_format_value(early_round_format)
    semifinal = _normalize_match_format_value(semifinal_format)
    final = _normalize_match_format_value(final_format)

    rules: list[tuple[int, str]] = []
    if total == 1:
        return [(1, final)]
    if total == 2:
        return [(1, semifinal), (2, final)]

    for round_number in range(1, total - 1):
        rules.append((round_number, early))
    rules.append((total - 1, semifinal))
    rules.append((total, final))
    return rules


def get_tournament_match_format_rules(tournament_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT tournament_id, round_number, match_format, created_at, updated_at
        FROM tournament_match_format_rules
        WHERE tournament_id=?
        ORDER BY round_number
    """, (tournament_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def replace_tournament_match_format_rules(tournament_id: int, rules: list[tuple[int, str]]):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tournament_match_format_rules WHERE tournament_id=?", (tournament_id,))
    for round_number, match_format in rules:
        cur.execute("""
            INSERT INTO tournament_match_format_rules (
                tournament_id, round_number, match_format, created_at, updated_at
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            tournament_id,
            int(round_number),
            _normalize_match_format_value(match_format),
        ))
    conn.commit()
    conn.close()


def get_tournament_main_round_count(tournament_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(round_number)
        FROM tournament_brackets
        WHERE tournament_id=? AND COALESCE(is_third_place, 0)=0
    """, (tournament_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return int(row[0])

    rules = list(get_tournament_match_format_rules(tournament_id))
    if rules:
        return max(int(row["round_number"]) for row in rules)

    tournament = get_tournament_by_id(tournament_id)
    return _calculate_total_rounds_for_bracket_size(tournament["max_teams"] if tournament else 0)


def get_tournament_stage_formats(tournament_id: int) -> dict:
    tournament = get_tournament_by_id(tournament_id)
    fallback = _normalize_match_format_value(tournament["match_format"] if tournament else "bo3")
    rules = list(get_tournament_match_format_rules(tournament_id))
    if not rules:
        return {
            "early_round_format": fallback,
            "semifinal_format": fallback,
            "final_format": fallback,
            "rules_total_rounds": 0,
        }

    total_rounds = max(int(row["round_number"]) for row in rules)
    final_format = _normalize_match_format_value(next(
        (row["match_format"] for row in reversed(rules) if int(row["round_number"]) == total_rounds),
        fallback,
    ))
    semifinal_round = total_rounds - 1 if total_rounds > 1 else total_rounds
    semifinal_format = _normalize_match_format_value(next(
        (row["match_format"] for row in reversed(rules) if int(row["round_number"]) == semifinal_round),
        final_format,
    ))
    early_format = _normalize_match_format_value(next(
        (row["match_format"] for row in rules if int(row["round_number"]) < semifinal_round),
        semifinal_format if total_rounds > 1 else final_format,
    ))
    return {
        "early_round_format": early_format,
        "semifinal_format": semifinal_format,
        "final_format": final_format,
        "rules_total_rounds": total_rounds,
    }


def sync_tournament_match_format_rules(tournament_id: int, total_rounds: int | None = None):
    profile = get_tournament_stage_formats(tournament_id)
    target_total_rounds = int(total_rounds or get_tournament_main_round_count(tournament_id) or 1)
    rules = expand_stage_formats_to_round_rules(
        target_total_rounds,
        profile["early_round_format"],
        profile["semifinal_format"],
        profile["final_format"],
    )
    replace_tournament_match_format_rules(tournament_id, rules)
    return rules


def resolve_tournament_round_format(tournament_id: int, round_number: int, total_main_rounds: int | None = None) -> str:
    tournament = get_tournament_by_id(tournament_id)
    fallback = _normalize_match_format_value(tournament["match_format"] if tournament else "bo3")
    rules = {
        int(row["round_number"]): _normalize_match_format_value(row["match_format"])
        for row in get_tournament_match_format_rules(tournament_id)
    }
    if not rules:
        return fallback

    target_round = int(round_number or 1)
    return rules.get(target_round, fallback)


def resolve_bracket_match_format(match_id: int) -> str:
    match = get_bracket_match_by_id(match_id)
    if not match:
        return "bo3"
    total_main_rounds = get_tournament_main_round_count(match["tournament_id"])
    target_round = int(match["round_number"] or 1)
    if int(match["is_third_place"] or 0) == 1:
        target_round = max(total_main_rounds - 1, 1)
    return resolve_tournament_round_format(match["tournament_id"], target_round, total_main_rounds)


def add_tournament_application(tournament_id, team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tournament_applications (tournament_id, team_id, status, applied_at, updated_at)
        VALUES (?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (tournament_id, team_id))
    conn.commit()
    conn.close()


def get_tournament_member_application_conflicts(tournament_id, team_id, statuses=None):
    """
    Возвращает список конфликтов по участникам команды в рамках турнира.

    Конфликтом считается ситуация, когда участник команды уже состоит в другой команде
    этого же турнира с одной из указанных заявок.
    """
    normalized_statuses = tuple(statuses or ("pending", "approved"))
    if not normalized_statuses:
        return []

    placeholders = ",".join(["?"] * len(normalized_statuses))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            u.id AS user_id,
            u.first_name,
            u.username,
            a.team_id AS conflict_team_id,
            t.name AS conflict_team_name,
            a.status AS conflict_status
        FROM team_members tm_current
        JOIN users u ON u.id = tm_current.user_id
        JOIN team_members tm_other ON tm_other.user_id = tm_current.user_id
        JOIN tournament_applications a ON a.team_id = tm_other.team_id
        JOIN teams t ON t.id = a.team_id
        WHERE tm_current.team_id=?
          AND tm_other.team_id<>?
          AND a.tournament_id=?
          AND a.status IN ({placeholders})
        ORDER BY u.first_name, t.name, a.status
    """, (team_id, team_id, tournament_id, *normalized_statuses))
    rows = cur.fetchall()
    conn.close()

    conflicts = []
    seen = set()
    for row in rows:
        key = (row["user_id"], row["conflict_team_id"], row["conflict_status"])
        if key in seen:
            continue
        seen.add(key)
        conflicts.append({
            "user_id": row["user_id"],
            "first_name": row["first_name"],
            "username": row["username"],
            "conflict_team_id": row["conflict_team_id"],
            "conflict_team_name": row["conflict_team_name"],
            "conflict_status": row["conflict_status"],
        })
    return conflicts


def get_pending_applications():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.tournament_id, a.team_id, a.status,
               t.name as team_name, tm.name as tournament_name
        FROM tournament_applications a
        JOIN teams t ON a.team_id = t.id
        JOIN tournaments tm ON a.tournament_id = tm.id
        WHERE a.status='pending'
    """)
    apps = cur.fetchall()
    conn.close()
    return apps


def reject_application(app_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tournament_applications SET status='rejected', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (app_id,)
    )
    conn.commit()
    conn.close()


def approve_application(app_id):
    """
    Пытается одобрить заявку атомарно.
    Возвращает dict:
      {"ok": True, "tournament_id": int}
      {"ok": False, "reason": "...", ...}
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Берем write-lock до проверок, чтобы исключить гонки между двумя approve.
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("""
            SELECT
                a.id,
                a.tournament_id,
                a.status AS app_status,
                t.max_teams,
                t.status AS tournament_status
            FROM tournament_applications a
            JOIN tournaments t ON t.id = a.tournament_id
            WHERE a.id=?
        """, (app_id,))
        row = cur.fetchone()

        if not row:
            conn.rollback()
            return {"ok": False, "reason": "not_found"}

        tournament_id = row['tournament_id']
        if row['app_status'] != 'pending':
            conn.rollback()
            return {"ok": False, "reason": "already_processed", "tournament_id": tournament_id}

        if row['tournament_status'] != 'registration':
            conn.rollback()
            return {
                "ok": False,
                "reason": "not_registration",
                "tournament_id": tournament_id,
                "tournament_status": row['tournament_status']
            }

        max_teams = row['max_teams']
        if max_teams is None or max_teams <= 0:
            conn.rollback()
            return {
                "ok": False,
                "reason": "limit_reached",
                "tournament_id": tournament_id,
                "approved": 0,
                "max_teams": max_teams or 0
            }

        cur.execute(
            "SELECT COUNT(*) FROM tournament_applications WHERE tournament_id=? AND status='approved'",
            (tournament_id,)
        )
        approved_count = cur.fetchone()[0]
        if approved_count >= max_teams:
            conn.rollback()
            return {
                "ok": False,
                "reason": "limit_reached",
                "tournament_id": tournament_id,
                "approved": approved_count,
                "max_teams": max_teams
            }

        cur.execute(
            "SELECT team_id FROM tournament_applications WHERE id=?",
            (app_id,)
        )
        team_row = cur.fetchone()
        if not team_row:
            conn.rollback()
            return {"ok": False, "reason": "not_found", "tournament_id": tournament_id}

        conflicts = get_tournament_member_application_conflicts(
            tournament_id,
            team_row["team_id"],
            statuses=("approved",),
        )
        if conflicts:
            conn.rollback()
            return {
                "ok": False,
                "reason": "member_conflict",
                "tournament_id": tournament_id,
                "conflicts": conflicts,
            }

        cur.execute(
            "UPDATE tournament_applications SET status='approved', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (app_id,)
        )
        if cur.rowcount == 0:
            conn.rollback()
            return {"ok": False, "reason": "already_processed", "tournament_id": tournament_id}

        conn.commit()
        try:
            referral_result = process_referral_application_approval(app_id)
        except Exception as exc:
            logger.warning("Не удалось обработать реферальный бонус для заявки %s: %s", app_id, exc)
            referral_result = {"ok": False, "reason": "referral_failed"}
        result = {"ok": True, "tournament_id": tournament_id}
        if referral_result.get("ok") and referral_result.get("awards"):
            result["referral_awards"] = int(referral_result["awards"])
            result["referral_sport_key"] = referral_result.get("sport_key")
        return result
    except sqlite3.Error:
        conn.rollback()
        return {"ok": False, "reason": "db_error"}
    finally:
        conn.close()


def exclude_team_from_tournament(app_id):
    """
    Исключает уже одобренную команду из турнира (переводит заявку в rejected).
    Допускается только на этапе регистрации и до генерации сетки.

    Возвращает dict:
      {"ok": True, "tournament_id": int}
      {"ok": False, "reason": "...", ...}
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("""
            SELECT
                a.id,
                a.tournament_id,
                a.status AS app_status,
                t.status AS tournament_status,
                t.bracket_generated
            FROM tournament_applications a
            JOIN tournaments t ON t.id = a.tournament_id
            WHERE a.id=?
        """, (app_id,))
        row = cur.fetchone()

        if not row:
            conn.rollback()
            return {"ok": False, "reason": "not_found"}

        tournament_id = row['tournament_id']
        if row['app_status'] != 'approved':
            conn.rollback()
            return {
                "ok": False,
                "reason": "not_approved",
                "tournament_id": tournament_id,
                "app_status": row['app_status']
            }

        if row['tournament_status'] != 'registration' or row['bracket_generated']:
            conn.rollback()
            return {
                "ok": False,
                "reason": "not_registration",
                "tournament_id": tournament_id,
                "tournament_status": row['tournament_status']
            }

        cur.execute("""
            UPDATE tournament_applications
            SET status='excluded', updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='approved'
        """, (app_id,))
        if cur.rowcount == 0:
            conn.rollback()
            return {"ok": False, "reason": "already_processed", "tournament_id": tournament_id}

        conn.commit()
        return {"ok": True, "tournament_id": tournament_id}
    except sqlite3.Error:
        conn.rollback()
        return {"ok": False, "reason": "db_error"}
    finally:
        conn.close()


def update_team_rating(team_id, sport, month, points_change):
    # Совместимая обёртка для старых вызовов: пишем в новую каноничную схему.
    apply_manual_rating_adjustment(
        entity_type=ENTITY_TEAM,
        entity_id=team_id,
        sport_key=sport,
        rating_scope=SCOPE_OVERALL,
        format_key=FORMAT_GENERAL,
        delta=int(points_change),
        actor_user_id=None,
        reason="Legacy rating wrapper",
    )

# ---------- Функции для управления пользователями ----------


def get_all_users(offset=0, limit=20):
    """Получить список пользователей с пагинацией"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, telegram_id, first_name, last_name, username, email, city, age, favorite_sports, role, is_banned
        FROM users
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    users = cur.fetchall()
    conn.close()
    return users


def search_users(query, offset=0, limit=20):
    conn = get_connection()
    cur = conn.cursor()
    pattern = f"%{query}%"
    cur.execute("""
        SELECT id, telegram_id, first_name, last_name, username, email, city, age, favorite_sports, role, is_banned
        FROM users
        WHERE first_name LIKE ? OR last_name LIKE ? OR username LIKE ? OR telegram_id LIKE ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """, (pattern, pattern, pattern, pattern, limit, offset))
    users = cur.fetchall()
    conn.close()
    return users


def get_user_by_id(user_id):
    """Получить пользователя по его внутреннему ID"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user = cur.fetchone()
    conn.close()
    return user


def update_user_role(user_id, new_role):
    """Изменить роль пользователя"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    conn.commit()
    conn.close()


def toggle_user_ban(user_id):
    """Переключить статус бана (0 -> 1, 1 -> 0)"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET is_banned = 1 - is_banned WHERE id=?", (user_id,))
    conn.commit()
    cur.execute("SELECT is_banned FROM users WHERE id=?", (user_id,))
    new_status = cur.fetchone()['is_banned']
    conn.close()
    return new_status


def update_team_rating_same_conn(conn, team_id, sport, month, points_change):
    apply_manual_rating_adjustment(
        entity_type=ENTITY_TEAM,
        entity_id=team_id,
        sport_key=sport,
        rating_scope=SCOPE_OVERALL,
        format_key=FORMAT_GENERAL,
        delta=int(points_change),
        actor_user_id=None,
        reason="Legacy rating wrapper",
    )


def get_user_by_username(username):
    """Найти пользователя по username (без @)"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=?", (username,))
    user = cur.fetchone()
    conn.close()
    return user


def is_team_member(user_id, team_id):
    """Проверяет, состоит ли пользователь в команде"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id))
    result = cur.fetchone() is not None
    conn.close()
    return result


def has_pending_invite(user_id, team_id):
    """Проверяет, есть ли активное приглашение для пользователя в эту команду"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM team_invites WHERE team_id=? AND user_id=? AND status='pending'", (team_id, user_id))
    result = cur.fetchone() is not None
    conn.close()
    return result


def create_invite(team_id, user_id):
    """Создаёт новое приглашение"""
    if is_team_member_blocked(team_id, user_id):
        return False
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO team_invites (team_id, user_id) VALUES (?, ?)", (team_id, user_id))
    conn.commit()
    conn.close()
    return True


def accept_invite(team_id, user_id):
    """Принять приглашение: добавить в команду и обновить статус"""
    if is_team_member_blocked(team_id, user_id):
        return False
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO team_members (team_id, user_id) VALUES (?, ?)", (team_id, user_id))
    cur.execute(
        "UPDATE team_invites SET status='accepted' WHERE team_id=? AND user_id=?", (team_id, user_id))
    conn.commit()
    conn.close()
    return True


def reject_invite(team_id, user_id):
    """Отклонить приглашение"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE team_invites SET status='rejected' WHERE team_id=? AND user_id=?", (team_id, user_id))
    conn.commit()
    conn.close()


def get_all_sports():
    """Возвращает список всех видов спорта из таблицы sports (как кортежи (name, display_name))"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, display_name FROM sports ORDER BY display_name")
    sports = cur.fetchall()   # каждая запись — (name, display_name)
    conn.close()
    return sports


def get_sport_display_map() -> dict:
    """Возвращает маппинг canonical-name -> display_name."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, display_name FROM sports")
    rows = cur.fetchall()
    conn.close()
    result = {row["name"]: row["display_name"] for row in rows}
    fallback = {
        "CS2": "CS2",
        "Basketball": "Баскетбол",
        "Football": "Футбол",
        "Volleyball": "Волейбол",
    }
    for key, value in fallback.items():
        result.setdefault(key, value)
    return result


def normalize_sport_name(name: str | None) -> str:
    """Нормализует название спорта к каноническому ключу БД."""
    if not name:
        return ""

    raw = str(name).strip()
    if not raw:
        return ""

    # Канонические ключи.
    if raw in ("CS2", "Basketball", "Football", "Volleyball"):
        return raw

    aliases = {
        "cs2": "CS2",
        "counter-strike 2": "CS2",
        "баскетбол": "Basketball",
        "basketball": "Basketball",
        "футбол": "Football",
        "football": "Football",
        "волейбол": "Volleyball",
        "volleyball": "Volleyball",
    }
    return aliases.get(raw.lower(), raw)


def get_sport_display_name(name: str) -> str:
    normalized = normalize_sport_name(name)
    if not normalized:
        return ""
    return get_sport_display_map().get(normalized, normalized)


def map_sports_to_display(sports: list[str]) -> list[str]:
    sport_map = get_sport_display_map()
    return [sport_map.get(normalize_sport_name(sport), normalize_sport_name(sport)) for sport in sports]


def get_team_application(tournament_id, team_id):
    """Возвращает статус заявки и время последнего обновления или None"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT status, updated_at FROM tournament_applications WHERE tournament_id=? AND team_id=?",
                (tournament_id, team_id))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'status': row['status'], 'updated_at': row['updated_at']}
    return None


def update_tournament_application_status(application_id, new_status):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tournament_applications SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_status, application_id))
    conn.commit()
    conn.close()


def can_retry_tournament_application(tournament_id, team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT updated_at FROM tournament_applications 
        WHERE tournament_id=? AND team_id=? AND status='rejected'
        ORDER BY updated_at DESC LIMIT 1
    """, (tournament_id, team_id))
    row = cur.fetchone()
    conn.close()
    if not row:
        return True, None
    if row['updated_at']:
        try:
            rejected_time = datetime.strptime(
                row['updated_at'], '%Y-%m-%d %H:%M:%S')
            now = datetime.now(timezone.utc).replace(
                tzinfo=None)  # <-- исправлено
            delta = now - rejected_time
            if delta >= timedelta(hours=1):
                return True, None
            else:
                minutes_left = 60 - int(delta.total_seconds() // 60)
                return False, minutes_left
        except Exception as e:
            logger.info(f"Ошибка парсинга updated_at: {e}")
            return True, None
    return True, None


def get_approved_teams_count(tournament_id):
    """Возвращает количество одобренных команд в турнире."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE tournament_id=? AND status='approved'", (tournament_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_tournament_teams(tournament_id, status='approved'):
    """Возвращает список команд с указанным статусом заявки."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.* FROM teams t
        JOIN tournament_applications a ON t.id = a.team_id
        WHERE a.tournament_id=? AND a.status=?
    """, (tournament_id, status))
    teams = cur.fetchall()
    conn.close()
    return teams


def delete_tournament(tournament_id):
    """Удалить турнир и все связанные записи"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM tournament_applications WHERE tournament_id=?", (tournament_id,))
    cur.execute("DELETE FROM matches WHERE tournament_id=?", (tournament_id,))
    cur.execute("DELETE FROM tournaments WHERE id=?", (tournament_id,))
    conn.commit()
    conn.close()


def delete_team_admin(team_id):
    """Удалить команду (админ) со всеми связями"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM team_member_blocks WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM team_invites WHERE team_id=?", (team_id,))
    cur.execute(
        "DELETE FROM tournament_applications WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM matches WHERE team1_id=? OR team2_id=?",
                (team_id, team_id))
    cur.execute("DELETE FROM ratings WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM entity_ratings WHERE entity_type='team' AND entity_id=?", (team_id,))
    cur.execute("DELETE FROM rating_adjustments WHERE entity_type='team' AND entity_id=?", (team_id,))
    cur.execute("DELETE FROM teams WHERE id=?", (team_id,))
    conn.commit()
    conn.close()


def get_all_teams():
    """Возвращает список всех команд"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, sport, city FROM teams ORDER BY name")
    teams = cur.fetchall()
    conn.close()
    return teams


def reset_sport_rating(sport):
    """Удалить все записи рейтинга для указанного спорта"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM ratings WHERE sport=?", (sport,))
    conn.commit()
    conn.close()


def reset_team_rating(team_id, sport):
    """Удалить все записи рейтинга для команды в указанном спорте"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM ratings WHERE team_id=? AND sport=?",
                (team_id, sport))
    conn.commit()
    conn.close()


def deduct_team_points(team_id, sport, points):
    """Снять points очков с команды в указанном спорте за текущий месяц"""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT points FROM ratings WHERE team_id=? AND sport=? AND month=?",
                (team_id, sport, month))
    row = cur.fetchone()
    if row:
        new_points = max(0, row['points'] - points)
        cur.execute("UPDATE ratings SET points=? WHERE team_id=? AND sport=? AND month=?",
                    (new_points, team_id, sport, month))
    conn.commit()
    conn.close()


def get_teams_with_rating(sport):
    """Возвращает команды с их актуальными очками по новой рейтинговой модели."""
    rows, _ = get_rating_leaderboard(
        entity_type=ENTITY_TEAM,
        sport_key=sport,
        rating_scope=SCOPE_OVERALL,
        format_key=None,
        limit=500,
        offset=0,
    )
    if rows:
        return [{"id": row["entity_id"], "name": row["team_name"], "points": row["rating_value"]} for row in rows]

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.name, COALESCE(r.points, 0) as points
        FROM teams t
        LEFT JOIN ratings r ON t.id = r.team_id AND r.sport=? AND r.month=?
        WHERE t.sport=?
        ORDER BY points DESC
    """, (sport, month, sport))
    teams = cur.fetchall()
    conn.close()
    return teams
# ---------- Настройки команды ----------


def get_team_settings(team_id):
    """Возвращает настройки команды: is_open_for_requests, notify_on_requests"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT is_open_for_requests, notify_on_requests FROM teams WHERE id=?", (team_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'is_open': bool(row['is_open_for_requests']), 'notify': bool(row['notify_on_requests'])}
    return {'is_open': False, 'notify': False}


def set_team_open_status(team_id, is_open):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET is_open_for_requests=? WHERE id=?",
                (1 if is_open else 0, team_id))
    conn.commit()
    conn.close()


def set_team_notify_status(team_id, notify):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET notify_on_requests=? WHERE id=?",
                (1 if notify else 0, team_id))
    conn.commit()
    conn.close()

# ---------- Заявки на вступление (requests) ----------


def create_team_request(team_id, user_id):
    """Создаёт заявку на вступление (type='request')"""
    if is_team_member_blocked(team_id, user_id):
        return False
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO team_invites (team_id, user_id, status, type)
        VALUES (?, ?, 'pending', 'request')
    """, (team_id, user_id))
    conn.commit()
    conn.close()
    return True


def get_team_requests(team_id, offset=0, limit=10):
    """Возвращает список заявок (pending) для команды с пагинацией"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT ti.id, ti.user_id, u.first_name, u.last_name, u.username, u.email, u.city, u.favorite_sports
        FROM team_invites ti
        JOIN users u ON ti.user_id = u.id
        WHERE ti.team_id=? AND ti.status='pending' AND ti.type='request'
        ORDER BY ti.created_at DESC
        LIMIT ? OFFSET ?
    """, (team_id, limit, offset))
    requests = cur.fetchall()
    conn.close()
    return requests


def get_team_requests_count(team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM team_invites WHERE team_id=? AND status='pending' AND type='request'", (team_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def accept_request(request_id):
    """Принять заявку: добавить в team_members, обновить статус и updated_at"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT team_id, user_id FROM team_invites WHERE id=? AND type='request'", (request_id,))
    req = cur.fetchone()
    if not req:
        conn.close()
        return False
    if is_team_member_blocked(int(req['team_id']), int(req['user_id'])):
        conn.close()
        return False
    cur.execute("INSERT OR IGNORE INTO team_members (team_id, user_id) VALUES (?, ?)",
                (req['team_id'], req['user_id']))
    cur.execute(
        "UPDATE team_invites SET status='accepted', updated_at=CURRENT_TIMESTAMP WHERE id=?", (request_id,))
    conn.commit()
    conn.close()
    return True


def reject_request(request_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE team_invites SET status='rejected', updated_at=CURRENT_TIMESTAMP WHERE id=? AND type='request'", (request_id,))
    conn.commit()
    conn.close()


def has_pending_request(user_id, team_id):
    """Проверяет, есть ли активная заявка от пользователя в эту команду"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM team_invites WHERE team_id=? AND user_id=? AND status='pending' AND type='request'", (team_id, user_id))
    result = cur.fetchone() is not None
    conn.close()
    return result

# ---------- Получение команд с пагинацией ----------


def get_all_teams_paginated(offset=0, limit=10, only_open=False):
    """Возвращает список команд. Если only_open=True, то только с is_open_for_requests=1"""
    conn = get_connection()
    cur = conn.cursor()
    if only_open:
        cur.execute("""
            SELECT t.*, u.first_name as captain_first, u.username as captain_username
            FROM teams t
            JOIN users u ON t.captain_id = u.id
            WHERE t.is_open_for_requests = 1
            ORDER BY t.name
            LIMIT ? OFFSET ?
        """, (limit, offset))
    else:
        cur.execute("""
            SELECT t.*, u.first_name as captain_first, u.username as captain_username
            FROM teams t
            JOIN users u ON t.captain_id = u.id
            ORDER BY t.name
            LIMIT ? OFFSET ?
        """, (limit, offset))
    teams = cur.fetchall()
    conn.close()
    return teams


def get_teams_count(only_open=False):
    conn = get_connection()
    cur = conn.cursor()
    if only_open:
        cur.execute("SELECT COUNT(*) FROM teams WHERE is_open_for_requests=1")
    else:
        cur.execute("SELECT COUNT(*) FROM teams")
    count = cur.fetchone()[0]
    conn.close()
    return count


def search_teams_by_name(query, offset=0, limit=10):
    """Поиск команд по названию (регистронезависимо) с пагинацией"""
    conn = get_connection()
    cur = conn.cursor()
    pattern = f"%{query}%"
    cur.execute("""
        SELECT t.*, u.first_name as captain_first, u.username as captain_username
        FROM teams t
        JOIN users u ON t.captain_id = u.id
        WHERE t.name LIKE ?
        ORDER BY t.name
        LIMIT ? OFFSET ?
    """, (pattern, limit, offset))
    teams = cur.fetchall()
    conn.close()
    return teams


def search_teams_count(query):
    conn = get_connection()
    cur = conn.cursor()
    pattern = f"%{query}%"
    cur.execute("SELECT COUNT(*) FROM teams WHERE name LIKE ?", (pattern,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_team_invite_status(team_id, user_id):
    """Возвращает статус заявки (pending/accepted/rejected) или None, если записи нет."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT status FROM team_invites WHERE team_id=? AND user_id=?", (team_id, user_id))
    row = cur.fetchone()
    conn.close()
    return row['status'] if row else None


def get_teams_by_sport(sport, only_open=False, offset=0, limit=10):
    conn = get_connection()
    cur = conn.cursor()
    if only_open:
        cur.execute("""
            SELECT t.*, u.first_name as captain_first, u.username as captain_username
            FROM teams t
            JOIN users u ON t.captain_id = u.id
            WHERE t.sport=? AND t.is_open_for_requests=1
            ORDER BY t.name
            LIMIT ? OFFSET ?
        """, (sport, limit, offset))
    else:
        cur.execute("""
            SELECT t.*, u.first_name as captain_first, u.username as captain_username
            FROM teams t
            JOIN users u ON t.captain_id = u.id
            WHERE t.sport=?
            ORDER BY t.name
            LIMIT ? OFFSET ?
        """, (sport, limit, offset))
    teams = cur.fetchall()
    conn.close()
    return teams


def get_teams_count_by_sport(sport, only_open=False):
    conn = get_connection()
    cur = conn.cursor()
    if only_open:
        cur.execute(
            "SELECT COUNT(*) FROM teams WHERE sport=? AND is_open_for_requests=1", (sport,))
    else:
        cur.execute("SELECT COUNT(*) FROM teams WHERE sport=?", (sport,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def is_email_unique(email, exclude_telegram_id=None):
    conn = get_connection()
    cur = conn.cursor()
    if exclude_telegram_id:
        cur.execute("SELECT id FROM users WHERE email=? AND telegram_id != ?",
                    (email, exclude_telegram_id))
    else:
        cur.execute("SELECT id FROM users WHERE email=?", (email,))
    result = cur.fetchone() is not None
    conn.close()
    return not result   # True если уникален


def parse_russian_date(date_str):
    """
    Парсит строку вида "день месяц" (например "1 янв.").
    Возвращает кортеж (day, month) или None при ошибке.
    """
    parts = date_str.strip().split()
    if len(parts) != 2:
        return None
    day_str, month_str = parts
    if not day_str.isdigit():
        return None
    day = int(day_str)
    if day < 1 or day > 31:
        return None
    month = RUSSIAN_MONTHS.get(month_str.lower())
    if month is None:
        return None
    return day, month


def parse_russian_datetime(datetime_str):
    """
    Парсит строку вида "день месяц часы:минуты" (например "1 янв. 18:00").
    Требует, чтобы часы и минуты были двузначными.
    Возвращает кортеж (day, month, hour, minute) или None.
    """
    parts = datetime_str.strip().split()
    if len(parts) != 3:
        return None
    day_str, month_str, time_str = parts
    # Проверка дня
    if not day_str.isdigit():
        return None
    day = int(day_str)
    if day < 1 or day > 31:
        return None
    # Проверка месяца
    month = RUSSIAN_MONTHS.get(month_str.lower())
    if month is None:
        return None
    # Проверка времени
    time_parts = time_str.split(':')
    if len(time_parts) != 2:
        return None
    hour_str, minute_str = time_parts
    # Проверяем, что обе части состоят из двух цифр (допускаем ведущие нули)
    if not (hour_str.isdigit() and minute_str.isdigit() and len(hour_str) == 2 and len(minute_str) == 2):
        return None
    hour = int(hour_str)
    minute = int(minute_str)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return day, month, hour, minute


def parse_msk_datetime_input(datetime_str: str) -> datetime | None:
    """
    Парсит дату/время в формате 'ДД.ММ.ГГГГ ЧЧ:ММ' в зоне Europe/Moscow.
    Возвращает timezone-aware datetime (MSK) или None.
    """
    raw = (datetime_str or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=MSK_TZ)


def datetime_to_utc_storage(dt: datetime) -> str:
    """Преобразует aware datetime в строку UTC для хранения в SQLite."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None).strftime(UTC_STORAGE_FORMAT)


def parse_utc_storage_datetime(value: str | None) -> datetime | None:
    """
    Парсит строку UTC из БД ('YYYY-MM-DD HH:MM:SS') в aware datetime (UTC).
    """
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, UTC_STORAGE_FORMAT)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def format_utc_to_msk(value: str | None, fallback: str = "не назначено") -> str:
    """Форматирует UTC-строку из БД в человекочитаемый вид MSK."""
    dt_utc = parse_utc_storage_datetime(value)
    if not dt_utc:
        return fallback
    return dt_utc.astimezone(MSK_TZ).strftime("%d.%m.%Y %H:%M")
# ---------- Функции для работы с лимитами команды ----------


def update_team_request_status(request_id, new_status):
    """Обновляет статус заявки и проставляет текущее время в updated_at"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE team_invites 
        SET status=?, updated_at=CURRENT_TIMESTAMP 
        WHERE id=?
    """, (new_status, request_id))
    conn.commit()
    conn.close()


def can_retry_request(team_id, user_id):
    """
    Проверяет, можно ли подать заявку повторно после отклонения.
    Возвращает (True, None) если можно, или (False, minutes_left) если нужно подождать.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT status, updated_at FROM team_invites 
        WHERE team_id=? AND user_id=? AND type='request' 
        ORDER BY updated_at DESC LIMIT 1
    """, (team_id, user_id))
    row = cur.fetchone()
    conn.close()
    if not row:
        return True, None  # заявок не было
    if row['status'] != 'rejected':
        # не отклонена, значит можно (или pending, accepted – обработаем отдельно)
        return True, None
    # Была отклонена
    if row['updated_at']:
        # updated_at хранится как строка в формате SQLite, преобразуем в datetime
        rejected_time = datetime.strptime(
            row['updated_at'], '%Y-%m-%d %H:%M:%S')
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        delta = now - rejected_time
        if delta >= timedelta(hours=1):
            return True, None
        else:
            minutes_left = 60 - int(delta.total_seconds() // 60)
            return False, minutes_left
    return True, None  # если нет времени (старые записи), разрешаем


def get_user_age(user_id):
    """Возвращает возраст пользователя по его id или None"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT age FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row['age'] if row else None


def update_user_age(user_id, new_age):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET age=? WHERE id=?", (new_age, user_id))
    conn.commit()
    conn.close()


def get_all_users_count():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count


def search_users_count(query):
    conn = get_connection()
    cur = conn.cursor()
    pattern = f"%{query}%"
    cur.execute("SELECT COUNT(*) FROM users WHERE first_name LIKE ? OR last_name LIKE ? OR username LIKE ? OR telegram_id LIKE ?",
                (pattern, pattern, pattern, pattern))
    count = cur.fetchone()[0]
    conn.close()
    return count


# ========== НОВЫЕ ФУНКЦИИ ДЛЯ ТУРНИРНЫХ СЕТОК И СТАТИСТИКИ ==========

def get_bracket_matches(tournament_id: int):
    """Возвращает все матчи сетки турнира."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            b.*,
            t1.name as team1_name,
            t2.name as team2_name,
            w.name as winner_name
        FROM tournament_brackets b
        LEFT JOIN teams t1 ON b.team1_id = t1.id
        LEFT JOIN teams t2 ON b.team2_id = t2.id
        LEFT JOIN teams w ON b.winner_id = w.id
        WHERE b.tournament_id=?
        ORDER BY b.round_number, b.match_number
    """, (tournament_id,))
    matches = cur.fetchall()
    conn.close()
    return matches


def get_bracket_match_by_id(match_id: int):
    """Возвращает матч сетки по ID вместе с названиями команд и турнира."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            b.*,
            t1.name AS team1_name,
            t2.name AS team2_name,
            w.name AS winner_name,
            tr.name AS tournament_name,
            tr.sport AS tournament_sport
        FROM tournament_brackets b
        LEFT JOIN teams t1 ON b.team1_id = t1.id
        LEFT JOIN teams t2 ON b.team2_id = t2.id
        LEFT JOIN teams w ON b.winner_id = w.id
        LEFT JOIN tournaments tr ON tr.id = b.tournament_id
        WHERE b.id=?
    """, (match_id,))
    row = cur.fetchone()
    conn.close()
    return row


def _snapshot_bracket_technical_participants(cur: sqlite3.Cursor, tournament_id: int, team1_id: int | None, team2_id: int | None, match_id: int):
    cur.execute("DELETE FROM bracket_match_technical_participants WHERE match_id=?", (match_id,))
    for team_id in (team1_id, team2_id):
        if not team_id:
            continue
        member_ids: list[int] = []
        if tournament_id:
            cur.execute("""
                SELECT user_id
                FROM tournament_team_rosters
                WHERE tournament_id=? AND team_id=? AND status='active'
                ORDER BY created_at, user_id
            """, (tournament_id, team_id))
            member_ids = [int(row["user_id"]) for row in cur.fetchall()]

        if not member_ids:
            cur.execute("""
                SELECT user_id
                FROM team_members
                WHERE team_id=?
                ORDER BY joined_at, user_id
            """, (team_id,))
            member_ids = [int(row["user_id"]) for row in cur.fetchall()]

        for user_id in member_ids:
            cur.execute("""
                INSERT OR IGNORE INTO bracket_match_technical_participants (match_id, user_id, team_id)
                VALUES (?, ?, ?)
            """, (match_id, user_id, team_id))


def clear_bracket_match_stats(match_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM player_match_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
    cur.execute("DELETE FROM match_map_results WHERE match_source='bracket' AND match_id=?", (match_id,))
    cur.execute("DELETE FROM football_player_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
    cur.execute("DELETE FROM basketball_player_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
    cur.execute("DELETE FROM volleyball_player_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
    cur.execute("DELETE FROM volleyball_set_scores WHERE match_source='bracket' AND match_id=?", (match_id,))
    conn.commit()
    conn.close()


def apply_bracket_technical_result(
    match_id: int,
    loser_team_id: int,
    actor_user_id: int | None = None,
    reason: str | None = None,
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT * FROM tournament_brackets WHERE id=?", (match_id,))
        match = cur.fetchone()
        if not match:
            conn.rollback()
            return {"ok": False, "reason": "not_found"}
        if match["status"] == "completed":
            conn.rollback()
            return {"ok": False, "reason": "already_completed"}
        if loser_team_id not in {match["team1_id"], match["team2_id"]}:
            conn.rollback()
            return {"ok": False, "reason": "invalid_loser"}

        winner_id = match["team2_id"] if loser_team_id == match["team1_id"] else match["team1_id"]
        if not winner_id:
            conn.rollback()
            return {"ok": False, "reason": "winner_not_found"}

        clean_reason = (reason or "").strip() or None
        score1 = 0 if loser_team_id == match["team1_id"] else 1
        score2 = 0 if loser_team_id == match["team2_id"] else 1

        from utils.bracket_utils import _auto_advance_ready_matches, _propagate_winner

        cur.execute("DELETE FROM player_match_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
        cur.execute("DELETE FROM match_map_results WHERE match_source='bracket' AND match_id=?", (match_id,))
        cur.execute("DELETE FROM football_player_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
        cur.execute("DELETE FROM basketball_player_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
        cur.execute("DELETE FROM volleyball_player_stats WHERE match_source='bracket' AND match_id=?", (match_id,))
        cur.execute("DELETE FROM volleyball_set_scores WHERE match_source='bracket' AND match_id=?", (match_id,))

        _snapshot_bracket_technical_participants(cur, match["tournament_id"], match["team1_id"], match["team2_id"], match_id)

        cur.execute("""
            UPDATE tournament_brackets
            SET winner_id=?,
                status='completed',
                score1=?,
                score2=?,
                result_type='technical',
                technical_winner_id=?,
                technical_loser_id=?,
                technical_reason=?,
                technical_assigned_by=?,
                technical_assigned_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            winner_id,
            score1,
            score2,
            winner_id,
            loser_team_id,
            clean_reason,
            actor_user_id,
            match_id,
        ))

        _propagate_winner(conn, match, winner_id)
        _auto_advance_ready_matches(conn, match["tournament_id"])
        conn.commit()
        return {
            "ok": True,
            "winner_id": winner_id,
            "loser_id": loser_team_id,
            "score1": score1,
            "score2": score2,
            "tournament_id": match["tournament_id"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_unscheduled_ready_bracket_matches(tournament_id: int):
    """
    Возвращает pending-матчи сетки, где уже есть обе команды,
    но не заполнены время/место.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            b.*,
            t1.name AS team1_name,
            t2.name AS team2_name,
            tr.name AS tournament_name,
            tr.sport AS tournament_sport
        FROM tournament_brackets b
        LEFT JOIN teams t1 ON b.team1_id = t1.id
        LEFT JOIN teams t2 ON b.team2_id = t2.id
        LEFT JOIN tournaments tr ON tr.id = b.tournament_id
        WHERE b.tournament_id=?
          AND b.status='pending'
          AND b.team1_id IS NOT NULL
          AND b.team2_id IS NOT NULL
          AND (
              b.scheduled_at_utc IS NULL OR TRIM(b.scheduled_at_utc)=''
              OR b.location IS NULL OR TRIM(b.location)=''
          )
        ORDER BY b.round_number, b.match_number
    """, (tournament_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def set_bracket_match_schedule(match_id: int, scheduled_at_utc: str, location: str):
    """
    Назначает/изменяет расписание матча сетки.
    Возвращает:
      {"ok": True, "is_new": bool, "is_changed": bool, "match": {...}, "old": {...}, "new": {...}}
      {"ok": False, "reason": "..."}
    """
    clean_time = (scheduled_at_utc or "").strip()
    clean_location = (location or "").strip()
    if not clean_time or not clean_location:
        return {"ok": False, "reason": "invalid_payload"}

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("""
            SELECT
                b.*,
                t1.name AS team1_name,
                t2.name AS team2_name,
                tr.name AS tournament_name,
                tr.sport AS tournament_sport
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON b.team1_id = t1.id
            LEFT JOIN teams t2 ON b.team2_id = t2.id
            LEFT JOIN tournaments tr ON tr.id = b.tournament_id
            WHERE b.id=?
        """, (match_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "reason": "not_found"}
        if row["status"] != "pending":
            conn.rollback()
            return {"ok": False, "reason": "not_pending", "status": row["status"]}

        old_time = (row["scheduled_at_utc"] or "").strip()
        old_location = (row["location"] or "").strip()
        is_new = not old_time or not old_location
        is_changed = old_time != clean_time or old_location != clean_location
        scheduled_dt = parse_utc_storage_datetime(clean_time)
        reminder_value = None
        if scheduled_dt and scheduled_dt <= datetime.now(timezone.utc) + timedelta(hours=1):
            # If less than an hour remains at the moment of scheduling, skip the 1-hour reminder.
            reminder_value = datetime.now(timezone.utc).strftime(UTC_STORAGE_FORMAT)

        if is_changed:
            cur.execute("""
                UPDATE tournament_brackets
                SET
                    scheduled_at_utc=?,
                    location=?,
                    schedule_updated_at=CURRENT_TIMESTAMP,
                    reminder_sent_at=?
                WHERE id=?
            """, (clean_time, clean_location, reminder_value, match_id))
            conn.commit()
        else:
            conn.rollback()

        row_payload = dict(row)
        row_payload["scheduled_at_utc"] = clean_time if is_changed else old_time
        row_payload["location"] = clean_location if is_changed else old_location

        return {
            "ok": True,
            "is_new": is_new,
            "is_changed": is_changed,
            "match": row_payload,
            "old": {"scheduled_at_utc": old_time, "location": old_location},
            "new": {"scheduled_at_utc": row_payload["scheduled_at_utc"], "location": row_payload["location"]},
        }
    except sqlite3.Error:
        conn.rollback()
        return {"ok": False, "reason": "db_error"}
    finally:
        conn.close()


def mark_bracket_schedule_notified(match_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tournament_brackets
        SET schedule_notified_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (match_id,))
    conn.commit()
    conn.close()


def get_bracket_matches_due_for_reminder():
    """Возвращает матчи сетки, по которым пора отправить напоминание."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            b.*,
            t1.name AS team1_name,
            t2.name AS team2_name,
            tr.name AS tournament_name,
            tr.sport AS tournament_sport
        FROM tournament_brackets b
        LEFT JOIN teams t1 ON b.team1_id = t1.id
        LEFT JOIN teams t2 ON b.team2_id = t2.id
        LEFT JOIN tournaments tr ON tr.id = b.tournament_id
        WHERE b.status='pending'
          AND b.team1_id IS NOT NULL
          AND b.team2_id IS NOT NULL
          AND b.scheduled_at_utc IS NOT NULL
          AND TRIM(b.scheduled_at_utc) <> ''
          AND b.location IS NOT NULL
          AND TRIM(b.location) <> ''
          AND b.reminder_sent_at IS NULL
          AND datetime(b.scheduled_at_utc, '-1 hour') <= datetime('now')
        ORDER BY b.scheduled_at_utc
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_bracket_reminder_sent(match_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tournament_brackets
        SET reminder_sent_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (match_id,))
    conn.commit()
    conn.close()


def update_bracket_match_winner(match_id: int, winner_id: int):
    """Обновляет победителя в матче сетки."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tournament_brackets 
        SET winner_id=?, status='completed'
        WHERE id=?
    """, (winner_id, match_id))
    conn.commit()
    conn.close()


def get_semifinal_matches(tournament_id: int):
    """Возвращает полуфинальные матчи."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM tournament_brackets
        WHERE tournament_id=?
          AND round_number = (
              SELECT MAX(round_number) - 1
              FROM tournament_brackets
              WHERE tournament_id=?
                AND COALESCE(is_third_place, 0)=0
          )
          AND COALESCE(is_third_place, 0)=0
        ORDER BY match_number
    """, (tournament_id, tournament_id))
    matches = cur.fetchall()
    conn.close()
    return matches


def create_third_place_bracket_match(tournament_id: int, team1_id: int, team2_id: int):
    """Создаёт матч за 3-е место в сетке."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(round_number)
        FROM tournament_brackets
        WHERE tournament_id=? AND COALESCE(is_third_place, 0)=0
    """, (tournament_id,))
    row = cur.fetchone()
    round_number = (int(row[0]) if row and row[0] else 0) + 1
    cur.execute("""
        INSERT INTO tournament_brackets 
        (tournament_id, round_number, round_name, match_number, team1_id, team2_id, status, is_third_place)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, (tournament_id, round_number, "Матч за 3-е место", 1, team1_id, team2_id, 'pending'))
    conn.commit()
    conn.close()


def _steam_lookup_variants(steam_value: str):
    """Формирует варианты одного Steam-профиля (id/url) для поиска дублей."""
    if not steam_value:
        return []

    raw = steam_value.strip().rstrip('/')
    variants = []
    if raw:
        variants.append(raw)

    steam_id64 = None
    if raw.isdigit() and len(raw) >= 17:
        steam_id64 = raw[:17]
    elif '/profiles/' in raw:
        part = raw.split('/profiles/', 1)[1].split('/', 1)[0].strip()
        if part.isdigit() and len(part) >= 17:
            steam_id64 = part[:17]

    if steam_id64:
        canonical = f"https://steamcommunity.com/profiles/{steam_id64}"
        for item in (steam_id64, canonical):
            if item not in variants:
                variants.append(item)

    return variants


def get_user_by_steam_id(steam_id: str):
    """Ищет пользователя по SteamID64/ссылке с учетом разных форматов хранения."""
    variants = _steam_lookup_variants(steam_id)
    if not variants:
        return None

    conn = get_connection()
    cur = conn.cursor()
    placeholders = ','.join(['?'] * len(variants))
    cur.execute(f"SELECT * FROM users WHERE steam_id IN ({placeholders}) LIMIT 1", tuple(variants))
    user = cur.fetchone()
    conn.close()
    return user


def get_users_without_steam_id():
    """Возвращает пользователей без указанного SteamID."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM users 
        WHERE steam_id IS NULL OR steam_id = ''
        ORDER BY id DESC
    """)
    users = cur.fetchall()
    conn.close()
    return users


def update_user_steam_id(user_id: int, steam_id: str):
    """Обновляет Steam-профиль пользователя с защитой от дублей."""
    normalized = (steam_id or '').strip()
    if normalized.isdigit() and len(normalized) >= 17:
        normalized = f"https://steamcommunity.com/profiles/{normalized[:17]}"

    variants = _steam_lookup_variants(normalized)

    conn = get_connection()
    cur = conn.cursor()

    if variants:
        placeholders = ','.join(['?'] * len(variants))
        cur.execute(
            f"SELECT id FROM users WHERE steam_id IN ({placeholders}) AND id<>? LIMIT 1",
            (*variants, user_id),
        )
        if cur.fetchone():
            conn.close()
            raise ValueError('steam_id_taken')

    cur.execute("UPDATE users SET steam_id=? WHERE id=?", (normalized, user_id))
    conn.commit()
    conn.close()


def get_steam_profile_name(steam_url: str) -> str:
    """
    Получает имя профиля Steam через Steam Web API.

    Примеры:
    - https://steamcommunity.com/id/customname -> имя из API
    - https://steamcommunity.com/profiles/76561198000000000 -> имя из API
    - 76561198000000000 -> имя из API
    """
    if not steam_url:
        return None

    from xml.etree import ElementTree
    import requests
    from utils.steam_utils import get_steam_id64_from_custom_url

    steam_url = steam_url.strip().rstrip('/')

    # Извлекаем SteamID64 из ссылки или используем как есть
    steam_id64 = None
    profile_xml_url = None

    # Проверяем это ли просто числовой ID
    if steam_url.isdigit() and len(steam_url) >= 17:
        steam_id64 = steam_url[:17]  # Берём первые 17 цифр
        profile_xml_url = f"https://steamcommunity.com/profiles/{steam_id64}/?xml=1"
    elif '/profiles/' in steam_url:
        # Числовой URL
        parts = steam_url.split('/profiles/')
        if len(parts) > 1:
            steam_id64 = parts[1].split('/')[0]
            profile_xml_url = f"https://steamcommunity.com/profiles/{steam_id64}/?xml=1"
    elif '/id/' in steam_url:
        # Кастомный URL - нужно resolve через API
        custom_name = steam_url.split('/id/')[1].split('/')[0]
        profile_xml_url = f"https://steamcommunity.com/id/{custom_name}/?xml=1"
        steam_id64 = get_steam_id64_from_custom_url(custom_name)

    headers = {
        "User-Agent": "RazryadArenaBot/1.0",
        "Accept": "application/json, text/xml, application/xml, text/plain, */*",
    }

    if profile_xml_url:
        try:
            response = requests.get(profile_xml_url, headers=headers, timeout=5)
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)
            persona_name = (root.findtext('steamID') or '').strip()
            if persona_name:
                return persona_name
        except requests.RequestException as e:
            logger.debug(f"Ошибка Steam XML profile lookup: {e}")
        except ElementTree.ParseError as e:
            logger.debug(f"Ошибка парсинга Steam XML profile: {e}")

    if not steam_id64 or not steam_id64.isdigit():
        return None

    steam_api_key = (os.getenv("STEAM_API_KEY") or "").strip()
    if len(steam_api_key) >= 2 and steam_api_key[0] == steam_api_key[-1] and steam_api_key[0] in {"'", '"'}:
        steam_api_key = steam_api_key[1:-1].strip()
    if not steam_api_key:
        return None

    # Фоллбек через GetPlayerSummaries, если XML профиль не отдал имя.
    try:
        response = requests.get(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
            params={"key": steam_api_key, "steamids": steam_id64},
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        players = data.get('response', {}).get('players', [])
        if players:
            return players[0].get('personaname')
    except requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else "unknown"
        logger.debug(f"GetPlayerSummaries returned HTTP {status_code}")
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"Ошибка GetPlayerSummaries: {e}")

    return None


def _resolve_match_source_case(alias: str = "pms") -> str:
    return (
        f"CASE "
        f"WHEN {alias}.match_source IS NOT NULL AND TRIM({alias}.match_source) <> '' THEN {alias}.match_source "
        f"WHEN EXISTS (SELECT 1 FROM tournament_brackets b WHERE b.id = {alias}.match_id) THEN 'bracket' "
        f"ELSE 'legacy' END"
    )


def _get_internal_user_id_by_telegram(conn, telegram_id: int):
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cur.fetchone()
    return user["id"] if user else None


def get_player_match_history_page(telegram_id: int, offset: int = 0, limit: int = 10):
    """Возвращает страницу истории матчей игрока: 1 запись = 1 матч."""
    conn = get_connection()
    cur = conn.cursor()

    internal_user_id = _get_internal_user_id_by_telegram(conn, telegram_id)
    if not internal_user_id:
        conn.close()
        return []

    cur.execute(f"""
        WITH stats_rows AS (
            SELECT
                pms.match_id,
                pms.created_at,
                {_resolve_match_source_case("pms")} AS resolved_source
            FROM player_match_stats pms
            WHERE pms.user_id = ?
        ),
        technical_rows AS (
            SELECT
                tp.match_id,
                tp.created_at,
                'bracket' AS resolved_source
            FROM bracket_match_technical_participants tp
            JOIN tournament_brackets b ON b.id = tp.match_id
            WHERE tp.user_id = ?
              AND COALESCE(b.result_type, 'regular') = 'technical'
        ),
        source_rows AS (
            SELECT match_id, created_at, resolved_source FROM stats_rows
            UNION ALL
            SELECT match_id, created_at, resolved_source FROM technical_rows
        ),
        user_matches AS (
            SELECT
                resolved_source AS match_source,
                match_id,
                MAX(created_at) AS last_stat_at
            FROM source_rows
            GROUP BY resolved_source, match_id
        ),
        history_rows AS (
            SELECT
                um.match_source,
                um.match_id,
                tour.name AS tournament_name,
                b.team1_id,
                b.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                b.score1,
                b.score2,
                COALESCE(b.result_type, 'regular') AS result_type,
                b.technical_winner_id,
                b.technical_loser_id,
                b.technical_reason,
                COALESCE(um.last_stat_at, b.created_at) AS played_at
            FROM user_matches um
            JOIN tournament_brackets b ON b.id = um.match_id
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            LEFT JOIN tournaments tour ON tour.id = b.tournament_id
            WHERE um.match_source = 'bracket'

            UNION ALL

            SELECT
                um.match_source,
                um.match_id,
                tour.name AS tournament_name,
                m.team1_id,
                m.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                m.score1,
                m.score2,
                'regular' AS result_type,
                NULL AS technical_winner_id,
                NULL AS technical_loser_id,
                NULL AS technical_reason,
                COALESCE(um.last_stat_at, m.match_date, m.created_at) AS played_at
            FROM user_matches um
            JOIN matches m ON m.id = um.match_id
            LEFT JOIN teams t1 ON t1.id = m.team1_id
            LEFT JOIN teams t2 ON t2.id = m.team2_id
            LEFT JOIN tournaments tour ON tour.id = m.tournament_id
            WHERE um.match_source = 'legacy'
        )
        SELECT *
        FROM history_rows
        ORDER BY played_at DESC, match_id DESC
        LIMIT ? OFFSET ?
    """, (internal_user_id, internal_user_id, limit, offset))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_player_match_history_count(telegram_id: int):
    """Возвращает количество матчей в истории игрока."""
    conn = get_connection()
    cur = conn.cursor()

    internal_user_id = _get_internal_user_id_by_telegram(conn, telegram_id)
    if not internal_user_id:
        conn.close()
        return 0

    cur.execute(f"""
        WITH source_rows AS (
            SELECT
                {_resolve_match_source_case("pms")} AS resolved_source,
                pms.match_id
            FROM player_match_stats pms
            WHERE pms.user_id = ?

            UNION ALL

            SELECT
                'bracket' AS resolved_source,
                tp.match_id
            FROM bracket_match_technical_participants tp
            JOIN tournament_brackets b ON b.id = tp.match_id
            WHERE tp.user_id = ?
              AND COALESCE(b.result_type, 'regular') = 'technical'
        )
        SELECT COUNT(*)
        FROM (
            SELECT resolved_source, match_id
            FROM source_rows
            GROUP BY resolved_source, match_id
        )
    """, (internal_user_id, internal_user_id))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_match_history_details(match_source: str, match_id: int):
    """
    Возвращает подробности матча для истории:
    заголовок матча, карты и статистику всех игроков по картам.
    """
    normalized_source = (match_source or "").strip().lower()
    if normalized_source not in ("bracket", "legacy"):
        return None

    conn = get_connection()
    cur = conn.cursor()

    if normalized_source == "bracket":
        cur.execute("""
            SELECT
                b.id AS match_id,
                'bracket' AS match_source,
                b.team1_id,
                b.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                b.score1,
                b.score2,
                COALESCE(b.result_type, 'regular') AS result_type,
                b.technical_winner_id,
                b.technical_loser_id,
                b.technical_reason,
                tour.name AS tournament_name,
                b.created_at AS played_at
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            LEFT JOIN tournaments tour ON tour.id = b.tournament_id
            WHERE b.id = ?
        """, (match_id,))
    else:
        cur.execute("""
            SELECT
                m.id AS match_id,
                'legacy' AS match_source,
                m.team1_id,
                m.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                m.score1,
                m.score2,
                'regular' AS result_type,
                NULL AS technical_winner_id,
                NULL AS technical_loser_id,
                NULL AS technical_reason,
                tour.name AS tournament_name,
                COALESCE(m.match_date, m.created_at) AS played_at
            FROM matches m
            LEFT JOIN teams t1 ON t1.id = m.team1_id
            LEFT JOIN teams t2 ON t2.id = m.team2_id
            LEFT JOIN tournaments tour ON tour.id = m.tournament_id
            WHERE m.id = ?
        """, (match_id,))

    header = cur.fetchone()
    if not header:
        conn.close()
        return None

    if (header["result_type"] or "regular") == "technical":
        conn.close()
        payload = dict(header)
        payload["is_technical_result"] = True
        payload["maps"] = []
        return payload

    cur.execute("""
        SELECT
            map_number,
            map_name,
            team1_score,
            team2_score,
            winner_id
        FROM match_map_results
        WHERE match_source=? AND match_id=?
        ORDER BY map_number
    """, (normalized_source, match_id))
    map_rows = cur.fetchall()

    cur.execute(f"""
        WITH stats_rows AS (
            SELECT
                pms.*,
                {_resolve_match_source_case("pms")} AS resolved_source
            FROM player_match_stats pms
            WHERE pms.match_id = ?
        )
        SELECT
            pms.map_number,
            pms.map_name,
            pms.user_id,
            pms.team_id,
            pms.kills,
            pms.deaths,
            pms.assists,
            pms.adr,
            pms.hs,
            pms.rating_3_0,
            pms.mvps,
            u.first_name,
            u.username
        FROM stats_rows pms
        JOIN users u ON u.id = pms.user_id
        WHERE pms.resolved_source = ?
        ORDER BY COALESCE(pms.map_number, 1), pms.team_id, pms.kills DESC, pms.rating_3_0 DESC
    """, (match_id, normalized_source))
    player_rows = cur.fetchall()
    conn.close()

    maps_by_number = {}
    for row in map_rows:
        map_number = row["map_number"] or 1
        maps_by_number[map_number] = {
            "map_number": map_number,
            "map_name": row["map_name"] or f"Карта {map_number}",
            "team1_score": row["team1_score"],
            "team2_score": row["team2_score"],
            "winner_id": row["winner_id"],
            "has_score": row["team1_score"] is not None and row["team2_score"] is not None,
            "players_team1": [],
            "players_team2": [],
            "players_other": [],
        }

    team1_id = header["team1_id"]
    team2_id = header["team2_id"]

    for row in player_rows:
        map_number = row["map_number"] or 1
        if map_number not in maps_by_number:
            maps_by_number[map_number] = {
                "map_number": map_number,
                "map_name": row["map_name"] or f"Карта {map_number}",
                "team1_score": None,
                "team2_score": None,
                "winner_id": None,
                "has_score": False,
                "players_team1": [],
                "players_team2": [],
                "players_other": [],
            }
        elif not maps_by_number[map_number]["map_name"] and row["map_name"]:
            maps_by_number[map_number]["map_name"] = row["map_name"]

        player_payload = {
            "user_id": row["user_id"],
            "first_name": row["first_name"] or f"ID {row['user_id']}",
            "username": row["username"],
            "kills": row["kills"] or 0,
            "deaths": row["deaths"] or 0,
            "assists": row["assists"] or 0,
            "adr": row["adr"] or 0,
            "hs": row["hs"] or 0,
            "rating": row["rating_3_0"] or 0.0,
            "mvps": row["mvps"] or 0,
            "team_id": row["team_id"],
        }

        if row["team_id"] == team1_id:
            maps_by_number[map_number]["players_team1"].append(player_payload)
        elif row["team_id"] == team2_id:
            maps_by_number[map_number]["players_team2"].append(player_payload)
        else:
            maps_by_number[map_number]["players_other"].append(player_payload)

    if not maps_by_number:
        maps_by_number[1] = {
            "map_number": 1,
            "map_name": "Карта 1",
            "team1_score": None,
            "team2_score": None,
            "winner_id": None,
            "has_score": False,
            "players_team1": [],
            "players_team2": [],
            "players_other": [],
        }

    maps = [maps_by_number[num] for num in sorted(maps_by_number.keys())]
    for one_map in maps:
        one_map["players_team1"].sort(key=lambda p: (p["kills"], p["rating"]), reverse=True)
        one_map["players_team2"].sort(key=lambda p: (p["kills"], p["rating"]), reverse=True)
        one_map["players_other"].sort(key=lambda p: (p["kills"], p["rating"]), reverse=True)

    payload = dict(header)
    payload["maps"] = maps
    return payload


# Обратная совместимость для старых вызовов.
def get_player_match_stats(telegram_id: int, offset: int = 0, limit: int = 10):
    return get_player_match_history_page(telegram_id, offset, limit)


def get_player_match_stats_count(telegram_id: int):
    return get_player_match_history_count(telegram_id)


def _normalize_match_source(match_source: str) -> str:
    source = (match_source or "").strip().lower()
    return source if source in ("bracket", "legacy") else "legacy"


def upsert_football_player_stat(match_source: str, match_id: int, user_id: int, team_id: int, goals: int, assists: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO football_player_stats
        (match_source, match_id, user_id, team_id, goals, assists, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(match_source, match_id, user_id) DO UPDATE SET
            team_id=excluded.team_id,
            goals=excluded.goals,
            assists=excluded.assists,
            updated_at=CURRENT_TIMESTAMP
        """,
        (_normalize_match_source(match_source), match_id, user_id, team_id, max(0, goals), max(0, assists)),
    )
    conn.commit()
    conn.close()


def upsert_basketball_player_stat(match_source: str, match_id: int, user_id: int, team_id: int, points: int, fouls: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO basketball_player_stats
        (match_source, match_id, user_id, team_id, points, fouls, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(match_source, match_id, user_id) DO UPDATE SET
            team_id=excluded.team_id,
            points=excluded.points,
            fouls=excluded.fouls,
            updated_at=CURRENT_TIMESTAMP
        """,
        (_normalize_match_source(match_source), match_id, user_id, team_id, max(0, points), max(0, fouls)),
    )
    conn.commit()
    conn.close()


def upsert_volleyball_player_stat(match_source: str, match_id: int, user_id: int, team_id: int, points: int, aces: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO volleyball_player_stats
        (match_source, match_id, user_id, team_id, points, aces, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(match_source, match_id, user_id) DO UPDATE SET
            team_id=excluded.team_id,
            points=excluded.points,
            aces=excluded.aces,
            updated_at=CURRENT_TIMESTAMP
        """,
        (_normalize_match_source(match_source), match_id, user_id, team_id, max(0, points), max(0, aces)),
    )
    conn.commit()
    conn.close()


def replace_volleyball_set_scores(match_source: str, match_id: int, set_scores: list[tuple[int, int]]):
    source = _normalize_match_source(match_source)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM volleyball_set_scores WHERE match_source=? AND match_id=?",
        (source, match_id),
    )
    for idx, (team1_points, team2_points) in enumerate(set_scores, start=1):
        cur.execute(
            """
            INSERT INTO volleyball_set_scores
            (match_source, match_id, set_number, team1_points, team2_points, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (source, match_id, idx, max(0, team1_points), max(0, team2_points)),
        )
    conn.commit()
    conn.close()


def _sport_stats_table(sport: str) -> str | None:
    sport = normalize_sport_name(sport)
    if sport == "Football":
        return "football_player_stats"
    if sport == "Basketball":
        return "basketball_player_stats"
    if sport == "Volleyball":
        return "volleyball_player_stats"
    return None


def get_player_career_stats_by_sport(telegram_id: int, sport: str):
    sport = normalize_sport_name(sport)
    if sport == "CS2":
        return get_player_career_stats(telegram_id)

    table = _sport_stats_table(sport)
    if not table:
        return None

    conn = get_connection()
    internal_user_id = _get_internal_user_id_by_telegram(conn, telegram_id)
    if not internal_user_id:
        conn.close()
        return None

    cur = conn.cursor()
    if sport == "Football":
        cur.execute(f"""
            SELECT
                AVG(goals) AS avg_goals,
                AVG(assists) AS avg_assists,
                SUM(goals) AS total_goals,
                SUM(assists) AS total_assists,
                COUNT(DISTINCT COALESCE(NULLIF(TRIM(match_source), ''), 'legacy') || ':' || match_id) AS matches_played
            FROM {table}
            WHERE user_id=?
        """, (internal_user_id,))
    elif sport == "Basketball":
        cur.execute(f"""
            SELECT
                AVG(points) AS avg_points,
                AVG(fouls) AS avg_fouls,
                SUM(points) AS total_points,
                SUM(fouls) AS total_fouls,
                COUNT(DISTINCT COALESCE(NULLIF(TRIM(match_source), ''), 'legacy') || ':' || match_id) AS matches_played
            FROM {table}
            WHERE user_id=?
        """, (internal_user_id,))
    else:
        cur.execute(f"""
            SELECT
                AVG(points) AS avg_points,
                AVG(aces) AS avg_aces,
                SUM(points) AS total_points,
                SUM(aces) AS total_aces,
                COUNT(DISTINCT COALESCE(NULLIF(TRIM(match_source), ''), 'legacy') || ':' || match_id) AS matches_played
            FROM {table}
            WHERE user_id=?
        """, (internal_user_id,))

    row = cur.fetchone()
    conn.close()
    return row


def get_player_match_history_count_by_sport(telegram_id: int, sport: str) -> int:
    sport = normalize_sport_name(sport)
    if sport == "CS2":
        return get_player_match_history_count(telegram_id)

    table = _sport_stats_table(sport)
    if not table:
        return 0

    conn = get_connection()
    internal_user_id = _get_internal_user_id_by_telegram(conn, telegram_id)
    if not internal_user_id:
        conn.close()
        return 0

    cur = conn.cursor()
    cur.execute(f"""
        WITH source_rows AS (
            SELECT COALESCE(NULLIF(TRIM(match_source), ''), 'legacy') AS source, match_id
            FROM {table}
            WHERE user_id=?

            UNION ALL

            SELECT 'bracket' AS source, tp.match_id
            FROM bracket_match_technical_participants tp
            JOIN tournament_brackets b ON b.id = tp.match_id
            JOIN tournaments tr ON tr.id = b.tournament_id
            WHERE tp.user_id=?
              AND COALESCE(b.result_type, 'regular')='technical'
              AND tr.sport=?
        )
        SELECT COUNT(*)
        FROM (
            SELECT source, match_id
            FROM source_rows
            GROUP BY source, match_id
        )
    """, (internal_user_id, internal_user_id, sport))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_player_match_history_page_by_sport(telegram_id: int, sport: str, offset: int = 0, limit: int = 10):
    sport = normalize_sport_name(sport)
    if sport == "CS2":
        return get_player_match_history_page(telegram_id, offset, limit)

    table = _sport_stats_table(sport)
    if not table:
        return []

    conn = get_connection()
    internal_user_id = _get_internal_user_id_by_telegram(conn, telegram_id)
    if not internal_user_id:
        conn.close()
        return []

    cur = conn.cursor()
    cur.execute(f"""
        WITH user_matches AS (
            SELECT
                COALESCE(NULLIF(TRIM(s.match_source), ''), 'legacy') AS match_source,
                s.match_id,
                MAX(s.created_at) AS last_stat_at
            FROM {table} s
            WHERE s.user_id=?
            GROUP BY match_source, s.match_id

            UNION ALL

            SELECT
                'bracket' AS match_source,
                tp.match_id,
                MAX(tp.created_at) AS last_stat_at
            FROM bracket_match_technical_participants tp
            JOIN tournament_brackets b ON b.id = tp.match_id
            JOIN tournaments tr ON tr.id = b.tournament_id
            WHERE tp.user_id=?
              AND COALESCE(b.result_type, 'regular')='technical'
              AND tr.sport=?
            GROUP BY tp.match_id
        ),
        history_rows AS (
            SELECT
                um.match_source,
                um.match_id,
                tour.name AS tournament_name,
                b.team1_id,
                b.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                b.score1,
                b.score2,
                COALESCE(b.result_type, 'regular') AS result_type,
                b.technical_winner_id,
                b.technical_loser_id,
                b.technical_reason,
                COALESCE(um.last_stat_at, b.created_at) AS played_at
            FROM user_matches um
            JOIN tournament_brackets b ON b.id = um.match_id
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            LEFT JOIN tournaments tour ON tour.id = b.tournament_id
            WHERE um.match_source='bracket'

            UNION ALL

            SELECT
                um.match_source,
                um.match_id,
                tour.name AS tournament_name,
                m.team1_id,
                m.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                m.score1,
                m.score2,
                'regular' AS result_type,
                NULL AS technical_winner_id,
                NULL AS technical_loser_id,
                NULL AS technical_reason,
                COALESCE(um.last_stat_at, m.match_date, m.created_at) AS played_at
            FROM user_matches um
            JOIN matches m ON m.id = um.match_id
            LEFT JOIN teams t1 ON t1.id = m.team1_id
            LEFT JOIN teams t2 ON t2.id = m.team2_id
            LEFT JOIN tournaments tour ON tour.id = m.tournament_id
            WHERE um.match_source='legacy'
        )
        SELECT *
        FROM history_rows
        ORDER BY played_at DESC, match_id DESC
        LIMIT ? OFFSET ?
    """, (internal_user_id, internal_user_id, sport, limit, offset))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_match_history_details_by_sport(sport: str, match_source: str, match_id: int):
    sport = normalize_sport_name(sport)
    if sport == "CS2":
        return get_match_history_details(match_source, match_id)

    table = _sport_stats_table(sport)
    source = _normalize_match_source(match_source)
    if not table:
        return None

    conn = get_connection()
    cur = conn.cursor()
    if source == "bracket":
        cur.execute("""
            SELECT
                b.id AS match_id,
                'bracket' AS match_source,
                b.team1_id,
                b.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                b.score1,
                b.score2,
                COALESCE(b.result_type, 'regular') AS result_type,
                b.technical_winner_id,
                b.technical_loser_id,
                b.technical_reason,
                tour.name AS tournament_name,
                b.created_at AS played_at
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            LEFT JOIN tournaments tour ON tour.id = b.tournament_id
            WHERE b.id = ?
        """, (match_id,))
    else:
        cur.execute("""
            SELECT
                m.id AS match_id,
                'legacy' AS match_source,
                m.team1_id,
                m.team2_id,
                t1.name AS team1_name,
                t2.name AS team2_name,
                m.score1,
                m.score2,
                'regular' AS result_type,
                NULL AS technical_winner_id,
                NULL AS technical_loser_id,
                NULL AS technical_reason,
                tour.name AS tournament_name,
                COALESCE(m.match_date, m.created_at) AS played_at
            FROM matches m
            LEFT JOIN teams t1 ON t1.id = m.team1_id
            LEFT JOIN teams t2 ON t2.id = m.team2_id
            LEFT JOIN tournaments tour ON tour.id = m.tournament_id
            WHERE m.id = ?
        """, (match_id,))
    header = cur.fetchone()
    if not header:
        conn.close()
        return None

    if (header["result_type"] or "regular") == "technical":
        conn.close()
        payload = dict(header)
        payload["is_technical_result"] = True
        payload["players_team1"] = []
        payload["players_team2"] = []
        payload["players_other"] = []
        payload["set_scores"] = []
        return payload

    if sport == "Football":
        cur.execute(f"""
            SELECT s.user_id, s.team_id, s.goals, s.assists, u.first_name, u.username
            FROM {table} s
            JOIN users u ON u.id = s.user_id
            WHERE COALESCE(NULLIF(TRIM(s.match_source), ''), 'legacy')=? AND s.match_id=?
            ORDER BY s.team_id, s.goals DESC, s.assists DESC
        """, (source, match_id))
    elif sport == "Basketball":
        cur.execute(f"""
            SELECT s.user_id, s.team_id, s.points, s.fouls, u.first_name, u.username
            FROM {table} s
            JOIN users u ON u.id = s.user_id
            WHERE COALESCE(NULLIF(TRIM(s.match_source), ''), 'legacy')=? AND s.match_id=?
            ORDER BY s.team_id, s.points DESC, s.fouls ASC
        """, (source, match_id))
    else:
        cur.execute(f"""
            SELECT s.user_id, s.team_id, s.points, s.aces, u.first_name, u.username
            FROM {table} s
            JOIN users u ON u.id = s.user_id
            WHERE COALESCE(NULLIF(TRIM(s.match_source), ''), 'legacy')=? AND s.match_id=?
            ORDER BY s.team_id, s.points DESC, s.aces DESC
        """, (source, match_id))
    players = cur.fetchall()

    set_scores = []
    if sport == "Volleyball":
        cur.execute("""
            SELECT set_number, team1_points, team2_points
            FROM volleyball_set_scores
            WHERE match_source=? AND match_id=?
            ORDER BY set_number
        """, (source, match_id))
        set_scores = [dict(row) for row in cur.fetchall()]

    conn.close()

    payload = dict(header)
    payload["players_team1"] = []
    payload["players_team2"] = []
    payload["players_other"] = []
    payload["set_scores"] = set_scores

    team1_id = payload["team1_id"]
    team2_id = payload["team2_id"]

    for row in players:
        item = dict(row)
        if item["team_id"] == team1_id:
            payload["players_team1"].append(item)
        elif item["team_id"] == team2_id:
            payload["players_team2"].append(item)
        else:
            payload["players_other"].append(item)

    return payload


def get_player_career_stats(user_id: int):
    """Возвращает общую статистику карьеры игрока."""
    conn = get_connection()
    cur = conn.cursor()

    # Сначала находим внутренний ID пользователя по telegram_id
    cur.execute("SELECT id FROM users WHERE telegram_id = ?", (user_id,))
    user = cur.fetchone()

    if not user:
        conn.close()
        return None

    internal_user_id = user['id']

    # Теперь получаем статистику по внутреннему ID
    cur.execute("""
        SELECT
            AVG(kills) as avg_kills,
            AVG(deaths) as avg_deaths,
            AVG(assists) as avg_assists,
            AVG(adr) as avg_adr,
            AVG(hs) as avg_hs,
            AVG(rating_3_0) as avg_rating,
            AVG(mvps) as avg_mvps,
            SUM(kills) as total_kills,
            SUM(deaths) as total_deaths,
            SUM(assists) as total_assists,
            SUM(mvps) as total_mvps,
            COUNT(DISTINCT (
                CASE
                    WHEN match_source IS NOT NULL AND TRIM(match_source) <> '' THEN match_source
                    WHEN EXISTS (SELECT 1 FROM tournament_brackets b WHERE b.id = player_match_stats.match_id) THEN 'bracket'
                    ELSE 'legacy'
                END
            ) || ':' || match_id) as matches_played
        FROM player_match_stats
        WHERE user_id = ?
    """, (internal_user_id,))
    stats = cur.fetchone()
    conn.close()
    return stats


def add_player_match_stats(match_id: int, user_id: int, team_id: int,
                           kills: int, deaths: int, assists: int,
                           rating: float, mvps: int, map_name: str, map_number: int,
                           match_source: str = 'bracket', adr: int = 0, hs: int = 0):
    """Добавляет статистику игрока в матче."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO player_match_stats 
        (match_id, match_source, user_id, team_id, kills, deaths, assists, adr, hs, rating_3_0, mvps, map_name, map_number)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (match_id, match_source, user_id, team_id, kills, deaths, assists, adr, hs, rating, mvps, map_name, map_number))
    conn.commit()
    conn.close()


def update_player_match_stats(match_id: int, user_id: int, map_number: int,
                              kills: int, deaths: int, assists: int,
                              rating: float, mvps: int, match_source: str = 'bracket',
                              adr: int = 0, hs: int = 0):
    """Обновляет статистику игрока в матче."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE player_match_stats 
        SET kills=?, deaths=?, assists=?, adr=?, hs=?, rating_3_0=?, mvps=?, updated_at=CURRENT_TIMESTAMP
        WHERE match_id=? AND user_id=? AND map_number=? AND COALESCE(match_source, 'bracket')=?
    """, (kills, deaths, assists, adr, hs, rating, mvps, match_id, user_id, map_number, match_source))
    conn.commit()
    conn.close()


def get_match_stats(match_id: int):
    """Возвращает всю статистику игроков в матче."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT pms.*, u.first_name, u.username
        FROM player_match_stats pms
        JOIN users u ON pms.user_id = u.id
        WHERE pms.match_id = ?
        ORDER BY pms.map_number, pms.team_id
    """, (match_id,))
    stats = cur.fetchall()
    conn.close()
    return stats


def get_team_members_count(team_id: int):
    """Возвращает количество участников в команде."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM team_members WHERE team_id=?", (team_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_team_max_members(team_id: int):
    """Возвращает максимальное количество участников команды."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT max_members FROM teams WHERE id=?", (team_id,))
    row = cur.fetchone()
    conn.close()
    return row['max_members'] if row else 5


def update_team_max_members(team_id: int, new_max: int):
    """Обновляет максимальное количество участников команды."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET max_members=? WHERE id=?",
                (new_max, team_id))
    conn.commit()
    conn.close()


def get_tournament_final_winner(tournament_id: int) -> int:
    """Возвращает ID победителя турнира (winner из финального матча)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT winner_id FROM tournament_brackets
        WHERE tournament_id=? AND round_number=(
            SELECT MAX(round_number) FROM tournament_brackets 
            WHERE tournament_id=? AND COALESCE(is_third_place, 0)=0
        )
        AND COALESCE(is_third_place, 0)=0
        ORDER BY match_number DESC
        LIMIT 1
    """, (tournament_id, tournament_id))
    result = cur.fetchone()
    conn.close()
    return result['winner_id'] if result else None


def get_tournament_second_place(tournament_id: int) -> int:
    """Возвращает ID команды, занявшей 2-е место (проигравший в финале)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT team1_id, team2_id, winner_id FROM tournament_brackets
        WHERE tournament_id=? AND round_number=(
            SELECT MAX(round_number) FROM tournament_brackets 
            WHERE tournament_id=? AND COALESCE(is_third_place, 0)=0
        )
        AND COALESCE(is_third_place, 0)=0
        AND status='completed'
        LIMIT 1
    """, (tournament_id, tournament_id))
    result = cur.fetchone()
    conn.close()
    if result and result['winner_id']:
        # Возвращаем проигравшего
        if result['winner_id'] == result['team1_id']:
            return result['team2_id']
        else:
            return result['team1_id']
    return None


def get_third_place_match_winner(tournament_id: int) -> int:
    """Возвращает ID победителя матча за 3-е место."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT winner_id FROM tournament_brackets
        WHERE tournament_id=? AND COALESCE(is_third_place, 0)=1
        AND status='completed'
        ORDER BY round_number DESC, id DESC
        LIMIT 1
    """, (tournament_id,))
    result = cur.fetchone()
    conn.close()
    return result['winner_id'] if result else None


def finish_tournament_with_awards(tournament_id: int) -> tuple:
    """
    Завершает турнир и начисляет очки за места:
    - 1 место: 20 очков
    - 2 место: 15 очков
    - 3 место: 10 очков (победитель матча за 3-е место)

    Возвращает кортеж (team1_id, team2_id, team3_id) или (None, None, None) если ошибка.
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        # Получаем турнир
        cur.execute(
            "SELECT sport, status FROM tournaments WHERE id=?", (tournament_id,))
        tournament = cur.fetchone()

        if not tournament:
            logger.info("Турнир не найден")
            return (None, None, None)

        if tournament['status'] == 'finished':
            logger.info("Турнир уже завершён")
            return (None, None, None)

        # Получаем победителя (1 место)
        first_place_id = get_tournament_final_winner(tournament_id)

        # Получаем 2-е место
        second_place_id = get_tournament_second_place(tournament_id)

        # Получаем 3-е место (победитель матча за 3-е место)
        third_place_id = get_third_place_match_winner(tournament_id)

        # Обновляем статус турнира
        cur.execute(
            "UPDATE tournaments SET status='finished' WHERE id=?", (tournament_id,))
        conn.commit()
        replace_tournament_rating_awards(tournament_id)

        # Получаем названия команд для возврата
        cur.execute("SELECT name FROM teams WHERE id=?", (first_place_id,))
        first_row = cur.fetchone() if first_place_id else None
        first_name = first_row['name'] if first_row else None
        cur.execute("SELECT name FROM teams WHERE id=?", (second_place_id,))
        second_row = cur.fetchone() if second_place_id else None
        second_name = second_row['name'] if second_row else None
        cur.execute("SELECT name FROM teams WHERE id=?", (third_place_id,))
        third_row = cur.fetchone() if third_place_id else None
        third_name = third_row['name'] if third_row else None

        logger.info(
            f"🏆 Турнир завершён! 1: {first_name}, 2: {second_name}, 3: {third_name}")

        return (first_place_id, second_place_id, third_place_id)

    except Exception as e:
        conn.rollback()
        logger.info(f"Ошибка завершения турнира: {e}")
        return (None, None, None)
    finally:
        conn.close()



def allow_reapply_excluded_application(app_id: int):
    """Действие админа: вернуть исключённую заявку в pending."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("""
            SELECT a.id, a.tournament_id, a.status AS app_status, t.status AS tournament_status
            FROM tournament_applications a
            JOIN tournaments t ON t.id = a.tournament_id
            WHERE a.id=?
        """, (app_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "reason": "not_found"}

        tournament_id = row['tournament_id']
        if row['tournament_status'] != 'registration':
            conn.rollback()
            return {"ok": False, "reason": "not_registration", "tournament_id": tournament_id}

        if row['app_status'] != 'excluded':
            conn.rollback()
            return {"ok": False, "reason": "not_excluded", "tournament_id": tournament_id, "app_status": row['app_status']}

        cur.execute("""
            UPDATE tournament_applications
            SET status='pending', updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='excluded'
        """, (app_id,))
        if cur.rowcount == 0:
            conn.rollback()
            return {"ok": False, "reason": "already_processed", "tournament_id": tournament_id}

        conn.commit()
        return {"ok": True, "tournament_id": tournament_id}
    except sqlite3.Error:
        conn.rollback()
        return {"ok": False, "reason": "db_error"}
    finally:
        conn.close()


def can_create_third_place_match(tournament_id: int):
    """Проверяет, можно ли создать матч за 3-е место."""
    matches = get_bracket_matches(tournament_id)
    if any(int(m['is_third_place'] or 0) == 1 for m in matches):
        return {"ok": False, "reason": "already_exists"}

    semifinals = get_semifinal_matches(tournament_id)
    if len(semifinals) < 2:
        return {"ok": False, "reason": "not_enough_semifinals"}

    for match in semifinals:
        if match['status'] not in ('completed', 'bye'):
            return {"ok": False, "reason": "semifinals_not_completed"}

    # Локальный импорт, чтобы избежать циклического импорта при загрузке модуля
    from utils.bracket_utils import get_semifinal_losers
    losers = get_semifinal_losers(semifinals)
    if len(losers) != 2:
        return {"ok": False, "reason": "not_enough_losers", "losers": losers}

    return {"ok": True, "losers": losers}


def auto_create_third_place_if_ready(tournament_id: int):
    """Автоматически создаёт матч за 3-е место, когда условия выполнены."""
    check = can_create_third_place_match(tournament_id)
    if not check.get('ok'):
        return {"ok": False, "created": False, "reason": check.get('reason')}

    losers = check['losers']
    create_third_place_bracket_match(tournament_id, losers[0], losers[1])
    return {"ok": True, "created": True, "losers": losers}


def clear_bracket_related_data(tournament_id: int):
    """Удаляет данные старой сетки турнира перед перегенерацией."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")

        bracket_match_ids_query = "SELECT id FROM tournament_brackets WHERE tournament_id=?"

        cur.execute(f"""
            DELETE FROM player_match_stats
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
              AND match_id IN ({bracket_match_ids_query})
        """, (tournament_id,))

        cur.execute(f"""
            DELETE FROM match_map_results
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
              AND match_id IN ({bracket_match_ids_query})
        """, (tournament_id,))

        cur.execute(f"""
            DELETE FROM football_player_stats
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
              AND match_id IN ({bracket_match_ids_query})
        """, (tournament_id,))

        cur.execute(f"""
            DELETE FROM basketball_player_stats
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
              AND match_id IN ({bracket_match_ids_query})
        """, (tournament_id,))

        cur.execute(f"""
            DELETE FROM volleyball_player_stats
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
              AND match_id IN ({bracket_match_ids_query})
        """, (tournament_id,))

        cur.execute(f"""
            DELETE FROM volleyball_set_scores
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
              AND match_id IN ({bracket_match_ids_query})
        """, (tournament_id,))

        cur.execute(f"""
            DELETE FROM bracket_match_technical_participants
            WHERE match_id IN ({bracket_match_ids_query})
        """, (tournament_id,))

        conn.commit()
        return {"ok": True}
    except sqlite3.Error:
        conn.rollback()
        return {"ok": False, "reason": "db_error"}
    finally:
        conn.close()


def _generate_unique_token(table_name: str, length: int = 22):
    conn = get_connection()
    cur = conn.cursor()
    try:
        while True:
            token = secrets.token_urlsafe(length)[:length]
            cur.execute(f"SELECT id FROM {table_name} WHERE invite_token=? LIMIT 1", (token,))
            if not cur.fetchone():
                return token
    finally:
        conn.close()


def set_team_invite_join_mode(team_id: int, mode: str):
    """Устанавливает режим ссылки команды: request или direct."""
    normalized = 'direct' if (mode or '').strip().lower() == 'direct' else 'request'
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET invite_join_mode=? WHERE id=?", (normalized, team_id))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def get_team_invite_join_mode(team_id: int):
    """Возвращает режим ссылки команды: request (по умолчанию) или direct."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT invite_join_mode FROM teams WHERE id=?", (team_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return 'request'
    value = (row['invite_join_mode'] or '').strip().lower()
    return 'direct' if value == 'direct' else 'request'



def set_team_invite_enabled(team_id: int, enabled: bool):
    """Включает или отключает работу инвайт-ссылки команды без удаления токена."""
    value = 1 if enabled else 0
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET invite_enabled=? WHERE id=?", (value, team_id))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def is_team_invite_enabled(team_id: int):
    """Возвращает статус инвайт-ссылки команды (по умолчанию включена)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT invite_enabled FROM teams WHERE id=?", (team_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    value = row['invite_enabled']
    if value is None:
        return True
    return int(value) == 1

def ensure_team_invite_token(team_id: int, regenerate: bool = False):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT invite_token FROM teams WHERE id=?", (team_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    current = (row['invite_token'] or '').strip()
    if current and not regenerate:
        conn.close()
        return current

    token = _generate_unique_token('teams')
    cur.execute("UPDATE teams SET invite_token=? WHERE id=?", (token, team_id))
    conn.commit()
    conn.close()
    return token


def clear_team_invite_token(team_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET invite_token=NULL WHERE id=?", (team_id,))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0


def get_team_by_invite_token(token: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM teams WHERE invite_token=? AND COALESCE(invite_enabled, 1)=1", ((token or '').strip(),))
    row = cur.fetchone()
    conn.close()
    return row


def ensure_tournament_invite_token(tournament_id: int, regenerate: bool = False):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT invite_token FROM tournaments WHERE id=?", (tournament_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    current = (row['invite_token'] or '').strip()
    if current and not regenerate:
        conn.close()
        return current

    token = _generate_unique_token('tournaments')
    cur.execute("UPDATE tournaments SET invite_token=? WHERE id=?", (token, tournament_id))
    conn.commit()
    conn.close()
    return token


def get_tournament_by_invite_token(token: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tournaments WHERE invite_token=?", ((token or '').strip(),))
    row = cur.fetchone()
    conn.close()
    return row


def get_active_user_telegram_ids():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users WHERE COALESCE(is_banned, 0)=0")
    rows = [r['telegram_id'] for r in cur.fetchall()]
    conn.close()
    return rows
