import sqlite3
import os
from pathlib import Path

# Определяем путь к базе (как в database.py)
if Path('/app/data').exists():
    DB_PATH = Path('/app/data') / 'powerplay.db'
else:
    DB_PATH = Path('.') / 'powerplay.db'

conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()

# Замени ID на свой Telegram ID (можно узнать у @userinfobot)
telegram_id = 2144911894  # ⬅️ поставь свой числовой ID

cursor.execute(
    "UPDATE users SET role='admin' WHERE telegram_id=?", (telegram_id,))
if cursor.rowcount == 0:
    print("❌ Пользователь с таким telegram_id не найден. Сначала зарегистрируйся в боте.")
else:
    print("✅ Роль обновлена на admin.")
conn.commit()
conn.close()
