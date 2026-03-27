from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import json
import logging
from database import get_connection
from razryad_arena_utils import (
    get_user, get_user_teams, is_captain, get_team_by_id, get_team_members,
    get_user_by_username, is_team_member, has_pending_invite,
    create_invite, accept_invite, reject_invite, get_user_by_id, get_all_sports,
    get_team_settings, set_team_open_status, set_team_notify_status,
    get_all_teams_paginated, get_teams_count, create_team_request,
    get_team_requests, get_team_requests_count, has_pending_request,
    accept_request, reject_request,
    search_teams_by_name, search_teams_count, get_team_invite_status,
    get_teams_by_sport, get_teams_count_by_sport, get_team_members_count, get_team_max_members, update_team_max_members,
    get_sport_display_name, map_sports_to_display
)
from keyboards import (
    teams_list_keyboard, team_management_extended_keyboard, main_menu_keyboard,
    back_to_main_keyboard, confirm_keyboard, invite_keyboard, sports_choice_keyboard,
    teams_main_keyboard, team_view_only_keyboard, team_view_join_keyboard,
    team_requests_keyboard, teams_sports_filter_keyboard, sports_choice_keyboard_no_done,
    team_view_search_keyboard, input_number_keyboard, back_to_teams_menu_keyboard, choose_new_captain_keyboard
)

logger = logging.getLogger(__name__)


async def get_team_card_data(team_id: int, user_id: int):
    """Возвращает текст и клавиатуру для карточки команды."""
    team = get_team_by_id(team_id)
    if not team:
        return None, None

    members = get_team_members(team_id)
    members_count = len(members)
    max_members = get_team_max_members(team_id)
    members_list_lines = []
    for m in members:
        age_str = f"[{m['age']} лет]" if m['age'] else "[возраст не указан]"
        members_list_lines.append(
            f"- {m['first_name']} (@{m['username']}) {age_str}")
    members_list = "\n".join(members_list_lines)

    captain = get_user_by_id(team['captain_id'])
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Неизвестно"
    settings = get_team_settings(team_id)
    sport_display = get_sport_display_name(team['sport'])

    text = f"""
Команда: {team['name']}
Вид спорта: {sport_display}
Город: {team['city']}
Капитан: {captain_name}
Участников: {members_count} / {max_members}
Набор: {"🔓 Открыт" if settings['is_open'] else "🔒 Закрыт"}
Состав:
{members_list}
    """

    user = get_user(user_id)
    is_capt = user and is_captain(user['id'], team_id)
    kb = team_management_extended_keyboard(
        team_id, is_capt, settings['is_open'], settings['notify'], max_members)
    return text, kb


class ConfirmLeave(StatesGroup):
    confirm = State()


class LeaveTeam(StatesGroup):
    choose_captain = State()


router = Router()


class CreateTeam(StatesGroup):
    name = State()
    sport = State()
    max_members = State()


class RenameTeam(StatesGroup):
    new_name = State()


class AddPlayer(StatesGroup):
    username = State()


class EditMaxMembers(StatesGroup):
    new_max = State()


class SearchTeam(StatesGroup):
    query = State()

# ---------- Существующие обработчики (без изменений) ----------


@router.callback_query(F.data == "my_teams")
async def my_teams(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйся", show_alert=True)
        return

    teams = get_user_teams(user['id'])
    if not teams:
        text = "У вас пока нет команд. Хотите создать?"
        kb = teams_list_keyboard([], show_create=True)
        await callback.message.edit_text(text, reply_markup=kb)
    else:
        text = "Ваши команды:"
        kb = teams_list_keyboard(teams, show_create=True)
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "create_team")
async def create_team_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateTeam.name)
    await callback.message.edit_text("Введите название команды:")
    await callback.answer()


@router.message(CreateTeam.name)
async def create_team_name(message: Message, state: FSMContext):
    name = message.text.strip()
    # Проверка длины
    if len(name) > 20:
        await message.answer("❌ Название команды не должно превышать 20 символов. Попробуйте снова:")
        return  # остаёмся в том же состоянии FSM, пользователь вводит заново
    if len(name) < 3:
        await message.answer("❌ Название команды должно быть не короче 3 символов. Попробуйте снова:")
        return
    # Если всё ок – сохраняем
    await state.update_data(name=name)
    await state.set_state(CreateTeam.sport)
    sports = get_all_sports()
    await message.answer("Выберите вид спорта:", reply_markup=sports_choice_keyboard_no_done(sports))


