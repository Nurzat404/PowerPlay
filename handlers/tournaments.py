from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import get_connection
from utils import (
    get_user, get_user_teams, get_tournaments_by_sport, get_tournament_by_id,
    add_tournament_application, get_all_sports, get_team_application,
    get_approved_teams_count, get_tournament_teams, is_admin, get_team_members_count, can_retry_tournament_application, get_team_by_id
)
from keyboards import (
    tournaments_main_keyboard, tournaments_list_keyboard,
    choose_team_keyboard, back_to_main_keyboard
)

router = Router()


@router.callback_query(F.data == "tournaments")
async def tournaments_main(callback: CallbackQuery):
    sports = get_all_sports()
    await callback.message.edit_text("Выберите вид спорта:", reply_markup=tournaments_main_keyboard(sports))
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_sport_"))
async def list_sport_tournaments(callback: CallbackQuery):
    sport = callback.data.replace("tournament_sport_", "")
    tournaments = get_tournaments_by_sport(sport)
    if not tournaments:
        await callback.message.edit_text(f"Турниров по {sport} пока нет.", reply_markup=back_to_main_keyboard())
    else:
        await callback.message.edit_text(f"Турниры по {sport}:", reply_markup=tournaments_list_keyboard(tournaments, sport))
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


@router.callback_query(F.data.startswith("tournament_"))
async def view_tournament(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[1])
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден")
        return

    user = get_user(callback.from_user.id)
    teams = get_user_teams(user['id']) if user else []
    approved_count = get_approved_teams_count(tournament_id)

    # Проверяем, может ли пользователь подать заявку
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

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
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

    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    # Получаем информацию о существующей заявке
    app = get_team_application(tournament_id, team_id)

    if app:
        status = app['status']
        if status == 'approved':   # было 'accepted'
            await callback.answer("❌ Ваша команда уже участвует в турнире.", show_alert=True)
            return
        elif status == 'pending':
            await callback.answer("⏳ Заявка уже отправлена, ожидайте решения.", show_alert=True)
            return
        elif status == 'rejected':
            # Проверяем, можно ли повторно
            ok, minutes = can_retry_tournament_application(
                tournament_id, team_id)
            if not ok:
                await callback.answer(f"⏱ Повторная подача будет доступна через {minutes} мин.", show_alert=True)
                return
            else:
                # Создаём НОВУЮ заявку (не обновляем старую)
                add_tournament_application(tournament_id, team_id)
                await callback.answer("✅ Заявка отправлена повторно!", show_alert=True)
                await callback.message.edit_text("Заявка отправлена. Ожидайте подтверждения администратора.", reply_markup=back_to_main_keyboard())
                return
    else:
        # Нет заявки – проверяем размер
        current_size = get_team_members_count(team_id)
        if current_size != tournament['required_team_size']:
            await callback.answer(f"❌ В вашей команде {current_size} чел., а требуется {tournament['required_team_size']}.", show_alert=True)
            return
        # Создаём новую заявку
        add_tournament_application(tournament_id, team_id)
        await callback.answer("✅ Заявка отправлена!", show_alert=True)
        await callback.message.edit_text("Заявка отправлена. Ожидайте подтверждения администратора.", reply_markup=back_to_main_keyboard())
        # Уведомление админу (можно добавить)
        await callback.answer()


@router.callback_query(F.data.startswith("apply_"))
async def apply_to_tournament(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[1])
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйся", show_alert=True)
        return

    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    # Все команды пользователя по данному спорту (даже неподходящие)
    teams = [t for t in get_user_teams(
        user['id']) if t['sport'] == tournament['sport']]
    if not teams:
        await callback.answer("У вас нет команды по этому виду спорта", show_alert=True)
        return

    # Показываем список всех этих команд (без фильтрации)
    await callback.message.edit_text(
        "Выберите команду для участия:",
        # используем существующую клавиатуру
        reply_markup=choose_team_keyboard(teams, tournament_id)
    )
    await callback.answer()
