from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import get_connection
from razryad_arena_utils import (
    get_user, get_user_teams, get_tournaments_by_sport, get_tournament_by_id,
    add_tournament_application, get_all_sports, get_team_application,
    get_approved_teams_count, get_tournament_teams, can_manage_tournament, is_admin, get_team_members_count, can_retry_tournament_application, get_team_by_id, get_team_members, is_captain,
    get_sport_display_name, normalize_sport_name, ensure_tournament_invite_token,
    get_tournament_member_application_conflicts, get_user_tournament_captain_teams,
)
from keyboards import (
    tournaments_main_keyboard, tournaments_list_keyboard,
    choose_team_keyboard, back_to_tournament_keyboard
)

router = Router()


CONFLICT_STATUS_TEXT = {
    "pending": "заявка на рассмотрении",
    "approved": "заявка уже одобрена",
}


def _format_member_display(member: dict) -> str:
    username = member.get("username")
    if username:
        return f"{member.get('first_name', 'Участник')} (@{username})"
    return member.get("first_name", "Участник")


def _format_tournament_member_conflicts(conflicts: list[dict]) -> str:
    lines = [
        "❌ Нельзя подать заявку: некоторые участники уже заявлены в другой команде этого турнира.",
        "",
    ]
    for conflict in conflicts:
        status_text = CONFLICT_STATUS_TEXT.get(conflict["conflict_status"], conflict["conflict_status"])
        lines.append(
            f"• {_format_member_display(conflict)} — команда «{conflict['conflict_team_name']}», {status_text}."
        )
    return "\n".join(lines)


@router.callback_query(F.data == "tournaments")
async def tournaments_main(callback: CallbackQuery):
    sports = get_all_sports()
    await callback.message.edit_text("Выберите вид спорта:", reply_markup=tournaments_main_keyboard(sports))
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_sport_"))
async def list_sport_tournaments(callback: CallbackQuery):
    sport = callback.data.replace("tournament_sport_", "")
    sport_display = get_sport_display_name(sport)
    tournaments = get_tournaments_by_sport(sport)
    if not tournaments:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К выбору спорта",
                                  callback_data="tournaments")]
        ])
        await callback.message.edit_text(f"Турниров по {sport_display} пока нет.", reply_markup=kb)
    else:
        await callback.message.edit_text(f"Турниры по {sport_display}:", reply_markup=tournaments_list_keyboard(tournaments, sport))
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_teams_"))
async def show_tournament_teams(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[2])
    teams = get_tournament_teams(tournament_id, 'approved')
    if not teams:
        text = "В этом турнире пока нет команд."
    else:
        text = "Команды, участвующие в турнире:\n" + \
            "\n".join([f"- {t['name']}" for t in teams])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔙 Назад", callback_data=f"tournament_{tournament_id}")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^tournament_\d+$"))
