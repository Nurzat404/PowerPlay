from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_keyboard():
    kb = [
        [InlineKeyboardButton(text="🏀 Мои команды", callback_data="my_teams")],
        [InlineKeyboardButton(text="🏆 Турниры", callback_data="tournaments")],
        [InlineKeyboardButton(text="📊 Рейтинги", callback_data="ratings")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def back_to_main_keyboard():
    kb = [[InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def sports_choice_keyboard(sports_list, selected=None):
    """
    sports_list: список кортежей (name, display_name)
    selected: список выбранных технических имён (для отметки галочкой)
    """
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        text = f"✅ {display}" if selected and name in selected else display
        builder.button(text=text, callback_data=f"sport_{name}")
    builder.button(text="✅ Готово", callback_data="sport_done")
    builder.adjust(2)
    return builder.as_markup()


def confirm_keyboard():
    kb = [
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def teams_list_keyboard(teams, show_create=True):
    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(text=team['name'], callback_data=f"team_{team['id']}")
    if show_create:
        builder.button(text="➕ Создать команду", callback_data="create_team")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def team_management_keyboard(team_id, is_captain=False):
    builder = InlineKeyboardBuilder()
    if is_captain:
        builder.button(text="✏️ Изменить название",
                       callback_data=f"rename_team_{team_id}")
        builder.button(text="👥 Добавить игроков",
                       callback_data=f"add_player_{team_id}")
        builder.button(text="🗑 Удалить команду",
                       callback_data=f"delete_team_{team_id}")
    builder.button(text="🔙 Назад", callback_data="my_teams")
    builder.adjust(1)
    return builder.as_markup()


def tournaments_main_keyboard(sports_list):
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(text=display, callback_data=f"tournament_sport_{name}")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def tournaments_list_keyboard(tournaments, sport):
    builder = InlineKeyboardBuilder()
    for t in tournaments:
        builder.button(text=t['name'], callback_data=f"tournament_{t['id']}")
    builder.button(text="🔙 Назад", callback_data="tournaments")
    builder.adjust(1)
    return builder.as_markup()


def tournament_card_keyboard(tournament_id, sport_name, can_apply):
    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="📝 Подать заявку",
                       callback_data=f"apply_{tournament_id}")
    builder.button(
        text="🔙 Назад", callback_data=f"tournament_sport_{sport_name}")
    builder.adjust(1)
    return builder.as_markup()


def choose_team_keyboard(teams, tournament_id):
    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(
            text=team['name'], callback_data=f"apply_team_{team['id']}_{tournament_id}")
    builder.button(text="🔙 Назад", callback_data=f"tournament_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def ratings_sport_keyboard(sports_list):
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(text=display, callback_data=f"rating_sport_{name}")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_menu_keyboard():
    kb = [
        [InlineKeyboardButton(text="➕ Создать турнир",
                              callback_data="admin_create_tournament")],
        [InlineKeyboardButton(text="📋 Заявки на турниры",
                              callback_data="admin_applications")],
        [InlineKeyboardButton(text="➕ Создать матч",
                              callback_data="admin_create_match")],
        [InlineKeyboardButton(text="✏️ Ввести результат",
                              callback_data="admin_enter_result")],
        [InlineKeyboardButton(
            text="👥 Управление пользователями", callback_data="admin_users")],
        [InlineKeyboardButton(text="🏆 Управление рейтингом",
                              callback_data="admin_rating")],
        [InlineKeyboardButton(text="⚙️ Удаление команд",
                              callback_data="admin_teams")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def invite_keyboard(team_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=f"accept_invite_{team_id}")
    builder.button(text="❌ Отклонить",
                   callback_data=f"reject_invite_{team_id}")
    builder.adjust(2)
    return builder.as_markup()


def admin_rating_menu_keyboard(sports_list):
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(
            text=display, callback_data=f"admin_rating_sport_{name}")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_sport_actions_keyboard(sport):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обнулить весь рейтинг",
                   callback_data=f"admin_rating_reset_all_{sport}")
    builder.button(text="📋 Список команд",
                   callback_data=f"admin_rating_list_teams_{sport}")
    builder.button(text="🔙 Назад", callback_data="admin_rating")
    builder.adjust(1)
    return builder.as_markup()


def admin_teams_list_keyboard(teams):
    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(
            text=f"🗑 {team['name']}", callback_data=f"admin_delete_team_{team['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_teams_list_keyboard(teams, sport):
    builder = InlineKeyboardBuilder()
    for team in teams:
        text = f"{team['name']} ({team['points']} очков)"
        builder.button(
            text=text, callback_data=f"admin_rating_team_{team['id']}_{sport}")
    builder.button(text="🔙 Назад", callback_data=f"admin_rating_sport_{sport}")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_team_actions_keyboard(team_id, sport):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обнулить рейтинг команды",
                   callback_data=f"admin_rating_reset_team_{team_id}_{sport}")
    builder.button(text="➖ Снять очки",
                   callback_data=f"admin_rating_deduct_{team_id}_{sport}")
    builder.button(
        text="🔙 Назад", callback_data=f"admin_rating_list_teams_{sport}")
    builder.adjust(1)
    return builder.as_markup()
