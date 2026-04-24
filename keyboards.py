from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from razryad_arena_utils import get_sport_display_name


def main_menu_keyboard():
    kb = [
        [InlineKeyboardButton(text="🏀 Мои команды", callback_data="my_teams")],
        [InlineKeyboardButton(text="👥 Команды", callback_data="teams_menu")],
        [InlineKeyboardButton(text="🏆 Турниры", callback_data="tournaments")],
        [InlineKeyboardButton(text="📊 Рейтинги", callback_data="ratings")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="📜 Правила проекта", callback_data="rules")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def back_to_main_keyboard():
    kb = [[InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def sports_choice_keyboard(sports_list, selected=None):
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


def team_management_extended_keyboard(team_id, is_captain, is_open, notify_enabled, max_members=None):
    builder = InlineKeyboardBuilder()
    # Кнопка выхода для всех
    builder.button(text="🚪 Выйти из команды",
                   callback_data=f"leave_team_{team_id}")
    if is_captain:
        builder.button(text="✏️ Изменить название",
                       callback_data=f"rename_team_{team_id}")
        builder.button(text="🏙 Город",
                       callback_data=f"edit_team_city_{team_id}")
        builder.button(text="👥 Добавить игроков",
                       callback_data=f"add_player_{team_id}")
        builder.button(text="🔗 Ссылка приглашения",
                       callback_data=f"team_invite_menu_{team_id}")
        builder.button(text="🗑 Удалить команду",
                       callback_data=f"delete_team_{team_id}")
        builder.button(text="📋 Заявки в команду",
                       callback_data=f"team_requests_{team_id}")
        limit_label = max_members if max_members is not None else "?"
        builder.button(text=f"👤 Лимит: {limit_label} чел.",
                       callback_data=f"edit_max_members_{team_id}")
        open_status = "🔓 Открыт" if is_open else "🔒 Закрыт"
        builder.button(
            text=f"Приём заявок: {open_status}", callback_data=f"toggle_open_{team_id}")
        notify_status = "🔔 Вкл" if notify_enabled else "🔕 Выкл"
        builder.button(
            text=f"Уведомления: {notify_status}", callback_data=f"toggle_notify_{team_id}")
    builder.button(text="🔙 Назад", callback_data="my_teams")
    builder.adjust(1)
    return builder.as_markup()


def choose_new_captain_keyboard(team_id, members):
    """Клавиатура для выбора нового капитана из списка участников (кроме текущего)"""
    builder = InlineKeyboardBuilder()
    for member in members:
        text = f"{member['first_name']} (@{member['username']})"
        builder.button(
            text=text, callback_data=f"set_captain_{team_id}_{member['id']}")
    builder.button(text="❌ Отмена", callback_data=f"team_{team_id}")
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


def back_to_tournament_keyboard(tournament_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К турниру",
                   callback_data=f"tournament_{tournament_id}")
    return builder.as_markup()


def choose_team_keyboard(teams, tournament_id, offset=0, page_size=10):
    builder = InlineKeyboardBuilder()
    total = len(teams)
    teams_slice = teams[offset:offset + page_size]
    for team in teams_slice:
        builder.button(
            text=team['name'], callback_data=f"tourn_apply_team_{team['id']}_{tournament_id}")

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=f"apply_page_{tournament_id}_{offset-page_size}"))
    if offset + page_size < total:
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=f"apply_page_{tournament_id}_{offset+page_size}"))
    if nav:
        builder.row(*nav)

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


def ratings_entity_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Игроки", callback_data="ratings_entity_p")
    builder.button(text="👥 Команды", callback_data="ratings_entity_t")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def ratings_sport_picker_keyboard(entity_type, sports_list):
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(text=display, callback_data=f"ratings_sport_{entity_type}_{name}")
    builder.button(text="🔙 Назад", callback_data="ratings")
    builder.adjust(1)
    return builder.as_markup()


def ratings_scope_keyboard(entity_type, sport_key):
    builder = InlineKeyboardBuilder()
    builder.button(text="🌍 Общий", callback_data=f"ratings_scope_{entity_type}_{sport_key}_o")
    builder.button(text="🗓 Сезонный", callback_data=f"ratings_scope_{entity_type}_{sport_key}_s")
    builder.button(text="🔙 Назад", callback_data=f"ratings_entity_{entity_type}")
    builder.adjust(1)
    return builder.as_markup()