@router.callback_query(CreateTeam.sport, F.data.startswith("create_team_sport_"))
async def create_team_sport_choice(callback: CallbackQuery, state: FSMContext):
    sport_name = callback.data.replace("create_team_sport_", "")
    await state.update_data(sport=sport_name)
    await state.set_state(CreateTeam.max_members)
    await callback.message.edit_text(
        "Введите максимальное количество участников команды (от 1 до 10):\n"
        "Можно ввести число или выбрать на клавиатуре.",
        reply_markup=input_number_keyboard()
    )
    await callback.answer()


@router.message(CreateTeam.max_members)
async def create_team_max_members_text(message: Message, state: FSMContext):
    try:
        max_members = int(message.text.strip())
        if max_members < 1 or max_members > 10:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 1 до 10.")
        return
    await finish_create_team(message.from_user.id, message, state, max_members)


@router.callback_query(CreateTeam.max_members, F.data.startswith("set_max_members_"))
async def create_team_max_members_callback(callback: CallbackQuery, state: FSMContext):
    max_members = int(callback.data.split("_")[3])
    await callback.message.delete()
    await finish_create_team(callback.from_user.id, callback.message, state, max_members)
    await callback.answer()


async def finish_create_team(user_id, message, state: FSMContext, max_members):
    data = await state.get_data()
    team_name = data['name']
    sport = data['sport']
    user = get_user(user_id)
    if not user:
        await message.answer("Ошибка")
        await state.clear()
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO teams (name, sport, city, captain_id, max_members)
        VALUES (?, ?, ?, ?, ?)
    """, (team_name, sport, user['city'], user['id'], max_members))
    team_id = cur.lastrowid
    cur.execute(
        "INSERT INTO team_members (team_id, user_id) VALUES (?, ?)", (team_id, user['id']))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(f"Команда '{team_name}' создана! Лимит участников: {max_members}.")
    text, kb = await get_team_card_data(team_id, user_id)
    await message.answer(text, reply_markup=kb)

# ВАЖНО: обработчик team_requests_ должен идти до общего team_


@router.callback_query(F.data.startswith("team_requests_"))
async def team_requests_menu(callback: CallbackQuery):
    """Вход в меню заявок команды (показывает список с пагинацией)"""
    team_id = int(callback.data.split("_")[2])
    await show_team_requests_page(callback.message, team_id, 0, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("team_"))
async def view_team(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    team_id = int(callback.data.split("_")[1])
    text, kb = await get_team_card_data(team_id, callback.from_user.id)
    if text is None:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

# ---------- Управление командой (rename, delete, add_player, accept_invite, reject_invite) ----------


@router.callback_query(F.data.startswith("leave_team_"))
async def leave_team_start(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[2])
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    if not is_team_member(user['id'], team_id):
        await callback.answer("Вы не состоите в этой команде", show_alert=True)
        return

    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    await state.update_data(team_id=team_id)

    if is_captain(user['id'], team_id):
        members = get_team_members(team_id)
        other_members = [m for m in members if m['id'] != user['id']]
        if not other_members:
            # Капитан один
            text = "Вы покинете команду и она будет удалена. Вы согласны?"
            await state.set_state(ConfirmLeave.confirm)
            await state.update_data(scenario="captain_alone")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да", callback_data="confirm_leave_yes"),
                 InlineKeyboardButton(text="❌ Нет", callback_data="confirm_leave_no")]
            ])
            await callback.message.edit_text(text, reply_markup=kb)
            await callback.answer()
            return
        else:
            # Капитан с другими → выбор нового капитана
            await state.set_state(LeaveTeam.choose_captain)
            await state.update_data(scenario="captain_with_others")
            await callback.message.edit_text(
                "Вы капитан. Чтобы выйти из команды, передайте капитанство другому участнику:",
                reply_markup=choose_new_captain_keyboard(
                    team_id, other_members)
            )
            await callback.answer()
            return
    else:
        # Обычный игрок
        text = "Вы точно хотите покинуть команду?"
        await state.set_state(ConfirmLeave.confirm)
        await state.update_data(scenario="regular")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="confirm_leave_yes"),
             InlineKeyboardButton(text="❌ Нет", callback_data="confirm_leave_no")]
        ])
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer()


@router.callback_query(ConfirmLeave.confirm, F.data.in_({"confirm_leave_yes", "confirm_leave_no"}))
async def confirm_leave_handler(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    team_id = data.get('team_id')
    scenario = data.get('scenario')
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()
        return

    if callback.data == "confirm_leave_no":
        # Получаем team_id до очистки состояния
        data = await state.get_data()
        team_id = data.get('team_id')
        await state.clear()
        if team_id:
            text, kb = await get_team_card_data(team_id, callback.from_user.id)
            if text:
                await callback.message.edit_text(text, reply_markup=kb)
            else:
                await callback.message.edit_text("Ошибка загрузки команды.")
        else:
            await callback.message.edit_text("Ошибка: команда не найдена.")
        await callback.answer()
        return

    # Подтверждено (Yes)
    if scenario == "captain_alone":
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
        cur.execute("DELETE FROM team_invites WHERE team_id=?", (team_id,))
        cur.execute(
            "DELETE FROM tournament_applications WHERE team_id=?", (team_id,))
        cur.execute(
            "DELETE FROM matches WHERE team1_id=? OR team2_id=?", (team_id, team_id))
        cur.execute("DELETE FROM teams WHERE id=?", (team_id,))
        cur.execute("DELETE FROM ratings WHERE team_id=?", (team_id,))
        conn.commit()
        conn.close()
        await state.clear()
        await callback.message.edit_text("Команда удалена, так как вы были единственным участником.")
        teams = get_user_teams(user['id'])
        await callback.message.answer("Ваши команды:", reply_markup=teams_list_keyboard(teams, show_create=True))
        await callback.answer()
        return

    elif scenario == "regular":
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user['id']))
        cur.execute(
            "DELETE FROM team_invites WHERE team_id=? AND user_id=?", (team_id, user['id']))
        conn.commit()
        conn.close()
        await state.clear()
        await callback.message.edit_text("Вы вышли из команды.")
        teams = get_user_teams(user['id'])
        await callback.message.answer("Ваши команды:", reply_markup=teams_list_keyboard(teams, show_create=True))
        await callback.answer()
        return

    elif scenario == "captain_with_others":
        new_captain_id = data.get('new_captain_id')
        if not new_captain_id:
            await callback.answer("Ошибка: не выбран новый капитан", show_alert=True)
            await state.clear()
            return

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE teams SET captain_id=? WHERE id=?",
                    (new_captain_id, team_id))
        cur.execute(
            "DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user['id']))
        cur.execute(
            "DELETE FROM team_invites WHERE team_id=? AND user_id=?", (team_id, user['id']))
        conn.commit()
        conn.close()

        new_captain = get_user_by_id(new_captain_id)
        if new_captain:
            try:
                await callback.bot.send_message(
                    new_captain['telegram_id'],
                    f"Вы стали капитаном команды «{get_team_by_id(team_id)['name']}», так как прошлый капитан покинул ее!"
                )
            except Exception as e:
                logger.info(f"Не удалось уведомить нового капитана: {e}")

        await state.clear()
        await callback.message.edit_text("Вы передали капитанство и покинули команду.")
        teams = get_user_teams(user['id'])
        await callback.message.answer("Ваши команды:", reply_markup=teams_list_keyboard(teams, show_create=True))
        await callback.answer()

    else:
        await state.clear()
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(LeaveTeam.choose_captain, F.data.startswith("set_captain_"))
async def set_new_captain(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    team_id = int(parts[2])
    new_captain_id = int(parts[3])

    data = await state.get_data()
    stored_team_id = data.get('team_id')
    if stored_team_id != team_id:
        await callback.answer("Ошибка данных", show_alert=True)
        await state.clear()
        return

    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()
        return

    if not is_captain(user['id'], team_id):
        await callback.answer("Вы больше не капитан", show_alert=True)
        await state.clear()
        return

    if not is_team_member(new_captain_id, team_id):
        await callback.answer("Этот пользователь не в команде", show_alert=True)
        await state.clear()
        return

    # Сохраняем выбранного нового капитана
    await state.update_data(new_captain_id=new_captain_id, scenario="captain_with_others")
    # Переходим к подтверждению
    await state.set_state(ConfirmLeave.confirm)
    text = "Вы точно хотите покинуть команду? Капитанство будет передано выбранному участнику."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_leave_yes"),
         InlineKeyboardButton(text="❌ Нет", callback_data="confirm_leave_no")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rename_team_"))
async def rename_team_start(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[2])
    await state.update_data(team_id=team_id)
    await state.set_state(RenameTeam.new_name)
    await callback.message.edit_text("Введите новое название команды:")
    await callback.answer()


@router.message(RenameTeam.new_name)
async def rename_team_name(message: Message, state: FSMContext):
    data = await state.get_data()
    team_id = data.get('team_id')
    if not team_id:
        await message.answer("Ошибка: команда не найдена")
        await state.clear()
        return

    new_name = message.text.strip()
    if not new_name:
        await message.answer("Название не может быть пустым.")
        return
    if len(new_name) > 20:
        await message.answer("❌ Название команды не должно превышать 20 символов. Попробуйте снова:")
        return
    if len(new_name) < 3:
        await message.answer("❌ Название команды должно быть не короче 3 символов. Попробуйте снова:")
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET name=? WHERE id=?", (new_name, team_id))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("Название изменено!")
    text, kb = await get_team_card_data(team_id, message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("delete_team_"))
async def delete_team_confirm(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[2])
    user = get_user(callback.from_user.id)
    if not user or not is_captain(user['id'], team_id):
        await callback.answer("Вы не капитан", show_alert=True)
        return

    await state.update_data(team_id=team_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить",
                              callback_data="confirm_delete_team")],
        [InlineKeyboardButton(text="❌ Нет", callback_data=f"team_{team_id}")]
    ])
    await callback.message.edit_text("Вы уверены, что хотите удалить команду? Это действие нельзя отменить.", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "confirm_delete_team")
async def delete_team_execute(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    team_id = data.get('team_id')
    if not team_id:
        await callback.answer("Ошибка: команда не найдена", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user or not is_captain(user['id'], team_id):
        await callback.answer("Вы не капитан", show_alert=True)
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM team_invites WHERE team_id=?", (team_id,))
    cur.execute(
        "DELETE FROM tournament_applications WHERE team_id=?", (team_id,))
    cur.execute("DELETE FROM matches WHERE team1_id=? OR team2_id=?",
                (team_id, team_id))
    cur.execute("DELETE FROM teams WHERE id=?", (team_id,))
    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text("Команда удалена.")
    teams = get_user_teams(user['id'])
    await callback.message.answer("Ваши команды:", reply_markup=teams_list_keyboard(teams, show_create=True))
    await callback.answer()


@router.callback_query(F.data.startswith("add_player_"))
async def add_player_start(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[2])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user or not is_captain(user['id'], team_id):
        await callback.answer("Вы не капитан", show_alert=True)
        return

    await state.update_data(team_id=team_id)
    await state.set_state(AddPlayer.username)
    await callback.message.edit_text("Введите username игрока (можно с @ или без):")
    await callback.answer()


@router.message(AddPlayer.username)
async def add_player_username(message: Message, state: FSMContext):
    username = message.text.strip()
    if username.startswith('@'):
        username = username[1:]

    user_to_add = get_user_by_username(username)
    if not user_to_add:
        await message.answer("Пользователь с таким username не найден. Попробуйте ещё раз или /cancel")
        return

    data = await state.get_data()
    team_id = data['team_id']
    current_user = get_user(message.from_user.id)

    if user_to_add['id'] == current_user['id']:
        await message.answer("Вы уже капитан этой команды.")
        await state.clear()
        return

    if is_team_member(user_to_add['id'], team_id):
        await message.answer("Этот игрок уже в команде.")
        await state.clear()
        return

    if has_pending_invite(user_to_add['id'], team_id):
        await message.answer("Приглашение этому игроку уже отправлено.")
        await state.clear()
        return
    current_count = get_team_members_count(team_id)
    max_members = get_team_max_members(team_id)
    if current_count >= max_members:
        await message.answer("❌ В команде уже максимальное количество участников.")
        return
    create_invite(team_id, user_to_add['id'])
    team = get_team_by_id(team_id)
    try:
        await message.bot.send_message(
            user_to_add['telegram_id'],
            f"Вас приглашают в команду «{team['name']}» (капитан: @{current_user['username']})!",
            reply_markup=invite_keyboard(team_id)
        )
        await message.answer(f"Приглашение отправлено пользователю @{username}.")
        text, kb = await get_team_card_data(team_id, message.from_user.id)
        await message.answer(text, reply_markup=kb)
    except Exception as e:
        logger.info(f"Ошибка отправки сообщения: {e}")
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM team_invites WHERE team_id=? AND user_id=?",
                    (team_id, user_to_add['id']))
        conn.commit()
        conn.close()
        await message.answer("Не удалось отправить приглашение: пользователь ещё не запускал бота.")
    await state.clear()


@router.callback_query(F.data.startswith("accept_invite_"))
async def accept_invite_handler(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[2])
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    if not has_pending_invite(user['id'], team_id):
        await callback.message.edit_text("Приглашение уже недействительно.")
        await callback.answer()
        return
    current_count = get_team_members_count(team_id)
    max_members = get_team_max_members(team_id)
    if current_count >= max_members:
        await callback.answer("❌ В команде уже максимальное количество участников.", show_alert=True)
        return
    accept_invite(team_id, user['id'])
    team = get_team_by_id(team_id)
    await callback.message.edit_text(f"Вы вступили в команду «{team['name']}»!")
    await callback.answer()


@router.callback_query(F.data.startswith("reject_invite_"))
async def reject_invite_handler(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[2])
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    if not has_pending_invite(user['id'], team_id):
        await callback.message.edit_text("Приглашение уже недействительно.")
        await callback.answer()
        return

    reject_invite(team_id, user['id'])
    await callback.message.edit_text("Вы отклонили приглашение.")
    await callback.answer()

# ========== НОВЫЙ РАЗДЕЛ: ПОИСК КОМАНД И ЗАЯВКИ ==========


@router.callback_query(F.data == "teams_menu")
async def teams_main_menu(callback: CallbackQuery):
    """Главное меню раздела 'Команды'"""
    await callback.message.edit_text("Выберите действие:", reply_markup=teams_main_keyboard())
    await callback.answer()

# --- Просмотр всех команд (без возможности подачи заявки) ---


@router.callback_query(F.data == "teams_list_all")
async def list_all_teams_sports(callback: CallbackQuery):
    sports = get_all_sports()
    await callback.message.edit_text(
        "Выберите вид спорта:",
        reply_markup=teams_sports_filter_keyboard(sports, "all")
    )
    await callback.answer()


@router.callback_query(F.data == "teams_list_open")
async def list_open_teams_sports(callback: CallbackQuery):
    sports = get_all_sports()
    await callback.message.edit_text(
        "Выберите вид спорта:",
        reply_markup=teams_sports_filter_keyboard(sports, "open")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("teams_filter_all_"))
async def filter_all_teams_by_sport(callback: CallbackQuery):
    sport = callback.data.replace("teams_filter_all_", "")
    await show_all_teams_page(callback.message, sport, 0, edit=True)
    await callback.answer()


async def show_all_teams_page(message, sport, offset, edit=False):
    sport_display = get_sport_display_name(sport)
    teams = get_teams_by_sport(sport, only_open=False, offset=offset, limit=10)
    total = get_teams_count_by_sport(sport, only_open=False)
    if not teams:
        text = f"Команд по виду спорта {sport_display} пока нет."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К выбору спорта",
                                  callback_data="teams_list_all")]
        ])
    else:
        text = f"Команды по {sport_display}:"
        builder = InlineKeyboardBuilder()
        for team in teams:
            builder.button(
                text=team['name'], callback_data=f"view_all_team_{team['id']}")
        builder.adjust(2)
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton(
                text="◀️", callback_data=f"all_teams_page_{sport}_{offset-10}"))
        if offset + 10 < total:
            nav.append(InlineKeyboardButton(
                text="▶️", callback_data=f"all_teams_page_{sport}_{offset+10}"))
        if nav:
            builder.row(*nav)
        builder.row(InlineKeyboardButton(
            text="🔙 К выбору спорта", callback_data="teams_list_all"))
        kb = builder.as_markup()
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("all_teams_page_"))
async def all_teams_page_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    sport = parts[3]
    offset = int(parts[4])
    await show_all_teams_page(callback.message, sport, offset, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("teams_filter_open_"))
async def filter_open_teams_by_sport(callback: CallbackQuery):
    sport = callback.data.replace("teams_filter_open_", "")
    await show_open_teams_page(callback.message, sport, 0, edit=True)
    await callback.answer()


async def show_open_teams_page(message, sport, offset, edit=False):
    sport_display = get_sport_display_name(sport)
    teams = get_teams_by_sport(sport, only_open=True, offset=offset, limit=10)
    total = get_teams_count_by_sport(sport, only_open=True)
    if not teams:
        text = f"Открытых команд по виду спорта {sport_display} пока нет."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К выбору спорта",
                                  callback_data="teams_list_open")]
        ])
    else:
        text = f"Открытые команды по {sport_display}:"
        builder = InlineKeyboardBuilder()
        for team in teams:
            builder.button(
                text=team['name'], callback_data=f"view_open_team_{team['id']}")
        builder.adjust(2)
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton(
                text="◀️", callback_data=f"open_teams_page_{sport}_{offset-10}"))
        if offset + 10 < total:
            nav.append(InlineKeyboardButton(
                text="▶️", callback_data=f"open_teams_page_{sport}_{offset+10}"))
        if nav:
            builder.row(*nav)
        builder.row(InlineKeyboardButton(
            text="🔙 К выбору спорта", callback_data="teams_list_open"))
        kb = builder.as_markup()
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("open_teams_page_"))
async def open_teams_page_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    sport = parts[3]
    offset = int(parts[4])
    await show_open_teams_page(callback.message, sport, offset, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("view_all_team_"))
async def view_all_team(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[3])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    captain = get_user_by_id(team['captain_id'])
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Неизвестно"
    members = get_team_members(team_id)
    members_count = len(members)
    max_members = get_team_max_members(team_id)
    members_list_lines = []
    for m in members:
        age_str = f"[{m['age']} лет]" if m['age'] else "[возраст не указан]"
        members_list_lines.append(
            f"- {m['first_name']} (@{m['username']}) {age_str}")
    members_list = "\n".join(members_list_lines)
    settings = get_team_settings(team_id)
    sport = team['sport']
    sport_display = get_sport_display_name(sport)
    text = f"""
