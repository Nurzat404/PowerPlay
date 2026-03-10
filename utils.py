import json
import sqlite3
from database import get_connection
import datetime
from datetime import datetime


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
        INSERT INTO tournament_applications (tournament_id, team_id, status)
        VALUES (?, ?, 'pending')
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


def approve_application(app_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tournament_applications SET status='approved' WHERE id=?", (app_id,))
    conn.commit()
    conn.close()


def reject_application(app_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tournament_applications SET status='rejected' WHERE id=?", (app_id,))
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

# ---------- Новые функции для управления пользователями ----------


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
# Добавьте эти функции в конец файла utils.py


def get_team_application(tournament_id, team_id):
    """Возвращает статус заявки команды на турнир или None, если заявки нет."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT status FROM tournament_applications WHERE tournament_id=? AND team_id=?",
                (tournament_id, team_id))
    app = cur.fetchone()
    conn.close()
    return app['status'] if app else None


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
