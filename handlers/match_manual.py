"""
Ручной ввод результатов матчей турнирной сетки.
Поддерживает BO1/BO3/BO5 по картам.
"""
from datetime import datetime, timezone
import os

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_connection
from handlers.states import ManualMatchInput
from razryad_arena_utils import (
    get_steam_profile_name,
    get_tournament_by_id,
    is_admin,
    update_team_rating,
    upsert_football_player_stat,
    upsert_basketball_player_stat,
    upsert_volleyball_player_stat,
    replace_volleyball_set_scores,
    get_sport_display_name,
    normalize_sport_name,
    auto_create_third_place_if_ready,
)
from utils.bracket_utils import advance_winner
from utils.bracket_visualizer import generate_bracket_png
from utils.notifications import notify_bracket_match_result

router = Router()

CS2_MAPS = [
    ("de_mirage", "Mirage"),
    ("de_inferno", "Inferno"),
    ("de_nuke", "Nuke"),
    ("de_overpass", "Overpass"),
    ("de_dust2", "Dust2"),
    ("de_ancient", "Ancient"),
    ("de_anubis", "Anubis"),
]
CUSTOM_CS2_MAP_TOKEN = "custom"


def _is_cancel(text: str) -> bool:
    return text.lower() in {"отмена", "cancel", "/cancel"}


def _format_player(player: dict) -> str:
    username = player.get("username") or "без_username"
    return f"{player.get('first_name', 'Игрок')} (@{username})"


def _series_required_wins(total_maps: int) -> int:
    return total_maps // 2 + 1


def _winner_from_score(team1_score: int, team2_score: int, team1_id: int, team2_id: int) -> int:
    return team1_id if team1_score > team2_score else team2_id


def _score_preview(team1_wins: int, team2_wins: int, map_winner_id: int, team1_id: int) -> tuple[int, int]:
    if map_winner_id == team1_id:
        return team1_wins + 1, team2_wins
    return team1_wins, team2_wins + 1


def _map_keyboard(tournament_id: int):
    builder = InlineKeyboardBuilder()
    for map_id, map_name in CS2_MAPS:
        builder.button(text=map_name, callback_data=f"manual_map_{map_id}")
    builder.button(text="✍️ Своя карта", callback_data=f"manual_map_{CUSTOM_CS2_MAP_TOKEN}")
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(3)
    return builder.as_markup()


def _cancel_keyboard(tournament_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _summary_keyboard(tournament_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К сетке", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _save_keyboard(tournament_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить карту", callback_data="manual_save_stats")
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _fetch_bracket_match(match_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT b.*, t1.name as team1_name, t2.name as team2_name
        FROM tournament_brackets b
        LEFT JOIN teams t1 ON b.team1_id = t1.id
        LEFT JOIN teams t2 ON b.team2_id = t2.id
        WHERE b.id=?
        """,
        (match_id,),
    )
    match = cur.fetchone()
    conn.close()
    return match


def _fetch_players(team1_id: int, team2_id: int) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.id, u.first_name, u.username, u.steam_id, tm.team_id
        FROM users u
        JOIN team_members tm ON u.id = tm.user_id
        WHERE tm.team_id IN (?, ?)
        ORDER BY tm.team_id, u.first_name
        """,
        (team1_id, team2_id),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _get_steam_label(steam_id: str | None) -> str:
    if not steam_id:
        return "Steam: ❌"
    profile_name = get_steam_profile_name(steam_id)
    return f"Steam: {profile_name}" if profile_name else "Steam: [профиль]"


async def _prompt_map_score_input(target: Message | CallbackQuery, state: FSMContext, map_name: str):
    data = await state.get_data()
    await state.update_data(current_map=map_name)

    text = (
        f"🗺 Карта {data.get('current_map_number', 1)}/{data.get('total_maps', 1)}: {map_name}\n\n"
        f"🔵 {data.get('team1_name')} vs 🔴 {data.get('team2_name')}\n\n"
        "Введите счет в формате team1:team2\n"
        "Пример: 16:14"
    )
    await state.set_state(ManualMatchInput.score_input)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))
    else:
        await target.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))