Команда: {team['name']}
Вид спорта: {sport_display}
Город: {team['city']}
Капитан: {captain_name}
Участников: {members_count} / {max_members}
Набор: {"🔓 Открыт" if settings['is_open'] else "🔒 Закрыт"}
Состав:
{members_list}
    """
    await callback.message.edit_text(text, reply_markup=team_view_only_keyboard(team_id, sport))
    await callback.answer()


@router.callback_query(F.data.startswith("view_open_team_"))
async def view_open_team(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[3])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    members = get_team_members(team_id)
    members_count = len(members)
    max_members = get_team_max_members(team_id)
    members_list_lines = []
    for m in members:
        age_str = f"[{m['age']} лет]" if m['age'] else "[возраст не указан]"
        members_list_lines.append(
            f"- {m['first_name']} (@{m['username']}) {age_str}")
    members_list = "\n".join(members_list_lines)

    captain = get_user_by_id(team['captain_id'])
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Неизвестно"
    settings = get_team_settings(team_id)

    # Проверяем, может ли пользователь подать заявку
    is_member = is_team_member(user['id'], team_id)
    has_request = has_pending_request(user['id'], team_id)
    can_apply = not is_member and not has_request and settings['is_open']
    sport_display = get_sport_display_name(team['sport'])

    text = f"""
