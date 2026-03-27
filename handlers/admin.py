from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import get_connection
from aiogram import Bot
import json
from razryad_arena_utils import (
    is_admin, get_pending_applications, approve_application, reject_application, exclude_team_from_tournament,
    update_team_rating, get_all_users, search_users, get_user_by_id,
    update_user_role, toggle_user_ban, update_team_rating_same_conn,
    get_team_members, get_tournament_by_id, get_team_by_id,
    delete_tournament, get_all_sports, reset_sport_rating,
    get_teams_with_rating, reset_team_rating, deduct_team_points,
    delete_team_admin, parse_russian_date, parse_russian_datetime,
    get_user, get_user_teams, get_team_application, get_approved_teams_count, get_all_users_count, search_users_count,
    get_team_members_count, get_team_max_members, get_team_settings, is_captain,
    get_sport_display_name, upsert_football_player_stat, upsert_basketball_player_stat,
    upsert_volleyball_player_stat, replace_volleyball_set_scores, map_sports_to_display,
    normalize_sport_name
)
from keyboards import (
    admin_menu_keyboard, back_to_main_keyboard,
    admin_rating_menu_keyboard, admin_rating_sport_actions_keyboard,
    admin_rating_teams_list_keyboard, admin_rating_team_actions_keyboard,
    sports_choice_keyboard_single
)
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


async def send_tournament_info(bot: Bot, chat_id: int, tournament_id: int, user_id: int):
    """Отправляет актуальную карточку турнира."""
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await bot.send_message(chat_id, "Турнир не найден.")
        return

    user = get_user(user_id)
    teams = get_user_teams(user['id']) if user else []
    approved_count = get_approved_teams_count(tournament_id)

    # Проверяем, может ли пользователь подать заявку
    can_apply = False
    if user and tournament['status'] == 'registration':
        for team in teams:
            if normalize_sport_name(team['sport']) == normalize_sport_name(tournament['sport']) and is_captain(user['id'], team['id']):
                status = get_team_application(tournament_id, team['id'])
                if status is None and approved_count < tournament['max_teams']:
                    can_apply = True
                    break

    status_map = {
        'registration': 'Регистрация',
        'active': 'Идёт',
        'finished': 'Завершён'
    }
    status_display = status_map.get(tournament['status'], tournament['status'])

    # Возрастные ограничения
    age_text = ""
    if tournament['min_age'] is not None and tournament['max_age'] is not None:
        if tournament['min_age'] == 0 and tournament['max_age'] == 100:
            age_text = "Без ограничений"
        else:
            age_text = f"{tournament['min_age']}–{tournament['max_age']} лет"
    else:
        age_text = "Не указан"

    text = f"""
🏆 {tournament['name']}
Вид спорта: {get_sport_display_name(tournament['sport'])}
Требуемый размер команды: {tournament['required_team_size']} чел.
Город: {tournament['city']}
Возраст: {age_text}
Даты: {tournament['start_date']} - {tournament['end_date']}
Макс. команд: {tournament['max_teams']}
Статус: {status_display}
"""
    if tournament['description'] and tournament['description'] != 'нет':
        text += f"\n📝 Описание: {tournament['description']}"

    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="📝 Подать заявку",
                       callback_data=f"apply_{tournament_id}")
    builder.button(text="📋 Список команд",
                   callback_data=f"tournament_teams_{tournament_id}")

    # Кнопка просмотра сетки (если сгенерирована)
    if tournament['bracket_generated']:
        builder.button(text="📊 Управление сеткой",
                       callback_data=f"view_bracket_{tournament_id}")

    # Кнопка управления турниром (только админ)
    if user and is_admin(user['telegram_id']):
        builder.button(text="⚙️ Управление турниром",
                       callback_data=f"admin_tournament_manage_{tournament_id}")

    builder.button(
        text="🔙 Назад", callback_data=f"tournament_sport_{tournament['sport']}")
    builder.adjust(1)

    await bot.send_message(chat_id, text, reply_markup=builder.as_markup())
router = Router()
PAGE_SIZE = 10


def _build_team_members_block(members):
    if not members:
        return "—"

    lines = []
    for idx, member in enumerate(members, 1):
        username = f"@{member['username']}" if member['username'] else "без username"
        age = member['age'] if member['age'] is not None else "не указан"
        steam = member['steam_id'] if member['steam_id'] else "❌ не указан"
        lines.append(
            f"{idx}. {member['first_name']} ({username}) | возраст: {age} | steam: {steam}")
    return "\n".join(lines)


def _build_tournament_compliance_block(tournament: dict, members: list[dict]) -> tuple[str, bool]:
    """Возвращает текст проверки и флаг полного соответствия."""
    checks = []
    has_errors = False

    required_size = tournament['required_team_size'] or 0
    actual_size = len(members)
    if actual_size == required_size:
        checks.append(f"✅ Размер состава: {actual_size}/{required_size}")
    else:
        has_errors = True
        checks.append(
            f"❌ Размер состава: {actual_size}/{required_size} (несоответствие)")

    min_age = tournament['min_age']
    max_age = tournament['max_age']
    if min_age is not None and max_age is not None:
        age_issues = []
        for member in members:
            age = member['age']
            username = f"@{member['username']}" if member['username'] else "без username"
            if age is None:
                age_issues.append(
                    f"{member['first_name']} ({username}) — возраст не указан")
            elif age < min_age or age > max_age:
                age_issues.append(
                    f"{member['first_name']} ({username}) — {age} лет (требуется {min_age}-{max_age})")

        if age_issues:
            has_errors = True
            checks.append("❌ Возрастные ограничения:\n" +
                          "\n".join(f"   - {item}" for item in age_issues))
        else:
            checks.append(f"✅ Возрастные ограничения: {min_age}-{max_age}")

    if normalize_sport_name(tournament['sport']) == 'CS2':
        steam_issues = []
        for member in members:
            if not member['steam_id']:
                username = f"@{member['username']}" if member['username'] else "без username"
                steam_issues.append(f"{member['first_name']} ({username})")
        if steam_issues:
            has_errors = True
            checks.append("❌ Steam обязателен для CS2:\n" +
                          "\n".join(f"   - {item}" for item in steam_issues))
        else:
            checks.append("✅ Steam профили указаны у всех участников")

    if not has_errors:
        checks.append("✅ Команда соответствует требованиям турнира")

    return "\n".join(checks), (not has_errors)


