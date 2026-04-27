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
    cur.execute("DELETE FROM rating_mvp_assignments WHERE sport_key=?", (sport_name,))
    cur.execute("DELETE FROM entity_ratings WHERE sport_key=?", (sport_name,))
    cur.execute("DELETE FROM rating_adjustments WHERE sport_key=?", (sport_name,))
    cur.execute("DELETE FROM rating_channel_posts WHERE sport_key=?", (sport_name,))
    cur.execute("DELETE FROM rating_seasons WHERE sport_key=?", (sport_name,))

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
                        pending_start_payload TEXT,
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

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS team_member_blocks (
                        team_id INTEGER NOT NULL REFERENCES teams(id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        blocked_by INTEGER REFERENCES users(id),
                        reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (team_id, user_id)
                    )
                """)

                # Турниры
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournaments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        sport TEXT NOT NULL,
                        city TEXT,
                        registration_start_date TEXT,
                        registration_end_date TEXT,
                        start_date TEXT,
                        end_date TEXT,
                        registration_deadline_notified_at TIMESTAMP,
                        max_teams INTEGER,
                        description TEXT,
                        status TEXT DEFAULT 'registration',
                        created_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_map_pool (
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        map_key TEXT NOT NULL,
                        map_name TEXT NOT NULL,
                        sort_order INTEGER DEFAULT 0,
                        PRIMARY KEY (tournament_id, map_key)
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_managers (
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        assigned_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (tournament_id, user_id)
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_team_rosters (
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        team_id INTEGER NOT NULL REFERENCES teams(id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        status TEXT DEFAULT 'active',
                        added_by INTEGER REFERENCES users(id),
                        removed_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        removed_at TIMESTAMP,
                        PRIMARY KEY (tournament_id, team_id, user_id)
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_team_captains (
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        team_id INTEGER NOT NULL REFERENCES teams(id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        assigned_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (tournament_id, team_id)
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_roster_change_requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        team_id INTEGER NOT NULL REFERENCES teams(id),
                        old_user_id INTEGER NOT NULL REFERENCES users(id),
                        new_user_id INTEGER NOT NULL REFERENCES users(id),
                        requested_by_user_id INTEGER REFERENCES users(id),
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        responded_at TIMESTAMP
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bracket_match_technical_participants (
                        match_id INTEGER NOT NULL REFERENCES tournament_brackets(id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        team_id INTEGER NOT NULL REFERENCES teams(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (match_id, user_id)
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_match_format_rules (
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        round_number INTEGER NOT NULL,
                        match_format TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (tournament_id, round_number)
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

                # Legacy monthly team ratings remain in `ratings`.
                # New canonical rating model: seasons + universal entity ratings + audit log.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rating_seasons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sport_key TEXT NOT NULL,
                        name TEXT NOT NULL,
                        sequence_no INTEGER NOT NULL,
                        start_date TEXT,
                        end_date TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        completed_at TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS entity_ratings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_type TEXT NOT NULL,
                        entity_id INTEGER NOT NULL,
                        sport_key TEXT NOT NULL,
                        format_key TEXT,
                        rating_scope TEXT NOT NULL,
                        season_id INTEGER REFERENCES rating_seasons(id),
                        rating_value INTEGER DEFAULT 0,
                        matches_played INTEGER DEFAULT 0,
                        matches_won INTEGER DEFAULT 0,
                        tournaments_played INTEGER DEFAULT 0,
                        tournaments_won INTEGER DEFAULT 0,
                        second_places INTEGER DEFAULT 0,
                        third_places INTEGER DEFAULT 0,
                        mvp_matches_count INTEGER DEFAULT 0,
                        mvp_tournaments_count INTEGER DEFAULT 0,
                        manual_adjustment_total INTEGER DEFAULT 0,
                        last_manual_adjustment_at TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rating_adjustments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_type TEXT NOT NULL,
                        entity_id INTEGER NOT NULL,
                        sport_key TEXT NOT NULL,
                        format_key TEXT,
                        rating_scope TEXT NOT NULL,
                        season_id INTEGER REFERENCES rating_seasons(id),
                        delta INTEGER NOT NULL DEFAULT 0,
                        matches_played_delta INTEGER DEFAULT 0,
                        matches_won_delta INTEGER DEFAULT 0,
                        tournaments_played_delta INTEGER DEFAULT 0,
                        tournaments_won_delta INTEGER DEFAULT 0,
                        second_places_delta INTEGER DEFAULT 0,
                        third_places_delta INTEGER DEFAULT 0,
                        mvp_matches_count_delta INTEGER DEFAULT 0,
                        mvp_tournaments_count_delta INTEGER DEFAULT 0,
                        reason TEXT,
                        source_type TEXT,
                        source_id INTEGER,
                        actor_user_id INTEGER REFERENCES users(id),
                        event_key TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rating_mvp_assignments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_type TEXT NOT NULL,
                        source_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        sport_key TEXT NOT NULL,
                        assigned_mode TEXT DEFAULT 'auto',
                        assigned_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(source_type, source_id)
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
                if 'registration_start_date' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN registration_start_date TEXT")
                    logger.info("Поле registration_start_date добавлено в таблицу tournaments")
                if 'registration_end_date' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN registration_end_date TEXT")
                    logger.info("Поле registration_end_date добавлено в таблицу tournaments")
                if 'registration_deadline_notified_at' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN registration_deadline_notified_at TIMESTAMP")
                    logger.info("Поле registration_deadline_notified_at добавлено в таблицу tournaments")
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
                if 'pending_start_payload' not in columns:
                    cur.execute("ALTER TABLE users ADD COLUMN pending_start_payload TEXT")
                    logger.info("Поле pending_start_payload добавлено в таблицу users")
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
                if 'map_veto_enabled' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN map_veto_enabled INTEGER DEFAULT 0")
                    logger.info("Поле map_veto_enabled добавлено в таблицу tournaments")
                if 'veto_launch_mode' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN veto_launch_mode TEXT DEFAULT 'admin_start'")
                    logger.info("Поле veto_launch_mode добавлено в таблицу tournaments")
                if 'replacements_enabled' not in columns:
                    cur.execute(
                        "ALTER TABLE tournaments ADD COLUMN replacements_enabled INTEGER DEFAULT 1")
                    logger.info("Поле replacements_enabled добавлено в таблицу tournaments")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_match_format_rules (
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        round_number INTEGER NOT NULL,
                        match_format TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (tournament_id, round_number)
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournament_match_format_rules_tournament
                    ON tournament_match_format_rules(tournament_id, round_number)
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_seasons_unique
                    ON rating_seasons(sport_key, sequence_no)
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_seasons_single_active
                    ON rating_seasons(sport_key, status)
                    WHERE status='active'
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_ratings_unique
                    ON entity_ratings(
                        entity_type,
                        entity_id,
                        sport_key,
                        COALESCE(format_key, ''),
                        rating_scope,
                        COALESCE(season_id, 0)
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_entity_ratings_lookup
                    ON entity_ratings(entity_type, sport_key, rating_scope, season_id, rating_value DESC)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_entity_ratings_entity
                    ON entity_ratings(entity_type, entity_id, sport_key, rating_scope, season_id)
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_adjustments_event_key
                    ON rating_adjustments(event_key)
                    WHERE event_key IS NOT NULL AND TRIM(event_key) <> ''
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rating_adjustments_source
                    ON rating_adjustments(source_type, source_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rating_adjustments_entity
                    ON rating_adjustments(entity_type, entity_id, sport_key, rating_scope, season_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rating_mvp_assignments_lookup
                    ON rating_mvp_assignments(source_type, source_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournament_team_rosters_lookup
                    ON tournament_team_rosters(tournament_id, team_id, status, user_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournament_team_rosters_user
                    ON tournament_team_rosters(tournament_id, user_id, status)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournament_team_captains_lookup
                    ON tournament_team_captains(tournament_id, team_id, user_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_team_member_blocks_team
                    ON team_member_blocks(team_id, created_at)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_team_member_blocks_user
                    ON team_member_blocks(user_id, created_at)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournament_roster_requests_target
                    ON tournament_roster_change_requests(new_user_id, status)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournament_roster_requests_team
                    ON tournament_roster_change_requests(tournament_id, team_id, status)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_bracket_technical_participants_user
                    ON bracket_match_technical_participants(user_id, match_id)
                """)

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
                        is_third_place INTEGER DEFAULT 0,
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

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS match_veto_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bracket_match_id INTEGER NOT NULL UNIQUE REFERENCES tournament_brackets(id),
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        team1_id INTEGER REFERENCES teams(id),
                        team2_id INTEGER REFERENCES teams(id),
                        match_format TEXT NOT NULL,
                        status TEXT DEFAULT 'not_ready',
                        ready_at TIMESTAMP,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        cancelled_at TIMESTAMP,
                        started_by_user_id INTEGER REFERENCES users(id),
                        started_by_kind TEXT,
                        start_source TEXT,
                        current_step_index INTEGER DEFAULT 0,
                        current_team_id INTEGER REFERENCES teams(id),
                        current_action_type TEXT,
                        current_turn_started_at TIMESTAMP,
                        timeout_notified_step_index INTEGER,
                        timeout_notified_at TIMESTAMP,
                        auto_start_consumed INTEGER DEFAULT 0,
                        admin_notified_at TIMESTAMP,
                        captains_notified_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("Таблица match_veto_sessions проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS match_veto_actions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER NOT NULL REFERENCES match_veto_sessions(id),
                        step_index INTEGER NOT NULL,
                        action_type TEXT NOT NULL,
                        actor_user_id INTEGER REFERENCES users(id),
                        actor_role TEXT,
                        team_id INTEGER REFERENCES teams(id),
                        map_key TEXT,
                        map_name TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("Таблица match_veto_actions проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS match_series_maps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id INTEGER NOT NULL REFERENCES match_veto_sessions(id),
                        map_order INTEGER NOT NULL,
                        map_key TEXT NOT NULL,
                        map_name TEXT NOT NULL,
                        selection_type TEXT NOT NULL,
                        selected_by_team_id INTEGER REFERENCES teams(id),
                        source_action_id INTEGER REFERENCES match_veto_actions(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(session_id, map_order),
                        UNIQUE(session_id, map_key)
                    )
                """)
                logger.info("Таблица match_series_maps проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS match_veto_message_targets (
                        session_id INTEGER NOT NULL REFERENCES match_veto_sessions(id),
                        chat_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (session_id, chat_id)
                    )
                """)
                logger.info("Таблица match_veto_message_targets проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rating_channel_posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_type TEXT NOT NULL,
                        sport_key TEXT NOT NULL,
                        rating_scope TEXT NOT NULL,
                        season_id INTEGER REFERENCES rating_seasons(id),
                        format_key TEXT,
                        chat_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL,
                        status TEXT DEFAULT 'active',
                        created_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("Таблица rating_channel_posts проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS admin_notification_preferences (
                        user_id INTEGER PRIMARY KEY REFERENCES users(id),
                        tournament_notifications_enabled INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("Таблица admin_notification_preferences проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_notification_overrides (
                        tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        notifications_mode TEXT NOT NULL DEFAULT 'inherit',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (tournament_id, user_id)
                    )
                """)
                logger.info("Таблица tournament_notification_overrides проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS admin_action_message_targets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        action_scope TEXT NOT NULL,
                        action_key TEXT NOT NULL,
                        tournament_id INTEGER REFERENCES tournaments(id),
                        bracket_match_id INTEGER REFERENCES tournament_brackets(id),
                        chat_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(action_scope, action_key, chat_id)
                    )
                """)
                logger.info("Таблица admin_action_message_targets проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS referral_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_user_id INTEGER NOT NULL REFERENCES users(id),
                        sport_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        token TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        disabled_at TIMESTAMP
                    )
                """)
                logger.info("Таблица referral_links проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS referral_attributions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        referred_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                        referral_link_id INTEGER NOT NULL REFERENCES referral_links(id),
                        owner_user_id INTEGER NOT NULL REFERENCES users(id),
                        sport_key TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("Таблица referral_attributions проверена")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS referral_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        referred_user_id INTEGER NOT NULL REFERENCES users(id),
                        owner_user_id INTEGER NOT NULL REFERENCES users(id),
                        sport_key TEXT NOT NULL,
                        tournament_id INTEGER REFERENCES tournaments(id),
                        match_id INTEGER REFERENCES tournament_brackets(id),
                        referral_link_id INTEGER NOT NULL REFERENCES referral_links(id),
                        owner_points INTEGER DEFAULT 0,
                        referred_points INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                logger.info("Таблица referral_events проверена")

                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_events_unique_type_user
                    ON referral_events(event_type, referred_user_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_referral_links_owner
                    ON referral_links(owner_user_id, sport_key, status, created_at)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_referral_attributions_link
                    ON referral_attributions(referral_link_id, created_at)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_referral_events_link
                    ON referral_events(referral_link_id, event_type, created_at)
                """)

                cur.execute("PRAGMA table_info(match_veto_sessions)")
                columns = [col[1] for col in cur.fetchall()]
                if 'auto_start_consumed' not in columns:
                    cur.execute(
                        "ALTER TABLE match_veto_sessions ADD COLUMN auto_start_consumed INTEGER DEFAULT 0")
                    logger.info("Поле auto_start_consumed добавлено в таблицу match_veto_sessions")
                if 'current_turn_started_at' not in columns:
                    cur.execute(
                        "ALTER TABLE match_veto_sessions ADD COLUMN current_turn_started_at TIMESTAMP")
                    logger.info("Поле current_turn_started_at добавлено в таблицу match_veto_sessions")
                if 'timeout_notified_step_index' not in columns:
                    cur.execute(
                        "ALTER TABLE match_veto_sessions ADD COLUMN timeout_notified_step_index INTEGER")
                    logger.info("Поле timeout_notified_step_index добавлено в таблицу match_veto_sessions")
                if 'timeout_notified_at' not in columns:
                    cur.execute(
                        "ALTER TABLE match_veto_sessions ADD COLUMN timeout_notified_at TIMESTAMP")
                    logger.info("Поле timeout_notified_at добавлено в таблицу match_veto_sessions")

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
                if 'is_third_place' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN is_third_place INTEGER DEFAULT 0")
                    logger.info("Поле is_third_place добавлено в таблицу tournament_brackets")
                cur.execute("""
                    UPDATE tournament_brackets
                    SET is_third_place=1
                    WHERE COALESCE(is_third_place, 0)=0
                      AND (
                        LOWER(COALESCE(round_name, '')) LIKE 'матч за 3-е%'
                        OR LOWER(COALESCE(round_name, '')) LIKE 'матч за 3е%'
                      )
                """)
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
                if 'result_type' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN result_type TEXT DEFAULT 'regular'")
                    logger.info("Поле result_type добавлено в таблицу tournament_brackets")
                if 'technical_winner_id' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN technical_winner_id INTEGER")
                    logger.info("Поле technical_winner_id добавлено в таблицу tournament_brackets")
                if 'technical_loser_id' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN technical_loser_id INTEGER")
                    logger.info("Поле technical_loser_id добавлено в таблицу tournament_brackets")
                if 'technical_reason' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN technical_reason TEXT")
                    logger.info("Поле technical_reason добавлено в таблицу tournament_brackets")
                if 'technical_assigned_by' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN technical_assigned_by INTEGER")
                    logger.info("Поле technical_assigned_by добавлено в таблицу tournament_brackets")
                if 'technical_assigned_at' not in columns:
                    cur.execute(
                        "ALTER TABLE tournament_brackets ADD COLUMN technical_assigned_at TIMESTAMP")
                    logger.info("Поле technical_assigned_at добавлено в таблицу tournament_brackets")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bracket_reminder_lookup ON tournament_brackets(status, scheduled_at_utc, reminder_sent_at)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bracket_tournament_round_match ON tournament_brackets(tournament_id, round_number, match_number)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bracket_result_type ON tournament_brackets(result_type, tournament_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tournament_managers_tournament ON tournament_managers(tournament_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tournament_managers_user ON tournament_managers(user_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_veto_sessions_status_ready ON match_veto_sessions(status, ready_at)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_veto_sessions_status_turn_started ON match_veto_sessions(status, current_turn_started_at)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_veto_sessions_bracket_match ON match_veto_sessions(bracket_match_id)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_veto_actions_session_step ON match_veto_actions(session_id, step_index)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_series_maps_session_order ON match_series_maps(session_id, map_order)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_veto_message_targets_session ON match_veto_message_targets(session_id)")
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_channel_posts_rating_key
                    ON rating_channel_posts(
                        entity_type,
                        sport_key,
                        rating_scope,
                        COALESCE(season_id, 0),
                        COALESCE(format_key, '')
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rating_channel_posts_status_lookup
                    ON rating_channel_posts(status, sport_key, entity_type, rating_scope)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournaments_registration_deadline_lookup
                    ON tournaments(status, registration_end_date, registration_deadline_notified_at)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_admin_notification_preferences_enabled
                    ON admin_notification_preferences(tournament_notifications_enabled)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tournament_notification_overrides_user
                    ON tournament_notification_overrides(user_id, notifications_mode)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_admin_action_targets_lookup
                    ON admin_action_message_targets(action_scope, action_key, status)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_admin_action_targets_match
                    ON admin_action_message_targets(bracket_match_id, status)
                """)

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
            try:
                from utils.rating_seasons import ensure_active_rating_seasons_for_all_sports

                ensure_active_rating_seasons_for_all_sports()
            except Exception:
                logger.exception("Не удалось подготовить активные сезоны рейтинга")
            logger.info("База данных успешно инициализирована.")
            return
        except sqlite3.OperationalError as e:
            logger.info(f"Попытка {attempt+1}/{max_attempts} не удалась: {e}")
            time.sleep(1)
    raise Exception(
        "Не удалось инициализировать базу данных после нескольких попыток.")
