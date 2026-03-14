from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import get_connection
from aiogram import Bot
from utils import (
    is_admin, get_pending_applications, approve_application, reject_application,
    update_team_rating, get_all_users, search_users, get_user_by_id,
    update_user_role, toggle_user_ban, update_team_rating_same_conn,
    get_team_members, get_tournament_by_id, get_team_by_id,
    delete_tournament, get_all_sports, reset_sport_rating,
    get_teams_with_rating, reset_team_rating, deduct_team_points,
    get_all_teams, delete_team_admin, parse_russian_date, parse_russian_datetime,
    get_user, get_user_teams, get_team_application, get_approved_teams_count
)
from keyboards import (
    admin_menu_keyboard, back_to_main_keyboard,
    admin_rating_menu_keyboard, admin_rating_sport_actions_keyboard,
    admin_rating_teams_list_keyboard, admin_rating_team_actions_keyboard,
    admin_teams_list_keyboard
)
from datetime import datetime


async def send_tournament_info(bot: Bot, chat_id: int, tournament_id: int, user_id: int):
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await bot.send_message(chat_id, "Турнир не найден.")
        return
    user = get_user(user_id)
    teams = get_user_teams(user['id']) if user else []
    approved_count = get_approved_teams_count(tournament_id)
    can_apply = False
    if user:
        for team in teams:
            if team['sport'] == tournament['sport']:
                status = get_team_application(tournament_id, team['id'])
                if status is None and approved_count < tournament['max_teams']:
                    can_apply = True
                    break
    status_map = {
        'registration': 'Регистрация',
        'active': 'Активен',
        'finished': 'Завершён'
    }
    status_display = status_map.get(tournament['status'], tournament['status'])
    text = f"""
🏆 {tournament['name']}
Вид спорта: {tournament['sport']}
Требуемый размер команды: {tournament['required_team_size']} чел.
Город: {tournament['city']}
Даты: {tournament['start_date']} - {tournament['end_date']}
Макс. команд: {tournament['max_teams']}
Статус: {status_display}
"""
    if tournament['description']:
        text += f"\n📝 Описание: {tournament['description']}"
    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="📝 Подать заявку",
                       callback_data=f"apply_{tournament_id}")
    builder.button(text="📋 Список команд",
                   callback_data=f"tournament_teams_{tournament_id}")
    if user and is_admin(user['telegram_id']):
        builder.button(text="✏️ Редактировать",
                       callback_data=f"admin_edit_tournament_{tournament_id}")
        builder.button(text="🗑 Удалить турнир",
                       callback_data=f"admin_delete_tournament_{tournament_id}")
    builder.button(
        text="🔙 Назад", callback_data=f"tournament_sport_{tournament['sport']}")
    builder.adjust(1)
    await bot.send_message(chat_id, text, reply_markup=builder.as_markup())
router = Router()

# ---------- Создание турнира ----------


class CreateTournament(StatesGroup):
    name = State()
    sport = State()
    city = State()
    start_date = State()
    end_date = State()
    max_teams = State()
    required_team_size = State()
    description = State()


@router.callback_query(F.data == "admin_create_tournament")
async def admin_create_tournament_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(CreateTournament.name)
    await callback.message.edit_text("Введите название турнира:")
    await callback.answer()


@router.message(CreateTournament.name)
async def create_tournament_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(CreateTournament.sport)
    await message.answer("Введите вид спорта (например, CS2):")


@router.message(CreateTournament.sport)
async def create_tournament_sport(message: Message, state: FSMContext):
    await state.update_data(sport=message.text)
    await state.set_state(CreateTournament.city)
    await message.answer("Введите город проведения:")


@router.message(CreateTournament.city)
async def create_tournament_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text)
    await state.set_state(CreateTournament.start_date)
    await message.answer("Введите дату начала турнира (в формате: день и сокращённое название месяца с точкой, например : 1 янв.):")


@router.message(CreateTournament.start_date)
async def create_tournament_start(message: Message, state: FSMContext):
    date_str = message.text.strip()
    if parse_russian_date(date_str) is None:
        await message.answer("❌ Неверный формат. Введите дату в формате: день месяц (например: 1 янв.)\n"
                             "Подсказка по месяцам:\n"
                             "'янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'"
                             )
        return
    await state.update_data(start_date=date_str)
    await state.set_state(CreateTournament.end_date)
    await message.answer("Введите дату окончания турнира (в формате: день и сокращённое название месяца с точкой, например: 2 февр.):")