def ratings_format_keyboard(entity_type, sport_key, scope_key, season_id, options):
    builder = InlineKeyboardBuilder()
    for format_key, label in options:
        if format_key == "general":
            continue
        token = format_key.replace("x", "x")
        builder.button(
            text=label,
            callback_data=f"ratings_view_{entity_type}_{sport_key}_{scope_key}_{season_id}_{token}_0",
        )
    builder.button(text="🔙 Назад", callback_data=f"ratings_view_{entity_type}_{sport_key}_{scope_key}_{season_id}_g_0")
    builder.adjust(1)
    return builder.as_markup()


def admin_menu_keyboard():
    kb = [
        [InlineKeyboardButton(text="🏆 Управление турнирами",
                              callback_data="admin_tournaments_list")],
        [InlineKeyboardButton(
            text="👥 Управление пользователями", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Управление рейтингом",
                              callback_data="admin_rating")],
        [InlineKeyboardButton(text="🔔 Мои турнирные уведомления",
                              callback_data="admin_notifications")],
        [InlineKeyboardButton(text="📢 Рассылка",
                              callback_data="admin_broadcast_start")],
        [InlineKeyboardButton(text="⚙️ Управление командами",
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

# ---------- КЛАВИАТУРЫ ДЛЯ РАЗДЕЛА "КОМАНДЫ" ----------


def teams_main_keyboard():
    kb = [
        [InlineKeyboardButton(text="📋 Список всех команд",
                              callback_data="teams_list_all")],
        [InlineKeyboardButton(
            text="🔓 Вступление в команду", callback_data="teams_list_open")],
        [InlineKeyboardButton(text="🔍 Поиск команды",
                              callback_data="teams_search")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def teams_list_pagination_keyboard(offset, total, callback_prefix):
    """Универсальная пагинация для списков команд"""
    builder = InlineKeyboardBuilder()
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"{callback_prefix}_page_{offset-10}"))
    if offset + 10 < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"{callback_prefix}_page_{offset+10}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(
        text="🔙 В меню команд", callback_data="teams_menu"))
    return builder.as_markup()


def teams_sports_filter_keyboard(sports_list, mode):
    """Клавиатура с видами спорта для фильтрации (mode: 'all' или 'open')"""
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(
            text=display, callback_data=f"teams_filter_{mode}_{name}")
    builder.button(text="🔙 Назад", callback_data="teams_menu")
    builder.adjust(2)
    return builder.as_markup()


def team_view_only_keyboard(team_id, sport):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=f"teams_filter_all_{sport}")
    return builder.as_markup()


def team_view_join_keyboard(team_id, can_apply, sport):
    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="📝 Подать заявку",
                       callback_data=f"apply_team_{team_id}")
    builder.button(text="🔙 Назад", callback_data=f"teams_filter_open_{sport}")
    builder.adjust(1)
    return builder.as_markup()


def team_requests_keyboard(requests, offset, team_id, total):
    builder = InlineKeyboardBuilder()
    for req in requests:
        text = f"{req['first_name']} (@{req['username']})"
        builder.row(
            InlineKeyboardButton(
                text=text, callback_data=f"view_user_{req['user_id']}"),
            InlineKeyboardButton(
                text="✅", callback_data=f"accept_req_{req['id']}"),
            InlineKeyboardButton(
                text="❌", callback_data=f"reject_req_{req['id']}")
        )
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=f"team_reqs_page_{team_id}_{offset-10}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=f"team_reqs_page_{team_id}_{offset+10}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(
        text="🔙 В управление", callback_data=f"team_{team_id}"))
    return builder.as_markup()

# Админские клавиатуры


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
            text=f"⚙️ {team['name']} ({get_sport_display_name(team['sport'])})",
            callback_data=f"admin_team_manage_{team['id']}",
        )
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