def _build_admin_team_card_text(team_id: int, tournament: dict | None = None, app_status: str | None = None) -> str | None:
    team = get_team_by_id(team_id)
    if not team:
        return None

    captain = get_user_by_id(team['captain_id'])
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Неизвестно"
    members = get_team_members(team_id)
    members_count = get_team_members_count(team_id)
    max_members = get_team_max_members(team_id)
    settings = get_team_settings(team_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE team_id=?", (team_id,))
    tournaments_total = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE team_id=? AND status='pending'", (team_id,))
    tournaments_pending = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM team_invites WHERE team_id=? AND type='request' AND status='pending'", (team_id,))
    join_requests_pending = cur.fetchone()[0]
    conn.close()

    created_at = team['created_at'] if 'created_at' in team.keys(
    ) and team['created_at'] else "н/д"

    text = (
        "⚙️ Карточка команды (админ)\n\n"
        f"Название: {team['name']}\n"
        f"Вид спорта: {get_sport_display_name(team['sport'])}\n"
        f"Город: {team['city']}\n"
        f"Капитан: {captain_name}\n"
        f"Участники: {members_count}/{max_members}\n"
        f"Набор в команду: {'🔓 открыт' if settings['is_open'] else '🔒 закрыт'}\n"
        f"Уведомления капитану: {'🔔 включены' if settings['notify'] else '🔕 выключены'}\n"
        f"Создана: {created_at}\n"
        f"Активность: заявок в турниры={tournaments_total}, pending турниров={tournaments_pending}, pending вступлений={join_requests_pending}\n"
    )

    if app_status:
        status_map = {'pending': '⏳ На рассмотрении',
                      'approved': '✅ Одобрено', 'rejected': '❌ Отклонено'}
        text += f"Статус заявки: {status_map.get(app_status, app_status)}\n"

    text += "\nСостав:\n" + _build_team_members_block(members)

    if tournament:
        compliance_text, _ = _build_tournament_compliance_block(
            tournament, members)
        text += (
            "\n\n"
            f"🏆 Турнир: {tournament['name']}\n"
            f"Требования: размер={tournament['required_team_size']}, возраст={tournament['min_age']}-{tournament['max_age']}, спорт={get_sport_display_name(tournament['sport'])}\n\n"
            "Проверка соответствия:\n"
            f"{compliance_text}"
        )

    return text


def _get_tournament_application_details(app_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.tournament_id, a.team_id, a.status,
               t.name as team_name, tm.name as tournament_name
        FROM tournament_applications a
        JOIN teams t ON a.team_id = t.id
        JOIN tournaments tm ON a.tournament_id = tm.id
        WHERE a.id=?
    """, (app_id,))
    app = cur.fetchone()
    conn.close()
    return app


def _admin_team_manage_card_keyboard(team_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить",
                   callback_data=f"admin_team_delete_confirm_{team_id}")
    builder.button(text="🔙 К списку", callback_data="admin_teams")
    builder.adjust(1)
    return builder.as_markup()


def _get_tournament_capacity_info(tournament_id: int) -> tuple[int, int | None]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT max_teams FROM tournaments WHERE id=?",
                (tournament_id,))
    tournament = cur.fetchone()
    if not tournament:
        conn.close()
        return 0, None

    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE tournament_id=? AND status='approved'",
        (tournament_id,)
    )
    approved_count = cur.fetchone()[0]
    conn.close()
    return approved_count, tournament['max_teams']


def _get_overbooked_tournaments_map(tournament_ids: list[int]) -> dict[int, dict]:
    if not tournament_ids:
        return {}

    placeholders = ",".join(["?"] * len(tournament_ids))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            t.id,
            t.name,
            t.max_teams,
            COALESCE(SUM(CASE WHEN a.status='approved' THEN 1 ELSE 0 END), 0) AS approved_count
        FROM tournaments t
        LEFT JOIN tournament_applications a ON a.tournament_id = t.id
        WHERE t.id IN ({placeholders})
        GROUP BY t.id, t.name, t.max_teams
    """, tournament_ids)
    rows = cur.fetchall()
    conn.close()

    overbooked = {}
    for row in rows:
        max_teams = row['max_teams']
        approved_count = row['approved_count']
        if max_teams is not None and max_teams > 0 and approved_count > max_teams:
            overbooked[row['id']] = {
                "name": row['name'],
                "approved": approved_count,
                "max_teams": max_teams
            }
    return overbooked


def _build_approve_error_text(result: dict) -> str:
    reason = result.get("reason")
    if reason == "limit_reached":
        approved = result.get("approved")
        max_teams = result.get("max_teams")
        if approved is not None and max_teams is not None:
            return f"Лимит команд уже достигнут ({approved}/{max_teams}), заявка оставлена в pending."
        return "Лимит команд уже достигнут, заявка оставлена в pending."
    if reason == "not_registration":
        return "Одобрение доступно только на этапе регистрации."
    if reason == "already_processed":
        return "Заявка уже обработана другим админом."
    if reason == "not_found":
        return "Заявка не найдена."
    return "Не удалось одобрить заявку. Попробуйте ещё раз."


def _build_exclude_error_text(result: dict) -> str:
    reason = result.get("reason")
    if reason == "not_registration":
        return "Исключение доступно только на этапе регистрации и до генерации сетки."
    if reason == "not_approved":
        return "Исключить можно только уже одобренную команду."
    if reason == "already_processed":
        return "Заявка уже была изменена другим админом."
    if reason == "not_found":
        return "Заявка не найдена."
    return "Не удалось исключить команду. Попробуйте ещё раз."

# ---------- Создание турнира ----------


class CreateTournament(StatesGroup):
    name = State()
    sport = State()
    city = State()
    start_date = State()
    end_date = State()
    max_teams = State()
    required_team_size = State()
    min_age = State()        # новое
    max_age = State()
    description = State()


@router.callback_query(F.data == "admin_create_tournament")
async def admin_create_tournament_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(CreateTournament.name)
    await callback.message.edit_text("Введите название турнира:")


@router.message(CreateTournament.name)
async def create_tournament_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(CreateTournament.sport)
    sports = get_all_sports()
    await message.answer("Выберите вид спорта:", reply_markup=sports_choice_keyboard_single(sports))


@router.callback_query(CreateTournament.sport, F.data.startswith("admin_tourn_sport_"))
async def create_tournament_sport_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    sport_name = callback.data.replace("admin_tourn_sport_", "")
    await state.update_data(sport=sport_name)
    await state.set_state(CreateTournament.city)
    await callback.message.edit_text("Введите город проведения:")


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
    await state.set_state(CreateTournament.min_age)
    await message.answer("Введите минимальный возраст участников (или 0, если нет ограничений):")


@router.message(CreateTournament.min_age)
async def create_tournament_min_age(message: Message, state: FSMContext):
    try:
        min_age = int(message.text)
        if min_age < 0 or min_age > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 0 до 100.")
        return
    await state.update_data(min_age=min_age)
    await state.set_state(CreateTournament.max_age)
    await message.answer("Введите максимальный возраст участников (или 100, если нет ограничений):")


@router.message(CreateTournament.max_age)
async def create_tournament_max_age(message: Message, state: FSMContext):
    try:
        max_age = int(message.text)
        if max_age < 0 or max_age > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 0 до 100.")
        return
    data = await state.get_data()
    min_age = data.get('min_age')
    if min_age is not None and min_age > max_age:
        await message.answer("❌ Минимальный возраст не может быть больше максимального.")
        return
    await state.update_data(max_age=max_age)
    await state.set_state(CreateTournament.description)
    await message.answer("Введите описание турнира (можно отправить 'нет', чтобы пропустить):")


@router.message(CreateTournament.description)
async def create_tournament_description(message: Message, state: FSMContext):
    data = await state.get_data()
    description = message.text if message.text.lower() != 'нет' else ''
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO tournaments (name, sport, city, start_date, end_date, max_teams, required_team_size, min_age, max_age, description, created_by, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'registration')
    """, (data['name'], data['sport'], data['city'], data['start_date'], data['end_date'], data['max_teams'], data['required_team_size'], data['min_age'], data['max_age'], description, message.from_user.id))
    tournament_id = cur.lastrowid
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Турнир создан!")
    # Отправляем отдельное сообщение с управлением
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Управление турниром",
                              callback_data=f"admin_tournament_manage_{tournament_id}")]
    ])
    await message.answer("Нажмите кнопку ниже для управления:", reply_markup=kb)

# ---------- Редактирование турнира ----------


class EditTournament(StatesGroup):
    field = State()
    value = State()
    sport_choice = State()


@router.callback_query(F.data.startswith("admin_edit_tournament_"))
async def edit_tournament_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament_id = int(callback.data.split("_")[3])
    await state.update_data(tournament_id=tournament_id)
    builder = InlineKeyboardBuilder()
    fields = ["name", "sport", "city", "start_date", "end_date",
              "max_teams", "required_team_size", "min_age", "max_age", "description"]
    for f in fields:
        builder.button(text=f, callback_data=f"edit_field_{f}")
    builder.button(
        text="🔙 Назад", callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(2)
    await callback.message.edit_text("Выберите поле для редактирования:", reply_markup=builder.as_markup())
    await state.set_state(EditTournament.field)


@router.callback_query(EditTournament.field, F.data.startswith("edit_field_"))
async def edit_tournament_field(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    field = callback.data.replace("edit_field_", "")
    await state.update_data(field=field)
    if field == "sport":
        sports = get_all_sports()
        await state.set_state(EditTournament.sport_choice)
        await callback.message.edit_text("Выберите новый вид спорта:", reply_markup=sports_choice_keyboard_single(sports))
    else:
        await state.set_state(EditTournament.value)
        await callback.message.edit_text(f"Введите новое значение для поля '{field}':")


@router.message(EditTournament.value)
async def edit_tournament_value(message: Message, state: FSMContext):
    data = await state.get_data()
    tournament_id = data['tournament_id']
    field = data['field']
    new_value = message.text
    if field == "max_teams" or field == "required_team_size" or field == "min_age" or field == "max_age":
        try:
            new_value = int(new_value)
            if field == "required_team_size" and (new_value < 1 or new_value > 10):
                await message.answer("❌ Требуемый размер команды должен быть от 1 до 10.")
                return
            elif (field == "min_age" or field == "max_age") and (new_value < 0 or new_value > 100):
                await message.answer("❌ Введите целое число от 0 до 100.")
                return
        except ValueError:
            await message.answer("Введите число!")
            return

        # Проверки на согласованность возраста
        if field == "min_age":
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT max_age FROM tournaments WHERE id=?", (tournament_id,))
            row = cur.fetchone()
            current_max_age = row['max_age'] if row else None
            conn.close()
            if current_max_age is not None and new_value > current_max_age:
                await message.answer("❌ Минимальный возраст не может быть больше максимального.")
                return
        elif field == "max_age":
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT min_age FROM tournaments WHERE id=?", (tournament_id,))
            row = cur.fetchone()
            current_min_age = row['min_age'] if row else None
            conn.close()
            if current_min_age is not None and current_min_age > new_value:
                await message.answer("❌ Минимальный возраст не может быть больше максимального.")
                return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE tournaments SET {field}=? WHERE id=?", (new_value, tournament_id))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Турнир обновлён!")
    # Отправляем отдельное сообщение с управлением
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Управление турниром",
                              callback_data=f"admin_tournament_manage_{tournament_id}")]
    ])
    await message.answer("Нажмите кнопку ниже для управления:", reply_markup=kb)


@router.callback_query(EditTournament.sport_choice, F.data.startswith("admin_tourn_sport_"))
async def edit_tournament_sport_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    new_sport = callback.data.replace("admin_tourn_sport_", "")
    data = await state.get_data()
    tournament_id = data['tournament_id']
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tournaments SET sport=? WHERE id=?",
                (new_sport, tournament_id))
    conn.commit()
    conn.close()
    await state.clear()
    await send_tournament_info(callback.bot, callback.message.chat.id, tournament_id, callback.from_user.id)


# ---------- Заявки на турниры ----------


# ---------- Управление турнирами (список) ----------

@router.callback_query(F.data == "admin_tournaments_list")
async def admin_tournaments_list(callback: CallbackQuery, state: FSMContext):
    """Список турниров для выбора управления с пагинацией."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    await state.clear()
    await show_tournaments_page(callback, 0)


async def show_tournaments_page(callback: CallbackQuery | None, offset: int, message=None):
    """Показывает страницу списка турниров."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tournaments")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT id, name, sport, status, bracket_generated
        FROM tournaments
        ORDER BY created_at DESC
        LIMIT 10 OFFSET ?
    """, (offset,))
    tournaments = cur.fetchall()
    conn.close()

    if not tournaments:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать турнир",
                                  callback_data="admin_create_tournament")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        text = "Нет турниров.\n\nСоздайте новый турнир:"
        if callback:
            await callback.message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        return

    text = f"🏆 Управление турнирами ({offset + 1}-{min(offset + 10, total)} из {total})\n\nВыберите турнир:\n"
    builder = InlineKeyboardBuilder()

    for t in tournaments:
        status_icon = "🟢" if t['status'] == 'registration' else "🟡" if t['status'] == 'active' else "⚪"
        bracket_icon = "📊" if t['bracket_generated'] else ""
        text += f"{status_icon}{bracket_icon} {t['name']} ({get_sport_display_name(t['sport'])})\n"
        builder.button(
            text=f"⚙️ {t['name']}",
            callback_data=f"admin_tournament_manage_{t['id']}"
        )

    # Пагинация
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_tournaments_page_{offset - 10}"))
    if offset + 10 < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_tournaments_page_{offset + 10}"))

    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="➕ Создать турнир",
                   callback_data="admin_create_tournament")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)

    if callback:
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_tournaments_page_"))
async def admin_tournaments_page_callback(callback: CallbackQuery):
    """Переключение страницы списка турниров."""
    offset = int(callback.data.split("_")[3])
    await show_tournaments_page(callback, offset)