@router.message(CreateTournament.end_date)
async def create_tournament_end(message: Message, state: FSMContext):
    date_str = message.text.strip()
    if parse_russian_date(date_str) is None:
        await message.answer("❌ Неверный формат. Введите дату в формате: день месяц (например: 2 февр.)\n"
                             "Подсказка по месяцам:\n"
                             "'янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'"
                             )
        return
    await state.update_data(end_date=date_str)
    await state.set_state(CreateTournament.max_teams)
    await message.answer("Введите максимальное количество команд (число):")


@router.message(CreateTournament.max_teams)
async def create_tournament_max(message: Message, state: FSMContext):
    try:
        max_teams = int(message.text)
    except ValueError:
        await message.answer("Введите число!")
        return
    await state.update_data(max_teams=max_teams)
    await state.set_state(CreateTournament.required_team_size)
    await message.answer("Введите требуемое количество игроков в команде для участия в турнире (от 1 до 10):")


@router.message(CreateTournament.required_team_size)
async def create_tournament_required_size(message: Message, state: FSMContext):
    try:
        size = int(message.text)
        if size < 1 or size > 10:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 1 до 10.")
        return
    await state.update_data(required_team_size=size)
    await state.set_state(CreateTournament.description)
    await message.answer("Введите описание турнира (можно отправить 'нет', чтобы пропустить):")


@router.message(CreateTournament.description)
async def create_tournament_description(message: Message, state: FSMContext):
    data = await state.get_data()
    description = message.text if message.text.lower() != 'нет' else ''
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tournaments (name, sport, city, start_date, end_date, max_teams, required_team_size, description, created_by, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'registration')
    """, (data['name'], data['sport'], data['city'], data['start_date'], data['end_date'], data['max_teams'], data['required_team_size'], description, message.from_user.id))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("Турнир создан!", reply_markup=admin_menu_keyboard())

# ---------- Редактирование турнира ----------


class EditTournament(StatesGroup):
    field = State()
    value = State()


@router.callback_query(F.data.startswith("admin_edit_tournament_"))
async def edit_tournament_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament_id = int(callback.data.split("_")[3])
    await state.update_data(tournament_id=tournament_id)
    builder = InlineKeyboardBuilder()
    fields = ["name", "sport", "city", "start_date", "end_date",
              "max_teams", "required_team_size", "description"]
    for f in fields:
        builder.button(text=f, callback_data=f"edit_field_{f}")
    builder.button(text="🔙 Назад", callback_data=f"tournament_{tournament_id}")
    builder.adjust(2)
    await callback.message.edit_text("Выберите поле для редактирования:", reply_markup=builder.as_markup())
    await state.set_state(EditTournament.field)


@router.callback_query(EditTournament.field, F.data.startswith("edit_field_"))
async def edit_tournament_field(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("edit_field_", "")
    await state.update_data(field=field)
    await state.set_state(EditTournament.value)
    await callback.message.edit_text(f"Введите новое значение для поля '{field}':")
    await callback.answer()


@router.message(EditTournament.value)
async def edit_tournament_value(message: Message, state: FSMContext):
    data = await state.get_data()
    tournament_id = data['tournament_id']
    field = data['field']
    new_value = message.text
    if field == "max_teams" or field == "required_team_size":
        try:
            new_value = int(new_value)
            if field == "required_team_size" and (new_value < 1 or new_value > 10):
                await message.answer("❌ Требуемый размер команды должен быть от 1 до 10.")
                return
        except ValueError:
            await message.answer("Введите число!")
            return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE tournaments SET {field}=? WHERE id=?", (new_value, tournament_id))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("Турнир обновлён!")
    await send_tournament_info(message.bot, message.chat.id, tournament_id, message.from_user.id)

# ---------- Заявки на турниры ----------


@router.callback_query(F.data == "admin_applications")
async def admin_applications(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    apps = get_pending_applications()
    if not apps:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Нет заявок.", reply_markup=kb)
        await callback.answer()
        return

    text = "Заявки на турниры:\n"
    builder = InlineKeyboardBuilder()
    for app in apps:
        text += f"\nID {app['id']}: {app['team_name']} -> {app['tournament_name']}"
        builder.button(text=f"✅ {app['id']}",
                       callback_data=f"approve_{app['id']}")
        builder.button(text=f"❌ {app['id']}",
                       callback_data=f"reject_{app['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(2)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("approve_"))
async def approve_app(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    app_id = int(callback.data.split("_")[1])
    approve_application(app_id)
    await callback.answer("Заявка одобрена!")
    await admin_applications(callback)


@router.callback_query(F.data.startswith("reject_"))
async def reject_app(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    app_id = int(callback.data.split("_")[1])
    reject_application(app_id)
    await callback.answer("Заявка отклонена!")
    await admin_applications(callback)

# ---------- Создание матча ----------


class CreateMatch(StatesGroup):
    tournament = State()
    team1 = State()
    team2 = State()
    date = State()
    location = State()


@router.callback_query(F.data == "admin_create_match")
async def admin_create_match_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM tournaments WHERE status IN ('registration', 'active')")
    tournaments = cur.fetchall()
    conn.close()
    if not tournaments:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Нет доступных турниров.", reply_markup=kb)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for t in tournaments:
        builder.button(
            text=t['name'], callback_data=f"match_tournament_{t['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text("Выберите турнир:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("match_tournament_"))
async def match_choose_tournament(callback: CallbackQuery, state: FSMContext):
    tournament_id = int(callback.data.split("_")[2])
    await state.update_data(tournament_id=tournament_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.name FROM teams t
        JOIN tournament_applications a ON t.id = a.team_id
        WHERE a.tournament_id=? AND a.status='approved'
    """, (tournament_id,))
    teams = cur.fetchall()
    conn.close()
    if len(teams) < 2:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Недостаточно команд в турнире (нужно минимум 2).", reply_markup=kb)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(text=team['name'],
                       callback_data=f"match_team1_{team['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_create_match")
    builder.adjust(1)
    await callback.message.edit_text("Выберите ПЕРВУЮ команду:", reply_markup=builder.as_markup())
    await state.set_state(CreateMatch.team1)
    await callback.answer()