async def view_tournament(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[1])
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден")
        return

    user = get_user(callback.from_user.id)
    teams = get_user_teams(user['id']) if user else []

    # Кнопка "Подать заявку" доступна только капитану команды нужного спорта.
    can_apply = False
    can_manage_roster = False
    if user and tournament['status'] == 'registration':
        for team in teams:
            if normalize_sport_name(team['sport']) == normalize_sport_name(tournament['sport']) and is_captain(user['id'], team['id']):
                can_apply = True
                break
    captain_teams = []
    if user and int(tournament['replacements_enabled'] or 0) == 1:
        captain_teams = list(get_user_tournament_captain_teams(tournament_id, user['telegram_id']))
        can_manage_roster = bool(captain_teams)

    status_map = {
        'registration': 'Регистрация',
        'active': 'Идёт',
        'finished': 'Завершён'
    }
    status_display = status_map.get(tournament['status'], tournament['status'])
    invite_token = ensure_tournament_invite_token(tournament_id, regenerate=False)
    bot_username = ""
    try:
        me = await callback.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""
    invite_url = f"https://t.me/{bot_username}?start=tournament_invite_{invite_token}" if (invite_token and bot_username) else "недоступна"
    age_restriction = ""
    if tournament['min_age'] is not None and tournament['max_age'] is not None:
        age_restriction = f"\nВозраст: {tournament['min_age']}–{tournament['max_age']} лет"

    text = f"""
🏆 {tournament['name']}
Вид спорта: {get_sport_display_name(tournament['sport'])}
Требуемый размер команды: {tournament['required_team_size']} чел.
Город: {tournament['city']}
Даты: {tournament['start_date']} - {tournament['end_date']}
Макс. команд: {tournament['max_teams']}
{age_restriction}
Статус: {status_display}
Ссылка-приглашение: {invite_url}
"""
    if tournament['description']:
        text += f"\n📝 Описание: {tournament['description']}"

    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="📝 Подать заявку",
                       callback_data=f"apply_{tournament_id}")
    builder.button(text="📋 Список команд",
                   callback_data=f"tournament_teams_{tournament_id}")

    # Кнопка просмотра сетки (если сгенерирована - показывают ВСЕМ)
    if tournament['bracket_generated']:
        builder.button(text="📊 Турнирная сетка",
                       callback_data=f"view_bracket_{tournament_id}")

    # Кнопка управления турниром (только админ)
    if user and can_manage_tournament(user['telegram_id'], tournament_id):
        builder.button(text="⚙️ Управление турниром",
                       callback_data=f"admin_tournament_manage_{tournament_id}")
    elif can_manage_roster:
        if len(captain_teams) == 1:
            builder.button(text="🔁 Замены состава", callback_data=f"tournament_roster_open_{tournament_id}_{captain_teams[0]['id']}")
        else:
            builder.button(text="🔁 Замены состава", callback_data=f"tournament_roster_manage_{tournament_id}")

    builder.button(
        text="🔙 Назад", callback_data=f"tournament_sport_{tournament['sport']}")
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


@router.callback_query(F.data.startswith("tourn_apply_team_"))
async def apply_with_team(callback: CallbackQuery):
    parts = callback.data.split("_")
    team_id = int(parts[3])
    tournament_id = int(parts[4])

    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    if tournament['status'] != 'registration':
        await callback.answer("❌ Подача заявок закрыта: турнир уже не в стадии регистрации.", show_alert=True)
        return

    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    if normalize_sport_name(team['sport']) != normalize_sport_name(tournament['sport']):
        await callback.answer("❌ Эта команда не подходит по виду спорта турнира.", show_alert=True)
        return

    if not is_captain(user['id'], team_id):
        await callback.answer("❌ Подать заявку может только капитан команды.", show_alert=True)
        return

    conflicts = get_tournament_member_application_conflicts(
        tournament_id,
        team_id,
        statuses=("pending", "approved"),
    )
    if conflicts:
        await callback.answer("❌ Есть участники, уже заявленные в другой команде.", show_alert=True)
        await callback.message.answer(_format_tournament_member_conflicts(conflicts))
        return

    # Проверка лимита команд (должна выполняться в любом случае)
    approved_count = get_approved_teams_count(tournament_id)
    if approved_count >= tournament['max_teams']:
        await callback.answer("❌ Все места в турнире уже заняты.", show_alert=True)
        return

    # Получаем информацию о существующей заявке
    app = get_team_application(tournament_id, team_id)

    if app:
        status = app['status']
        if status == 'approved':
            await callback.answer("❌ Ваша команда уже участвует в турнире.", show_alert=True)
            return
        elif status == 'pending':
            await callback.answer("⏳ Заявка уже отправлена, ожидайте решения.", show_alert=True)
            return
        elif status == 'excluded':
            await callback.answer(
                "❌ Команда исключена из этого турнира. Повторная заявка возможна только после решения администратора.",
                show_alert=True
            )
            return
        elif status == 'rejected':
            # Проверяем, можно ли подать повторно
            ok, minutes = can_retry_tournament_application(
                tournament_id, team_id)
            if not ok:
                await callback.answer(f"⏱ Повторная подача будет доступна через {minutes} мин.", show_alert=True)
                return

            conflicts = get_tournament_member_application_conflicts(
                tournament_id,
                team_id,
                statuses=("pending", "approved"),
            )
            if conflicts:
                await callback.answer("❌ Есть участники, уже заявленные в другой команде.", show_alert=True)
                await callback.message.answer(_format_tournament_member_conflicts(conflicts))
                return
            # Обновляем статус
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE tournament_applications SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE tournament_id=? AND team_id=?",
                (tournament_id, team_id))
            conn.commit()
            conn.close()
            await callback.answer("✅ Заявка отправлена повторно!", show_alert=True)
            await callback.message.edit_text("Заявка отправлена! Ожидайте подтверждения администратора.",
                                             reply_markup=back_to_tournament_keyboard(tournament_id))
            return
    else:
        # НОВАЯ ЗАЯВКА – проверяем размер, возраст и SteamID
        current_size = get_team_members_count(team_id)
        if current_size != tournament['required_team_size']:
            await callback.answer(f"❌ В вашей команде {current_size} чел., а требуется {tournament['required_team_size']}.", show_alert=True)
            return

        members = get_team_members(team_id)
        problems = []

        for member in members:
            # Проверка возраста
            age = member['age']
            if age is None:
                problems.append(
                    f"❌ {member['first_name']} (@{member['username']}) не указал возраст.")
            elif tournament['min_age'] is not None and tournament['max_age'] is not None:
                if age < tournament['min_age'] or age > tournament['max_age']:
                    problems.append(
                        f"❌ {member['first_name']} (@{member['username']}) имеет возраст {age}, требуется {tournament['min_age']}-{tournament['max_age']}.")

            # Проверка SteamID (только для CS2)
            if normalize_sport_name(tournament['sport']) == 'CS2':
                if not member['steam_id']:
                    problems.append(
                        f"❌ {member['first_name']} (@{member['username']}) не указал свой Steam профиль. Необходимо для участия в CS2 турнире.")

        if problems:
            await callback.answer("\n".join(problems), show_alert=True)
            return

        # Создаём новую заявку (лимит команд уже проверен выше)
        add_tournament_application(tournament_id, team_id)
        await callback.answer("✅ Заявка отправлена!", show_alert=True)
        await callback.message.edit_text("Заявка отправлена! Ожидайте подтверждения администратора.",
                                         reply_markup=back_to_tournament_keyboard(tournament_id))