# ---------- Управление конкретным турниром ----------

@router.callback_query(F.data.startswith("admin_tournament_manage_"))
async def admin_tournament_manage(callback: CallbackQuery):
    """Меню управления конкретным турниром для админа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    tournament_id = int(callback.data.split("_")[3])
    tournament = get_tournament_by_id(tournament_id)

    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    # Получаем количество команд и заявок
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM tournament_applications
        WHERE tournament_id=? AND status='approved'
    """, (tournament_id,))
    approved_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM tournament_applications
        WHERE tournament_id=? AND status='pending'
    """, (tournament_id,))
    pending_count = cur.fetchone()[0]

    conn.close()

    # Показываем только принятые команды (approved)
    teams_text = f"Команд: {approved_count} из {tournament['max_teams']}"

    status_map = {
        'registration': 'Регистрация',
        'active': 'Идёт',
        'finished': 'Завершён'
    }
    status_display = status_map.get(tournament['status'], tournament['status'])

    # Возрастные ограничения
    age_text = ""
    if tournament['min_age'] is not None and tournament['max_age'] is not None:
        if tournament['min_age'] == 0 and tournament['max_age'] == 100:
            age_text = "Без ограничений"
        else:
            age_text = f"{tournament['min_age']}–{tournament['max_age']} лет"
    else:
        age_text = "Не указан"

    text = f"""
⚙️ Управление турниром

