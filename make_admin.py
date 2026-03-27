import sqlite3
from pathlib import Path

DB_NAME = "razryad_arena.db"


def resolve_db_path() -> Path:
    data_dir = Path('/app/data') if Path('/app/data').exists() else Path('.')
    return data_dir / DB_NAME


def main() -> int:
    db_path = resolve_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        telegram_id = 2144911894  # Set your numeric Telegram ID here
        cursor.execute("UPDATE users SET role='admin' WHERE telegram_id=?", (telegram_id,))
        if cursor.rowcount == 0:
            print("User with this telegram_id was not found. Register in the bot first.")
            conn.commit()
            return 1

        print("Role was updated to admin.")
        conn.commit()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
