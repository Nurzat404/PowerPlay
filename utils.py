import json
import sqlite3
from database import get_connection
import datetime
from datetime import datetime, timedelta

RUSSIAN_MONTHS = {
    'янв.': 1, 'февр.': 2, 'марта': 3, 'апр.': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'авг.': 8, 'сент.': 9, 'окт.': 10, 'нояб.': 11, 'дек.': 12
}


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
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM tournaments WHERE sport=? AND status='registration'", (sport,))
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
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tournament_applications SET status='approved', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (app_id,)
    )
    conn.commit()
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
        SELECT id, telegram_id, first_name, last_name, username, email, city, role, is_banned
        FROM users
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    users = cur.fetchall()
    conn.close()
    return users


def search_users(query):
    """Поиск пользователей по имени, username или telegram_id"""
    conn = get_connection()
    cur = conn.cursor()
    pattern = f"%{query}%"
    cur.execute("""
        SELECT id, telegram_id, first_name, last_name, username, email, city, role, is_banned
        FROM users
        WHERE first_name LIKE ? OR last_name LIKE ? OR username LIKE ? OR telegram_id LIKE ?
        ORDER BY id DESC
        LIMIT 20
    """, (pattern, pattern, pattern, pattern))
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
            now = datetime.utcnow()  # <-- исправлено
            delta = now - rejected_time
            if delta >= timedelta(hours=1):
                return True, None
            else:
                minutes_left = 60 - int(delta.total_seconds() // 60)
                return False, minutes_left
        except Exception as e:
            print(f"Ошибка парсинга updated_at: {e}")
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
    month = datetime.now().strftime("%Y-%m")
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
    month = datetime.now().strftime("%Y-%m")
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
# ---------- Функции для работы с лимитами команды ----------


def get_team_members_count(team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM team_members WHERE team_id=?", (team_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_team_max_members(team_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT max_members FROM teams WHERE id=?", (team_id,))
    row = cur.fetchone()
    conn.close()
    return row['max_members'] if row else 5


def update_team_max_members(team_id, new_max):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET max_members=? WHERE id=?",
                (new_max, team_id))
    conn.commit()
    conn.close()


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
        now = datetime.now()
        delta = now - rejected_time
        if delta >= timedelta(hours=1):
            return True, None
        else:
            minutes_left = 60 - int(delta.total_seconds() // 60)
            return False, minutes_left
    return True, None  # если нет времени (старые записи), разрешаем