🏆 {tournament['name']}
Вид спорта: {get_sport_display_name(tournament['sport'])}
Требуемый размер команды: {tournament['required_team_size']} чел.
Город: {tournament['city']}
Возраст: {age_text}
Даты: {tournament['start_date']} - {tournament['end_date']}
{teams_text}
Сетка: {'✅ Сгенерирована' if tournament['bracket_generated'] else '❌ Не сгенерирована'}
Статус: {status_display}
"""
    if tournament['description'] and tournament['description'] != 'нет':
        text += f"\n📝 Описание: {tournament['description']}"

    builder = InlineKeyboardBuilder()

    # Генерация сетки
    if not tournament['bracket_generated']:
        builder.button(text="🔷 Сгенерировать сетку",
                       callback_data=f"admin_generate_bracket_{tournament_id}")
    else:
        builder.button(text="📊 Управление сеткой",
                       callback_data=f"view_bracket_{tournament_id}")
        if tournament['status'] != 'finished':
            builder.button(text="♻️ Перегенерировать сетку",
                           callback_data=f"admin_regenerate_bracket_{tournament_id}")

    # Завершение турнира (только если сетка сгенерирована и статус не 'finished')
    if tournament['bracket_generated'] and tournament['status'] != 'finished':
        builder.button(text="🏆 Завершить турнир",
                       callback_data=f"admin_finish_tournament_{tournament_id}")

    # Список команд и заявок (объединяем)
    builder.button(text="📋 Список команд и заявок",
                   callback_data=f"admin_tournament_teams_{tournament_id}")

    # Редактирование турнира
    builder.button(text="✏️ Редактировать турнир",
                   callback_data=f"admin_edit_tournament_{tournament_id}")

    # Удаление турнира
    builder.button(text="🗑 Удалить турнир",
                   callback_data=f"admin_delete_tournament_{tournament_id}")

    # Назад к списку турниров
    builder.button(text="🔙 Назад к турнирам",
                   callback_data="admin_tournaments_list")
    builder.adjust(1)

    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    except Exception as e:
        if "there is no text" in str(e):
            # Если сообщение содержит фото, отправляем новое
            await callback.message.answer(text, reply_markup=builder.as_markup())
        else:
            raise
    await callback.answer()


# ---------- Завершение турнира ----------

@router.callback_query(F.data.startswith("admin_finish_tournament_"))
async def admin_finish_tournament_confirm(callback: CallbackQuery):
    """Подтверждение завершения турнира."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    tournament_id = int(callback.data.split("_")[3])
    tournament = get_tournament_by_id(tournament_id)

    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, завершить",
              callback_data=f"confirm_finish_tournament_{tournament_id}")
    kb.button(text="❌ Нет",
              callback_data=f"admin_tournament_manage_{tournament_id}")
    kb.adjust(1)

    await callback.message.edit_text(
        f"Завершить турнир «{tournament['name']}»?\n\n"
        f"🏅 Будут начислены очки за места:\n"
        f"🥇 1 место: 20 очков\n"
        f"🥈 2 место: 15 очков\n"
        f"🥉 3 место: 10 очков\n\n"
        f"Статус турнира изменится на 'Завершён'.",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_finish_tournament_"))
async def confirm_finish_tournament(callback: CallbackQuery):
    """Завершение турнира с начислением очков."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    tournament_id = int(callback.data.split("_")[3])

    # Импортируем функцию завершения
    from razryad_arena_utils import finish_tournament_with_awards, get_team_by_id

    first, second, third = finish_tournament_with_awards(tournament_id)

    if first is None:
        await callback.answer("❌ Ошибка при завершении турнира", show_alert=True)
        return

    # Получаем названия команд
    first_team = get_team_by_id(first) if first else None
    second_team = get_team_by_id(second) if second else None
    third_team = get_team_by_id(third) if third else None

    first_name = first_team['name'] if first_team else "???"
    second_name = second_team['name'] if second_team else "???"
    third_name = third_team['name'] if third_team else "???"

    await callback.answer(f"✅ Турнир завершён!", show_alert=True)

    await callback.message.answer(
        f"🏆 Турнир завершён!\n\n"
        f"🥇 1 место: {first_name} (+20 очков)\n"
        f"🥈 2 место: {second_name} (+15 очков)\n"
        f"🥉 3 место: {third_name} (+10 очков)"
    )


# ---------- Заявки на турниры (общие) ----------

@router.callback_query(F.data == "admin_applications")
async def admin_applications(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_admin_applications_list(callback.message)


async def show_admin_applications_list(message):
    """Показывает общий список pending-заявок на турниры."""
    apps = get_pending_applications()
    if not apps:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await message.edit_text("Нет заявок.", reply_markup=kb)
        return

    tournament_ids = sorted({app['tournament_id'] for app in apps})
    overbooked_map = _get_overbooked_tournaments_map(tournament_ids)

    text = "Заявки на турниры:\n"
    builder = InlineKeyboardBuilder()
    for app in apps:
        text += f"\nID {app['id']}: {app['team_name']} -> {app['tournament_name']}"
        builder.button(text=f"ℹ️ {app['id']}",
                       callback_data=f"admin_pending_team_info_{app['id']}")
        builder.button(text=f"✅ Одобрить",
                       callback_data=f"approve_{app['id']}")
        builder.button(text=f"❌ Отклонить",
                       callback_data=f"reject_{app['id']}")

    if overbooked_map:
        text += "\n\n⚠️ Внимание: есть переполненные турниры (approved > max):"
        for tournament_id in sorted(overbooked_map.keys()):
            meta = overbooked_map[tournament_id]
            text += f"\n- {meta['name']}: {meta['approved']}/{meta['max_teams']}"

    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(3)
    await message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_pending_team_info_"))
async def admin_pending_team_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    app_id = int(callback.data.split("_")[4])
    app = _get_tournament_application_details(app_id)
    if not app:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    tournament = get_tournament_by_id(app['tournament_id'])
    text = _build_admin_team_card_text(
        app['team_id'],
        tournament=tournament,
        app_status=app['status']
    )
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if app['status'] == 'pending':
        builder.button(text="✅ Одобрить", callback_data=f"approve_{app['id']}")
        builder.button(text="❌ Отклонить", callback_data=f"reject_{app['id']}")
    builder.button(text="🔙 Назад к заявкам",
                   callback_data="admin_applications")
    builder.adjust(2)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^approve_\d+(?:_\d+)?(?:_\d+)?$"))
async def approve_app(callback: CallbackQuery):
    """Одобрение заявки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    app_id = int(parts[1])
    tournament_id = int(parts[2]) if len(parts) > 2 else None
    page_offset = int(parts[3]) if len(parts) > 3 else 0

    result = approve_application(app_id)
    if result.get("ok"):
        await callback.answer("Заявка одобрена!")
    else:
        await callback.answer(_build_approve_error_text(result), show_alert=True)

    # Возврат к списку команд турнира
    if tournament_id:
        await show_tournament_teams_list(callback.message, tournament_id, offset=page_offset)
    else:
        await show_admin_applications_list(callback.message)


@router.callback_query(F.data.regexp(r"^reject_\d+(?:_\d+)?(?:_\d+)?$"))
async def reject_app(callback: CallbackQuery):
    """Отклонение заявки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    app_id = int(parts[1])
    tournament_id = int(parts[2]) if len(parts) > 2 else None
    page_offset = int(parts[3]) if len(parts) > 3 else 0

    reject_application(app_id)
    await callback.answer("Заявка отклонена!")

    # Возврат к списку команд турнира
    if tournament_id:
        await show_tournament_teams_list(callback.message, tournament_id, offset=page_offset)
    else:
        await show_admin_applications_list(callback.message)


@router.callback_query(F.data.regexp(r"^exclude_\d+_\d+$"))
async def exclude_app(callback: CallbackQuery):
    """Legacy callback: запрашивает подтверждение исключения."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    app_id = int(parts[1])
    tournament_id = int(parts[2])
    await show_exclusion_confirm(callback.message, tournament_id, app_id, offset=0)
    await callback.answer()


# ---------- Заявки на конкретный турнир ----------

async def show_tournament_teams_list(message, tournament_id, offset: int = 0):
    """Показывает список команд и заявок на турнир."""
    tournament = get_tournament_by_id(tournament_id)

    if not tournament:
        await message.answer("Турнир не найден.")
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE tournament_id=?", (tournament_id,))
    total = cur.fetchone()[0]

    if total > 0 and offset >= total:
        offset = ((total - 1) // PAGE_SIZE) * PAGE_SIZE
    if offset < 0:
        offset = 0

    cur.execute("""
        SELECT a.id, a.team_id, a.status, t.name as team_name, t.captain_id
        FROM tournament_applications a
        JOIN teams t ON a.team_id = t.id
        WHERE a.tournament_id=?
        ORDER BY
            CASE a.status
                WHEN 'approved' THEN 1
                WHEN 'pending' THEN 2
                WHEN 'rejected' THEN 3
            END,
            a.applied_at DESC
        LIMIT ? OFFSET ?
    """, (tournament_id, PAGE_SIZE, offset))
    apps = cur.fetchall()
    conn.close()

    if not apps:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад",
                                  callback_data=f"admin_tournament_manage_{tournament_id}")]
        ])
        await message.edit_text(
            f"Нет команд в турнире «{tournament['name']}».",
            reply_markup=kb
        )
        return

    end_pos = min(offset + PAGE_SIZE, total)
    text = f"📋 Список команд и заявок ({offset + 1}-{end_pos} из {total})\nТурнир: {tournament['name']}\n\n"
    builder = InlineKeyboardBuilder()

    approved_count, max_teams = _get_tournament_capacity_info(tournament_id)
    if max_teams is not None and max_teams > 0 and approved_count > max_teams:
        text += f"⚠️ Переполнение: одобрено {approved_count}/{max_teams}\n\n"

    for app in apps:
        status_map = {
            'approved': '✅',
            'pending': '⏳',
            'rejected': '❌'
        }
        status_text = {
            'approved': 'Одобрено',
            'pending': 'На рассмотрении',
            'rejected': 'Отклонено'
        }
        icon = status_map.get(app['status'], '❓')
        text += f"{icon} {app['team_name']} ({status_text.get(app['status'], app['status'])})\n"

        # Кнопки только для pending
        if app['status'] == 'pending':
            builder.button(text=f"ℹ️ {app['team_name']}",
                           callback_data=f"admin_tournament_team_info_{tournament_id}_{app['team_id']}_{app['id']}_{offset}")
            builder.button(text=f"✅ Одобрить",
                           callback_data=f"approve_{app['id']}_{tournament_id}_{offset}")
            builder.button(text=f"❌ Отклонить",
                           callback_data=f"reject_{app['id']}_{tournament_id}_{offset}")

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_tournament_teams_page_{tournament_id}_{offset-PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_tournament_teams_page_{tournament_id}_{offset+PAGE_SIZE}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    if tournament['status'] == 'registration' and not tournament['bracket_generated']:
        builder.button(text="🚫 Исключение команд из турнира",
                       callback_data=f"admin_tournament_exclusions_{tournament_id}")
    builder.button(text="🔙 Назад",
                   callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(3)

    await message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_tournament_teams_page_"))
async def admin_tournament_teams_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    offset = int(parts[5])
    await show_tournament_teams_list(callback.message, tournament_id, offset=offset)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_tournament_teams_\d+$"))
async def admin_tournament_teams(callback: CallbackQuery):
    """Список команд и заявок на конкретный турнир."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    tournament_id = int(callback.data.split("_")[3])
    await show_tournament_teams_list(callback.message, tournament_id, offset=0)
    await callback.answer()


async def show_tournament_exclusions_list(message, tournament_id, offset: int = 0):
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await message.answer("Турнир не найден.")
        return
    if tournament['status'] != 'registration' or tournament['bracket_generated']:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К списку заявок",
                                  callback_data=f"admin_tournament_teams_{tournament_id}")]
        ])
        await message.edit_text(
            "Исключение команд доступно только на этапе регистрации и до генерации сетки.",
            reply_markup=kb
        )
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM tournament_applications
        WHERE tournament_id=? AND status='approved'
    """, (tournament_id,))
    total = cur.fetchone()[0]

    if total > 0 and offset >= total:
        offset = ((total - 1) // PAGE_SIZE) * PAGE_SIZE
    if offset < 0:
        offset = 0

    cur.execute("""
        SELECT a.id, t.name as team_name
        FROM tournament_applications a
        JOIN teams t ON t.id = a.team_id
        WHERE a.tournament_id=? AND a.status='approved'
        ORDER BY a.updated_at DESC, a.id DESC
        LIMIT ? OFFSET ?
    """, (tournament_id, PAGE_SIZE, offset))
    apps = cur.fetchall()
    conn.close()

    if not apps:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К списку заявок",
                                  callback_data=f"admin_tournament_teams_{tournament_id}")]
        ])
        await message.edit_text("Нет одобренных команд для исключения.", reply_markup=kb)
        return

    end_pos = min(offset + PAGE_SIZE, total)
    text = (
        f"🚫 Исключение команд из турнира ({offset + 1}-{end_pos} из {total})\n"
        f"Турнир: {tournament['name']}\n\n"
        "Выберите команду для исключения:"
    )

    builder = InlineKeyboardBuilder()
    for app in apps:
        builder.button(
            text=f"🚫 {app['team_name']}",
            callback_data=f"admin_tournament_exclude_pick_{tournament_id}_{app['id']}_{offset}"
        )

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_tournament_exclusions_page_{tournament_id}_{offset-PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_tournament_exclusions_page_{tournament_id}_{offset+PAGE_SIZE}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="🔙 К списку заявок",
                   callback_data=f"admin_tournament_teams_{tournament_id}")
    builder.adjust(1)
    await message.edit_text(text, reply_markup=builder.as_markup())