@router.callback_query(CreateMatch.team1, F.data.startswith("match_team1_"))
async def match_choose_team1(callback: CallbackQuery, state: FSMContext):
    team1_id = int(callback.data.split("_")[2])
    await state.update_data(team1_id=team1_id)
    data = await state.get_data()
    tournament_id = data['tournament_id']
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.name FROM teams t
        JOIN tournament_applications a ON t.id = a.team_id
        WHERE a.tournament_id=? AND a.status='approved' AND t.id != ?
    """, (tournament_id, team1_id))
    teams = cur.fetchall()
    conn.close()
    if not teams:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Нет других команд в турнире.", reply_markup=kb)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(text=team['name'],
                       callback_data=f"match_team2_{team['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_create_match")
    builder.adjust(1)
    await callback.message.edit_text("Выберите ВТОРУЮ команду:", reply_markup=builder.as_markup())
    await state.set_state(CreateMatch.team2)
    await callback.answer()


@router.callback_query(CreateMatch.team2, F.data.startswith("match_team2_"))
async def match_choose_team2(callback: CallbackQuery, state: FSMContext):
    team2_id = int(callback.data.split("_")[2])
    await state.update_data(team2_id=team2_id)
    await state.set_state(CreateMatch.date)
    await callback.message.edit_text("Введите дату и время матча (например, 1 янв. 18:00):")
    await callback.answer()


@router.message(CreateMatch.date)
async def match_enter_date(message: Message, state: FSMContext):
    datetime_str = message.text.strip()
    if parse_russian_datetime(datetime_str) is None:
        await message.answer("❌ Неверный формат. Введите дату и время в формате: день месяц часы:минуты (например: 1 янв. 18:00)\n"
                             "Часы и минуты должны быть двузначными (например, 09:05, 18:00)."
                             "Подсказка по месяцам:\n"
                             "'янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'"
                             )
        return
    await state.update_data(date=datetime_str)
    await state.set_state(CreateMatch.location)
    await message.answer("Введите место проведения:")


@router.message(CreateMatch.location)
async def match_enter_location(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO matches (tournament_id, team1_id, team2_id, match_date, location, status)
        VALUES (?, ?, ?, ?, ?, 'scheduled')
    """, (data['tournament_id'], data['team1_id'], data['team2_id'], data['date'], message.text))
    conn.commit()

    # ---------- Уведомления командам ----------
    members1 = get_team_members(data['team1_id'])
    members2 = get_team_members(data['team2_id'])
    tournament = get_tournament_by_id(data['tournament_id'])
    team1_name = get_team_by_id(data['team1_id'])['name']
    team2_name = get_team_by_id(data['team2_id'])['name']
    match_info = (f"⚔️ Новый матч в турнире {tournament['name']}!\n"
                  f"Команда: {team1_name} vs {team2_name}\n"
                  f"📅 Дата: {data['date']}\n"
                  f"📍 Место: {message.text}")
    for member in members1 + members2:
        try:
            await message.bot.send_message(member['telegram_id'], match_info)
        except Exception as e:
            print(
                f"Не удалось отправить уведомление {member['telegram_id']}: {e}")

    conn.close()
    await state.clear()
    await message.answer("Матч создан!", reply_markup=admin_menu_keyboard())

