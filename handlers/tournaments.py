from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils import (
    get_user, get_user_teams, get_tournaments_by_sport, get_tournament_by_id,
    add_tournament_application, get_all_sports, get_team_application,
    get_approved_teams_count, get_tournament_teams, is_admin
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

    # Русские статусы
    status_map = {
        'registration': 'Регистрация',
        'active': 'Активен',
        'finished': 'Завершён'
    }
    status_display = status_map.get(tournament['status'], tournament['status'])

    text = f"""
🏆 {tournament['name']}
Вид спорта: {tournament['sport']}
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


@router.callback_query(F.data.startswith("apply_team_"))
async def apply_with_team(callback: CallbackQuery):
    parts = callback.data.split("_")
    team_id = int(parts[2])
    tournament_id = int(parts[3])
    status = get_team_application(tournament_id, team_id)
    if status:
        await callback.answer("Заявка уже подана или команда участвует", show_alert=True)
        return
    add_tournament_application(tournament_id, team_id)
    await callback.message.edit_text("Заявка отправлена! Ожидайте подтверждения администратора.",
                                     reply_markup=back_to_main_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("apply_"))
async def apply_to_tournament(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[1])
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйся", show_alert=True)
        return

    teams = [t for t in get_user_teams(
        user['id']) if t['sport'] == get_tournament_by_id(tournament_id)['sport']]
    if not teams:
        await callback.answer("У вас нет команды по этому виду спорта", show_alert=True)
        return

    valid_teams = []
    for team in teams:
        status = get_team_application(tournament_id, team['id'])
        if status is None:
            valid_teams.append(team)

    if not valid_teams:
        await callback.answer("Вы уже подали заявку или участвуете в турнире", show_alert=True)
        return

    if len(valid_teams) == 1:
        team = valid_teams[0]
        add_tournament_application(tournament_id, team['id'])
        await callback.message.edit_text("Заявка отправлена! Ожидайте подтверждения администратора.",
                                         reply_markup=back_to_main_keyboard())
    else:
        await callback.message.edit_text("Выберите команду для участия:",
                                         reply_markup=choose_team_keyboard(valid_teams, tournament_id))
    await callback.answer()
