"""
Обработчики статистики игроков по видам спорта.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from razryad_arena_utils import (
    get_user,
    get_player_career_stats_by_sport,
    get_player_match_history_page_by_sport,
    get_player_match_history_count_by_sport,
    get_match_history_details_by_sport,
    get_sport_display_name,
)

router = Router()

PAGE_SIZE = 10
SPORTS_ORDER = ["CS2", "Football", "Volleyball", "Basketball"]
SPORT_ICONS = {
    "CS2": "🔫",
    "Football": "⚽",
    "Volleyball": "🏐",
    "Basketball": "🏀",
}
SPORT_SHORT = {
    "CS2": "c",
    "Football": "f",
    "Volleyball": "v",
    "Basketball": "b",
}
SHORT_SPORT = {v: k for k, v in SPORT_SHORT.items()}
SOURCE_TO_SHORT = {"bracket": "b", "legacy": "l"}
SHORT_TO_SOURCE = {"b": "bracket", "l": "legacy"}


def _decode_source_token(token: str) -> str:
    if token in SHORT_TO_SOURCE:
        return SHORT_TO_SOURCE[token]
    if token in ("bracket", "legacy"):
        return token
    return "bracket"


def _safe_series_score(value):
    return value if value is not None else 0


def _sport_button_text(sport: str) -> str:
    return f"{SPORT_ICONS.get(sport, '🏅')} {get_sport_display_name(sport)}"


def _history_open_callback(sport: str) -> str:
    return f"ps_hist_{SPORT_SHORT[sport]}"


def _history_page_callback(sport: str, offset: int) -> str:
    return f"ps_histp_{SPORT_SHORT[sport]}_{offset}"


def _history_match_callback(sport: str, source: str, match_id: int, return_offset: int) -> str:
    return f"ps_match_{SPORT_SHORT[sport]}_{SOURCE_TO_SHORT.get(source, 'b')}_{match_id}_{return_offset}"


def _history_map_callback(sport: str, source: str, match_id: int, return_offset: int, map_index: int) -> str:
    return f"ps_map_{SPORT_SHORT[sport]}_{SOURCE_TO_SHORT.get(source, 'b')}_{match_id}_{return_offset}_{map_index}"


def _safe_float(value) -> float:
    return float(value) if value is not None else 0.0


@router.callback_query(F.data == "player_stats")
async def player_stats_menu(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for sport in SPORTS_ORDER:
        builder.button(
            text=_sport_button_text(sport),
            callback_data=f"player_stats_sport_{sport}",
        )
    builder.button(text="🔙 Назад", callback_data="profile")
    builder.adjust(1)

    await callback.message.edit_text(
        "📊 Выберите вид спорта для просмотра статистики:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


async def _render_sport_overview(callback: CallbackQuery, sport: str):
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        return

    stats = get_player_career_stats_by_sport(callback.from_user.id, sport)
    matches_played = (stats["matches_played"] if stats and stats["matches_played"] is not None else 0)
    if matches_played == 0:
        text = (
            f"📊 Статистика игрока ({get_sport_display_name(sport)})\n\n"
            "❌ Вы ещё не сыграли ни одного матча."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="player_stats")
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer()
        return

    title = f"📊 Статистика игрока ({get_sport_display_name(sport)})\n👤 {user['first_name']}\n\n"
    if sport == "CS2":
        avg_kills = round(_safe_float(stats["avg_kills"]), 2)
        avg_deaths = round(_safe_float(stats["avg_deaths"]), 2)
        avg_adr = round(_safe_float(stats["avg_adr"]), 2)
        avg_hs = round(_safe_float(stats["avg_hs"]), 2)
        avg_kd = avg_kills / avg_deaths if avg_deaths > 0 else avg_kills
        body = (
            f"⚔️ Avg K/D: {avg_kd:.2f}\n"
            f"🔫 Avg kills: {avg_kills:.2f}\n"
            f"💀 Avg deaths: {avg_deaths:.2f}\n"
            f"🎯 Avg adr: {avg_adr:.2f}\n"
            f"🎪 Avg hs: {avg_hs:.2f} %\n"
            f"📈 Сыграно матчей: {matches_played}"
        )
    elif sport == "Football":
        avg_goals = _safe_float(stats["avg_goals"])
        avg_assists = _safe_float(stats["avg_assists"])
        body = (
            f"⚽ Avg goals: {avg_goals:.2f}\n"
            f"🎯 Avg assists: {avg_assists:.2f}\n"
            f"🥅 Всего голов: {stats['total_goals'] or 0}\n"
            f"🧠 Всего ассистов: {stats['total_assists'] or 0}\n"
            f"📈 Сыграно матчей: {matches_played}"
        )
    elif sport == "Basketball":
        avg_points = _safe_float(stats["avg_points"])
        avg_fouls = _safe_float(stats["avg_fouls"])
        body = (
            f"🏀 Avg points: {avg_points:.2f}\n"
            f"🚫 Avg fouls: {avg_fouls:.2f}\n"
            f"🔥 Всего очков: {stats['total_points'] or 0}\n"
            f"⚠️ Всего фолов: {stats['total_fouls'] or 0}\n"
            f"📈 Сыграно матчей: {matches_played}"
        )
    else:
        avg_points = _safe_float(stats["avg_points"])
        avg_aces = _safe_float(stats["avg_aces"])
        body = (
            f"🏐 Avg points: {avg_points:.2f}\n"
            f"⚡ Avg aces: {avg_aces:.2f}\n"
            f"🔥 Всего очков: {stats['total_points'] or 0}\n"
            f"🌩 Всего эйсов: {stats['total_aces'] or 0}\n"
            f"📈 Сыграно матчей: {matches_played}"
        )

    builder = InlineKeyboardBuilder()
    builder.button(text="📜 История матчей", callback_data=_history_open_callback(sport))
    builder.button(text="🔙 Назад", callback_data="player_stats")
    builder.adjust(1)

    await callback.message.edit_text(title + body, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("player_stats_sport_"))
async def player_stats_sport(callback: CallbackQuery):
    sport = callback.data.replace("player_stats_sport_", "")
    if sport not in SPORTS_ORDER:
        await callback.answer("Неизвестный вид спорта", show_alert=True)
        return
    await _render_sport_overview(callback, sport)


# Обратная совместимость старой кнопки CS2
@router.callback_query(F.data == "player_stats_cs2")
async def player_stats_cs2_compat(callback: CallbackQuery):
    await _render_sport_overview(callback, "CS2")


async def show_player_history_page(callback: CallbackQuery, sport: str, offset: int):
    user_id = callback.from_user.id
    matches = get_player_match_history_page_by_sport(user_id, sport, offset, PAGE_SIZE)
    total = get_player_match_history_count_by_sport(user_id, sport)

    if not matches:
        text = "У вас пока нет сыгранных матчей."
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data=f"player_stats_sport_{sport}")
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        return

    text = (
        f"📜 История матчей ({get_sport_display_name(sport)}) "
        f"({offset + 1}-{min(offset + PAGE_SIZE, total)} из {total})\n\n"
        "Выберите матч:"
    )
    builder = InlineKeyboardBuilder()
    icon = SPORT_ICONS.get(sport, "🏅")

    for match in matches:
        source = match["match_source"] or "bracket"
        tournament = match["tournament_name"] or "Турнир"
        team1 = match["team1_name"] or "Команда 1"
        team2 = match["team2_name"] or "Команда 2"
        score1 = _safe_series_score(match["score1"])
        score2 = _safe_series_score(match["score2"])

        builder.button(
            text=f"{icon} {tournament} | {team1} {score1}:{score2} {team2}",
            callback_data=_history_match_callback(sport, source, match["match_id"], offset),
        )

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=_history_page_callback(sport, offset - PAGE_SIZE),
            )
        )
    if offset + PAGE_SIZE < total:
        nav_buttons.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=_history_page_callback(sport, offset + PAGE_SIZE),
            )
        )
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="🔙 Назад", callback_data=f"player_stats_sport_{sport}")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())


async def show_match_history_card(callback: CallbackQuery, sport: str, source: str, match_id: int, return_offset: int, map_index: int = 0):
    details = get_match_history_details_by_sport(sport, source, match_id)
    if not details:
        await callback.answer("Матч не найден", show_alert=True)
        return

    team1_name = details.get("team1_name") or "Команда 1"
    team2_name = details.get("team2_name") or "Команда 2"
    score1 = _safe_series_score(details.get("score1"))
    score2 = _safe_series_score(details.get("score2"))
    tournament_name = details.get("tournament_name") or "Турнир"

    if sport == "CS2":
        maps = details.get("maps", [])
        if not maps:
            maps = [{
                "map_number": 1,
                "map_name": "Карта 1",
                "team1_score": None,
                "team2_score": None,
                "has_score": False,
                "players_team1": [],
                "players_team2": [],
                "players_other": [],
            }]
        map_index = max(0, min(map_index, len(maps) - 1))
        current_map = maps[map_index]

        text = (
            f"🏆 {tournament_name}\n"
            f"📊 Счет серии: {team1_name} {score1}:{score2} {team2_name}\n\n"
            f"🗺 Карта {map_index + 1}/{len(maps)}: {current_map['map_name']}\n"
        )
        if current_map.get("has_score"):
            text += (
                f"Счет карты: {team1_name} {current_map['team1_score']}:"
                f"{current_map['team2_score']} {team2_name}\n\n"
            )
        else:
            text += "Счет карты: нет данных\n\n"

        text += f"🔵 {team1_name}:\n"
        if current_map["players_team1"]:
            for p in current_map["players_team1"]:
                username = f" @{p['username']}" if p.get("username") else ""
                text += (
                    f"• {p['first_name']}{username}: "
                    f"K:{p['kills']} D:{p['deaths']} A:{p['assists']} | "
                    f"ADR:{p['adr']} HS:{p['hs']}\n"
                )
        else:
            text += "• нет данных\n"

        text += f"\n🔴 {team2_name}:\n"
        if current_map["players_team2"]:
            for p in current_map["players_team2"]:
                username = f" @{p['username']}" if p.get("username") else ""
                text += (
                    f"• {p['first_name']}{username}: "
                    f"K:{p['kills']} D:{p['deaths']} A:{p['assists']} | "
                    f"ADR:{p['adr']} HS:{p['hs']}\n"
                )
        else:
            text += "• нет данных\n"

        builder = InlineKeyboardBuilder()
        nav = []
        if map_index > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀️ Карта",
                    callback_data=_history_map_callback(sport, source, match_id, return_offset, map_index - 1),
                )
            )
        if map_index + 1 < len(maps):
            nav.append(
                InlineKeyboardButton(
                    text="Карта ▶️",
                    callback_data=_history_map_callback(sport, source, match_id, return_offset, map_index + 1),
                )
            )
        if nav:
            builder.row(*nav)
        builder.button(text="🔙 К списку", callback_data=_history_page_callback(sport, return_offset))
        builder.adjust(1)
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        return

    # Не-CS2 карточки
    text = (
        f"🏆 {tournament_name}\n"
        f"📊 Счет матча: {team1_name} {score1}:{score2} {team2_name}\n\n"
    )

    if sport == "Football":
        p1 = details.get("players_team1", [])
        p2 = details.get("players_team2", [])
        scorers_1 = [f"{p['first_name']} x{p['goals']}" for p in p1 if (p.get("goals") or 0) > 0]
        scorers_2 = [f"{p['first_name']} x{p['goals']}" for p in p2 if (p.get("goals") or 0) > 0]
        assists_1 = [f"{p['first_name']} x{p['assists']}" for p in p1 if (p.get("assists") or 0) > 0]
        assists_2 = [f"{p['first_name']} x{p['assists']}" for p in p2 if (p.get("assists") or 0) > 0]

        text += f"⚽ Голы {team1_name}: {', '.join(scorers_1) if scorers_1 else 'нет данных'}\n"
        text += f"⚽ Голы {team2_name}: {', '.join(scorers_2) if scorers_2 else 'нет данных'}\n"
        text += f"🎯 Ассисты {team1_name}: {', '.join(assists_1) if assists_1 else 'нет данных'}\n"
        text += f"🎯 Ассисты {team2_name}: {', '.join(assists_2) if assists_2 else 'нет данных'}\n"
    elif sport == "Basketball":
        text += "🏀 Очки и фолы по игрокам:\n"
    else:
        sets = details.get("set_scores", [])
        if sets:
            text += "🏐 Счет по партиям:\n"
            for s in sets:
                text += f"Партия {s['set_number']}: {s['team1_points']}:{s['team2_points']}\n"
        else:
            text += "🏐 Счет по партиям: нет данных\n"
        text += "\n⚡ Очки и эйсы по игрокам:\n"

    p1 = details.get("players_team1", [])
    p2 = details.get("players_team2", [])
    text += f"\n🔵 {team1_name}:\n"
    if p1:
        for p in p1:
            username = f" @{p['username']}" if p.get("username") else ""
            if sport == "Football":
                text += f"• {p['first_name']}{username}: ⚽ {p.get('goals', 0)} | 🎯 {p.get('assists', 0)}\n"
            elif sport == "Basketball":
                text += f"• {p['first_name']}{username}: 🏀 {p.get('points', 0)} | 🚫 {p.get('fouls', 0)}\n"
            else:
                text += f"• {p['first_name']}{username}: 🏐 {p.get('points', 0)} | ⚡ {p.get('aces', 0)}\n"
    else:
        text += "• нет данных\n"

    text += f"\n🔴 {team2_name}:\n"
    if p2:
        for p in p2:
            username = f" @{p['username']}" if p.get("username") else ""
            if sport == "Football":
                text += f"• {p['first_name']}{username}: ⚽ {p.get('goals', 0)} | 🎯 {p.get('assists', 0)}\n"
            elif sport == "Basketball":
                text += f"• {p['first_name']}{username}: 🏀 {p.get('points', 0)} | 🚫 {p.get('fouls', 0)}\n"
            else:
                text += f"• {p['first_name']}{username}: 🏐 {p.get('points', 0)} | ⚡ {p.get('aces', 0)}\n"
    else:
        text += "• нет данных\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К списку", callback_data=_history_page_callback(sport, return_offset))
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "player_stats_matches")
async def player_stats_matches_compat(callback: CallbackQuery):
    await show_player_history_page(callback, "CS2", 0)


@router.callback_query(F.data.startswith("ps_hist_"))
async def history_open(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        sport = SHORT_SPORT[parts[2]]
    except Exception:
        await callback.answer("Ошибка истории", show_alert=True)
        return
    await show_player_history_page(callback, sport, 0)


@router.callback_query(F.data.startswith("ps_histp_"))
async def history_page(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        sport = SHORT_SPORT[parts[2]]
        offset = int(parts[3])
    except Exception:
        await callback.answer("Ошибка пагинации", show_alert=True)
        return
    await show_player_history_page(callback, sport, offset)


# Обратная совместимость старого callback player_stats_matches_page_<offset>
@router.callback_query(F.data.startswith("player_stats_matches_page_"))
async def history_page_compat(callback: CallbackQuery):
    try:
        offset = int(callback.data.split("_")[4])
    except Exception:
        await callback.answer("Ошибка пагинации", show_alert=True)
        return
    await show_player_history_page(callback, "CS2", offset)


@router.callback_query(F.data.startswith("ps_match_"))
async def history_match(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        sport = SHORT_SPORT[parts[2]]
        source = SHORT_TO_SOURCE[parts[3]]
        match_id = int(parts[4])
        return_offset = int(parts[5])
    except Exception:
        await callback.answer("Ошибка открытия матча", show_alert=True)
        return
    await show_match_history_card(callback, sport, source, match_id, return_offset, map_index=0)


# Обратная совместимость старого callback player_stats_match_...
@router.callback_query(F.data.startswith("player_stats_match_"))
async def history_match_compat(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        source = _decode_source_token(parts[3])
        match_id = int(parts[4])
        return_offset = int(parts[5])
    except Exception:
        await callback.answer("Ошибка открытия матча", show_alert=True)
        return
    await show_match_history_card(callback, "CS2", source, match_id, return_offset, map_index=0)


@router.callback_query(F.data.startswith("ps_map_"))
async def history_map(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        sport = SHORT_SPORT[parts[2]]
        source = SHORT_TO_SOURCE[parts[3]]
        match_id = int(parts[4])
        return_offset = int(parts[5])
        map_index = int(parts[6])
    except Exception:
        await callback.answer("Ошибка переключения карты", show_alert=True)
        return
    await show_match_history_card(callback, sport, source, match_id, return_offset, map_index=map_index)


# Обратная совместимость старого callback player_stats_map_...
@router.callback_query(F.data.startswith("player_stats_map_"))
async def history_map_compat(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        source = _decode_source_token(parts[3])
        match_id = int(parts[4])
        return_offset = int(parts[5])
        map_index = int(parts[6])
    except Exception:
        await callback.answer("Ошибка переключения карты", show_alert=True)
        return
    await show_match_history_card(callback, "CS2", source, match_id, return_offset, map_index=map_index)