# ---------- Ввод результата матча ----------


class EnterResult(StatesGroup):
    match = State()
    score1 = State()
    score2 = State()


@router.callback_query(F.data == "admin_enter_result")
async def admin_enter_result_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.id, t1.name as team1, t2.name as team2, m.match_date
        FROM matches m
        JOIN teams t1 ON m.team1_id = t1.id
        JOIN teams t2 ON m.team2_id = t2.id
        WHERE m.status='scheduled'
    """)
    matches = cur.fetchall()
    conn.close()
    if not matches:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Нет матчей, ожидающих результата.", reply_markup=kb)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for m in matches:
        builder.button(text=f"{m['team1']} vs {m['team2']} ({m['match_date']})",
                       callback_data=f"result_match_{m['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text("Выберите матч для ввода результата:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("result_match_"))
async def result_choose_match(callback: CallbackQuery, state: FSMContext):
    match_id = int(callback.data.split("_")[2])
    await state.update_data(match_id=match_id)
    await state.set_state(EnterResult.score1)
    await callback.message.edit_text("Введите счёт первой команды (число):")
    await callback.answer()


@router.message(EnterResult.score1)
async def result_enter_score1(message: Message, state: FSMContext):
    try:
        score1 = int(message.text)
    except ValueError:
        await message.answer("Введите число!")
        return
    await state.update_data(score1=score1)
    await state.set_state(EnterResult.score2)
    await message.answer("Введите счёт второй команды (число):")


@router.message(EnterResult.score2)
async def result_enter_score2(message: Message, state: FSMContext):
    try:
        score2 = int(message.text)
    except ValueError:
        await message.answer("Введите число!")
        return
    data = await state.get_data()
    match_id = data['match_id']
    score1 = data['score1']

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE matches SET score1=?, score2=?, status='completed' WHERE id=?",
                    (score1, score2, match_id))

        cur.execute(
            "SELECT team1_id, team2_id, tournament_id FROM matches WHERE id=?", (match_id,))
        match = cur.fetchone()
        if match:
            month = datetime.now().strftime("%Y-%m")
            cur.execute("SELECT sport FROM tournaments WHERE id=?",
                        (match['tournament_id'],))
            tour = cur.fetchone()
            sport = tour['sport'] if tour else "CS2"

            if score1 > score2:
                update_team_rating_same_conn(
                    conn, match['team1_id'], sport, month, 3)
                update_team_rating_same_conn(
                    conn, match['team2_id'], sport, month, 0)
            elif score1 < score2:
                update_team_rating_same_conn(
                    conn, match['team1_id'], sport, month, 0)
                update_team_rating_same_conn(
                    conn, match['team2_id'], sport, month, 3)
            else:
                update_team_rating_same_conn(
                    conn, match['team1_id'], sport, month, 1)
                update_team_rating_same_conn(
                    conn, match['team2_id'], sport, month, 1)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при сохранении результата: {e}")
        await message.answer("Произошла ошибка, попробуйте позже.")
        return
    finally:
        conn.close()

    await state.clear()
    await message.answer("Результат сохранён!", reply_markup=admin_menu_keyboard())

# ---------- Управление пользователями ----------


class UserManagement(StatesGroup):
    searching = State()


@router.callback_query(F.data == "admin_users")
async def admin_users_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список пользователей",
                   callback_data="admin_user_list")
    builder.button(text="🔍 Поиск", callback_data="admin_user_search")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text("Управление пользователями:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "admin_user_list")
async def admin_user_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    users = get_all_users(0, 10)
    await show_user_list(callback.message, users, 0)


async def show_user_list(message, users, offset):
    if not users:
        text = "Пользователи не найдены."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
    else:
        text = "Список пользователей (первые 10):\n\n"
        builder = InlineKeyboardBuilder()
        for u in users:
            status = "🔴" if u['is_banned'] else "🟢"
            text += f"{status} {u['id']}: {u['first_name']} @{u['username']} ({u['role']})\n"
            builder.button(
                text=f"👤 {u['id']}", callback_data=f"admin_user_view_{u['id']}")
        builder.button(text="▶️ Далее", callback_data="admin_user_next")
        builder.button(text="🔙 Назад", callback_data="admin_users")
        builder.adjust(4)
        kb = builder.as_markup()
    await message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("admin_user_view_"))
async def admin_user_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[3])
    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    text = f"""
