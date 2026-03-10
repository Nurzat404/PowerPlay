from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import get_connection
from utils import (
    get_user, get_user_teams, is_captain, get_team_by_id, get_team_members,
    get_user_by_username, is_team_member, has_pending_invite,
    create_invite, accept_invite, reject_invite, get_user_by_id, get_all_sports
)
from keyboards import (
    teams_list_keyboard, team_management_keyboard, main_menu_keyboard,
    back_to_main_keyboard, confirm_keyboard, invite_keyboard, sports_choice_keyboard
)

router = Router()


class CreateTeam(StatesGroup):
    name = State()
    sport = State()


class RenameTeam(StatesGroup):
    new_name = State()


class AddPlayer(StatesGroup):
    username = State()


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
    await state.update_data(name=message.text)
    await state.set_state(CreateTeam.sport)
    sports = get_all_sports()
    await message.answer("Выберите вид спорта:", reply_markup=sports_choice_keyboard(sports))


@router.callback_query(CreateTeam.sport, F.data.startswith("sport_"))
async def create_team_sport_choice(callback: CallbackQuery, state: FSMContext):
    sport_name = callback.data.replace("sport_", "")
    await state.update_data(sport=sport_name)
    data = await state.get_data()
    team_name = data['name']
    sport = data['sport']
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO teams (name, sport, city, captain_id)
        VALUES (?, ?, ?, ?)
    """, (team_name, sport, user['city'], user['id']))
    team_id = cur.lastrowid
    cur.execute(
        "INSERT INTO team_members (team_id, user_id) VALUES (?, ?)", (team_id, user['id']))
    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text(f"Команда '{team_name}' создана!")
    await callback.answer()


@router.callback_query(F.data.startswith("team_"))
async def view_team(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[1])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена")
        return

    members = get_team_members(team_id)
    members_list = "\n".join(
        [f"- {m['first_name']} (@{m['username']})" for m in members])
    captain = get_user_by_id(team['captain_id'])
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Неизвестно"
    text = f"""
Команда: {team['name']}
Вид спорта: {team['sport']}
Город: {team['city']}
Капитан: {captain_name}
Состав:
{members_list}
    """
    user = get_user(callback.from_user.id)
    is_capt = user and is_captain(user['id'], team_id)
    await callback.message.edit_text(text, reply_markup=team_management_keyboard(team_id, is_capt))
    await callback.answer()

# ---------- Редактирование названия команды ----------


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

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET name=? WHERE id=?", (new_name, team_id))
    conn.commit()
    conn.close()
    await state.clear()
    user = get_user(message.from_user.id)
    teams = get_user_teams(user['id'])
    await message.answer(f"Название команды изменено на '{new_name}'.")
    await message.answer("Ваши команды:", reply_markup=teams_list_keyboard(teams, show_create=True))

# ---------- Удаление команды ----------


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

# ---------- Добавление игроков ----------


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

    create_invite(team_id, user_to_add['id'])
    team = get_team_by_id(team_id)
    try:
        await message.bot.send_message(
            user_to_add['telegram_id'],
            f"Вас приглашают в команду «{team['name']}» (капитан: @{current_user['username']})!",
            reply_markup=invite_keyboard(team_id)
        )
        await message.answer(f"Приглашение отправлено пользователю @{username}.")
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")
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