async def _show_map_selection(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    map_no = data.get("current_map_number", 1)
    total_maps = data.get("total_maps", 1)
    team1_name = data.get("team1_name", "Команда 1")
    team2_name = data.get("team2_name", "Команда 2")
    team1_wins = data.get("team1_wins", 0)
    team2_wins = data.get("team2_wins", 0)
    tournament_id = data.get("tournament_id")

    text = (
        f"📊 Формат: {data.get('match_format', 'BO1')}\n"
        f"🗺 Карта {map_no}/{total_maps}\n\n"
        f"🔵 {team1_name}: {team1_wins}\n"
        f"🔴 {team2_name}: {team2_wins}\n\n"
        "Выберите карту:"
    )

    await state.set_state(ManualMatchInput.map_select)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=_map_keyboard(tournament_id))
    else:
        await target.answer(text, reply_markup=_map_keyboard(tournament_id))


async def _show_player_input(message: Message, state: FSMContext, player_index: int):
    data = await state.get_data()
    players = data.get("all_players", [])
    if player_index >= len(players):
        await _show_confirm(message, state)
        return

    player = players[player_index]
    team1_id = data.get("team1_id")
    team_name = data.get("team1_name") if player["team_id"] == team1_id else data.get("team2_name")
    tournament_id = data.get("tournament_id")
    sport_mode = normalize_sport_name(data.get("sport_mode", "CS2"))

    if sport_mode == "Football":
        stat_prompt = "Введите: goals:assists\nПример: 2:1"
    elif sport_mode == "Basketball":
        stat_prompt = "Введите: points:fouls\nПример: 22:3"
    elif sport_mode == "Volleyball":
        stat_prompt = "Введите: points:aces\nПример: 18:4"
    else:
        stat_prompt = "Введите: kills:deaths:assists:adr:hs\nПример: 25:18:5:85:12"

    text = (
        f"✏️ Игрок {player_index + 1}/{len(players)}\n\n"
        f"Команда: {team_name}\n"
        f"Игрок: {_format_player(player)}\n"
        f"{_get_steam_label(player.get('steam_id'))}\n\n"
        f"{stat_prompt}"
    )

    await state.update_data(current_player=player, current_player_index=player_index)
    await message.answer(text, reply_markup=_cancel_keyboard(tournament_id))