Команда: {team['name']}
Вид спорта: {sport_display}
Город: {team['city']}
Капитан: {captain_name}
Участников: {members_count} / {max_members}
Набор: {"🔓 Открыт" if settings['is_open'] else "🔒 Закрыт"}
Состав:
{members_list}
    """
    sport = team['sport']
    await callback.message.edit_text(text, reply_markup=team_view_join_keyboard(team_id, can_apply, sport))
    await callback.answer()

# --- Поиск команды по названию ---


@router.callback_query(F.data == "teams_search")
async def teams_search_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchTeam.query)
    await callback.message.edit_text("Введите название команды для поиска:")
    await callback.answer()


@router.message(SearchTeam.query)
async def teams_search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()
    await show_search_results(message, query, 0)


async def show_search_results(message, query, offset, edit=False):
    teams = search_teams_by_name(query, offset, 10)
    total = search_teams_count(query)
    if not teams:
        text = f"По запросу «{query}» ничего не найдено."
        kb = back_to_teams_menu_keyboard()
    else:
        text = f"Результаты поиска по запросу «{query}»:"
        builder = InlineKeyboardBuilder()
        for team in teams:
            builder.button(
                text=team['name'], callback_data=f"view_search_team_{team['id']}_{query}")
        builder.adjust(2)
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton(
                text="◀️", callback_data=f"search_page_{query}_{offset-10}"))
        if offset + 10 < total:
            nav.append(InlineKeyboardButton(
                text="▶️", callback_data=f"search_page_{query}_{offset+10}"))
        if nav:
            builder.row(*nav)
        builder.row(InlineKeyboardButton(
            text="🔙 В меню команд", callback_data="teams_menu"))
        kb = builder.as_markup()
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("search_page_"))
async def search_page_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    query = parts[2]
    offset = int(parts[3])
    await show_search_results(callback.message, query, offset, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("view_search_team_"))
async def view_search_team(callback: CallbackQuery):
    parts = callback.data.split("_")
    team_id = int(parts[3])
    query = parts[4] if len(parts) > 4 else ""
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    members = get_team_members(team_id)
    members_count = len(members)
    max_members = get_team_max_members(team_id)
    members_list_lines = []
    for m in members:
        age_str = f"[{m['age']} лет]" if m['age'] else "[возраст не указан]"
        members_list_lines.append(
            f"- {m['first_name']} (@{m['username']}) {age_str}")
    members_list = "\n".join(members_list_lines)

    captain = get_user_by_id(team['captain_id'])
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Неизвестно"
    settings = get_team_settings(team_id)

    is_member = is_team_member(user['id'], team_id)
    has_request = has_pending_request(user['id'], team_id)
    can_apply = not is_member and not has_request and settings['is_open']
    sport_display = get_sport_display_name(team['sport'])

    text = f"""
