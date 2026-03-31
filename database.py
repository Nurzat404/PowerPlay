import sqlite3
import time
import json
import logging
from pathlib import Path
import shutil  # добавили библиотеку для копирования файлов

PRIMARY_DB_NAME = "razryad_arena.db"
PRIMARY_SEED_NAME = "razryad_arena_seed.db"

logger = logging.getLogger(__name__)


# Определяем папку для базы данных
DATA_DIR = Path('/app/data')
if not DATA_DIR.exists():
    DATA_DIR = Path('.')

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / PRIMARY_DB_NAME
SEED_PATH = Path("db_seed") / PRIMARY_SEED_NAME

# ВАЖНО: если мы на сервере (папка /app/data существует)
# и основной базы ещё нет, то копируем seed-файл
if DATA_DIR == Path('/app/data') and not DB_PATH.exists():
    if SEED_PATH.exists():
        shutil.copy(SEED_PATH, DB_PATH)
        logger.info(f"✅ База скопирована из {SEED_PATH} в {DB_PATH}")
    else:
        logger.info(f"❌ Seed-файл {SEED_PATH} не найден, будет создана новая база.")


def get_connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _purge_removed_sport(cur: sqlite3.Cursor, sport_name: str):
    """Удаляет вид спорта и все связанные данные."""
    cur.execute(
        """
        DELETE FROM player_match_stats
        WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
          AND match_id IN (
              SELECT b.id
              FROM tournament_brackets b
              JOIN tournaments tt ON tt.id = b.tournament_id
              WHERE tt.sport=?
          )
        """,
        (sport_name,),
    )
    cur.execute(
        """
        DELETE FROM match_map_results
        WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'bracket')='bracket'
          AND match_id IN (
              SELECT b.id
              FROM tournament_brackets b
              JOIN tournaments tt ON tt.id = b.tournament_id
              WHERE tt.sport=?
          )
        """,
        (sport_name,),
    )

    for table in ("football_player_stats", "basketball_player_stats", "volleyball_player_stats"):
        cur.execute(
            f"""
            DELETE FROM {table}
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'legacy')='bracket'
              AND match_id IN (
                  SELECT b.id
                  FROM tournament_brackets b
                  JOIN tournaments tt ON tt.id = b.tournament_id
                  WHERE tt.sport=?
              )
            """,
            (sport_name,),
        )
        cur.execute(
            f"""
            DELETE FROM {table}
            WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'legacy')='legacy'
              AND match_id IN (
                  SELECT m.id
                  FROM matches m
                  LEFT JOIN tournaments tt ON tt.id = m.tournament_id
                  LEFT JOIN teams t1 ON t1.id = m.team1_id
                  LEFT JOIN teams t2 ON t2.id = m.team2_id
                  WHERE tt.sport=? OR t1.sport=? OR t2.sport=?
              )
            """,
            (sport_name, sport_name, sport_name),
        )

    cur.execute(
        """
        DELETE FROM volleyball_set_scores
        WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'legacy')='bracket'
          AND match_id IN (
              SELECT b.id
              FROM tournament_brackets b
              JOIN tournaments tt ON tt.id = b.tournament_id
              WHERE tt.sport=?
          )
        """,
        (sport_name,),
    )
    cur.execute(
        """
        DELETE FROM volleyball_set_scores
        WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'legacy')='legacy'
          AND match_id IN (
              SELECT m.id
              FROM matches m
              LEFT JOIN tournaments tt ON tt.id = m.tournament_id
              LEFT JOIN teams t1 ON t1.id = m.team1_id
              LEFT JOIN teams t2 ON t2.id = m.team2_id
              WHERE tt.sport=? OR t1.sport=? OR t2.sport=?
          )
        """,
        (sport_name, sport_name, sport_name),
    )

    cur.execute(
        """
        DELETE FROM player_match_stats
        WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'legacy')='legacy'
          AND match_id IN (
              SELECT m.id
              FROM matches m
              LEFT JOIN tournaments tt ON tt.id = m.tournament_id
              LEFT JOIN teams t1 ON t1.id = m.team1_id
              LEFT JOIN teams t2 ON t2.id = m.team2_id
              WHERE tt.sport=? OR t1.sport=? OR t2.sport=?
          )
        """,
        (sport_name, sport_name, sport_name),
    )
    cur.execute(
        """
        DELETE FROM match_map_results
        WHERE COALESCE(NULLIF(TRIM(match_source), ''), 'legacy')='legacy'
          AND match_id IN (
              SELECT m.id
              FROM matches m
              LEFT JOIN tournaments tt ON tt.id = m.tournament_id
              LEFT JOIN teams t1 ON t1.id = m.team1_id
              LEFT JOIN teams t2 ON t2.id = m.team2_id
              WHERE tt.sport=? OR t1.sport=? OR t2.sport=?
          )
        """,
        (sport_name, sport_name, sport_name),
    )
    cur.execute(
        """
        DELETE FROM matches
        WHERE id IN (
            SELECT m.id
            FROM matches m
            LEFT JOIN tournaments tt ON tt.id = m.tournament_id
            LEFT JOIN teams t1 ON t1.id = m.team1_id
            LEFT JOIN teams t2 ON t2.id = m.team2_id
            WHERE tt.sport=? OR t1.sport=? OR t2.sport=?
        )
        """,
        (sport_name, sport_name, sport_name),
    )

    cur.execute(
        """
        DELETE FROM tournament_brackets
        WHERE tournament_id IN (SELECT id FROM tournaments WHERE sport=?)
        """,
        (sport_name,),
    )
    cur.execute(
        """
        DELETE FROM tournament_applications
        WHERE tournament_id IN (SELECT id FROM tournaments WHERE sport=?)
           OR team_id IN (SELECT id FROM teams WHERE sport=?)
        """,
        (sport_name, sport_name),
    )
    cur.execute(
        "DELETE FROM team_members WHERE team_id IN (SELECT id FROM teams WHERE sport=?)",
        (sport_name,),
    )
    cur.execute(
        "DELETE FROM team_invites WHERE team_id IN (SELECT id FROM teams WHERE sport=?)",
        (sport_name,),
    )
    cur.execute(
        "DELETE FROM ratings WHERE sport=? OR team_id IN (SELECT id FROM teams WHERE sport=?)",
        (sport_name, sport_name),
    )

    cur.execute("DELETE FROM tournaments WHERE sport=?", (sport_name,))
    cur.execute("DELETE FROM teams WHERE sport=?", (sport_name,))
    cur.execute("DELETE FROM sports WHERE name=? OR display_name=?",
                (sport_name, sport_name))