👤 Пользователь #{user['id']}
Telegram ID: {user['telegram_id']}
Имя: {user['first_name']} {user['last_name']}
Username: @{user['username']}
Email: {user['email']}
Город: {user['city']}
Любимые виды спорта: {user['favorite_sports']}
Роль: {user['role']}
Статус: {'🔴 Забанен' if user['is_banned'] else '🟢 Активен'}
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Сменить роль",
                   callback_data=f"admin_user_changerole_{user_id}")
    builder.button(text="🔨 Бан/Разбан",
                   callback_data=f"admin_user_toggleban_{user_id}")
    builder.button(text="🔙 Назад", callback_data="admin_users")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_changerole_"))
async def admin_user_changerole(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[3])
    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for role in ['player', 'captain', 'admin']:
        mark = "✅" if user['role'] == role else ""
        builder.button(
            text=f"{mark} {role}", callback_data=f"admin_user_setrole_{user_id}_{role}")
    builder.button(text="🔙 Назад", callback_data=f"admin_user_view_{user_id}")
    builder.adjust(1)
    await callback.message.edit_text(f"Выберите новую роль для {user['first_name']}:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_setrole_"))
async def admin_user_setrole(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    user_id = int(parts[3])
    new_role = parts[4]
    update_user_role(user_id, new_role)
    await callback.answer(f"Роль изменена на {new_role}")
    await admin_user_view(callback)


@router.callback_query(F.data.startswith("admin_user_toggleban_"))
async def admin_user_toggleban(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[3])
    new_status = toggle_user_ban(user_id)
    status_text = "забанен" if new_status else "разбанен"
    await callback.answer(f"Пользователь {status_text}")
    await admin_user_view(callback)


@router.callback_query(F.data == "admin_user_search")
async def admin_user_search_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(UserManagement.searching)
    await callback.message.edit_text("Введите имя, username или Telegram ID для поиска:")
    await callback.answer()


@router.message(UserManagement.searching)
async def admin_user_search_results(message: Message, state: FSMContext):
    query = message.text.strip()
    users = search_users(query)
    await state.clear()
    if not users:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await message.answer("Ничего не найдено.", reply_markup=kb)
        return
    text = f"Результаты поиска по запросу «{query}»:\n\n"
    builder = InlineKeyboardBuilder()
    for u in users:
        status = "🔴" if u['is_banned'] else "🟢"
        text += f"{status} {u['id']}: {u['first_name']} @{u['username']} ({u['role']})\n"
        builder.button(text=f"👤 {u['id']}",
                       callback_data=f"admin_user_view_{u['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_users")
    builder.adjust(4)
    await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "admin_user_next")
async def admin_user_next(callback: CallbackQuery):
    await callback.answer("Пагинация пока не реализована, используйте поиск.", show_alert=True)

# ---------- Удаление турнира (кнопка в карточке турнира) ----------


@router.callback_query(F.data.startswith("admin_delete_tournament_"))
async def admin_delete_tournament_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament_id = int(callback.data.split("_")[3])
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return
    await state.update_data(tournament_id=tournament_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить",
                              callback_data="admin_confirm_delete_tournament")],
        [InlineKeyboardButton(
            text="❌ Нет", callback_data=f"tournament_{tournament_id}")]
    ])
    await callback.message.edit_text(f"Вы уверены, что хотите удалить турнир «{tournament['name']}»? Это действие нельзя отменить.", reply_markup=kb)
    await callback.answer()
    await state.update_data(tournament_id=tournament_id, sport=tournament['sport'])


@router.callback_query(F.data == "admin_confirm_delete_tournament")
async def admin_delete_tournament_execute(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    tournament_id = data.get('tournament_id')
    if not tournament_id:
        await callback.answer("Ошибка", show_alert=True)
        return

    tournament = get_tournament_by_id(tournament_id)
    sport = tournament['sport'] if tournament else None

    delete_tournament(tournament_id)
    await state.clear()

    if sport:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🔙 К турнирам по {sport}", callback_data=f"tournament_sport_{sport}")]
        ])
    else:
        kb = back_to_main_keyboard()

    await callback.message.edit_text("Турнир удалён.", reply_markup=kb)