Команда: {team['name']}
Вид спорта: {sport_display}
Город: {team['city']}
Капитан: {captain_name}
Участников: {members_count} / {max_members}
Набор: {"🔓 Открыт" if settings['is_open'] else "🔒 Закрыт"}
Состав:
{members_list}
    """
    await callback.message.edit_text(text, reply_markup=team_view_search_keyboard(team_id, can_apply, query))
    await callback.answer()

# --- Обработчики подачи заявки и уведомления ---


@router.callback_query(F.data.startswith("apply_team_"))
async def apply_to_team(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[2])
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    if is_team_member(user['id'], team_id):
        await callback.answer("Вы уже в этой команде", show_alert=True)
        return

    # Проверка лимита – добавляем перед проверкой статуса
    current_count = get_team_members_count(team_id)
    max_members = get_team_max_members(team_id)
    if current_count >= max_members:
        await callback.answer("❌ В команде уже максимальное количество участников.", show_alert=True)
        return

    existing_status = get_team_invite_status(team_id, user['id'])

    if existing_status == 'pending':
        await callback.answer("Заявка уже отправлена", show_alert=True)
        return
    if existing_status == 'accepted':
        await callback.answer("Вы уже в этой команде", show_alert=True)
        return

    settings = get_team_settings(team_id)
    if not settings['is_open']:
        await callback.answer("Набор в команду закрыт", show_alert=True)
        return

    if existing_status == 'rejected':
        # Обновляем существующую запись на 'pending'
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE team_invites
            SET status='pending', type='request'
            WHERE team_id=? AND user_id=?
        """, (team_id, user['id']))
        conn.commit()
        conn.close()
    else:
        # Создаём новую запись
        create_team_request(team_id, user['id'])

    team = get_team_by_id(team_id)

    if settings['notify']:
        fav_sports = json.loads(
            user['favorite_sports']) if user['favorite_sports'] else []
        sports_str = ", ".join(map_sports_to_display(fav_sports)) if fav_sports else "не указаны"
        age_str = f"{user['age']} лет" if user['age'] else "не указан"
        text = (
            f"📩 Новая заявка на вступление в команду «{team['name']}»!\n\n"
            f"👤 {user['first_name']} {user['last_name']} (@{user['username']})\n"
            f"📧 Email: {user['email']}\n"
            f"🏙 Город: {user['city']}\n"
            f"🎂 Возраст: {age_str}\n"
            f"🎯 Любимые виды спорта: {sports_str}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_req_{team_id}_{user['id']}"),
             InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_req_{team_id}_{user['id']}")]
        ])
        captain = get_user_by_id(team['captain_id'])
        if captain:
            try:
                await callback.bot.send_message(captain['telegram_id'], text, reply_markup=kb)
            except Exception as e:
                logger.info(
                    f"Не удалось отправить уведомление капитану {captain['telegram_id']}: {e}")
        else:
            logger.info(f"Капитан с id {team['captain_id']} не найден")

        await callback.answer("Заявка отправлена! Ожидайте решения капитана.", show_alert=True)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К команде",
                                  callback_data=f"team_{team_id}")]
        ])
        await callback.message.edit_text("Заявка отправлена! Ожидайте решения капитана.", reply_markup=kb)

