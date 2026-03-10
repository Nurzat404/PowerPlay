from aiogram import Router, F
from aiogram.types import CallbackQuery
from database import get_connection
from utils import get_all_sports
from keyboards import ratings_sport_keyboard, back_to_main_keyboard
from datetime import datetime

router = Router()


@router.callback_query(F.data == "ratings")
async def ratings_menu(callback: CallbackQuery):
    sports = get_all_sports()
    await callback.message.edit_text(
        "Выберите вид спорта для просмотра рейтинга:",
        reply_markup=ratings_sport_keyboard(sports)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rating_sport_"))
async def show_sport_rating(callback: CallbackQuery):
    sport = callback.data.replace("rating_sport_", "")
    month = datetime.now().strftime("%Y-%m")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.name, r.points FROM ratings r
        JOIN teams t ON r.team_id = t.id
        WHERE r.sport=? AND r.month=?
        ORDER BY r.points DESC
        LIMIT 10
    """, (sport, month))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        text = f"Рейтинг по {sport} пока пуст."
    else:
        text = f"🏆 Рейтинг команд {sport}:\n\n"
        for i, row in enumerate(rows, 1):
            text += f"{i}. {row['name']} — {row['points']} очков\n"

    await callback.message.edit_text(text, reply_markup=back_to_main_keyboard())
    await callback.answer()
