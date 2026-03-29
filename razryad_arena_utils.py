import json
import os
import sqlite3
from database import get_connection
import datetime
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import logging

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


def add_tournament_application(tournament_id, team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tournament_applications (tournament_id, team_id, status, applied_at, updated_at)
        VALUES (?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (tournament_id, team_id))
    conn.commit()
    conn.close()


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
            "UPDATE tournament_applications SET status='approved', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (app_id,)
        )
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
            SET status='rejected', updated_at=CURRENT_TIMESTAMP
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
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT points FROM ratings WHERE team_id=? AND sport=? AND month=?",
                (team_id, sport, month))
    existing = cur.fetchone()
    if existing:
        cur.execute("UPDATE ratings SET points = points + ? WHERE team_id=? AND sport=? AND month=?",
                    (points_change, team_id, sport, month))
    else:
        cur.execute("INSERT INTO ratings (team_id, sport, month, points) VALUES (?, ?, ?, ?)",
                    (team_id, sport, month, points_change))
    conn.commit()
    conn.close()

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
    cur = conn.cursor()
    cur.execute("SELECT points FROM ratings WHERE team_id=? AND sport=? AND month=?",
                (team_id, sport, month))
    existing = cur.fetchone()
    if existing:
        cur.execute("UPDATE ratings SET points = points + ? WHERE team_id=? AND sport=? AND month=?",
                    (points_change, team_id, sport, month))
    else:
        cur.execute("INSERT INTO ratings (team_id, sport, month, points) VALUES (?, ?, ?, ?)",
                    (team_id, sport, month, points_change))


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
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO team_invites (team_id, user_id) VALUES (?, ?)", (team_id, user_id))
    conn.commit()
    conn.close()


def accept_invite(team_id, user_id):
    """Принять приглашение: добавить в команду и обновить статус"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO team_members (team_id, user_id) VALUES (?, ?)", (team_id, user_id))
    cur.execute(
        "UPDATE team_invites SET status='accepted' WHERE team_id=? AND user_id=?", (team_id, user_id))
    conn.commit()
    conn.close()


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
    cur.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM team_invites WHERE team_id=?", (team_id,))
    cur.execute(
        "DELETE FROM tournament_applications WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM matches WHERE team1_id=? OR team2_id=?",
                (team_id, team_id))
    cur.execute("DELETE FROM ratings WHERE team_id=?", (team_id,))
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
    """Возвращает команды с их текущими очками для указанного спорта (за текущий месяц)"""
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
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO team_invites (team_id, user_id, status, type)
        VALUES (?, ?, 'pending', 'request')
    """, (team_id, user_id))
    conn.commit()
    conn.close()


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

        if is_changed:
            cur.execute("""
                UPDATE tournament_brackets
                SET
                    scheduled_at_utc=?,
                    location=?,
                    schedule_updated_at=CURRENT_TIMESTAMP,
                    reminder_sent_at=NULL
                WHERE id=?
            """, (clean_time, clean_location, match_id))
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
                AND round_number < 5
          )
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
        INSERT INTO tournament_brackets 
        (tournament_id, round_number, round_name, match_number, team1_id, team2_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (tournament_id, 5, "Матч за 3-е место", 1, team1_id, team2_id, 'pending'))
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

    import urllib.request
    import json

    steam_api_key = os.getenv("STEAM_API_KEY")
    if not steam_api_key:
        logger.info("Steam API key is not configured. Set STEAM_API_KEY in environment.")
        return None

    # Извлекаем SteamID64 из ссылки или используем как есть
    steam_id64 = None

    # Проверяем это ли просто числовой ID
    if steam_url.isdigit() and len(steam_url) >= 17:
        steam_id64 = steam_url[:17]  # Берём первые 17 цифр
    elif '/profiles/' in steam_url:
        # Числовой URL
        parts = steam_url.split('/profiles/')
        if len(parts) > 1:
            steam_id64 = parts[1].split('/')[0]
    elif '/id/' in steam_url:
        # Кастомный URL - нужно resolve через API
        custom_name = steam_url.split('/id/')[1].split('/')[0]
        # Делаем запрос к ResolveVanityURL
        try:
            resolve_url = (
                "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/"
                f"?key={steam_api_key}&vanityurl={custom_name}"
            )
            with urllib.request.urlopen(resolve_url, timeout=5) as response:
                data = json.loads(response.read().decode())
                if data.get('response', {}).get('success') == 1:
                    steam_id64 = data['response'].get('steamid')
        except Exception as e:
            logger.info(f"Ошибка ResolveVanityURL: {e}")
            return None

    if not steam_id64 or not steam_id64.isdigit():
        return None

    # Получаем информацию о игроке через GetPlayerSummaries
    try:
        url = (
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
            f"?key={steam_api_key}&steamids={steam_id64}"
        )
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode())
            players = data.get('response', {}).get('players', [])
            if players:
                return players[0].get('personaname')  # Имя профиля
    except Exception as e:
        logger.info(f"Ошибка GetPlayerSummaries: {e}")

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
        user_matches AS (
            SELECT
                resolved_source AS match_source,
                match_id,
                MAX(created_at) AS last_stat_at
            FROM stats_rows
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
    """, (internal_user_id, limit, offset))
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
        SELECT COUNT(*)
        FROM (
            SELECT
                {_resolve_match_source_case("pms")} AS resolved_source,
                pms.match_id
            FROM player_match_stats pms
            WHERE pms.user_id = ?
            GROUP BY resolved_source, pms.match_id
        )
    """, (internal_user_id,))
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
        SELECT COUNT(*)
        FROM (
            SELECT COALESCE(NULLIF(TRIM(match_source), ''), 'legacy') AS source, match_id
            FROM {table}
            WHERE user_id=?
            GROUP BY source, match_id
        )
    """, (internal_user_id,))
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
    """, (internal_user_id, limit, offset))
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
            WHERE tournament_id=? AND round_number < 5
        )
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
            WHERE tournament_id=? AND round_number < 5
        )
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
    """Возвращает ID победителя матча за 3-е место (round_number=5)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT winner_id FROM tournament_brackets
        WHERE tournament_id=? AND round_number=5
        AND status='completed'
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

        sport = tournament['sport']
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        # Получаем победителя (1 место)
        first_place_id = get_tournament_final_winner(tournament_id)

        # Получаем 2-е место
        second_place_id = get_tournament_second_place(tournament_id)

        # Получаем 3-е место (победитель матча за 3-е место)
        third_place_id = get_third_place_match_winner(tournament_id)

        # Начисляем очки
        if first_place_id:
            update_team_rating_same_conn(
                conn, first_place_id, sport, month, 20)
            logger.info(f"✅ 1 место: команда {first_place_id} получает 20 очков")

        if second_place_id:
            update_team_rating_same_conn(
                conn, second_place_id, sport, month, 15)
            logger.info(f"✅ 2 место: команда {second_place_id} получает 15 очков")

        if third_place_id:
            update_team_rating_same_conn(
                conn, third_place_id, sport, month, 10)
            logger.info(f"✅ 3 место: команда {third_place_id} получает 10 очков")

        # Обновляем статус турнира
        cur.execute(
            "UPDATE tournaments SET status='finished' WHERE id=?", (tournament_id,))

        conn.commit()

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