# --- Обработчики заявок для капитана ---


async def show_team_requests_page(message, team_id, offset, edit=False):
    requests = get_team_requests(team_id, offset, 10)
    total = get_team_requests_count(team_id)
    if not requests:
        text = "Нет заявок на вступление."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔙 Назад", callback_data=f"team_{team_id}")]
        ])
    else:
        text = f"Заявки в команду (всего: {total}):"
        kb = team_requests_keyboard(requests, offset, team_id, total)
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("team_reqs_page_"))
async def team_reqs_page_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    team_id = int(parts[3])
    offset = int(parts[4])
    await show_team_requests_page(callback.message, team_id, offset, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("accept_req_"))
async def accept_request_handler(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) == 3:
        request_id = int(parts[2])
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT team_id, user_id FROM team_invites WHERE id=?", (request_id,))
        req = cur.fetchone()
        conn.close()
        if not req:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        team_id, user_id = req['team_id'], req['user_id']
        accept_request(request_id)
    else:
        team_id = int(parts[2])
        user_id = int(parts[3])
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM team_invites WHERE team_id=? AND user_id=? AND type='request'", (team_id, user_id))
        row = cur.fetchone()
        conn.close()
        if not row:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        request_id = row['id']
        current_count = get_team_members_count(team_id)
        max_members = get_team_max_members(team_id)
        if current_count >= max_members:
            await callback.answer("❌ В команде уже максимальное количество участников.", show_alert=True)
            return
        accept_request(request_id)

    team = get_team_by_id(team_id)
    user = get_user_by_id(user_id)
    try:
        await callback.bot.send_message(user['telegram_id'],
                                        f"✅ Ваша заявка в команду «{team['name']}» принята! Теперь вы в составе.")
    except Exception as e:
        logger.info(f"Не удалось отправить уведомление игроку {user_id}: {e}")

    await callback.answer("Заявка принята!", show_alert=True)
    await show_team_requests_page(callback.message, team_id, 0, edit=True)


