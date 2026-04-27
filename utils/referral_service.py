from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from database import get_connection
from utils.rating_rules import normalize_sport_key
from utils.rating_service import apply_referral_rating_bonus, rebuild_entity_ratings

REFERRAL_LINK_STATUS_ACTIVE = "active"
REFERRAL_LINK_STATUS_DISABLED = "disabled"

REFERRAL_EVENT_OWNER_REGISTRATION = "owner_registration_bonus"
REFERRAL_EVENT_OWNER_PARTICIPATION = "owner_participation_bonus"
REFERRAL_EVENT_REFERRED_FIRST_MATCH = "referred_first_match_bonus"


def _dict_rows(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _normalize_sport_key(value: str | None) -> str:
    return normalize_sport_key(value)


def _get_sport_display_name(name: str) -> str:
    normalized = _normalize_sport_key(name)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT display_name FROM sports WHERE name=? LIMIT 1", (normalized,))
    row = cur.fetchone()
    conn.close()
    return (row["display_name"] if row and row["display_name"] else normalized) or normalized


def build_referral_start_payload(token: str) -> str:
    return f"ref_{token}"


def build_referral_url(bot_username: str, token: str) -> str:
    username = (bot_username or "").strip().lstrip("@")
    return f"https://t.me/{username}?start={build_referral_start_payload(token)}"


def _generate_referral_token() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")


def create_referral_link(owner_user_id: int, sport_key: str, title: str) -> dict[str, Any]:
    normalized_sport = _normalize_sport_key(sport_key)
    normalized_title = (title or "").strip()
    if not normalized_sport:
        raise ValueError("sport_required")
    if not normalized_title:
        raise ValueError("title_required")

    conn = get_connection()
    cur = conn.cursor()
    token = _generate_referral_token()
    while True:
        try:
            cur.execute(
                """
                INSERT INTO referral_links (
                    owner_user_id, sport_key, title, token, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (int(owner_user_id), normalized_sport, normalized_title[:80], token, REFERRAL_LINK_STATUS_ACTIVE),
            )
            conn.commit()
            break
        except sqlite3.IntegrityError:
            token = _generate_referral_token()

    link_id = int(cur.lastrowid)
    cur.execute("SELECT * FROM referral_links WHERE id=?", (link_id,))
    row = dict(cur.fetchone())
    conn.close()
    return row


def list_referral_links(owner_user_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM referral_links
        WHERE owner_user_id=?
        ORDER BY created_at DESC, id DESC
        """,
        (int(owner_user_id),),
    )
    rows = _dict_rows(cur.fetchall())
    conn.close()
    return rows


def get_referral_link(link_id: int, owner_user_id: int | None = None) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    if owner_user_id is None:
        cur.execute("SELECT * FROM referral_links WHERE id=?", (int(link_id),))
    else:
        cur.execute("SELECT * FROM referral_links WHERE id=? AND owner_user_id=?", (int(link_id), int(owner_user_id)))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def disable_referral_link(link_id: int, owner_user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE referral_links
        SET status=?, disabled_at=CURRENT_TIMESTAMP
        WHERE id=? AND owner_user_id=? AND status=?
        """,
        (
            REFERRAL_LINK_STATUS_DISABLED,
            int(link_id),
            int(owner_user_id),
            REFERRAL_LINK_STATUS_ACTIVE,
        ),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_referral_link_by_token(token: str) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM referral_links WHERE token=? LIMIT 1", ((token or "").strip(),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_referral_attribution(referred_user_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.*, l.title AS link_title, l.token AS link_token, l.status AS link_status
        FROM referral_attributions a
        JOIN referral_links l ON l.id = a.referral_link_id
        WHERE a.referred_user_id=?
        LIMIT 1
        """,
        (int(referred_user_id),),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def attach_user_to_referral(
    *,
    referred_user_id: int,
    referral_token: str,
    allow_for_unfinished_profile: bool = True,
) -> dict[str, Any]:
    link = get_referral_link_by_token(referral_token)
    if not link:
        return {"ok": False, "reason": "invalid"}
    if link["status"] != REFERRAL_LINK_STATUS_ACTIVE:
        return {"ok": False, "reason": "disabled"}
    if int(link["owner_user_id"]) == int(referred_user_id):
        return {"ok": False, "reason": "self_referral"}

    existing = get_referral_attribution(referred_user_id)
    if existing:
        return {"ok": False, "reason": "already_attributed", "attribution": existing}

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT email FROM users WHERE id=? LIMIT 1", (int(referred_user_id),))
    user = cur.fetchone()
    if not user:
        conn.close()
        return {"ok": False, "reason": "user_not_found"}
    if not allow_for_unfinished_profile and user["email"]:
        conn.close()
        return {"ok": False, "reason": "existing_registered_user"}

    try:
        cur.execute(
            """
            INSERT INTO referral_attributions (
                referred_user_id, referral_link_id, owner_user_id, sport_key, created_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                int(referred_user_id),
                int(link["id"]),
                int(link["owner_user_id"]),
                _normalize_sport_key(link["sport_key"]),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return {"ok": False, "reason": "already_attributed"}
    conn.close()
    return {"ok": True, "link": link}


def _fetch_event(cur: sqlite3.Cursor, event_type: str, referred_user_id: int) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT *
        FROM referral_events
        WHERE event_type=? AND referred_user_id=?
        LIMIT 1
        """,
        (event_type, int(referred_user_id)),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _create_referral_event(
    cur: sqlite3.Cursor,
    *,
    event_type: str,
    referred_user_id: int,
    owner_user_id: int,
    sport_key: str,
    tournament_id: int | None,
    match_id: int | None,
    referral_link_id: int,
    owner_points: int,
    referred_points: int,
) -> int | None:
    if _fetch_event(cur, event_type, referred_user_id):
        return None
    cur.execute(
        """
        INSERT INTO referral_events (
            event_type,
            referred_user_id,
            owner_user_id,
            sport_key,
            tournament_id,
            match_id,
            referral_link_id,
            owner_points,
            referred_points,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            event_type,
            int(referred_user_id),
            int(owner_user_id),
            _normalize_sport_key(sport_key),
            tournament_id,
            match_id,
            int(referral_link_id),
            int(owner_points),
            int(referred_points),
        ),
    )
    return int(cur.lastrowid)


def _award_referral_event_points(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    owner_user_id: int,
    referred_user_id: int,
    sport_key: str,
    owner_points: int,
    referred_points: int,
    owner_reason: str,
    referred_reason: str,
) -> None:
    if owner_points > 0:
        apply_referral_rating_bonus(
            entity_id=int(owner_user_id),
            sport_key=sport_key,
            delta=int(owner_points),
            reason=owner_reason,
            source_id=int(event_id),
            conn=conn,
            rebuild=False,
        )
    if referred_points > 0:
        apply_referral_rating_bonus(
            entity_id=int(referred_user_id),
            sport_key=sport_key,
            delta=int(referred_points),
            reason=referred_reason,
            source_id=int(event_id),
            conn=conn,
            rebuild=False,
        )


def process_referral_application_approval(application_id: int) -> dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id, a.tournament_id, a.team_id, a.status, t.sport
        FROM tournament_applications a
        JOIN tournaments t ON t.id = a.tournament_id
        WHERE a.id=?
        LIMIT 1
        """,
        (int(application_id),),
    )
    app = cur.fetchone()
    if not app or app["status"] != "approved":
        conn.close()
        return {"ok": False, "reason": "not_approved"}

    sport_key = _normalize_sport_key(app["sport"])
    cur.execute(
        """
        SELECT DISTINCT user_id
        FROM tournament_team_rosters
        WHERE tournament_id=? AND team_id=? AND status='active'
        """,
        (int(app["tournament_id"]), int(app["team_id"])),
    )
    user_ids = [int(row["user_id"]) for row in cur.fetchall()]
    if not user_ids:
        cur.execute(
            """
            SELECT DISTINCT user_id
            FROM team_members
            WHERE team_id=?
            ORDER BY user_id
            """,
            (int(app["team_id"]),),
        )
        user_ids = [int(row["user_id"]) for row in cur.fetchall()]
    if not user_ids:
        conn.close()
        return {"ok": True, "processed_users": 0, "awards": 0, "sport_key": sport_key}

    awards = 0
    for user_id in user_ids:
        cur.execute(
            """
            SELECT a.*, l.status AS link_status
            FROM referral_attributions a
            JOIN referral_links l ON l.id = a.referral_link_id
            WHERE a.referred_user_id=?
            LIMIT 1
            """,
            (user_id,),
        )
        attribution = cur.fetchone()
        if not attribution:
            continue
        attribution = dict(attribution)
        if _normalize_sport_key(attribution["sport_key"]) != sport_key:
            continue
        event_id = _create_referral_event(
            cur,
            event_type=REFERRAL_EVENT_OWNER_REGISTRATION,
            referred_user_id=user_id,
            owner_user_id=int(attribution["owner_user_id"]),
            sport_key=sport_key,
            tournament_id=int(app["tournament_id"]),
            match_id=None,
            referral_link_id=int(attribution["referral_link_id"]),
            owner_points=2,
            referred_points=0,
        )
        if not event_id:
            continue
        _award_referral_event_points(
            conn,
            event_id=event_id,
            owner_user_id=int(attribution["owner_user_id"]),
            referred_user_id=user_id,
            sport_key=sport_key,
            owner_points=2,
            referred_points=0,
            owner_reason="Реферальный бонус за одобренную заявку в турнир",
            referred_reason="",
        )
        awards += 1

    if awards:
        rebuild_entity_ratings(conn)
        conn.commit()
    conn.close()
    return {"ok": True, "processed_users": len(user_ids), "awards": awards, "sport_key": sport_key}


def _get_match_context(match_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT b.id, b.tournament_id, b.status, COALESCE(b.result_type, 'regular') AS result_type, t.sport
        FROM tournament_brackets b
        JOIN tournaments t ON t.id = b.tournament_id
        WHERE b.id=?
        LIMIT 1
        """,
        (int(match_id),),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _get_match_participant_user_ids(cur: sqlite3.Cursor, match_id: int, sport_key: str) -> list[int]:
    normalized_sport = _normalize_sport_key(sport_key)
    table_name = {
        "CS2": "player_match_stats",
        "Football": "football_player_stats",
        "Basketball": "basketball_player_stats",
        "Volleyball": "volleyball_player_stats",
    }.get(normalized_sport)
    if not table_name:
        return []
    cur.execute(
        f"""
        SELECT DISTINCT user_id
        FROM {table_name}
        WHERE COALESCE(match_source, 'bracket')='bracket'
          AND match_id=?
          AND user_id IS NOT NULL
        ORDER BY user_id
        """,
        (int(match_id),),
    )
    return [int(row["user_id"]) for row in cur.fetchall()]


def process_referral_match_participation(match_id: int) -> dict[str, Any]:
    context = _get_match_context(match_id)
    if not context:
        return {"ok": False, "reason": "match_not_found"}
    if context["status"] != "completed":
        return {"ok": False, "reason": "match_not_completed"}
    if (context["result_type"] or "regular") == "technical":
        return {"ok": True, "awards": 0, "sport_key": _normalize_sport_key(context["sport"])}

    sport_key = _normalize_sport_key(context["sport"])
    conn = get_connection()
    cur = conn.cursor()
    user_ids = _get_match_participant_user_ids(cur, int(match_id), sport_key)
    if not user_ids:
        conn.close()
        return {"ok": True, "awards": 0, "sport_key": sport_key}

    awards = 0
    for user_id in user_ids:
        cur.execute(
            """
            SELECT a.*, l.status AS link_status
            FROM referral_attributions a
            JOIN referral_links l ON l.id = a.referral_link_id
            WHERE a.referred_user_id=?
            LIMIT 1
            """,
            (user_id,),
        )
        attribution = cur.fetchone()
        if not attribution:
            continue
        attribution = dict(attribution)
        if _normalize_sport_key(attribution["sport_key"]) != sport_key:
            continue

        owner_event_id = _create_referral_event(
            cur,
            event_type=REFERRAL_EVENT_OWNER_PARTICIPATION,
            referred_user_id=user_id,
            owner_user_id=int(attribution["owner_user_id"]),
            sport_key=sport_key,
            tournament_id=int(context["tournament_id"]),
            match_id=int(match_id),
            referral_link_id=int(attribution["referral_link_id"]),
            owner_points=3,
            referred_points=0,
        )
        if owner_event_id:
            _award_referral_event_points(
                conn,
                event_id=owner_event_id,
                owner_user_id=int(attribution["owner_user_id"]),
                referred_user_id=user_id,
                sport_key=sport_key,
                owner_points=3,
                referred_points=0,
                owner_reason="Реферальный бонус за первый сыгранный матч реферала",
                referred_reason="",
            )
            awards += 1

        referred_event_id = _create_referral_event(
            cur,
            event_type=REFERRAL_EVENT_REFERRED_FIRST_MATCH,
            referred_user_id=user_id,
            owner_user_id=int(attribution["owner_user_id"]),
            sport_key=sport_key,
            tournament_id=int(context["tournament_id"]),
            match_id=int(match_id),
            referral_link_id=int(attribution["referral_link_id"]),
            owner_points=0,
            referred_points=2,
        )
        if referred_event_id:
            _award_referral_event_points(
                conn,
                event_id=referred_event_id,
                owner_user_id=int(attribution["owner_user_id"]),
                referred_user_id=user_id,
                sport_key=sport_key,
                owner_points=0,
                referred_points=2,
                owner_reason="",
                referred_reason="Бонус за первый сыгранный матч по реферальной ссылке",
            )
            awards += 1

    if awards:
        rebuild_entity_ratings(conn)
        conn.commit()
    conn.close()
    return {"ok": True, "awards": awards, "sport_key": sport_key}


def get_referral_link_stats(link_id: int) -> dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            COUNT(DISTINCT a.referred_user_id) AS attributed_users,
            COALESCE(SUM(CASE WHEN e.event_type=? THEN 1 ELSE 0 END), 0) AS registration_bonus_count,
            COALESCE(SUM(CASE WHEN e.event_type=? THEN 1 ELSE 0 END), 0) AS participation_bonus_count,
            COALESCE(SUM(COALESCE(e.owner_points, 0)), 0) AS owner_points_total,
            COALESCE(SUM(COALESCE(e.referred_points, 0)), 0) AS referred_points_total
        FROM referral_links l
        LEFT JOIN referral_attributions a ON a.referral_link_id = l.id
        LEFT JOIN referral_events e ON e.referral_link_id = l.id
        WHERE l.id=?
        """,
        (
            REFERRAL_EVENT_OWNER_REGISTRATION,
            REFERRAL_EVENT_OWNER_PARTICIPATION,
            int(link_id),
        ),
    )
    row = dict(cur.fetchone() or {})
    conn.close()
    return {
        "attributed_users": int(row.get("attributed_users") or 0),
        "registration_bonus_count": int(row.get("registration_bonus_count") or 0),
        "participation_bonus_count": int(row.get("participation_bonus_count") or 0),
        "owner_points_total": int(row.get("owner_points_total") or 0),
        "referred_points_total": int(row.get("referred_points_total") or 0),
    }


def list_referral_link_referees(link_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            a.referred_user_id,
            u.first_name,
            u.username,
            MAX(CASE WHEN e.event_type=? THEN 1 ELSE 0 END) AS registration_done,
            MAX(CASE WHEN e.event_type=? THEN 1 ELSE 0 END) AS participation_done
        FROM referral_attributions a
        JOIN users u ON u.id = a.referred_user_id
        LEFT JOIN referral_events e ON e.referred_user_id = a.referred_user_id
        WHERE a.referral_link_id=?
        GROUP BY a.referred_user_id, u.first_name, u.username
        ORDER BY a.created_at DESC, a.referred_user_id DESC
        """,
        (
            REFERRAL_EVENT_OWNER_REGISTRATION,
            REFERRAL_EVENT_OWNER_PARTICIPATION,
            int(link_id),
        ),
    )
    rows = _dict_rows(cur.fetchall())
    conn.close()
    for row in rows:
        if int(row.get("participation_done") or 0) == 1:
            row["status_label"] = "сыграл первый матч"
        elif int(row.get("registration_done") or 0) == 1:
            row["status_label"] = "одобрен в турнир"
        else:
            row["status_label"] = "привязан"
    return rows


def render_referral_link_summary(link: dict[str, Any]) -> str:
    stats = get_referral_link_stats(int(link["id"]))
    status = "Активна" if link["status"] == REFERRAL_LINK_STATUS_ACTIVE else "Отключена"
    return (
        f"{link['title']} · {_get_sport_display_name(link['sport_key'])}\n"
        f"Статус: {status}\n"
        f"Привязано: {stats['attributed_users']} · +2: {stats['registration_bonus_count']} · +3: {stats['participation_bonus_count']}"
    )


__all__ = [
    "REFERRAL_EVENT_OWNER_PARTICIPATION",
    "REFERRAL_EVENT_OWNER_REGISTRATION",
    "REFERRAL_EVENT_REFERRED_FIRST_MATCH",
    "REFERRAL_LINK_STATUS_ACTIVE",
    "REFERRAL_LINK_STATUS_DISABLED",
    "attach_user_to_referral",
    "build_referral_start_payload",
    "build_referral_url",
    "create_referral_link",
    "disable_referral_link",
    "get_referral_attribution",
    "get_referral_link",
    "get_referral_link_by_token",
    "get_referral_link_stats",
    "list_referral_link_referees",
    "list_referral_links",
    "process_referral_application_approval",
    "process_referral_match_participation",
    "render_referral_link_summary",
]