# ---------- Управление рейтингом ----------


@router.callback_query(F.data == "admin_rating")
async def admin_rating_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sports = get_all_sports()
    await callback.message.edit_text("Выберите вид спорта:", reply_markup=admin_rating_menu_keyboard(sports))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rating_sport_"))
async def admin_rating_sport_actions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sport = callback.data.replace("admin_rating_sport_", "")
    await callback.message.edit_text(f"Действия для спорта {sport}:", reply_markup=admin_rating_sport_actions_keyboard(sport))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rating_reset_all_"))
async def admin_rating_reset_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sport = callback.data.replace("admin_rating_reset_all_", "")
    reset_sport_rating(sport)
    await callback.answer(f"Рейтинг по {sport} обнулён!", show_alert=True)
    await admin_rating_menu(callback)


@router.callback_query(F.data.startswith("admin_rating_list_teams_"))
async def admin_rating_list_teams(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sport = callback.data.replace("admin_rating_list_teams_", "")
    teams = get_teams_with_rating(sport)
    if not teams:
        await callback.message.edit_text(f"Нет команд по спорту {sport}.", reply_markup=back_to_main_keyboard())
        await callback.answer()
        return
    await callback.message.edit_text(f"Команды по {sport}:", reply_markup=admin_rating_teams_list_keyboard(teams, sport))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rating_team_"))
async def admin_rating_team_actions(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    team_id = int(parts[3])
    sport = parts[4]
    await state.update_data(team_id=team_id, sport=sport)
    await callback.message.edit_text(f"Действия для команды:", reply_markup=admin_rating_team_actions_keyboard(team_id, sport))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rating_reset_team_"))
async def admin_rating_reset_team(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    team_id = int(parts[4])
    sport = parts[5]
    reset_team_rating(team_id, sport)
    await callback.answer("Рейтинг команды обнулён!", show_alert=True)
    # Вернуться к списку команд этого спорта
    teams = get_teams_with_rating(sport)
    await callback.message.edit_text(f"Команды по {sport}:", reply_markup=admin_rating_teams_list_keyboard(teams, sport))
    await callback.answer()


class DeductPoints(StatesGroup):
    points = State()


@router.callback_query(F.data.startswith("admin_rating_deduct_"))
async def admin_rating_deduct_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    team_id = int(parts[3])
    sport = parts[4]
    await state.update_data(team_id=team_id, sport=sport)
    await state.set_state(DeductPoints.points)
    await callback.message.edit_text("Введите количество очков для снятия:")
    await callback.answer()


@router.message(DeductPoints.points)
async def admin_rating_deduct_execute(message: Message, state: FSMContext):
    try:
        points = int(message.text)
        if points <= 0:
            await message.answer("Введите положительное число.")
            return
    except ValueError:
        await message.answer("Введите число!")
        return
    data = await state.get_data()
    team_id = data['team_id']
    sport = data['sport']
    deduct_team_points(team_id, sport, points)
    await state.clear()
    await message.answer(f"С команды снято {points} очков.")
    # Вернуться в меню рейтинга
    await message.answer("Панель администратора:", reply_markup=admin_menu_keyboard())

# ---------- Управление командами (удаление) ----------


@router.callback_query(F.data == "admin_teams")
async def admin_teams_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    teams = get_all_teams()
    if not teams:
        await callback.message.edit_text("Нет команд.", reply_markup=back_to_main_keyboard())
        await callback.answer()
        return
    await callback.message.edit_text("Список команд:", reply_markup=admin_teams_list_keyboard(teams))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_team_"))
async def admin_delete_team_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[3])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    await state.update_data(team_id=team_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить",
                              callback_data="admin_confirm_delete_team")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="admin_teams")]
    ])
    await callback.message.edit_text(f"Вы уверены, что хотите удалить команду «{team['name']}»? Это действие нельзя отменить.", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_confirm_delete_team")
async def admin_delete_team_execute(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    team_id = data.get('team_id')
    if not team_id:
        await callback.answer("Ошибка", show_alert=True)
        return
    delete_team_admin(team_id)
    await state.clear()
    await callback.message.edit_text("Команда удалена.")
    await callback.message.answer("Панель администратора:", reply_markup=admin_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_menu")
async def back_to_admin_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.message.edit_text("Панель администратора:", reply_markup=admin_menu_keyboard())
    await callback.answer()