async def show_exclusion_confirm(message, tournament_id: int, app_id: int, offset: int):
    app = _get_tournament_application_details(app_id)
    tournament = get_tournament_by_id(tournament_id)
    if not app or not tournament or app['tournament_id'] != tournament_id:
        await message.edit_text("Заявка не найдена.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К исключению команд",
                                  callback_data=f"admin_tournament_exclusions_{tournament_id}")]
        ]))
        return

    text = (
        f"⚠️ Подтвердите исключение\n\n"
        f"Турнир: {tournament['name']}\n"
        f"Команда: {app['team_name']}\n\n"
        "Команда будет исключена из турнира (статус заявки станет «Отклонено»)."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, исключить",
                              callback_data=f"admin_tournament_exclude_confirm_{tournament_id}_{app_id}_{offset}")],
        [InlineKeyboardButton(text="❌ Нет",
                              callback_data=f"admin_tournament_exclusions_page_{tournament_id}_{offset}")]
    ])
    await message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^admin_tournament_exclusions_\d+$"))
async def admin_tournament_exclusions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament_id = int(callback.data.split("_")[3])
    await show_tournament_exclusions_list(callback.message, tournament_id, offset=0)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_exclusions_page_"))
async def admin_tournament_exclusions_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    offset = int(parts[5])
    await show_tournament_exclusions_list(callback.message, tournament_id, offset=offset)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_exclude_pick_"))