async def _show_confirm(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stats = data.get("player_stats_list", [])
    sport_mode = normalize_sport_name(data.get("sport_mode", "CS2"))
    team1_id = data.get("team1_id")
    team1_name = data.get("team1_name", "")
    team2_name = data.get("team2_name", "")
    team1_score = data.get("team1_score", 0)
    team2_score = data.get("team2_score", 0)
    team1_wins = data.get("team1_wins", 0)
    team2_wins = data.get("team2_wins", 0)
    map_name = data.get("current_map")
    map_no = data.get("current_map_number", 1)
    total_maps = data.get("total_maps", 1)
    tournament_id = data.get("tournament_id")

    team1_stats = [s for s in stats if s.get("team_id") == team1_id]
    team2_stats = [s for s in stats if s.get("team_id") != team1_id]

    if sport_mode == "CS2":
        map_winner_id = _winner_from_score(team1_score, team2_score, team1_id, data.get("team2_id"))
        projected1, projected2 = _score_preview(team1_wins, team2_wins, map_winner_id, team1_id)
        map_winner_name = team1_name if map_winner_id == team1_id else team2_name

        text = (
            f"🗺 Карта {map_no}/{total_maps}: {map_name}\n"
            f"📊 Счет карты: {team1_name} {team1_score} : {team2_score} {team2_name}\n"
            f"🏆 Победитель карты: {map_winner_name}\n"
            f"📈 Счет серии после карты: {team1_name} {projected1} : {projected2} {team2_name}\n\n"
            f"🔵 {team1_name}:"
        )

        for p in team1_stats:
            kd = p["kills"] / p["deaths"] if p["deaths"] > 0 else p["kills"]
            text += f"\n  {p['user_name']}: K:{p['kills']} D:{p['deaths']} A:{p['assists']} | K/D: {kd:.2f}"

        text += f"\n\n🔴 {team2_name}:"
        for p in team2_stats:
            kd = p["kills"] / p["deaths"] if p["deaths"] > 0 else p["kills"]
            text += f"\n  {p['user_name']}: K:{p['kills']} D:{p['deaths']} A:{p['assists']} | K/D: {kd:.2f}"
    elif sport_mode == "Football":
        text = (
            f"⚽ Подтверждение результата\n"
            f"📊 Счет матча: {team1_name} {team1_score}:{team2_score} {team2_name}\n\n"
            f"🔵 {team1_name}:"
        )
        for p in team1_stats:
            text += f"\n  {p['user_name']}: ⚽ {p['goals']} | 🎯 {p['assists']}"
        text += f"\n\n🔴 {team2_name}:"
        for p in team2_stats:
            text += f"\n  {p['user_name']}: ⚽ {p['goals']} | 🎯 {p['assists']}"
    elif sport_mode == "Basketball":
        text = (
            f"🏀 Подтверждение результата\n"
            f"📊 Счет матча: {team1_name} {team1_score}:{team2_score} {team2_name}\n\n"
            f"🔵 {team1_name}:"
        )
        for p in team1_stats:
            text += f"\n  {p['user_name']}: 🏀 {p['points']} | 🚫 {p['fouls']}"
        text += f"\n\n🔴 {team2_name}:"
        for p in team2_stats:
            text += f"\n  {p['user_name']}: 🏀 {p['points']} | 🚫 {p['fouls']}"
    else:
        sets = data.get("volleyball_sets", [])
        sets_text = ", ".join([f"{idx + 1}) {s[0]}:{s[1]}" for idx, s in enumerate(sets)]) if sets else "нет данных"
        text = (
            f"🏐 Подтверждение результата\n"
            f"📊 Счет по партиям: {team1_name} {team1_score}:{team2_score} {team2_name}\n"
            f"🧮 Партии: {sets_text}\n\n"
            f"🔵 {team1_name}:"
        )
        for p in team1_stats:
            text += f"\n  {p['user_name']}: 🏐 {p['points']} | ⚡ {p['aces']}"
        text += f"\n\n🔴 {team2_name}:"
        for p in team2_stats:
            text += f"\n  {p['user_name']}: 🏐 {p['points']} | ⚡ {p['aces']}"

    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=_save_keyboard(tournament_id))
    else:
        await target.answer(text, reply_markup=_save_keyboard(tournament_id))