@router.callback_query(F.data.startswith("reject_req_"))
async def reject_request_handler(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) == 3:
        request_id = int(parts[2])
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT team_id, user_id FROM team_invites WHERE id=?", (request_id,))
        req = cur.fetchone()
        conn.close()
        if not req:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        team_id, user_id = req['team_id'], req['user_id']
        reject_request(request_id)
    else:
        team_id = int(parts[2])
        user_id = int(parts[3])
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE team_invites SET status='rejected' WHERE team_id=? AND user_id=? AND type='request'", (team_id, user_id))
        conn.commit()
        conn.close()

    team = get_team_by_id(team_id)
    user = get_user_by_id(user_id)
    try:
        await callback.bot.send_message(user['telegram_id'],
                                        f"❌ Ваша заявка в команду «{team['name']}» отклонена.")
    except Exception as e:
        logger.info(f"Не удалось отправить уведомление игроку {user_id}: {e}")

    await callback.answer("Заявка отклонена!", show_alert=True)
    await show_team_requests_page(callback.message, team_id, 0, edit=True)

# --- Переключатели ---


@router.callback_query(F.data.startswith("toggle_open_"))
async def toggle_team_open(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[2])
    settings = get_team_settings(team_id)
    new_status = not settings['is_open']
    set_team_open_status(team_id, new_status)
    await callback.answer(f"Приём заявок {'открыт' if new_status else 'закрыт'}.")
    user = get_user(callback.from_user.id)
    is_capt = user and is_captain(user['id'], team_id)
    await callback.message.edit_reply_markup(reply_markup=team_management_extended_keyboard(
        team_id, is_capt, new_status, settings['notify']))