async def admin_tournament_exclude_pick(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    app_id = int(parts[5])
    offset = int(parts[6])
    await show_exclusion_confirm(callback.message, tournament_id, app_id, offset)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_exclude_confirm_"))
async def admin_tournament_exclude_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    app_id = int(parts[5])
    offset = int(parts[6])

    result = exclude_team_from_tournament(app_id)
    if result.get("ok"):
        await callback.answer("Команда исключена из турнира.")
    else:
        await callback.answer(_build_exclude_error_text(result), show_alert=True)

    await show_tournament_exclusions_list(callback.message, tournament_id, offset=offset)


@router.callback_query(F.data.startswith("admin_tournament_team_info_"))
async def admin_tournament_team_info(callback: CallbackQuery):
    """Подробная информация о команде в контексте заявки на конкретный турнир."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    team_id = int(parts[5])
    app_id = int(parts[6])
    offset = int(parts[7]) if len(parts) > 7 else 0

    tournament = get_tournament_by_id(tournament_id)
    app = _get_tournament_application_details(app_id)
    if not tournament or not app or app['tournament_id'] != tournament_id or app['team_id'] != team_id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    text = _build_admin_team_card_text(
        team_id,
        tournament=tournament,
        app_status=app['status']
    )
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if app['status'] == 'pending':
        builder.button(text="✅ Одобрить",
                       callback_data=f"approve_{app_id}_{tournament_id}_{offset}")
        builder.button(text="❌ Отклонить",
                       callback_data=f"reject_{app_id}_{tournament_id}_{offset}")
    builder.button(text="🔙 Назад к заявкам",
                   callback_data=f"admin_tournament_teams_page_{tournament_id}_{offset}")
    builder.adjust(2)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_applications_"))
async def admin_tournament_applications(callback: CallbackQuery):
    """Заявки на конкретный турнир (устаревшее, перенаправляет на teams)."""
    # Перенаправляем на новый обработчик teams
    tournament_id = int(callback.data.split("_")[3])
    await show_tournament_teams_list(callback.message, tournament_id)
    await callback.answer()


# ---------- Создание матча ----------


class CreateMatch(StatesGroup):
    tournament = State()
    team1 = State()
    team2 = State()
    date = State()
    location = State()


@router.callback_query(F.data == "admin_create_match")
async def admin_create_match_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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


@router.callback_query(F.data.startswith("match_tournament_"))
async def match_choose_tournament(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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


@router.callback_query(CreateMatch.team1, F.data.startswith("match_team1_"))
async def match_choose_team1(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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


@router.callback_query(CreateMatch.team2, F.data.startswith("match_team2_"))
async def match_choose_team2(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    team2_id = int(callback.data.split("_")[2])
    await state.update_data(team2_id=team2_id)
    await state.set_state(CreateMatch.date)
    await callback.message.edit_text("Введите дату и время матча (например, 1 янв. 18:00):")


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
            logger.info(
                f"Не удалось отправить уведомление {member['telegram_id']}: {e}")

    conn.close()
    await state.clear()
    await message.answer("Матч создан!", reply_markup=admin_menu_keyboard())

# ---------- Ввод результата матча ----------


class EnterResult(StatesGroup):
    match = State()
    score1 = State()
    score2 = State()
    volleyball_sets = State()
    player_stats = State()


# Обработчик admin_enter_result больше не используется
# Ввод результата теперь через brackets.py → match_input.py


def _load_legacy_match_for_stats(match_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.*, t.name AS tournament_name, t.sport,
               tm1.name AS team1_name, tm2.name AS team2_name
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        LEFT JOIN teams tm1 ON tm1.id = m.team1_id
        LEFT JOIN teams tm2 ON tm2.id = m.team2_id
        WHERE m.id=?
    """, (match_id,))
    row = cur.fetchone()
    conn.close()
    return row


