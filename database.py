import sqlite3
import time
from pathlib import Path

# Определяем папку для базы данных
DATA_DIR = Path('/app/data')
if not DATA_DIR.exists():
    DATA_DIR = Path('.')
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'powerplay.db'


def get_connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def init_db():
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            conn = get_connection()
            with conn:
                cur = conn.cursor()

                # Пользователи
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        telegram_id INTEGER UNIQUE NOT NULL,
                        first_name TEXT,
                        last_name TEXT,
                        username TEXT,
                        email TEXT,
                        city TEXT,
                        favorite_sports TEXT,
                        role TEXT DEFAULT 'player',
                        is_banned INTEGER DEFAULT 0,
                        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Команды
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS teams (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        sport TEXT NOT NULL,
                        city TEXT,
                        captain_id INTEGER NOT NULL REFERENCES users(id),
                        is_open_for_requests INTEGER DEFAULT 1,
                        notify_on_requests INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Состав команды
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS team_members (
                        team_id INTEGER REFERENCES teams(id),
                        user_id INTEGER REFERENCES users(id),
                        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (team_id, user_id)
                    )
                """)

                # Приглашения в команду (теперь с полем type)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS team_invites (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        team_id INTEGER REFERENCES teams(id),
                        user_id INTEGER REFERENCES users(id),
                        status TEXT DEFAULT 'pending',  -- pending, accepted, rejected
                        type TEXT DEFAULT 'invite',     -- invite, request
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(team_id, user_id)
                    )
                """)

                # Турниры
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournaments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        sport TEXT NOT NULL,
                        city TEXT,
                        start_date TEXT,
                        end_date TEXT,
                        max_teams INTEGER,
                        description TEXT,
                        status TEXT DEFAULT 'registration',
                        created_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Заявки на турниры
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_applications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tournament_id INTEGER REFERENCES tournaments(id),
                        team_id INTEGER REFERENCES teams(id),
                        status TEXT DEFAULT 'pending',
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Матчи
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS matches (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tournament_id INTEGER REFERENCES tournaments(id),
                        team1_id INTEGER REFERENCES teams(id),
                        team2_id INTEGER REFERENCES teams(id),
                        match_date TEXT,
                        location TEXT,
                        score1 INTEGER DEFAULT 0,
                        score2 INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'scheduled',
                        confirmed_by_captain1 BOOLEAN DEFAULT 0,
                        confirmed_by_captain2 BOOLEAN DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Статистика игроков в матче
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS player_match_stats (
                        match_id INTEGER REFERENCES matches(id),
                        user_id INTEGER REFERENCES users(id),
                        team_id INTEGER REFERENCES teams(id),
                        goals INTEGER DEFAULT 0,
                        PRIMARY KEY (match_id, user_id)
                    )
                """)

                # Рейтинг команд
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ratings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        team_id INTEGER REFERENCES teams(id),
                        sport TEXT NOT NULL,
                        month TEXT NOT NULL,
                        points INTEGER DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(team_id, sport, month)
                    )
                """)

                # Таблица видов спорта
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        display_name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Добавляем начальные виды спорта
                sports_list = [
                    ("CS2", "CS2"),
                    ("Brawl Stars", "Brawl Stars"),
                    ("Basketball", "Basketball")
                ]
                for name, display in sports_list:
                    cur.execute(
                        "INSERT OR IGNORE INTO sports (name, display_name) VALUES (?, ?)", (name, display))

                # Проверяем наличие полей в старых таблицах (для совместимости)
                cur.execute("PRAGMA table_info(teams)")
                columns = [col[1] for col in cur.fetchall()]
                if 'is_open_for_requests' not in columns:
                    cur.execute(
                        "ALTER TABLE teams ADD COLUMN is_open_for_requests INTEGER DEFAULT 1")
                if 'notify_on_requests' not in columns:
                    cur.execute(
                        "ALTER TABLE teams ADD COLUMN notify_on_requests INTEGER DEFAULT 1")

                cur.execute("PRAGMA table_info(team_invites)")
                columns = [col[1] for col in cur.fetchall()]
                if 'type' not in columns:
                    cur.execute(
                        "ALTER TABLE team_invites ADD COLUMN type TEXT DEFAULT 'invite'")

            conn.close()
            print("База данных успешно инициализирована.")
            return
        except sqlite3.OperationalError as e:
            print(f"Попытка {attempt+1}/{max_attempts} не удалась: {e}")
            time.sleep(1)
    raise Exception(
        "Не удалось инициализировать базу данных после нескольких попыток.")


init_db()