@router.callback_query(F.data.startswith("toggle_notify_"))
async def toggle_team_notify(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[2])
    settings = get_team_settings(team_id)
    new_status = not settings['notify']
    set_team_notify_status(team_id, new_status)
    await callback.answer(f"Уведомления {'включены' if new_status else 'выключены'}.")
    user = get_user(callback.from_user.id)
    is_capt = user and is_captain(user['id'], team_id)
    await callback.message.edit_reply_markup(reply_markup=team_management_extended_keyboard(
        team_id, is_capt, settings['is_open'], new_status))


@router.callback_query(F.data.startswith("edit_max_members_"))
async def edit_max_members_start(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[3])
    user = get_user(callback.from_user.id)
    if not user or not is_captain(user['id'], team_id):
        await callback.answer("Вы не капитан", show_alert=True)
        return
    await state.update_data(team_id=team_id)
    await state.set_state(EditMaxMembers.new_max)
    await callback.message.edit_text(
        "Введите новый лимит участников (от 1 до 10):",
        reply_markup=input_number_keyboard()
    )
    await callback.answer()


@router.message(EditMaxMembers.new_max)
async def edit_max_members_text(message: Message, state: FSMContext):
    try:
        new_max = int(message.text.strip())
        if new_max < 1 or new_max > 10:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 1 до 10.")
        return
    await finish_edit_max_members(message.from_user.id, message, state, new_max)


@router.callback_query(EditMaxMembers.new_max, F.data.startswith("set_max_members_"))
async def edit_max_members_callback(callback: CallbackQuery, state: FSMContext):
    new_max = int(callback.data.split("_")[3])
    await callback.message.delete()
    await finish_edit_max_members(callback.from_user.id, callback.message, state, new_max)
    await callback.answer()


async def finish_edit_max_members(user_id, message, state: FSMContext, new_max):
    data = await state.get_data()
    team_id = data['team_id']
    current_count = get_team_members_count(team_id)
    if new_max < current_count:
        await message.answer(f"❌ Нельзя установить лимит меньше текущего количества участников ({current_count}).")
        return
    update_team_max_members(team_id, new_max)
    await state.clear()
    await message.answer("Лимит изменен!")
    text, kb = await get_team_card_data(team_id, user_id)
    await message.answer(text, reply_markup=kb)