def _fetch_players(team1_id: int, team2_id: int) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.id, u.first_name, u.username, tm.team_id
        FROM users u
        JOIN team_members tm ON tm.user_id = u.id
        WHERE tm.team_id IN (?, ?)
        ORDER BY tm.team_id, u.first_name
        """,
        (team1_id, team2_id),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


async def _prompt_next_legacy_player(message: Message, state: FSMContext):
    data = await state.get_data()
    players = data.get("legacy_players", [])
    idx = data.get("legacy_player_index", 0)
    if idx >= len(players):
        await _save_legacy_player_stats_and_finish(message, state)
        return

    player = players[idx]
    team1_id = data.get("team1_id")
    team_name = data.get("team1_name") if player["team_id"] == team1_id else data.get("team2_name")
    sport = normalize_sport_name(data.get("sport_mode"))

    if sport == "Football":
        prompt = "Введите goals:assists (пример: 1:0)"
    elif sport == "Basketball":
        prompt = "Введите points:fouls (пример: 18:3)"
    else:
        prompt = "Введите points:aces (пример: 14:2)"

    await message.answer(
        f"Игрок {idx + 1}/{len(players)}\n"
        f"Команда: {team_name}\n"
        f"Игрок: {player['first_name']} (@{player['username'] or 'без_username'})\n\n"
        f"{prompt}"
    )


async def _save_legacy_player_stats_and_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    sport = normalize_sport_name(data.get("sport_mode"))
    match_id = data.get("match_id")
    stats = data.get("legacy_player_stats", [])

    for item in stats:
        if sport == "Football":
            upsert_football_player_stat(
                "legacy",
                match_id,
                item["user_id"],
                item["team_id"],
                item.get("goals", 0),
                item.get("assists", 0),
            )
        elif sport == "Basketball":
            upsert_basketball_player_stat(
                "legacy",
                match_id,
                item["user_id"],
                item["team_id"],
                item.get("points", 0),
                item.get("fouls", 0),
            )
        elif sport == "Volleyball":
            upsert_volleyball_player_stat(
                "legacy",
                match_id,
                item["user_id"],
                item["team_id"],
                item.get("points", 0),
                item.get("aces", 0),
            )

    if sport == "Volleyball":
        replace_volleyball_set_scores("legacy", match_id, data.get("volleyball_sets", []))

    await state.clear()
    await message.answer("Результат и персональная статистика сохранены!", reply_markup=admin_menu_keyboard())


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
    score2_value = score2

    conn = get_connection()
    sport = "CS2"
    match_meta = None
    try:
        cur = conn.cursor()
        cur.execute("UPDATE matches SET score1=?, score2=?, status='completed' WHERE id=?",
                    (score1, score2, match_id))

        cur.execute(
            "SELECT team1_id, team2_id, tournament_id FROM matches WHERE id=?", (match_id,))
        match = cur.fetchone()
        if match:
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            cur.execute("SELECT sport FROM tournaments WHERE id=?",
                        (match['tournament_id'],))
            tour = cur.fetchone()
            sport = normalize_sport_name(tour['sport']) if tour else "CS2"
            match_meta = match

            if score1 > score2:
                update_team_rating_same_conn(
                    conn, match['team1_id'], sport, month, 5)
                update_team_rating_same_conn(
                    conn, match['team2_id'], sport, month, 0)
            elif score1 < score2:
                update_team_rating_same_conn(
                    conn, match['team1_id'], sport, month, 0)
                update_team_rating_same_conn(
                    conn, match['team2_id'], sport, month, 5)
            else:
                update_team_rating_same_conn(
                    conn, match['team1_id'], sport, month, 1)
                update_team_rating_same_conn(
                    conn, match['team2_id'], sport, month, 1)

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.info(f"Ошибка при сохранении результата: {e}")
        await message.answer("Произошла ошибка, попробуйте позже.")
        return
    finally:
        conn.close()

    if match_meta and sport in ("Football", "Basketball", "Volleyball"):
        players = _fetch_players(match_meta["team1_id"], match_meta["team2_id"])
        if not players:
            await state.clear()
            await message.answer("Результат сохранён! В командах нет игроков для статистики.", reply_markup=admin_menu_keyboard())
            return

        await state.update_data(
            sport_mode=sport,
            team1_id=match_meta["team1_id"],
            team2_id=match_meta["team2_id"],
            score1=score1,
            score2=score2_value,
            legacy_players=players,
            legacy_player_index=0,
            legacy_player_stats=[],
        )

        t1 = get_team_by_id(match_meta["team1_id"])
        t2 = get_team_by_id(match_meta["team2_id"])
        await state.update_data(
            team1_name=t1["name"] if t1 else "Команда 1",
            team2_name=t2["name"] if t2 else "Команда 2",
        )

        if sport == "Volleyball":
            await state.set_state(EnterResult.volleyball_sets)
            await message.answer(
                "🏐 Результат матча сохранён.\n"
                "Введите счет по партиям в формате 25:20,23:25,15:13\n"
                "Количество выигранных партий должно совпадать с итоговым счетом."
            )
            return

        await state.set_state(EnterResult.player_stats)
        await _prompt_next_legacy_player(message, state)
        return

    await state.clear()
    await message.answer("Результат сохранён!", reply_markup=admin_menu_keyboard())


@router.message(EnterResult.volleyball_sets)
async def result_enter_volleyball_sets(message: Message, state: FSMContext):
    data = await state.get_data()
    raw = message.text.strip()
    try:
        sets_raw = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
        if not sets_raw:
            raise ValueError
        parsed = []
        team1_wins = 0
        team2_wins = 0
        for chunk in sets_raw:
            if ":" not in chunk:
                raise ValueError
            left, right = chunk.split(":", 1)
            p1 = int(left)
            p2 = int(right)
            if p1 < 0 or p2 < 0 or p1 == p2:
                raise ValueError
            parsed.append((p1, p2))
            if p1 > p2:
                team1_wins += 1
            else:
                team2_wins += 1
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: 25:20,23:25,15:13")
        return

    if team1_wins != data.get("score1") or team2_wins != data.get("score2"):
        await message.answer("❌ Партии не совпадают с итоговым счетом матча.")
        return

    await state.update_data(volleyball_sets=parsed)
    await state.set_state(EnterResult.player_stats)
    await _prompt_next_legacy_player(message, state)


@router.message(EnterResult.player_stats)
async def result_enter_player_stats(message: Message, state: FSMContext):
    data = await state.get_data()
    sport = normalize_sport_name(data.get("sport_mode"))
    players = data.get("legacy_players", [])
    idx = data.get("legacy_player_index", 0)
    stats = data.get("legacy_player_stats", [])

    if idx >= len(players):
        await _save_legacy_player_stats_and_finish(message, state)
        return

    player = players[idx]
    text = message.text.strip()
    try:
        parts = text.split(":")
        left = int(parts[0]) if len(parts) > 0 else 0
        right = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        await message.answer("❌ Неверный формат. Введите два числа через ':'.")
        return

    if left < 0 or right < 0:
        await message.answer("❌ Значения не могут быть отрицательными.")
        return

    payload = {
        "user_id": player["id"],
        "team_id": player["team_id"],
    }
    if sport == "Football":
        payload["goals"] = left
        payload["assists"] = right
    elif sport == "Basketball":
        payload["points"] = left
        payload["fouls"] = right
    else:
        payload["points"] = left
        payload["aces"] = right

    stats.append(payload)
    await state.update_data(legacy_player_stats=stats, legacy_player_index=idx + 1)
    await _prompt_next_legacy_player(message, state)

# ---------- Управление пользователями ----------


class UserManagement(StatesGroup):
    searching = State()


@router.callback_query(F.data == "admin_users")
async def admin_users_menu(callback: CallbackQuery):
    await callback.answer()
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


@router.callback_query(F.data == "admin_user_list")
async def admin_user_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    users = get_all_users(0, 10)
    total = get_all_users_count()  # нужно добавить функцию в utils
    await show_user_list(callback.message, users, 0, total)
    await callback.answer()


async def show_user_list(message, users, offset, total):
    """
    Отображает список пользователей в виде кнопок.
    users – список пользователей для текущей страницы
    offset – текущее смещение
    total – общее количество пользователей
    """
    if not users:
        text = "Пользователи не найдены."
        kb = back_to_main_keyboard()
        await message.edit_text(text, reply_markup=kb)
        return

    builder = InlineKeyboardBuilder()
    for u in users:
        status_icon = "🟢" if not u['is_banned'] else "🔴"
        button_text = f"{status_icon} {u['id']}: {u['first_name']} @{u['username']} ({u['role']})"
        builder.button(
            text=button_text, callback_data=f"admin_user_view_{u['id']}_list_{offset}")
    builder.adjust(1)

    # Кнопки пагинации
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_user_page_{offset-10}"))
    if offset + 10 < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_user_page_{offset+10}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    # Кнопка назад в меню пользователей
    builder.row(InlineKeyboardButton(
        text="🔙 Назад", callback_data="admin_users"))

    await message.edit_text("Список пользователей:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_user_page_"))
async def admin_user_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    offset = int(callback.data.split("_")[3])
    users = get_all_users(offset, 10)
    total = get_all_users_count()
    await show_user_list(callback.message, users, offset, total)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_view_"))
async def admin_user_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    user_id = int(parts[3])
    source = parts[4]  # "list" или "search"
    offset = int(parts[5]) if source == "list" else 0
    query = parts[5] if source == "search" else ""

    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    age_str = str(user['age']) if user['age'] is not None else "не указан"
    favorite_sports_display = "не указаны"
    if user['favorite_sports']:
        try:
            raw_sports = json.loads(user['favorite_sports'])
            if isinstance(raw_sports, list):
                favorite_sports_display = ", ".join(map_sports_to_display(raw_sports)) or "не указаны"
            else:
                favorite_sports_display = str(user['favorite_sports'])
        except Exception:
            favorite_sports_display = str(user['favorite_sports'])
    text = f"""