def _cleanup_user_favorite_sports(cur: sqlite3.Cursor, removed_sports: set[str]):
    """Удаляет удаленные виды спорта из users.favorite_sports."""
    if not removed_sports:
        return

    cur.execute(
        """
        SELECT id, favorite_sports
        FROM users
        WHERE favorite_sports IS NOT NULL AND TRIM(favorite_sports) <> ''
        """
    )
    rows = cur.fetchall()
    for row in rows:
        raw = row["favorite_sports"]
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                continue
            filtered = [sport for sport in parsed if sport not in removed_sports]
            if filtered != parsed:
                cur.execute(
                    "UPDATE users SET favorite_sports=? WHERE id=?",
                    (json.dumps(filtered, ensure_ascii=False), row["id"]),
                )
        except Exception:
            continue


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
                        invite_join_mode TEXT DEFAULT 'request',
                        invite_enabled INTEGER DEFAULT 1,
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
                try:
                    cur.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_team ON tournament_applications(tournament_id, team_id)")
                except sqlite3.OperationalError as e:
                    logger.info(
                        f"Не удалось создать индекс (возможно, есть дубли): {e}")
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
                    ("Basketball", "Баскетбол"),
                    ("Football", "Футбол"),
                    ("Volleyball", "Волейбол"),
                ]
                for name, display in sports_list:
                    cur.execute(
                        """
                        INSERT INTO sports (name, display_name)
                        VALUES (?, ?)
                        ON CONFLICT(name) DO UPDATE SET display_name=excluded.display_name
                        """,
                        (name, display),
                    )

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
                # Добавляем поле max_members в teams
                # Миграции таблицы teams
                cur.execute("PRAGMA table_info(teams)")
                columns = [col[1] for col in cur.fetchall()]
                if 'max_members' not in columns:
                    cur.execute(
                        "ALTER TABLE teams ADD COLUMN max_members INTEGER DEFAULT 5")
                    logger.info("Column max_members added to teams (default 5)")

                if 'invite_join_mode' not in columns:
                    cur.execute(
                        "ALTER TABLE teams ADD COLUMN invite_join_mode TEXT DEFAULT 'request'")
                    logger.info("Column invite_join_mode added to teams (default request)")

                if 'invite_enabled' not in columns:
                    cur.execute(
                        "ALTER TABLE teams ADD COLUMN invite_enabled INTEGER DEFAULT 1")
                    logger.info("Column invite_enabled added to teams (default 1)")

                # Добавляем поле required_team_size в tournaments
                cur.execute("PRAGMA table_info(tournaments)")
                columns = [col[1] for col in cur.fetchall()]
                if 'required_team_size' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN required_team_size INTEGER DEFAULT 2")
                    logger.info(
                        "Column required_team_size added to tournaments (default 2)")

                # Инвайт-ссылки: токены команд и турниров
                cur.execute("PRAGMA table_info(teams)")
                columns = [col[1] for col in cur.fetchall()]
                if 'invite_token' not in columns:
                    cur.execute("ALTER TABLE teams ADD COLUMN invite_token TEXT")
                    logger.info("Поле invite_token добавлено в таблицу teams")

                cur.execute("PRAGMA table_info(tournaments)")
                columns = [col[1] for col in cur.fetchall()]
                if 'invite_token' not in columns:
                    cur.execute("ALTER TABLE tournaments ADD COLUMN invite_token TEXT")
                    logger.info("Поле invite_token добавлено в таблицу tournaments")

                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_invite_token_unique
                    ON teams(invite_token)
                    WHERE invite_token IS NOT NULL AND TRIM(invite_token) <> ''
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_tournaments_invite_token_unique
                    ON tournaments(invite_token)
                    WHERE invite_token IS NOT NULL AND TRIM(invite_token) <> ''
                """)
                # Добавляем поле updated_at в team_invites (если нет)
                cur.execute("PRAGMA table_info(team_invites)")
                columns = [col[1] for col in cur.fetchall()]
                if 'updated_at' not in columns:
                    cur.execute(
                        "ALTER TABLE team_invites ADD COLUMN updated_at TIMESTAMP")
                    logger.info(
                        "Поле updated_at добавлено в таблицу team_invites (без DEFAULT)")
                cur.execute("PRAGMA table_info(tournament_applications)")
                columns = [col[1] for col in cur.fetchall()]
                if 'updated_at' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_applications ADD COLUMN updated_at TIMESTAMP")
                    logger.info(
                        "Поле updated_at добавлено в таблицу tournament_applications (без DEFAULT)")
                cur.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cur.fetchall()]
                if 'age' not in columns:
                    cur.execute("ALTER TABLE users ADD COLUMN age INTEGER")
                    logger.info("Поле age добавлено в таблицу users")
                cur.execute("PRAGMA table_info(tournaments)")
                columns = [col[1] for col in cur.fetchall()]
                if 'min_age' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN min_age INTEGER DEFAULT 0")
                    logger.info(
                        "Поле min_age добавлено в таблицу tournaments (по умолчанию 0)")
                if 'max_age' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN max_age INTEGER DEFAULT 100")
                    logger.info(
                        "Поле max_age добавлено в таблицу tournaments (по умолчанию 100)")

                # Создаём таблицу tournament_brackets для турнирной сетки
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_brackets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tournament_id INTEGER REFERENCES tournaments(id),
                        round_number INTEGER,
                        round_name TEXT,
                        match_number INTEGER,
                        team1_id INTEGER REFERENCES teams(id),
                        team2_id INTEGER REFERENCES teams(id),
                        winner_id INTEGER REFERENCES teams(id),
                        score1 INTEGER DEFAULT 0,
                        score2 INTEGER DEFAULT 0,
                        next_match_id INTEGER REFERENCES tournament_brackets(id),
                        is_bye INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'pending',
                        scheduled_at_utc TEXT,
                        location TEXT,
                        schedule_updated_at TIMESTAMP,
                        schedule_notified_at TIMESTAMP,
                        reminder_sent_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("Таблица tournament_brackets проверена")

                # Создаём таблицу player_match_stats с правильными полями для CS2
                # ИСПРАВЛЕНО: убрали DROP TABLE - теперь таблица не пересоздаётся каждый раз
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS player_match_stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_id INTEGER REFERENCES tournament_brackets(id),
                        match_source TEXT DEFAULT 'bracket',
                        user_id INTEGER REFERENCES users(id),
                        team_id INTEGER REFERENCES teams(id),
                        kills INTEGER DEFAULT 0,
                        deaths INTEGER DEFAULT 0,
                        assists INTEGER DEFAULT 0,
                        adr INTEGER DEFAULT 0,
                        hs INTEGER DEFAULT 0,
                        rating_3_0 REAL DEFAULT 0.0,
                        mvps INTEGER DEFAULT 0,
                        map_name TEXT,
                        map_number INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(match_source, match_id, user_id, map_number)
                    )
                """)
                logger.info("Таблица player_match_stats проверена")

                # Таблица результатов карт для истории матчей.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS match_map_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_source TEXT DEFAULT 'bracket',
                        match_id INTEGER NOT NULL,
                        map_number INTEGER NOT NULL,
                        map_name TEXT,
                        team1_score INTEGER,
                        team2_score INTEGER,
                        winner_id INTEGER REFERENCES teams(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(match_source, match_id, map_number)
                    )
                """)
                logger.info("Таблица match_map_results проверена")

                # Создаём индексы для ускорения поиска
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_player_stats_user ON player_match_stats(user_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_player_stats_match ON player_match_stats(match_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_map_results_source_match ON match_map_results(match_source, match_id)")

                # Футбол: статистика игроков (голы, ассисты)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS football_player_stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_source TEXT DEFAULT 'legacy',
                        match_id INTEGER NOT NULL,
                        user_id INTEGER REFERENCES users(id),
                        team_id INTEGER REFERENCES teams(id),
                        goals INTEGER DEFAULT 0,
                        assists INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(match_source, match_id, user_id)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_football_stats_source_match ON football_player_stats(match_source, match_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_football_stats_user ON football_player_stats(user_id)")

                # Баскетбол: статистика игроков (очки, фолы)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS basketball_player_stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_source TEXT DEFAULT 'legacy',
                        match_id INTEGER NOT NULL,
                        user_id INTEGER REFERENCES users(id),
                        team_id INTEGER REFERENCES teams(id),
                        points INTEGER DEFAULT 0,
                        fouls INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(match_source, match_id, user_id)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_basketball_stats_source_match ON basketball_player_stats(match_source, match_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_basketball_stats_user ON basketball_player_stats(user_id)")

                # Волейбол: статистика игроков (очки, эйсы)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS volleyball_player_stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_source TEXT DEFAULT 'legacy',
                        match_id INTEGER NOT NULL,
                        user_id INTEGER REFERENCES users(id),
                        team_id INTEGER REFERENCES teams(id),
                        points INTEGER DEFAULT 0,
                        aces INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(match_source, match_id, user_id)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_volleyball_stats_source_match ON volleyball_player_stats(match_source, match_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_volleyball_stats_user ON volleyball_player_stats(user_id)")

                # Волейбол: счёт по партиям
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS volleyball_set_scores (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_source TEXT DEFAULT 'legacy',
                        match_id INTEGER NOT NULL,
                        set_number INTEGER NOT NULL,
                        team1_points INTEGER NOT NULL,
                        team2_points INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(match_source, match_id, set_number)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_volleyball_sets_source_match ON volleyball_set_scores(match_source, match_id)")

                # Миграции для tournament_brackets
                cur.execute("PRAGMA table_info(tournament_brackets)")
                columns = [col[1] for col in cur.fetchall()]
                if 'score1' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN score1 INTEGER DEFAULT 0")
                    logger.info("Поле score1 добавлено в таблицу tournament_brackets")
                if 'score2' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN score2 INTEGER DEFAULT 0")
                    logger.info("Поле score2 добавлено в таблицу tournament_brackets")
                if 'scheduled_at_utc' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN scheduled_at_utc TEXT")
                    logger.info("Поле scheduled_at_utc добавлено в таблицу tournament_brackets")
                if 'location' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN location TEXT")
                    logger.info("Поле location добавлено в таблицу tournament_brackets")
                if 'schedule_updated_at' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN schedule_updated_at TIMESTAMP")
                    logger.info("Поле schedule_updated_at добавлено в таблицу tournament_brackets")
                if 'schedule_notified_at' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN schedule_notified_at TIMESTAMP")
                    logger.info("Поле schedule_notified_at добавлено в таблицу tournament_brackets")
                if 'reminder_sent_at' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN reminder_sent_at TIMESTAMP")
                    logger.info("Поле reminder_sent_at добавлено в таблицу tournament_brackets")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bracket_reminder_lookup ON tournament_brackets(status, scheduled_at_utc, reminder_sent_at)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bracket_tournament_round_match ON tournament_brackets(tournament_id, round_number, match_number)")

                # Миграции для player_match_stats
                cur.execute("PRAGMA table_info(player_match_stats)")
                columns = [col[1] for col in cur.fetchall()]
                if 'match_source' not in columns:
                    cur.execute(
                        "ALTER TABLE player_match_stats ADD COLUMN match_source TEXT DEFAULT 'bracket'")
                    logger.info("Поле match_source добавлено в таблицу player_match_stats")
                if 'adr' not in columns:
                    cur.execute(
                        "ALTER TABLE player_match_stats ADD COLUMN adr INTEGER DEFAULT 0")
                    logger.info("Поле adr добавлено в таблицу player_match_stats")
                if 'hs' not in columns:
                    cur.execute(
                        "ALTER TABLE player_match_stats ADD COLUMN hs INTEGER DEFAULT 0")
                    logger.info("Поле hs добавлено в таблицу player_match_stats")

                # Миграция UNIQUE ключа на (match_source, match_id, user_id, map_number)
                cur.execute("""
                    SELECT sql
                    FROM sqlite_master
                    WHERE type='table' AND name='player_match_stats'
                """)
                table_sql_row = cur.fetchone()
                table_sql = table_sql_row[0] if table_sql_row else ""
                if "UNIQUE(match_source, match_id, user_id, map_number)" not in table_sql:
                    cur.execute("DROP TABLE IF EXISTS player_match_stats_new")
                    cur.execute("""
                        CREATE TABLE player_match_stats_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            match_id INTEGER REFERENCES tournament_brackets(id),
                            match_source TEXT DEFAULT 'bracket',
                            user_id INTEGER REFERENCES users(id),
                            team_id INTEGER REFERENCES teams(id),
                            kills INTEGER DEFAULT 0,
                            deaths INTEGER DEFAULT 0,
                            assists INTEGER DEFAULT 0,
                            adr INTEGER DEFAULT 0,
                            hs INTEGER DEFAULT 0,
                            rating_3_0 REAL DEFAULT 0.0,
                            mvps INTEGER DEFAULT 0,
                            map_name TEXT,
                            map_number INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(match_source, match_id, user_id, map_number)
                        )
                    """)
                    cur.execute("""
                        INSERT INTO player_match_stats_new
                        (id, match_id, match_source, user_id, team_id, kills, deaths, assists, adr, hs, rating_3_0, mvps, map_name, map_number, created_at, updated_at)
                        SELECT
                            id,
                            match_id,
                            COALESCE(NULLIF(TRIM(match_source), ''), 'bracket'),
                            user_id,
                            team_id,
                            COALESCE(kills, 0),
                            COALESCE(deaths, 0),
                            COALESCE(assists, 0),
                            COALESCE(adr, 0),
                            COALESCE(hs, 0),
                            COALESCE(rating_3_0, 0.0),
                            COALESCE(mvps, 0),
                            map_name,
                            map_number,
                            COALESCE(created_at, CURRENT_TIMESTAMP),
                            COALESCE(updated_at, CURRENT_TIMESTAMP)
                        FROM player_match_stats
                    """)
                    cur.execute("DROP TABLE player_match_stats")
                    cur.execute(
                        "ALTER TABLE player_match_stats_new RENAME TO player_match_stats")
                    logger.info(
                        "Таблица player_match_stats мигрирована на UNIQUE(match_source, match_id, user_id, map_number)")

                # Backfill источника матчей без потери данных
                cur.execute("""
                    UPDATE player_match_stats
                    SET match_source='bracket'
                    WHERE match_source IS NULL OR TRIM(match_source)=''
                """)
                cur.execute("""
                    UPDATE player_match_stats
                    SET match_source='legacy'
                    WHERE match_id IN (SELECT id FROM matches)
                      AND match_id NOT IN (SELECT id FROM tournament_brackets)
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_player_stats_user ON player_match_stats(user_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_player_stats_match ON player_match_stats(match_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_player_stats_source_match ON player_match_stats(match_source, match_id)")

                # Миграции и индексы для match_map_results
                cur.execute("PRAGMA table_info(match_map_results)")
                map_columns = [col[1] for col in cur.fetchall()]
                if map_columns:
                    if 'match_source' not in map_columns:
                        cur.execute(
                            "ALTER TABLE match_map_results ADD COLUMN match_source TEXT DEFAULT 'bracket'")
                    if 'map_name' not in map_columns:
                        cur.execute(
                            "ALTER TABLE match_map_results ADD COLUMN map_name TEXT")
                    if 'team1_score' not in map_columns:
                        cur.execute(
                            "ALTER TABLE match_map_results ADD COLUMN team1_score INTEGER")
                    if 'team2_score' not in map_columns:
                        cur.execute(
                            "ALTER TABLE match_map_results ADD COLUMN team2_score INTEGER")
                    if 'winner_id' not in map_columns:
                        cur.execute(
                            "ALTER TABLE match_map_results ADD COLUMN winner_id INTEGER REFERENCES teams(id)")
                    if 'created_at' not in map_columns:
                        cur.execute(
                            "ALTER TABLE match_map_results ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                    if 'updated_at' not in map_columns:
                        cur.execute(
                            "ALTER TABLE match_map_results ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

                cur.execute("""
                    SELECT sql
                    FROM sqlite_master
                    WHERE type='table' AND name='match_map_results'
                """)
                map_table_sql_row = cur.fetchone()
                map_table_sql = map_table_sql_row[0] if map_table_sql_row else ""
                if map_table_sql and "UNIQUE(match_source, match_id, map_number)" not in map_table_sql:
                    cur.execute("DROP TABLE IF EXISTS match_map_results_new")
                    cur.execute("""
                        CREATE TABLE match_map_results_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            match_source TEXT DEFAULT 'bracket',
                            match_id INTEGER NOT NULL,
                            map_number INTEGER NOT NULL,
                            map_name TEXT,
                            team1_score INTEGER,
                            team2_score INTEGER,
                            winner_id INTEGER REFERENCES teams(id),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(match_source, match_id, map_number)
                        )
                    """)
                    cur.execute("""
                        INSERT INTO match_map_results_new
                        (id, match_source, match_id, map_number, map_name, team1_score, team2_score, winner_id, created_at, updated_at)
                        SELECT
                            id,
                            COALESCE(NULLIF(TRIM(match_source), ''), 'bracket'),
                            match_id,
                            COALESCE(map_number, 1),
                            map_name,
                            team1_score,
                            team2_score,
                            winner_id,
                            COALESCE(created_at, CURRENT_TIMESTAMP),
                            COALESCE(updated_at, CURRENT_TIMESTAMP)
                        FROM match_map_results
                    """)
                    cur.execute("DROP TABLE match_map_results")
                    cur.execute(
                        "ALTER TABLE match_map_results_new RENAME TO match_map_results")
                    logger.info(
                        "Таблица match_map_results мигрирована на UNIQUE(match_source, match_id, map_number)")

                cur.execute("""
                    UPDATE match_map_results
                    SET match_source='bracket'
                    WHERE match_source IS NULL OR TRIM(match_source)=''
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_map_results_source_match ON match_map_results(match_source, match_id)")

                # Добавляем поле total_maps в matches
                cur.execute("PRAGMA table_info(matches)")
                columns = [col[1] for col in cur.fetchall()]
                if 'total_maps' not in columns:
                    cur.execute(
                        "ALTER TABLE matches ADD COLUMN total_maps INTEGER DEFAULT 1")
                    logger.info("Поле total_maps добавлено в таблицу matches")

                # Добавляем поле match_format в matches
                cur.execute("PRAGMA table_info(matches)")
                columns = [col[1] for col in cur.fetchall()]
                if 'match_format' not in columns:
                    cur.execute(
                        "ALTER TABLE matches ADD COLUMN match_format TEXT DEFAULT 'bo3'")
                    logger.info("Поле match_format добавлено в таблицу matches")

                # Добавляем поля для времени/места матча
                cur.execute("PRAGMA table_info(matches)")
                columns = [col[1] for col in cur.fetchall()]
                if 'match_date' not in columns:
                    cur.execute(
                        "ALTER TABLE matches ADD COLUMN match_date TEXT")
                    logger.info("Поле match_date добавлено в таблицу matches")
                if 'location' not in columns:
                    cur.execute(
                        "ALTER TABLE matches ADD COLUMN location TEXT")
                    logger.info("Поле location добавлено в таблицу matches")
                if 'notified' not in columns:
                    cur.execute(
                        "ALTER TABLE matches ADD COLUMN notified INTEGER DEFAULT 0")
                    logger.info("Поле notified добавлено в таблицу matches")

                # Добавляем поле bracket_generated в tournaments
                cur.execute("PRAGMA table_info(tournaments)")
                columns = [col[1] for col in cur.fetchall()]
                if 'bracket_generated' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN bracket_generated INTEGER DEFAULT 0")
                    logger.info("Поле bracket_generated добавлено в таблицу tournaments")

                # Добавляем поле match_format в tournaments
                cur.execute("PRAGMA table_info(tournaments)")
                columns = [col[1] for col in cur.fetchall()]
                if 'match_format' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN match_format TEXT DEFAULT 'bo3'")
                    logger.info("Поле match_format добавлено в таблицу tournaments")

                # Добавляем поле steam_id в users
                cur.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cur.fetchall()]
                if 'steam_id' not in columns:
                    cur.execute(
                        "ALTER TABLE users ADD COLUMN steam_id TEXT")
                    logger.info("Поле steam_id добавлено в таблицу users")

                # Нормализуем старые numeric SteamID в канонический URL
                cur.execute("""
                    UPDATE users
                    SET steam_id = 'https://steamcommunity.com/profiles/' || substr(TRIM(steam_id), 1, 17)
                    WHERE steam_id IS NOT NULL
                      AND TRIM(steam_id) <> ''
                      AND TRIM(steam_id) NOT LIKE 'https://steamcommunity.com/profiles/%'
                      AND TRIM(steam_id) NOT GLOB '*[^0-9]*'
                      AND LENGTH(TRIM(steam_id)) >= 17
                """)
                cur.execute("""
                    UPDATE users
                    SET steam_id = RTRIM(TRIM(steam_id), '/')
                    WHERE steam_id LIKE 'https://steamcommunity.com/profiles/%/'
                """)

                # Обеспечиваем уникальность Steam-профиля на уровне БД
                try:
                    cur.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_steam_id_unique
                        ON users(steam_id)
                        WHERE steam_id IS NOT NULL AND TRIM(steam_id) <> ''
                    """)
                except sqlite3.IntegrityError:
                    logger.warning(
                        "Не удалось создать unique-индекс steam_id: уже есть дубли. "
                        "Новые дубли блокируются проверкой в коде."
                    )

                _purge_removed_sport(cur, "Brawl Stars")
                _cleanup_user_favorite_sports(cur, {"Brawl Stars"})

            conn.close()
            logger.info("База данных успешно инициализирована.")
            return
        except sqlite3.OperationalError as e:
            logger.info(f"Попытка {attempt+1}/{max_attempts} не удалась: {e}")
            time.sleep(1)
    raise Exception(
        "Не удалось инициализировать базу данных после нескольких попыток.")