async def start_manual_input_by_match(callback: CallbackQuery, state: FSMContext, match_id: int, tournament_id: int | None = None):
    """Запускает ручной ввод результата для конкретного матча сетки."""
    match = _fetch_bracket_match(match_id)

    if not match:
        await callback.answer("Матч не найден", show_alert=True)
        return
    match = dict(match)
    if not match["team1_id"] or not match["team2_id"]:
        await callback.answer("В этом матче еще нет команд.", show_alert=True)
        return
    if match["status"] == "completed":
        await callback.answer("Этот матч уже завершен.", show_alert=True)
        return
    if not match.get("scheduled_at_utc") or not match.get("location"):
        await callback.answer("Сначала укажите время и место матча.", show_alert=True)
        from handlers.brackets import start_schedule_wizard_for_tournament
        await start_schedule_wizard_for_tournament(
            callback,
            state,
            tournament_id=match["tournament_id"],
            match_ids=[match_id],
            return_callback=f"view_bracket_{match['tournament_id']}",
        )
        return

    if not tournament_id:
        tournament_id = match["tournament_id"]

    tournament = get_tournament_by_id(tournament_id)
    sport_mode = normalize_sport_name(tournament["sport"]) if tournament else "CS2"

    await state.update_data(
        bracket_match_id=match_id,
        tournament_id=tournament_id,
        team1_id=match["team1_id"],
        team2_id=match["team2_id"],
        team1_name=match["team1_name"],
        team2_name=match["team2_name"],
        sport_mode=sport_mode,
        team1_wins=0,
        team2_wins=0,
        current_map_number=1,
        map_results=[],
    )

    if sport_mode != "CS2":
        await state.set_state(ManualMatchInput.score_input)
        await callback.message.answer(
            "✏️ Ввод результата\n\n"
            f"Вид спорта: {get_sport_display_name(sport_mode)}\n"
            f"🔵 {match['team1_name']} vs 🔴 {match['team2_name']}\n"
            f"📌 Раунд: {match['round_name']}\n\n"
            "Введите итоговый счет в формате team1:team2\n"
            "Пример: 2:1",
            reply_markup=_cancel_keyboard(tournament_id),
        )
        await callback.answer()
        return

    text = (
        "✏️ Ввод результата\n\n"
        f"🔵 {match['team1_name']} vs 🔴 {match['team2_name']}\n"
        f"📌 Раунд: {match['round_name']}\n\n"
        "Выберите формат:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="BO1", callback_data="manual_format_bo1")
    builder.button(text="BO3", callback_data="manual_format_bo3")
    builder.button(text="BO5", callback_data="manual_format_bo5")
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)

    await state.set_state(ManualMatchInput.format)
    await callback.message.answer(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("manual_match_result_"))