👤 Пользователь #{user['id']}
Telegram ID: {user['telegram_id']}
Имя: {user['first_name']} {user['last_name']}
Username: @{user['username']}
Email: {user['email']}
Город: {user['city']}
Возраст: {age_str}
Любимые виды спорта: {favorite_sports_display}
Роль: {user['role']}
Статус: {'🔴 Забанен' if user['is_banned'] else '🟢 Активен'}
    """

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Сменить роль",
                   callback_data=f"admin_user_changerole_{user_id}")
    builder.button(text="🔨 Бан/Разбан",
                   callback_data=f"admin_user_toggleban_{user_id}")

    # Кнопка назад в зависимости от источника
    if source == "list":
        builder.button(text="🔙 Назад к списку",
                       callback_data=f"admin_user_page_{offset}")
    elif source == "search":
        builder.button(text="🔙 К результатам поиска",
                       callback_data=f"admin_search_page_{query}_{offset}")
    else:
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
    await state.clear()
    users = search_users(query)
    total = search_users_count(query)  # нужно добавить в utils
    await show_search_results(message, query, users, 0, total)


async def show_search_results(message, query, users, offset, total):
    if not users:
        kb = back_to_main_keyboard()
        await message.answer(f"По запросу «{query}» ничего не найдено.", reply_markup=kb)
        return

    builder = InlineKeyboardBuilder()
    for u in users:
        status_icon = "🟢" if not u['is_banned'] else "🔴"
        button_text = f"{status_icon} {u['id']}: {u['first_name']} @{u['username']} ({u['role']})"
        builder.button(
            text=button_text, callback_data=f"admin_user_view_{u['id']}_search_{query}_{offset}")
    builder.adjust(1)

    # Пагинация
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_search_page_{query}_{offset-10}"))
    if offset + 10 < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_search_page_{query}_{offset+10}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(
        text="🔙 Назад", callback_data="admin_users"))

    await message.answer(f"Результаты поиска по запросу «{query}»:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_search_page_"))
async def admin_search_page(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    query = parts[3]
    offset = int(parts[4])
    # нужно изменить search_users, добавив offset/limit
    users = search_users(query, offset, 10)
    total = search_users_count(query)
    await show_search_results(callback.message, query, users, offset, total)


# ---------- Удаление турнира (кнопка в карточке турнира) ----------


@router.callback_query(F.data.startswith("admin_delete_tournament_"))
async def admin_delete_tournament_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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
            text="❌ Нет", callback_data=f"admin_tournament_manage_{tournament_id}")]
    ])
    await callback.message.edit_text(f"Вы уверены, что хотите удалить турнир «{tournament['name']}»? Это действие нельзя отменить.", reply_markup=kb)
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

    await callback.message.answer("✅ Турнир удалён.")
    # Отправляем отдельное сообщение с кнопкой
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Управление турнирами",
                              callback_data="admin_tournaments_list")]
    ])
    await callback.message.answer("Нажмите кнопку ниже:", reply_markup=kb)

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
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sport = callback.data.replace("admin_rating_sport_", "")
    await callback.message.edit_text(
        f"Действия для спорта {get_sport_display_name(sport)}:",
        reply_markup=admin_rating_sport_actions_keyboard(sport),
    )


@router.callback_query(F.data.startswith("admin_rating_reset_all_"))
async def admin_rating_reset_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sport = callback.data.replace("admin_rating_reset_all_", "")
    reset_sport_rating(sport)
    await callback.answer(f"Рейтинг по {get_sport_display_name(sport)} обнулён!", show_alert=True)
    await admin_rating_menu(callback)


@router.callback_query(F.data.startswith("admin_rating_list_teams_"))
async def admin_rating_list_teams(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sport = callback.data.replace("admin_rating_list_teams_", "")
    teams = get_teams_with_rating(sport)
    if not teams:
        await callback.message.edit_text(
            f"Нет команд по спорту {get_sport_display_name(sport)}.",
            reply_markup=back_to_main_keyboard(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"Команды по {get_sport_display_name(sport)}:",
        reply_markup=admin_rating_teams_list_keyboard(teams, sport),
    )


@router.callback_query(F.data.startswith("admin_rating_team_"))
async def admin_rating_team_actions(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    team_id = int(parts[3])
    sport = parts[4]
    await state.update_data(team_id=team_id, sport=sport)
    await callback.message.edit_text(f"Действия для команды:", reply_markup=admin_rating_team_actions_keyboard(team_id, sport))


@router.callback_query(F.data.startswith("admin_rating_reset_team_"))
async def admin_rating_reset_team(callback: CallbackQuery):
    await callback.answer()
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
    await callback.message.edit_text(
        f"Команды по {get_sport_display_name(sport)}:",
        reply_markup=admin_rating_teams_list_keyboard(teams, sport),
    )


class DeductPoints(StatesGroup):
    points = State()


@router.callback_query(F.data.startswith("admin_rating_deduct_"))
async def admin_rating_deduct_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    team_id = int(parts[3])
    sport = parts[4]
    await state.update_data(team_id=team_id, sport=sport)
    await state.set_state(DeductPoints.points)
    await callback.message.edit_text("Введите количество очков для снятия:")


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

# ---------- Управление командами ----------


@router.callback_query(F.data == "admin_teams")
async def admin_teams_list(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_admin_teams_page(callback.message, 0)


async def show_admin_teams_page(message, offset: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM teams")
    total = cur.fetchone()[0]
    if total > 0 and offset >= total:
        offset = ((total - 1) // PAGE_SIZE) * PAGE_SIZE
    if offset < 0:
        offset = 0

    cur.execute("""
        SELECT id, name, sport, city
        FROM teams
        ORDER BY name
        LIMIT ? OFFSET ?
    """, (PAGE_SIZE, offset))
    teams = cur.fetchall()
    conn.close()

    if not teams:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await message.edit_text("Нет команд.", reply_markup=kb)
        return

    end_pos = min(offset + PAGE_SIZE, total)
    text = f"Список команд ({offset + 1}-{end_pos} из {total}):"
    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(
            text=f"⚙️ {team['name']} ({get_sport_display_name(team['sport'])})",
            callback_data=f"admin_team_manage_{team['id']}"
        )

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_teams_page_{offset - PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_teams_page_{offset + PAGE_SIZE}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    await message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_teams_page_"))
async def admin_teams_page(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    offset = int(callback.data.split("_")[3])
    await show_admin_teams_page(callback.message, offset)


@router.callback_query(F.data.startswith("admin_team_manage_"))
async def admin_team_manage_card(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[3])
    text = _build_admin_team_card_text(team_id)
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    await callback.message.edit_text(text, reply_markup=_admin_team_manage_card_keyboard(team_id))


@router.callback_query(F.data.startswith("admin_team_delete_confirm_"))
async def admin_team_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[4])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить",
                              callback_data=f"admin_team_delete_execute_{team_id}")],
        [InlineKeyboardButton(
            text="❌ Нет", callback_data=f"admin_team_manage_{team_id}")]
    ])
    await callback.message.edit_text(f"Вы уверены, что хотите удалить команду «{team['name']}»? Это действие нельзя отменить.", reply_markup=kb)


@router.callback_query(F.data.startswith("admin_team_delete_execute_"))
async def admin_team_delete_execute(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[4])
    delete_team_admin(team_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку команд",
                              callback_data="admin_teams")],
        [InlineKeyboardButton(text="🔙 В админ-меню",
                              callback_data="admin_menu")]
    ])
    await callback.message.edit_text("✅ Команда удалена.", reply_markup=kb)


@router.callback_query(F.data.startswith("admin_delete_team_"))
async def admin_delete_team_legacy_redirect(callback: CallbackQuery):
    """Legacy callback: перенаправляем старые кнопки в новый поток карточки команды."""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[3])
    text = _build_admin_team_card_text(team_id)
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    await callback.message.edit_text(text, reply_markup=_admin_team_manage_card_keyboard(team_id))


@router.callback_query(F.data == "admin_confirm_delete_team")
async def admin_delete_team_execute_legacy(callback: CallbackQuery, state: FSMContext):
    """Legacy callback: поддержка старых сообщений с FSM-подтверждением удаления."""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    data = await state.get_data()
    team_id = data.get('team_id')
    if not team_id:
        await callback.answer("Старая кнопка устарела. Откройте «Управление командами».", show_alert=True)
        return

    delete_team_admin(team_id)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку команд",
                              callback_data="admin_teams")],
        [InlineKeyboardButton(text="🔙 В админ-меню",
                              callback_data="admin_menu")]
    ])
    await callback.message.edit_text("✅ Команда удалена.", reply_markup=kb)


@router.callback_query(F.data == "admin_menu")
async def back_to_admin_menu(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.message.edit_text("Панель администратора:", reply_markup=admin_menu_keyboard())