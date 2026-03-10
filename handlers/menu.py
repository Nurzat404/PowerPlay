from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from utils import get_user, is_admin
from keyboards import main_menu_keyboard, back_to_main_keyboard, admin_menu_keyboard
import json

router = Router()


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        return

    # Получаем строку из БД
    fav_sports_raw = user['favorite_sports']
    if fav_sports_raw:
        try:
            sports_list = json.loads(fav_sports_raw)
            sports_display = ", ".join(sports_list)
        except:
            sports_display = fav_sports_raw  # если вдруг не JSON
    else:
        sports_display = "не указаны"

    text = f"""
👤 Профиль
Имя: {user['first_name']}
Email: {user['email']}
Город: {user['city']}
Любимые виды спорта: {sports_display}
Роль: {user['role']}
    """
    await callback.message.edit_text(text, reply_markup=back_to_main_keyboard())
    await callback.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if is_admin(message.from_user.id):
        await message.answer("Панель администратора:", reply_markup=admin_menu_keyboard())
    else:
        await message.answer("У вас нет прав администратора.")