async def start_manual_input(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    match_id = int(parts[3])
    tournament_id = int(parts[4]) if len(parts) > 4 else None
    await start_manual_input_by_match(callback, state, match_id, tournament_id)


@router.callback_query(ManualMatchInput.format, F.data.startswith("manual_format_"))
async def select_format(callback: CallbackQuery, state: FSMContext):
    format_choice = callback.data.split("_")[2]
    total_maps = int(format_choice[2])
    await state.update_data(
        match_format=format_choice.upper(),
        total_maps=total_maps,
        maps_to_win=_series_required_wins(total_maps),
        team1_wins=0,
        team2_wins=0,
        current_map_number=1,
        map_results=[],
    )
    await _show_map_selection(callback, state)
    await callback.answer()


@router.callback_query(ManualMatchInput.map_select, F.data.startswith("manual_map_"))
async def select_map(callback: CallbackQuery, state: FSMContext):
    map_name = callback.data.split("_", 2)[2]
    if map_name == CUSTOM_CS2_MAP_TOKEN:
        await state.set_state(ManualMatchInput.custom_map_input)
        await callback.message.answer(
            "✍️ Введите название карты вручную.\n\n"
            "Например: Train, Cache или любая другая карта.",
            reply_markup=_cancel_keyboard((await state.get_data()).get("tournament_id"))
        )
        await callback.answer()
        return

    await _prompt_map_score_input(callback, state, map_name)
    await callback.answer()


@router.message(ManualMatchInput.custom_map_input)
async def input_custom_map_name(message: Message, state: FSMContext):
    map_name = (message.text or "").strip()
    if _is_cancel(map_name):
        await _show_map_selection(message, state)
        return

    if not map_name:
        await message.answer("❌ Название карты не может быть пустым. Введите название карты.")
        return

    if len(map_name) > 50:
        await message.answer("❌ Название карты слишком длинное. Используйте до 50 символов.")
        return

    await _prompt_map_score_input(message, state, map_name)


@router.message(ManualMatchInput.score_input)
async def input_score(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    sport_mode = normalize_sport_name(data.get("sport_mode", "CS2"))
    if _is_cancel(text):
        await state.clear()
        await message.answer("❌ Отменено")
        return

    if ":" not in text:
        await message.answer("❌ Неверный формат. Введите: 16:14")
        return

    try:
        left, right = text.split(":", 1)
        team1_score = int(left)
        team2_score = int(right)
    except ValueError:
        await message.answer("❌ Неверный формат. Введите: 16:14")
        return

    if team1_score < 0 or team2_score < 0:
        await message.answer("❌ Счет не может быть отрицательным.")
        return
    if team1_score == team2_score:
        await message.answer("❌ Ничья по карте недопустима. Введите победный счет.")
        return

    if sport_mode != "CS2":
        players = _fetch_players(data.get("team1_id"), data.get("team2_id"))
        if not players:
            await message.answer("❌ Нет игроков в командах.")
            await state.clear()
            return

        await state.update_data(
            team1_score=team1_score,
            team2_score=team2_score,
            all_players=players,
            player_stats_list=[],
            current_player_index=0,
            current_player=players[0],
        )

        if sport_mode == "Volleyball":
            await state.set_state(ManualMatchInput.volleyball_sets)
            await message.answer(
                "Введите счет по партиям в формате:\n"
                "25:20,23:25,15:13\n\n"
                "Количество выигранных партий должно совпадать с итоговым счетом.",
                reply_markup=_cancel_keyboard(data.get("tournament_id")),
            )
            return

        await state.set_state(ManualMatchInput.player_stats)
        await _show_player_input(message, state, 0)
        return

    players = _fetch_players(data.get("team1_id"), data.get("team2_id"))
    if not players:
        await message.answer("❌ Нет игроков в командах.")
        await state.clear()
        return

    await state.update_data(
        team1_score=team1_score,
        team2_score=team2_score,
        all_players=players,
        player_stats_list=[],
        current_player_index=0,
        current_player=players[0],
    )
    await state.set_state(ManualMatchInput.player_stats)
    await _show_player_input(message, state, 0)


@router.message(ManualMatchInput.volleyball_sets)
async def input_volleyball_sets(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    if _is_cancel(text):
        await state.clear()
        await message.answer("❌ Отменено")
        return

    try:
        raw_sets = [chunk.strip() for chunk in text.split(",") if chunk.strip()]
        if not raw_sets:
            raise ValueError

        parsed_sets = []
        team1_wins = 0
        team2_wins = 0
        for chunk in raw_sets:
            if ":" not in chunk:
                raise ValueError
            left, right = chunk.split(":", 1)
            p1 = int(left)
            p2 = int(right)
            if p1 < 0 or p2 < 0 or p1 == p2:
                raise ValueError
            if p1 > p2:
                team1_wins += 1
            else:
                team2_wins += 1
            parsed_sets.append((p1, p2))
    except ValueError:
        await message.answer(
            "❌ Неверный формат партий.\nВведите, например: 25:20,23:25,15:13",
            reply_markup=_cancel_keyboard(data.get("tournament_id")),
        )
        return

    if team1_wins != data.get("team1_score") or team2_wins != data.get("team2_score"):
        await message.answer(
            "❌ Счет по партиям не совпадает с итоговым счетом матча.\n"
            "Исправьте ввод партий.",
            reply_markup=_cancel_keyboard(data.get("tournament_id")),
        )
        return

    await state.update_data(volleyball_sets=parsed_sets)
    await state.set_state(ManualMatchInput.player_stats)
    await _show_player_input(message, state, 0)


@router.message(ManualMatchInput.player_stats)
async def input_player_stats(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    sport_mode = normalize_sport_name(data.get("sport_mode", "CS2"))
    if _is_cancel(text):
        await state.clear()
        await message.answer("❌ Отменено")
        return

    player = data.get("current_player")
    players = data.get("all_players", [])
    stats = data.get("player_stats_list", [])
    idx = data.get("current_player_index", 0)

    if not player:
        await message.answer("❌ Ошибка состояния. Начните ввод заново.")
        return

    if sport_mode == "CS2":
        try:
            parts = text.split(":")
            kills = int(parts[0]) if len(parts) > 0 else 0
            deaths = int(parts[1]) if len(parts) > 1 else 0
            assists = int(parts[2]) if len(parts) > 2 else 0
            adr = int(parts[3]) if len(parts) > 3 else 0
            hs = int(parts[4]) if len(parts) > 4 else 0
        except ValueError:
            await message.answer("❌ Неверный формат: kills:deaths:assists:adr:hs")
            return

        if min(kills, deaths, assists, adr, hs) < 0:
            await message.answer("❌ Статистика не может быть отрицательной.")
            return

        stats.append(
            {
                "user_id": player["id"],
                "user_name": player["first_name"],
                "username": player["username"],
                "team_id": player["team_id"],
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "adr": adr,
                "hs": hs,
            }
        )
    else:
        try:
            parts = text.split(":")
            left = int(parts[0]) if len(parts) > 0 else 0
            right = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            if sport_mode == "Football":
                await message.answer("❌ Неверный формат: goals:assists")
            elif sport_mode == "Basketball":
                await message.answer("❌ Неверный формат: points:fouls")
            else:
                await message.answer("❌ Неверный формат: points:aces")
            return

        if left < 0 or right < 0:
            await message.answer("❌ Статистика не может быть отрицательной.")
            return

        payload = {
            "user_id": player["id"],
            "user_name": player["first_name"],
            "username": player["username"],
            "team_id": player["team_id"],
        }
        if sport_mode == "Football":
            payload["goals"] = left
            payload["assists"] = right
        elif sport_mode == "Basketball":
            payload["points"] = left
            payload["fouls"] = right
        else:
            payload["points"] = left
            payload["aces"] = right
        stats.append(payload)

    next_idx = idx + 1
    await state.update_data(player_stats_list=stats, current_player_index=next_idx)
    if next_idx >= len(players):
        await _show_confirm(message, state)
        return

    await state.update_data(current_player=players[next_idx])
    await _show_player_input(message, state, next_idx)


@router.callback_query(F.data == "manual_save_stats")
async def save_manual_stats(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    players_stats = data.get("player_stats_list", [])
    sport_mode = normalize_sport_name(data.get("sport_mode", "CS2"))
    if not players_stats:
        await callback.answer("❌ Нет статистики для сохранения", show_alert=True)
        return

    match_id = data.get("bracket_match_id")
    map_name = data.get("current_map")
    map_number = data.get("current_map_number", 1)
    team1_score = data.get("team1_score", 0)
    team2_score = data.get("team2_score", 0)
    team1_id = data.get("team1_id")
    team2_id = data.get("team2_id")

    if sport_mode != "CS2":
        for p in players_stats:
            if sport_mode == "Football":
                upsert_football_player_stat(
                    "bracket",
                    match_id,
                    p["user_id"],
                    p["team_id"],
                    p.get("goals", 0),
                    p.get("assists", 0),
                )
            elif sport_mode == "Basketball":
                upsert_basketball_player_stat(
                    "bracket",
                    match_id,
                    p["user_id"],
                    p["team_id"],
                    p.get("points", 0),
                    p.get("fouls", 0),
                )
            elif sport_mode == "Volleyball":
                upsert_volleyball_player_stat(
                    "bracket",
                    match_id,
                    p["user_id"],
                    p["team_id"],
                    p.get("points", 0),
                    p.get("aces", 0),
                )

        if sport_mode == "Volleyball":
            replace_volleyball_set_scores("bracket", match_id, data.get("volleyball_sets", []))

        await _finalize_non_cs2_match(callback, state)
        return

    conn = get_connection()
    cur = conn.cursor()
    for p in players_stats:
        cur.execute(
            """
            INSERT OR REPLACE INTO player_match_stats
            (match_id, match_source, user_id, team_id, kills, deaths, assists, adr, hs, rating_3_0, mvps, map_name, map_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                "bracket",
                p["user_id"],
                p["team_id"],
                p["kills"],
                p["deaths"],
                p["assists"],
                p.get("adr", 0),
                p.get("hs", 0),
                0.0,
                0,
                map_name,
                map_number,
            ),
        )

    # Сохраняем результат карты для истории матчей.
    winner_id = _winner_from_score(team1_score, team2_score, team1_id, team2_id)
    cur.execute(
        """
        INSERT INTO match_map_results
        (match_source, match_id, map_number, map_name, team1_score, team2_score, winner_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(match_source, match_id, map_number) DO UPDATE SET
            map_name=excluded.map_name,
            team1_score=excluded.team1_score,
            team2_score=excluded.team2_score,
            winner_id=excluded.winner_id,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            "bracket",
            match_id,
            map_number,
            map_name,
            team1_score,
            team2_score,
            winner_id,
        ),
    )
    conn.commit()
    conn.close()

    team1_wins = data.get("team1_wins", 0)
    team2_wins = data.get("team2_wins", 0)
    if winner_id == team1_id:
        team1_wins += 1
    else:
        team2_wins += 1

    map_results = data.get("map_results", [])
    map_results.append(
        {
            "map_number": map_number,
            "map_name": map_name,
            "team1_score": team1_score,
            "team2_score": team2_score,
            "winner_id": winner_id,
        }
    )

    await state.update_data(team1_wins=team1_wins, team2_wins=team2_wins, map_results=map_results)
    maps_to_win = data.get("maps_to_win", 1)
    total_maps = data.get("total_maps", 1)

    if team1_wins >= maps_to_win or team2_wins >= maps_to_win or map_number >= total_maps:
        await _finalize_series(callback, state)
        return

    await state.update_data(
        current_map_number=map_number + 1,
        current_map=None,
        team1_score=0,
        team2_score=0,
        all_players=[],
        player_stats_list=[],
        current_player=None,
        current_player_index=0,
    )

    text = (
        f"✅ Карта {map_number} сохранена.\n\n"
        f"Текущий счет серии:\n"
        f"🔵 {data.get('team1_name')}: {team1_wins}\n"
        f"🔴 {data.get('team2_name')}: {team2_wins}\n\n"
        f"Нужно побед: {maps_to_win}"
    )
    await callback.message.answer(text)
    await _show_map_selection(callback, state)
    await callback.answer()


async def _finalize_non_cs2_match(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_id = data.get("bracket_match_id")
    tournament_id = data.get("tournament_id")
    team1_id = data.get("team1_id")
    team2_id = data.get("team2_id")
    team1_name = data.get("team1_name")
    team2_name = data.get("team2_name")
    team1_score = data.get("team1_score", 0)
    team2_score = data.get("team2_score", 0)
    sport_mode = normalize_sport_name(data.get("sport_mode", "CS2"))

    winner_id = team1_id if team1_score > team2_score else team2_id
    winner_name = team1_name if winner_id == team1_id else team2_name

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tournament_brackets SET score1=?, score2=? WHERE id=?",
        (team1_score, team2_score, match_id),
    )
    conn.commit()
    conn.close()

    if not advance_winner(match_id, winner_id):
        await callback.answer("❌ Ошибка обновления сетки", show_alert=True)
        return

    third_place_result = auto_create_third_place_if_ready(tournament_id)

    match = _fetch_bracket_match(match_id)
    round_number = match["round_number"] if match else 0
    tournament = get_tournament_by_id(tournament_id)
    if tournament:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        points = 3 if round_number == 5 else 5
        update_team_rating(winner_id, tournament["sport"], month, points)

    temp_dir = os.path.abspath("temp/brackets")
    os.makedirs(temp_dir, exist_ok=True)
    png_path = os.path.join(temp_dir, f"bracket_{tournament_id}.png")
    png_result = generate_bracket_png(tournament_id, png_path)

    third_place_note = "\n🥉 Матч за 3-е место создан автоматически." if third_place_result.get("created") else ""

    summary = (
        "✅ Результат матча сохранен!\n"
        f"🏆 Победитель: {winner_name}\n"
        f"📊 Счет матча: {team1_name} {team1_score} : {team2_score} {team2_name}\n"
        f"🏅 Вид спорта: {get_sport_display_name(sport_mode)}"
    )

    if png_result and os.path.exists(png_path):
        from aiogram.types import FSInputFile
        try:
            await callback.message.answer_photo(photo=FSInputFile(png_path), caption=summary)
        except Exception:
            await callback.message.answer(summary)
    else:
        await callback.message.answer(summary)

    await notify_bracket_match_result(callback.bot, match_id, sport_mode)
    await callback.message.answer("Выберите действие:", reply_markup=_summary_keyboard(tournament_id))
    await state.clear()
    from handlers.brackets import start_schedule_wizard_for_tournament
    await start_schedule_wizard_for_tournament(
        callback,
        state,
        tournament_id=tournament_id,
        return_callback=f"view_bracket_{tournament_id}",
    )
    await callback.answer()


async def _finalize_series(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_id = data.get("bracket_match_id")
    tournament_id = data.get("tournament_id")
    team1_id = data.get("team1_id")
    team2_id = data.get("team2_id")
    team1_name = data.get("team1_name")
    team2_name = data.get("team2_name")
    team1_wins = data.get("team1_wins", 0)
    team2_wins = data.get("team2_wins", 0)

    winner_id = team1_id if team1_wins > team2_wins else team2_id
    winner_name = team1_name if winner_id == team1_id else team2_name

    # Сохраняем итоговый счет серии в матче сетки.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tournament_brackets SET score1=?, score2=? WHERE id=?",
        (team1_wins, team2_wins, match_id),
    )
    conn.commit()
    conn.close()

    if not advance_winner(match_id, winner_id):
        await callback.answer("❌ Ошибка обновления сетки", show_alert=True)
        return

    third_place_result = auto_create_third_place_if_ready(tournament_id)

    match = _fetch_bracket_match(match_id)
    round_number = match["round_number"] if match else 0
    tournament = get_tournament_by_id(tournament_id)
    if tournament:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        points = 3 if round_number == 5 else 5
        update_team_rating(winner_id, tournament["sport"], month, points)

    temp_dir = os.path.abspath("temp/brackets")
    os.makedirs(temp_dir, exist_ok=True)
    png_path = os.path.join(temp_dir, f"bracket_{tournament_id}.png")
    png_result = generate_bracket_png(tournament_id, png_path)

    third_place_note = "\n🥉 Матч за 3-е место создан автоматически." if third_place_result.get("created") else ""

    summary = (
        "✅ Результат матча сохранен!\n"
        f"🏆 Победитель: {winner_name}\n"
        f"📊 Счет серии: {team1_name} {team1_wins} : {team2_wins} {team2_name}"
    )

    if png_result and os.path.exists(png_path):
        from aiogram.types import FSInputFile

        try:
            await callback.message.answer_photo(photo=FSInputFile(png_path), caption=summary)
        except Exception:
            await callback.message.answer(summary)
    else:
        await callback.message.answer(summary)

    sport_mode = normalize_sport_name(tournament["sport"]) if tournament else "CS2"
    await notify_bracket_match_result(callback.bot, match_id, sport_mode)
    await callback.message.answer("Выберите действие:", reply_markup=_summary_keyboard(tournament_id))
    await state.clear()
    from handlers.brackets import start_schedule_wizard_for_tournament
    await start_schedule_wizard_for_tournament(
        callback,
        state,
        tournament_id=tournament_id,
        return_callback=f"view_bracket_{tournament_id}",
    )
    await callback.answer()
