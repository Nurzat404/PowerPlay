from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from utils import get_user, is_admin, get_all_sports, update_user, is_email_unique
from keyboards import main_menu_keyboard, back_to_main_keyboard, admin_menu_keyboard, edit_profile_menu_keyboard, cancel_keyboard, sports_choice_keyboard
import json

router = Router()


class EditProfile(StatesGroup):
    waiting_name = State()
    waiting_email = State()
    waiting_city = State()
    waiting_sports = State()


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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать профиль",
                              callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if is_admin(message.from_user.id):
        await message.answer("Панель администратора:", reply_markup=admin_menu_keyboard())
    else:
        await message.answer("У вас нет прав администратора.")


@router.callback_query(F.data == "edit_profile")
async def edit_profile_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите, что хотите изменить:",
        reply_markup=edit_profile_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "edit_name")
async def edit_name_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.waiting_name)
    await callback.message.edit_text(
        "Введите новое имя:",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@router.message(EditProfile.waiting_name)
async def edit_name_finish(message: Message, state: FSMContext):
    new_name = message.text.strip()
    if not new_name:
        await message.answer("Имя не может быть пустым. Попробуйте ещё раз:", reply_markup=cancel_keyboard())
        return
    update_user(message.from_user.id, first_name=new_name)
    await state.clear()
    user = get_user(message.from_user.id)
    fav_sports_raw = user['favorite_sports']
    if fav_sports_raw:
        try:
            sports_list = json.loads(fav_sports_raw)
            sports_display = ", ".join(sports_list)
        except:
            sports_display = fav_sports_raw
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать профиль",
                              callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "edit_email")
async def edit_email_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.waiting_email)
    await callback.message.edit_text(
        "Введите новый email:",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@router.message(EditProfile.waiting_email)
async def edit_email_finish(message: Message, state: FSMContext):
    new_email = message.text.strip()
    if "@" not in new_email or "." not in new_email:
        await message.answer("Некорректный email. Попробуйте ещё раз:", reply_markup=cancel_keyboard())
        return
    # Проверяем уникальность
    if not is_email_unique(new_email, exclude_telegram_id=message.from_user.id):
        await message.answer("Этот email уже используется другим пользователем. Введите другой:", reply_markup=cancel_keyboard())
        return
    update_user(message.from_user.id, email=new_email)
    await state.clear()
    user = get_user(message.from_user.id)
    fav_sports_raw = user['favorite_sports']
    if fav_sports_raw:
        try:
            sports_list = json.loads(fav_sports_raw)
            sports_display = ", ".join(sports_list)
        except:
            sports_display = fav_sports_raw
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать профиль",
                              callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "edit_city")
async def edit_city_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.waiting_city)
    await callback.message.edit_text(
        "Введите новый город:",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@router.message(EditProfile.waiting_city)
async def edit_city_finish(message: Message, state: FSMContext):
    new_city = message.text.strip()
    if not new_city:
        await message.answer("Город не может быть пустым. Попробуйте ещё раз:", reply_markup=cancel_keyboard())
        return
    update_user(message.from_user.id, city=new_city)
    await state.clear()
    user = get_user(message.from_user.id)
    fav_sports_raw = user['favorite_sports']
    if fav_sports_raw:
        try:
            sports_list = json.loads(fav_sports_raw)
            sports_display = ", ".join(sports_list)
        except:
            sports_display = fav_sports_raw
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать профиль",
                              callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "edit_sports")
async def edit_sports_start(callback: CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    # Получаем текущие выбранные спорты
    current_sports = json.loads(
        user['favorite_sports']) if user['favorite_sports'] else []
    sports_list = get_all_sports()
    await state.set_state(EditProfile.waiting_sports)
    # Сохраняем текущий выбор в state, чтобы потом обновлять
    await state.update_data(selected_sports=current_sports)
    await callback.message.edit_text(
        "Выберите любимые виды спорта (можно несколько):",
        reply_markup=sports_choice_keyboard(
            sports_list, selected=current_sports)
    )
    await callback.answer()


@router.callback_query(EditProfile.waiting_sports, F.data.startswith("sport_"), F.data != "sport_done")
async def edit_sports_choice(callback: CallbackQuery, state: FSMContext):
    sport = callback.data.replace("sport_", "")
    data = await state.get_data()
    selected = data.get("selected_sports", [])
    if sport in selected:
        selected.remove(sport)
    else:
        selected.append(sport)
    await state.update_data(selected_sports=selected)
    # Обновляем клавиатуру с отметками
    sports_list = get_all_sports()
    await callback.message.edit_reply_markup(
        reply_markup=sports_choice_keyboard(sports_list, selected=selected)
    )
    await callback.answer()


@router.callback_query(EditProfile.waiting_sports, F.data == "sport_done")
async def edit_sports_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_sports", [])
    if not selected:
        await callback.answer("Выберите хотя бы один вид спорта!", show_alert=True)
        return
    # Сохраняем в БД
    update_user(callback.from_user.id, favorite_sports=json.dumps(
        selected, ensure_ascii=False))
    await state.clear()
    # Показываем обновлённый профиль
    user = get_user(callback.from_user.id)
    sports_display = ", ".join(selected)
    text = f"""
👤 Профиль
Имя: {user['first_name']}
Email: {user['email']}
Город: {user['city']}
Любимые виды спорта: {sports_display}
Роль: {user['role']}
    """
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать профиль",
                              callback_data="edit_profile")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "cancel_edit")
async def cancel_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    # Возвращаемся в меню редактирования
    await callback.message.edit_text(
        "Выберите, что хотите изменить:",
        reply_markup=edit_profile_menu_keyboard()
    )
    await callback.answer()