def admin_rating_scope_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🌍 Общий рейтинг", callback_data="admin_rating_scope_overall")
    builder.button(text="🗓 Сезонный рейтинг", callback_data="admin_rating_scope_seasonal")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_entity_keyboard(scope_key: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Игроки", callback_data=f"admin_rating_entity_player")
    builder.button(text="👥 Команды", callback_data=f"admin_rating_entity_team")
    builder.button(text="🔙 Назад", callback_data="admin_rating")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_sport_picker_keyboard(sports_list):
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(text=display, callback_data=f"admin_rating_pick_sport_{name}")
    builder.button(text="🔙 Назад", callback_data="admin_rating_back_entity")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_format_picker_keyboard(options, *, allow_next_season: bool = False):
    builder = InlineKeyboardBuilder()
    for format_key, label in options:
        if format_key == "general":
            continue
        builder.button(text=label, callback_data=f"admin_rating_pick_format_{format_key}")
    if allow_next_season:
        builder.button(text="➡️ Следующий сезон", callback_data="admin_rating_next_season")
    builder.button(text="🔙 Назад", callback_data="admin_rating_back_entities_general")
    builder.adjust(1)
    return builder.as_markup()

def admin_rating_season_picker_keyboard(seasons, *, active_season_id: int | None = None, allow_next_season: bool = True):
    builder = InlineKeyboardBuilder()
    for season in seasons:
        season_id = int(season["id"])
        label = str(season["name"])
        if active_season_id and season_id == int(active_season_id):
            label = f"✅ {label}"
        builder.button(text=label[:60], callback_data=f"admin_rating_pick_season_{season_id}")
    if allow_next_season:
        builder.button(text="➡️ Следующий сезон", callback_data="admin_rating_next_season")
    builder.button(text="🔙 Назад", callback_data="admin_rating_back_sport")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_entity_list_keyboard(items, *, entity_type: str, offset: int, has_prev: bool, has_next: bool, show_format_button: bool = False, show_next_season_button: bool = False, show_publish_button: bool = False, show_season_button: bool = False):
    builder = InlineKeyboardBuilder()
    for item in items:
        rating_value = int(item.get("rating_value") or 0)
        if entity_type == "team":
            label = f"{item['name']} — {rating_value}"
        else:
            username = item.get("username") or ""
            base_label = f"{item.get('first_name') or 'Игрок'} (@{username})" if username else (item.get("first_name") or "Игрок")
            label = f"{base_label} — {rating_value}"
        builder.button(text=label[:60], callback_data=f"admin_rating_pick_entity_{item['id']}")

    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_rating_page_{offset - 10}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_rating_page_{offset + 10}"))
    if nav:
        builder.row(*nav)

    if show_format_button:
        builder.button(text="🗂 Формат", callback_data="admin_rating_open_formats")
    if show_season_button:
        builder.button(text="🗓 Сезон", callback_data="admin_rating_choose_season")
    if show_publish_button:
        builder.button(text="📢 Опубликовать в канал", callback_data="admin_rating_publish_channel")
    if show_next_season_button:
        builder.button(text="➡️ Следующий сезон", callback_data="admin_rating_next_season")
    builder.button(text="🔙 Назад", callback_data="admin_rating_back_format")
    builder.adjust(1)
    return builder.as_markup()


def admin_rating_action_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить очки", callback_data="admin_rating_action_add")
    builder.button(text="➖ Снять очки", callback_data="admin_rating_action_sub")
    builder.button(text="🔙 Назад", callback_data="admin_rating_back_entities")
    builder.adjust(1)
    return builder.as_markup()


def team_view_search_keyboard(team_id, can_apply, query):
    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="📝 Подать заявку",
                       callback_data=f"apply_team_{team_id}")
    builder.button(text="🔙 К результатам поиска",
                   callback_data=f"search_page_{query}_0")
    builder.adjust(1)
    return builder.as_markup()


def sports_choice_keyboard_no_done(sports_list):
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(text=display, callback_data=f"create_team_sport_{name}")
    builder.adjust(2)
    return builder.as_markup()


def edit_profile_menu_keyboard():
    kb = [
        [InlineKeyboardButton(text="✏️ Имя", callback_data="edit_name")],
        [InlineKeyboardButton(text="✏️ Email", callback_data="edit_email")],
        [InlineKeyboardButton(text="✏️ Город", callback_data="edit_city")],
        [InlineKeyboardButton(text="✏️ Возраст", callback_data="edit_age")],
        [InlineKeyboardButton(text="✏️ Любимые виды спорта",
                              callback_data="edit_sports")],
        [InlineKeyboardButton(text="✏️ Профиль steam", callback_data="edit_steam_id")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="profile")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def cancel_keyboard():
    kb = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def input_number_keyboard():
    """Клавиатура с кнопками цифр от 1 до 10 для быстрого ввода"""
    builder = InlineKeyboardBuilder()
    for i in range(1, 11):
        builder.button(text=str(i), callback_data=f"set_max_members_{i}")
    builder.adjust(5)
    return builder.as_markup()


def back_to_teams_menu_keyboard():
    kb = [[InlineKeyboardButton(
        text="🔙 В меню команд", callback_data="teams_menu")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def sports_choice_keyboard_single(sports_list):
    builder = InlineKeyboardBuilder()
    for name, display in sports_list:
        builder.button(text=display, callback_data=f"admin_tourn_sport_{name}")
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    builder.adjust(2)
    return builder.as_markup()



def subscription_required_keyboard(channel_username: str):
    channel = (channel_username or "razryadarena").strip().lstrip("@")
    kb = [
        [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{channel}")],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