async def _build_apply_teams_context(callback: CallbackQuery, tournament_id: int):
    user = get_user(callback.from_user.id)
    if not user:
        return None, None, None, "Сначала зарегистрируйся"

    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        return None, None, None, "Турнир не найден"

    if tournament['status'] != 'registration':
        return None, None, None, "❌ Подача заявок закрыта: турнир уже не в стадии регистрации."

    # Только команды нужного спорта, где пользователь капитан.
    teams = [
        t for t in get_user_teams(user['id'])
        if normalize_sport_name(t['sport']) == normalize_sport_name(tournament['sport']) and is_captain(user['id'], t['id'])
    ]
    if not teams:
        return None, None, None, "❌ У вас нет команды по этому виду спорта, где вы капитан."

    # Проверяем лимит команд в турнире
    approved_count = get_approved_teams_count(tournament_id)
    if approved_count >= tournament['max_teams']:
        return None, None, None, f"❌ Достигнуто максимальное количество команд в турнире ({tournament['max_teams']})."

    return user, tournament, teams, None


@router.callback_query(F.data.startswith("apply_page_"))
async def apply_teams_page(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[2])
    offset = int(parts[3])

    _, _, teams, error = await _build_apply_teams_context(callback, tournament_id)
    if error:
        await callback.answer(error, show_alert=True)
        return

    # Показываем список команд капитана (без дополнительной фильтрации по статусам заявок).
    total = len(teams)
    safe_offset = max(0, min(offset, max(total - 1, 0)))
    safe_offset = (safe_offset // 10) * 10
    await callback.message.edit_text(
        "Выберите команду для участия:",
        reply_markup=choose_team_keyboard(teams, tournament_id, offset=safe_offset, page_size=10)
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^apply_\d+$"))
async def apply_to_tournament(callback: CallbackQuery):
    """Показывает список команд пользователя по спорту, где он капитан."""
    tournament_id = int(callback.data.split("_")[1])
    _, _, teams, error = await _build_apply_teams_context(callback, tournament_id)
    if error:
        await callback.answer(error, show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите команду для участия:",
        reply_markup=choose_team_keyboard(teams, tournament_id, offset=0, page_size=10)
    )
    await callback.answer()

