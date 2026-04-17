"""
Ручной ввод результатов матчей турнирной сетки.
Поддерживает BO1/BO3/BO5 по картам.
"""
from datetime import datetime, timezone
import os
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_connection
from handlers.states import ManualMatchInput, BracketTechnicalResultInput
from razryad_arena_utils import (
    apply_bracket_technical_result,
    can_manage_bracket_match,
    get_user,
    get_steam_profile_name,
    get_tournament_team_members,
    get_tournament_by_id,
    resolve_bracket_match_format,
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
from utils.cs2_maps import CS2_MAPS, get_cs2_map_name
from utils.demo_import import (
    DemoImportError,
    auto_match_demo_players,
    finalize_demo_import_payload,
    is_demo_source_message,
    parse_demo_source_message,
)
from utils.notifications import notify_bracket_match_result
from utils.site_sync import request_site_sync
from utils.veto_service import close_veto_for_technical_result, get_completed_series_maps_for_match, get_match_veto_details, refresh_veto_messages

router = Router()

CS2_MAP_OPTIONS = [(row["key"], row["name"]) for row in CS2_MAPS]
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
    for map_id, map_name in CS2_MAP_OPTIONS:
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


def _input_method_keyboard(tournament_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="📥/🔗 Импорт из демки", callback_data="manual_method_demo")
    builder.button(text="✍️ Вручную", callback_data="manual_method_manual")
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _demo_confirm_keyboard(tournament_id: int, overwrite_required: bool = False):
    builder = InlineKeyboardBuilder()
    if overwrite_required:
        builder.button(text="♻️ Подтвердить перезапись карты", callback_data="manual_demo_confirm")
    else:
        builder.button(text="✅ Импортировать карту", callback_data="manual_demo_confirm")
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _demo_mapping_keyboard(
    tournament_id: int,
    candidates: list[dict[str, Any]],
    team1_id: int,
    team1_name: str,
    team2_name: str,
):
    builder = InlineKeyboardBuilder()
    for player in candidates:
        team_name = team1_name if int(player["team_id"]) == int(team1_id) else team2_name
        username = f"@{player['username']}" if player.get("username") else "без_username"
        builder.button(
            text=f"{player.get('first_name') or 'Игрок'} ({team_name}) {username}",
            callback_data=f"manual_demo_map_{int(player['id'])}",
        )
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _tech_loss_loser_keyboard(match_id: int, tournament_id: int, team1_id: int, team1_name: str, team2_id: int, team2_name: str):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"❌ {team1_name}", callback_data=f"manual_tech_pick_loser_{match_id}_{tournament_id}_{team1_id}")
    builder.button(text=f"❌ {team2_name}", callback_data=f"manual_tech_pick_loser_{match_id}_{tournament_id}_{team2_id}")
    builder.button(text="🔙 Назад", callback_data=f"bracket_match_{match_id}_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _tech_loss_reason_keyboard(match_id: int, tournament_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без причины", callback_data=f"manual_tech_reason_skip_{match_id}_{tournament_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"bracket_match_{match_id}_{tournament_id}")],
    ])


def _tech_loss_confirm_keyboard(match_id: int, tournament_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить тех.поражение", callback_data="manual_tech_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"bracket_match_{match_id}_{tournament_id}")],
    ])


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


def _fetch_players(team1_id: int, team2_id: int, tournament_id: int | None = None) -> list[dict]:
    rows: list[dict] = []
    for team_id in (team1_id, team2_id):
        members = get_tournament_team_members(tournament_id, team_id) if tournament_id else []
        if not members:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT u.id, u.first_name, u.username, u.steam_id, tm.team_id
                FROM users u
                JOIN team_members tm ON u.id = tm.user_id
                WHERE tm.team_id=?
                ORDER BY u.first_name
                """,
                (team_id,),
            )
            members = [dict(r) for r in cur.fetchall()]
            conn.close()
        else:
            members = [dict(member) for member in members]
            for member in members:
                member["team_id"] = team_id
        rows.extend(members)
    return rows


def _fetch_saved_map_results(match_id: int) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT map_number, map_name, team1_score, team2_score, winner_id
        FROM match_map_results
        WHERE match_source='bracket' AND match_id=?
        ORDER BY map_number
        """,
        (match_id,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _normalize_map_compare(name: str | None) -> str:
    return "".join(ch for ch in str(name or "").strip().lower() if ch.isalnum())


def _build_cs2_series_state(saved_map_results: list[dict], team1_id: int) -> dict[str, Any]:
    team1_wins = 0
    team2_wins = 0
    max_map_number = 0
    for row in saved_map_results:
        map_number = int(row.get("map_number") or 0)
        max_map_number = max(max_map_number, map_number)
        if int(row.get("winner_id") or 0) == int(team1_id):
            team1_wins += 1
        else:
            team2_wins += 1
    return {
        "team1_wins": team1_wins,
        "team2_wins": team2_wins,
        "current_map_number": max_map_number + 1 if max_map_number else 1,
        "map_results": saved_map_results,
        "existing_map_numbers": [int(row.get("map_number") or 0) for row in saved_map_results],
    }


def _build_cs2_format_keyboard(tournament_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="BO1", callback_data="manual_format_bo1")
    builder.button(text="BO3", callback_data="manual_format_bo3")
    builder.button(text="BO5", callback_data="manual_format_bo5")
    builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _get_steam_label(steam_id: str | None) -> str:
    if not steam_id:
        return "Steam: ❌"
    profile_name = get_steam_profile_name(steam_id)
    return f"Steam: {profile_name}" if profile_name else "Steam: [профиль]"


async def _prompt_cs2_format_selection(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(ManualMatchInput.format)
    text = (
        "✏️ Ввод результата\n\n"
        f"🔵 {data.get('team1_name')} vs 🔴 {data.get('team2_name')}\n"
        f"📌 Раунд: {data.get('round_name')}\n\n"
        "Выберите формат:"
    )
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=_build_cs2_format_keyboard(data.get("tournament_id")))
    else:
        await target.answer(text, reply_markup=_build_cs2_format_keyboard(data.get("tournament_id")))


async def _show_cs2_input_method(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    maps_text = ""
    if data.get("map_results"):
        lines = [
            f"{row['map_number']}. {row.get('map_name') or 'Карта'} - {row.get('team1_score', 0)}:{row.get('team2_score', 0)}"
            for row in data.get("map_results", [])
        ]
        maps_text = "\n\nУже сохранены карты:\n" + "\n".join(lines)

    format_text = f"\n📊 Формат: {data.get('match_format')}" if data.get("match_format") else ""
    text = (
        "✏️ Ввод результата\n\n"
        f"🔵 {data.get('team1_name')} vs 🔴 {data.get('team2_name')}\n"
        f"📌 Раунд: {data.get('round_name')}{format_text}\n"
        f"📈 Текущий счет серии: {data.get('team1_wins', 0)}:{data.get('team2_wins', 0)}\n"
        f"🗺 Следующая карта: {data.get('current_map_number', 1)}"
        f"{maps_text}\n\n"
        "Выберите способ ввода:"
    )
    await state.set_state(ManualMatchInput.input_method)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=_input_method_keyboard(data.get("tournament_id")))
    else:
        await target.answer(text, reply_markup=_input_method_keyboard(data.get("tournament_id")))


async def _start_cs2_manual_flow(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    predefined_maps = data.get("predefined_maps") or []
    if predefined_maps:
        maps_text = "\n".join(f"{row['map_order']}. {row['map_name']}" for row in predefined_maps)
        prompt = (
            "✏️ Ввод результата\n\n"
            f"🔵 {data.get('team1_name')} vs 🔴 {data.get('team2_name')}\n"
            f"📌 Раунд: {data.get('round_name')}\n"
            f"📊 Формат: {data.get('match_format')}\n\n"
            "Карты уже определены через pick/ban:\n"
            f"{maps_text}"
        )
        if isinstance(target, CallbackQuery):
            await target.message.answer(prompt)
        else:
            await target.answer(prompt)
        await _show_map_selection(target, state)
        return

    if data.get("total_maps"):
        prompt = (
            "✏️ Ввод результата\n\n"
            f"🔵 {data.get('team1_name')} vs 🔴 {data.get('team2_name')}\n"
            f"📌 Раунд: {data.get('round_name')}\n"
            f"📊 Формат: {data.get('match_format')}\n\n"
            "Выберите карту:"
        )
        if isinstance(target, CallbackQuery):
            await target.message.answer(prompt)
        else:
            await target.answer(prompt)
        await _show_map_selection(target, state)
        return

    await _prompt_cs2_format_selection(target, state)


async def _show_demo_input_prompt(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = (
        "📥 Импорт из демки\n\n"
        "Отправьте одним сообщением:\n"
        "• .dem или .zip файлом\n"
        "• либо публичную ссылку на .dem/.zip\n\n"
        "После этого бот автоматически сопоставит игроков по SteamID, а несовпавших предложит связать вручную."
    )
    await state.set_state(ManualMatchInput.demo_input)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))
    else:
        await target.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))


def _infer_demo_team_target_id(
    demo_players: list[dict[str, Any]],
    expected_players: list[dict[str, Any]],
    mappings: dict[str, int],
    team_index: int,
) -> int | None:
    expected_by_id = {int(player["id"]): dict(player) for player in expected_players}
    team_ids = {
        int(expected_by_id[mappings[player["steamid"]]]["team_id"])
        for player in demo_players
        if player["team_index"] == team_index
        and player.get("steamid") in mappings
        and mappings[player["steamid"]] in expected_by_id
    }
    if len(team_ids) == 1:
        return team_ids.pop()
    return None


def _build_demo_candidates(
    demo_player: dict[str, Any],
    expected_players: list[dict[str, Any]],
    mappings: dict[str, int],
    team1_id: int,
    team2_id: int,
    demo_players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    used_user_ids = {int(user_id) for user_id in mappings.values()}
    inferred_team_id = _infer_demo_team_target_id(
        demo_players,
        expected_players,
        mappings,
        int(demo_player["team_index"]),
    )
    candidates: list[dict[str, Any]] = []
    for player in expected_players:
        player_id = int(player["id"])
        player_team_id = int(player["team_id"])
        if player_id in used_user_ids:
            continue
        if player_team_id not in {int(team1_id), int(team2_id)}:
            continue
        if inferred_team_id is not None and player_team_id != int(inferred_team_id):
            continue
        candidates.append(dict(player))
    return candidates


async def _show_demo_mapping_prompt(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    unresolved = data.get("demo_unresolved_players") or []
    mapping_index = int(data.get("demo_mapping_index") or 0)
    if mapping_index >= len(unresolved):
        await _show_demo_preview(target, state)
        return

    current_player = unresolved[mapping_index]
    expected_players = data.get("demo_expected_players") or []
    mappings = data.get("demo_mappings") or {}
    candidates = _build_demo_candidates(
        current_player,
        expected_players,
        mappings,
        data.get("team1_id"),
        data.get("team2_id"),
        data.get("demo_players") or [],
    )
    if not candidates:
        text = "❌ Не осталось доступных игроков для сопоставления. Отмените импорт и попробуйте снова."
        if isinstance(target, CallbackQuery):
            await target.message.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))
        else:
            await target.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))
        return

    text = (
        f"🔗 Ручное сопоставление {mapping_index + 1}/{len(unresolved)}\n\n"
        f"Игрок из демки: {current_player.get('name')}\n"
        f"SteamID: {current_player.get('steamid')}\n"
        f"Команда в демке: {current_player.get('team_name')}\n\n"
        "Выберите игрока матча:"
    )
    await state.set_state(ManualMatchInput.demo_mapping)
    if isinstance(target, CallbackQuery):
        await target.message.answer(
            text,
            reply_markup=_demo_mapping_keyboard(
                data.get("tournament_id"),
                candidates,
                data.get("team1_id"),
                data.get("team1_name"),
                data.get("team2_name"),
            ),
        )
    else:
        await target.answer(
            text,
            reply_markup=_demo_mapping_keyboard(
                data.get("tournament_id"),
                candidates,
                data.get("team1_id"),
                data.get("team1_name"),
                data.get("team2_name"),
            ),
        )


def _build_demo_preview_text(data: dict[str, Any], payload: dict[str, Any]) -> str:
    lines = [
        "📥 Предпросмотр импорта демки",
        "",
        f"Источник: {data.get('demo_source_label')}",
        f"Карта: {payload.get('map_name')}",
        f"Счет карты: {data.get('team1_name')} {payload.get('team1_score')} : {payload.get('team2_score')} {data.get('team2_name')}",
        f"Карта серии: {data.get('current_map_number', 1)}/{data.get('total_maps', 1)}",
        "",
        "Сопоставленные игроки:",
    ]
    for stat in sorted(payload.get("player_stats", []), key=lambda item: (item["team_id"], -(item["kills"] or 0), item.get("user_name") or "")):
        team_name = data.get("team1_name") if int(stat["team_id"]) == int(data.get("team1_id")) else data.get("team2_name")
        username = f"@{stat['username']}" if stat.get("username") else "без_username"
        lines.append(
            f"• {stat.get('demo_name')} [{stat.get('steamid')}] → {stat.get('user_name')} ({team_name}, {username})"
        )
    if data.get("demo_overwrite_required"):
        lines.extend(["", "⚠️ Для этой карты уже есть сохраненные данные. Они будут перезаписаны."])
    return "\n".join(lines)


async def _show_demo_preview(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        payload = finalize_demo_import_payload(
            data.get("demo_parsed_result") or {},
            data.get("demo_expected_players") or [],
            data.get("demo_mappings") or {},
            int(data.get("team1_id") or 0),
            int(data.get("team2_id") or 0),
        )
    except DemoImportError as exc:
        if isinstance(target, CallbackQuery):
            await target.message.answer(f"❌ {exc}", reply_markup=_cancel_keyboard(data.get("tournament_id")))
        else:
            await target.answer(f"❌ {exc}", reply_markup=_cancel_keyboard(data.get("tournament_id")))
        return

    expected_maps = data.get("predefined_maps") or []
    map_number = int(data.get("current_map_number") or 1)
    if expected_maps and 1 <= map_number <= len(expected_maps):
        expected_name = expected_maps[map_number - 1]["map_name"]
        if _normalize_map_compare(payload["map_name"]) != _normalize_map_compare(expected_name):
            text = (
                "❌ Карта из демки не совпадает с ожидаемой картой серии.\n\n"
                f"Ожидается: {expected_name}\n"
                f"В демке: {payload['map_name']}"
            )
            if isinstance(target, CallbackQuery):
                await target.message.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))
            else:
                await target.answer(text, reply_markup=_cancel_keyboard(data.get("tournament_id")))
            return

    overwrite_required = map_number in set(data.get("existing_map_numbers") or [])
    await state.update_data(
        current_map=payload["map_name"],
        team1_score=payload["team1_score"],
        team2_score=payload["team2_score"],
        player_stats_list=payload["player_stats"],
        demo_ready_payload=payload,
        demo_overwrite_required=overwrite_required,
    )
    await state.set_state(ManualMatchInput.demo_confirm)
    if isinstance(target, CallbackQuery):
        await target.message.answer(
            _build_demo_preview_text(await state.get_data(), payload),
            reply_markup=_demo_confirm_keyboard(data.get("tournament_id"), overwrite_required),
        )
    else:
        await target.answer(
            _build_demo_preview_text(await state.get_data(), payload),
            reply_markup=_demo_confirm_keyboard(data.get("tournament_id"), overwrite_required),
        )


def _save_cs2_map_stats(
    match_id: int,
    map_number: int,
    map_name: str,
    team1_score: int,
    team2_score: int,
    team1_id: int,
    team2_id: int,
    players_stats: list[dict[str, Any]],
) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM player_match_stats WHERE match_source='bracket' AND match_id=? AND map_number=?",
        (match_id, map_number),
    )
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
    return winner_id


async def _after_cs2_map_saved(
    callback: CallbackQuery,
    state: FSMContext,
    map_name: str,
    team1_score: int,
    team2_score: int,
    players_stats: list[dict[str, Any]],
) -> bool:
    data = await state.get_data()
    match_id = data.get("bracket_match_id")
    map_number = int(data.get("current_map_number", 1))
    team1_id = data.get("team1_id")
    team2_id = data.get("team2_id")
    winner_id = _save_cs2_map_stats(
        match_id,
        map_number,
        map_name,
        team1_score,
        team2_score,
        team1_id,
        team2_id,
        players_stats,
    )

    team1_wins = int(data.get("team1_wins", 0))
    team2_wins = int(data.get("team2_wins", 0))
    if winner_id == team1_id:
        team1_wins += 1
    else:
        team2_wins += 1

    map_results = [row for row in list(data.get("map_results", [])) if int(row.get("map_number") or 0) != map_number]
    map_results.append(
        {
            "map_number": map_number,
            "map_name": map_name,
            "team1_score": team1_score,
            "team2_score": team2_score,
            "winner_id": winner_id,
        }
    )
    map_results.sort(key=lambda item: int(item.get("map_number") or 0))

    maps_to_win = int(data.get("maps_to_win", 1) or 1)
    total_maps = int(data.get("total_maps", 1) or 1)
    existing_map_numbers = sorted({int(num) for num in (data.get("existing_map_numbers") or [])} | {map_number})

    await state.update_data(
        team1_wins=team1_wins,
        team2_wins=team2_wins,
        map_results=map_results,
        existing_map_numbers=existing_map_numbers,
    )

    if team1_wins >= maps_to_win or team2_wins >= maps_to_win or map_number >= total_maps:
        await _finalize_series(callback, state)
        return True

    await state.update_data(
        current_map_number=map_number + 1,
        current_map=None,
        team1_score=0,
        team2_score=0,
        all_players=[],
        player_stats_list=[],
        current_player=None,
        current_player_index=0,
        demo_ready_payload=None,
        demo_source_label=None,
        demo_overwrite_required=False,
        demo_parsed_result=None,
        demo_expected_players=[],
        demo_players=[],
        demo_mappings={},
        demo_unresolved_players=[],
        demo_mapping_index=0,
    )

    text = (
        f"✅ Карта {map_number} сохранена.\n\n"
        f"Текущий счет серии:\n"
        f"🔵 {data.get('team1_name')}: {team1_wins}\n"
        f"🔴 {data.get('team2_name')}: {team2_wins}\n\n"
        f"Нужно побед: {maps_to_win}"
    )
    await callback.message.answer(text)
    next_data = await state.get_data()
    if next_data.get("input_mode") == "demo":
        await _show_cs2_input_method(callback, state)
    else:
        await _show_map_selection(callback, state)
    return False


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
    predefined_maps = data.get("predefined_maps") or []
    if predefined_maps:
        map_no = data.get("current_map_number", 1)
        if 1 <= map_no <= len(predefined_maps):
            await _prompt_map_score_input(target, state, predefined_maps[map_no - 1]["map_name"])
            return

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


async def _show_technical_result_confirm(target: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    loser_team_id = data.get("technical_loser_team_id")
    loser_name = data.get("team1_name") if loser_team_id == data.get("team1_id") else data.get("team2_name")
    winner_name = data.get("team2_name") if loser_team_id == data.get("team1_id") else data.get("team1_name")
    reason = (data.get("technical_reason") or "").strip() or "не указана"
    text = (
        "🚫 Подтверждение тех.поражения\n\n"
        f"Матч: {data.get('team1_name')} vs {data.get('team2_name')}\n"
        f"Победитель: {winner_name}\n"
        f"Тех.поражение: {loser_name}\n"
        f"Итоговый счет: {data.get('technical_score1', 0)}:{data.get('technical_score2', 0)}\n"
        f"Причина: {reason}"
    )
    await state.set_state(BracketTechnicalResultInput.confirm)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=_tech_loss_confirm_keyboard(data["bracket_match_id"], data["tournament_id"]))
    else:
        await target.answer(text, reply_markup=_tech_loss_confirm_keyboard(data["bracket_match_id"], data["tournament_id"]))


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
    veto_series_maps = get_completed_series_maps_for_match(match_id) if sport_mode == "CS2" else []
    effective_match_format = resolve_bracket_match_format(match_id).upper() if sport_mode == "CS2" else None
    saved_map_results = _fetch_saved_map_results(match_id) if sport_mode == "CS2" else []
    series_state = _build_cs2_series_state(saved_map_results, match["team1_id"]) if sport_mode == "CS2" else {}

    await state.update_data(
        bracket_match_id=match_id,
        tournament_id=tournament_id,
        team1_id=match["team1_id"],
        team2_id=match["team2_id"],
        team1_name=match["team1_name"],
        team2_name=match["team2_name"],
        round_name=match["round_name"],
        sport_mode=sport_mode,
        input_mode=None,
        team1_wins=series_state.get("team1_wins", 0),
        team2_wins=series_state.get("team2_wins", 0),
        current_map_number=series_state.get("current_map_number", 1),
        map_results=series_state.get("map_results", []),
        existing_map_numbers=series_state.get("existing_map_numbers", []),
        predefined_maps=veto_series_maps,
        match_format=effective_match_format,
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

    if veto_series_maps:
        total_maps = len(veto_series_maps)
        format_label = effective_match_format or (f"BO{total_maps}")
        await state.update_data(
            match_format=format_label,
            total_maps=total_maps,
            maps_to_win=_series_required_wins(total_maps),
        )
        await _show_cs2_input_method(callback, state)
        await callback.answer()
        return

    if effective_match_format:
        total_maps = int(effective_match_format[-1])
        await state.update_data(
            match_format=effective_match_format,
            total_maps=total_maps,
            maps_to_win=_series_required_wins(total_maps),
        )
        await _show_cs2_input_method(callback, state)
        await callback.answer()
        return

    await _show_cs2_input_method(callback, state)
    await callback.answer()


@router.callback_query(F.data.startswith("manual_match_result_"))
async def start_manual_input(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    match_id = int(parts[3])
    if not can_manage_bracket_match(callback.from_user.id, match_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament_id = int(parts[4]) if len(parts) > 4 else None
    await start_manual_input_by_match(callback, state, match_id, tournament_id)


@router.callback_query(ManualMatchInput.input_method, F.data == "manual_method_manual")
async def choose_manual_method(callback: CallbackQuery, state: FSMContext):
    await state.update_data(input_mode="manual")
    await _start_cs2_manual_flow(callback, state)
    await callback.answer()


@router.callback_query(ManualMatchInput.input_method, F.data == "manual_method_demo")
async def choose_demo_method(callback: CallbackQuery, state: FSMContext):
    await state.update_data(input_mode="demo")
    data = await state.get_data()
    if not data.get("total_maps"):
        await _prompt_cs2_format_selection(callback, state)
    else:
        await _show_demo_input_prompt(callback, state)
    await callback.answer()


@router.callback_query(F.data.startswith("manual_match_technical_"))
async def start_technical_result_input(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    match_id = int(parts[3])
    tournament_id = int(parts[4]) if len(parts) > 4 else 0
    if not can_manage_bracket_match(callback.from_user.id, match_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    match = _fetch_bracket_match(match_id)
    if not match:
        await callback.answer("Матч не найден", show_alert=True)
        return
    match = dict(match)
    if match["status"] == "completed":
        await callback.answer("Этот матч уже завершен.", show_alert=True)
        return
    if not match["team1_id"] or not match["team2_id"]:
        await callback.answer("В этом матче еще нет пары команд.", show_alert=True)
        return

    await state.clear()
    await state.update_data(
        bracket_match_id=match_id,
        tournament_id=tournament_id or match["tournament_id"],
        team1_id=match["team1_id"],
        team2_id=match["team2_id"],
        team1_name=match["team1_name"],
        team2_name=match["team2_name"],
        sport_mode=normalize_sport_name(match.get("tournament_sport", "CS2")),
    )
    await callback.message.edit_text(
        "🚫 Тех.поражение\n\nВыберите команду, которой будет засчитано тех.поражение:",
        reply_markup=_tech_loss_loser_keyboard(
            match_id,
            tournament_id or match["tournament_id"],
            match["team1_id"],
            match["team1_name"],
            match["team2_id"],
            match["team2_name"],
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("manual_tech_pick_loser_"))
async def pick_technical_loser(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    match_id = int(parts[4])
    tournament_id = int(parts[5])
    loser_team_id = int(parts[6])
    if not can_manage_bracket_match(callback.from_user.id, match_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    team1_id = data.get("team1_id")
    score1 = 0 if loser_team_id == team1_id else 1
    score2 = 0 if loser_team_id != team1_id else 1
    await state.update_data(
        bracket_match_id=match_id,
        tournament_id=tournament_id,
        technical_loser_team_id=loser_team_id,
        technical_score1=score1,
        technical_score2=score2,
    )
    await state.set_state(BracketTechnicalResultInput.reason_input)
    await callback.message.edit_text(
        "Введите причину тех.поражения.\n\n"
        "Можно отправить короткий текст или нажать «Без причины».",
        reply_markup=_tech_loss_reason_keyboard(match_id, tournament_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("manual_tech_reason_skip_"))
async def skip_technical_reason(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    match_id = int(parts[4])
    if not can_manage_bracket_match(callback.from_user.id, match_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.update_data(technical_reason="")
    await _show_technical_result_confirm(callback, state)


@router.message(BracketTechnicalResultInput.reason_input)
async def technical_reason_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if _is_cancel(text):
        data = await state.get_data()
        await state.clear()
        await message.answer(
            "Отменено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 К матчу", callback_data=f"bracket_match_{data.get('bracket_match_id')}_{data.get('tournament_id')}")
            ]]),
        )
        return
    await state.update_data(technical_reason=text)
    await _show_technical_result_confirm(message, state)


@router.callback_query(F.data == "manual_tech_confirm")
async def confirm_technical_result(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    match_id = int(data.get("bracket_match_id") or 0)
    tournament_id = int(data.get("tournament_id") or 0)
    loser_team_id = int(data.get("technical_loser_team_id") or 0)
    if not can_manage_bracket_match(callback.from_user.id, match_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    actor = get_user(callback.from_user.id)
    match = _fetch_bracket_match(match_id)
    if not match:
        await callback.answer("Матч не найден", show_alert=True)
        return
    match = dict(match)

    veto_details = get_match_veto_details(match_id)
    ok, error = close_veto_for_technical_result(match_id, callback.from_user.id)
    if not ok:
        await callback.answer(error or "Не удалось остановить pick/ban.", show_alert=True)
        return

    result = apply_bracket_technical_result(
        match_id,
        loser_team_id,
        actor["id"] if actor else None,
        data.get("technical_reason"),
    )
    if not result.get("ok"):
        reason = result.get("reason")
        reason_map = {
            "not_found": "Матч не найден.",
            "already_completed": "Матч уже завершен.",
            "invalid_loser": "Некорректная команда для тех.поражения.",
            "winner_not_found": "Не удалось определить победителя.",
        }
        await callback.answer(reason_map.get(reason, "Не удалось оформить тех.поражение."), show_alert=True)
        return

    third_place_result = auto_create_third_place_if_ready(tournament_id)
    tournament = get_tournament_by_id(tournament_id)
    round_number = match["round_number"] if match else 0
    winner_id = result["winner_id"]
    winner_name = match["team2_name"] if loser_team_id == match["team1_id"] else match["team1_name"]
    loser_name = match["team1_name"] if loser_team_id == match["team1_id"] else match["team2_name"]

    if tournament:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        points = 3 if round_number == 5 else 5
        update_team_rating(winner_id, tournament["sport"], month, points)

    temp_dir = os.path.abspath("temp/brackets")
    os.makedirs(temp_dir, exist_ok=True)
    png_path = os.path.join(temp_dir, f"bracket_{tournament_id}.png")
    png_result = generate_bracket_png(tournament_id, png_path)
    request_site_sync(f"match_technical_result_saved:{tournament_id}:{match_id}")

    summary = (
        "🚫 Тех.поражение оформлено\n"
        f"🏆 Победитель: {winner_name}\n"
        f"❌ Тех.поражение: {loser_name}\n"
        f"📊 Счет матча: {result['score1']}:{result['score2']}"
    )
    reason = (data.get("technical_reason") or "").strip()
    if reason:
        summary += f"\n📝 Причина: {reason}"
    if third_place_result.get("created"):
        summary += "\n🥉 Матч за 3-е место создан автоматически."

    if png_result and os.path.exists(png_path):
        from aiogram.types import FSInputFile
        try:
            await callback.message.answer_photo(photo=FSInputFile(png_path), caption=summary)
        except Exception:
            await callback.message.answer(summary)
    else:
        await callback.message.answer(summary)

    if veto_details and veto_details.get("session"):
        await refresh_veto_messages(callback.bot, match_id)

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


@router.callback_query(ManualMatchInput.format, F.data.startswith("manual_format_"))
async def select_format(callback: CallbackQuery, state: FSMContext):
    format_choice = callback.data.split("_")[2]
    total_maps = int(format_choice[2])
    data = await state.get_data()
    await state.update_data(
        match_format=format_choice.upper(),
        total_maps=total_maps,
        maps_to_win=_series_required_wins(total_maps),
    )
    if data.get("input_mode") == "demo":
        await _show_demo_input_prompt(callback, state)
    else:
        await _show_map_selection(callback, state)
    await callback.answer()


@router.callback_query(ManualMatchInput.map_select, F.data.startswith("manual_map_"))
async def select_map(callback: CallbackQuery, state: FSMContext):
    map_key = callback.data.split("_", 2)[2]
    if map_key == CUSTOM_CS2_MAP_TOKEN:
        await state.set_state(ManualMatchInput.custom_map_input)
        await callback.message.answer(
            "✍️ Введите название карты вручную.\n\n"
            "Например: Train, Cache или любая другая карта.",
            reply_markup=_cancel_keyboard((await state.get_data()).get("tournament_id"))
        )
        await callback.answer()
        return

    await _prompt_map_score_input(callback, state, get_cs2_map_name(map_key))
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


@router.message(ManualMatchInput.demo_input)
async def input_demo_source(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text and _is_cancel(text):
        await state.clear()
        await message.answer("❌ Отменено")
        return
    if not is_demo_source_message(message):
        await message.answer(
            "❌ Отправьте .dem/.zip файлом или публичную ссылку на .dem/.zip.",
            reply_markup=_cancel_keyboard((await state.get_data()).get("tournament_id")),
        )
        return

    data = await state.get_data()
    if not data.get("total_maps"):
        await message.answer("❌ Сначала выберите формат серии.")
        return
    if int(data.get("current_map_number") or 1) > int(data.get("total_maps") or 1):
        await message.answer("❌ Все карты серии уже заполнены.")
        return

    await message.answer("⏳ Обрабатываю демку, это может занять немного времени...")
    try:
        demo_result = await parse_demo_source_message(message.bot, message)
    except DemoImportError as exc:
        await message.answer(f"❌ {exc}", reply_markup=_cancel_keyboard(data.get("tournament_id")))
        return

    expected_players = _fetch_players(data.get("team1_id"), data.get("team2_id"), data.get("tournament_id"))
    if not expected_players:
        await message.answer("❌ Не удалось получить составы команд для сопоставления.")
        return

    try:
        mapping_result = auto_match_demo_players(demo_result["parsed_result"], expected_players)
    except DemoImportError as exc:
        await message.answer(f"❌ {exc}", reply_markup=_cancel_keyboard(data.get("tournament_id")))
        return

    await state.update_data(
        demo_source_label=demo_result["source_label"],
        demo_parsed_result=demo_result["parsed_result"],
        demo_expected_players=mapping_result["expected_players"],
        demo_players=mapping_result["demo_players"],
        demo_mappings=mapping_result["mappings"],
        demo_unresolved_players=mapping_result["unresolved"],
        demo_mapping_index=0,
        demo_ready_payload=None,
        demo_overwrite_required=False,
    )

    if mapping_result["unresolved"]:
        await _show_demo_mapping_prompt(message, state)
        return
    await _show_demo_preview(message, state)


@router.callback_query(ManualMatchInput.demo_mapping, F.data.startswith("manual_demo_map_"))
async def map_demo_player(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.rsplit("_", 1)[1])
    data = await state.get_data()
    unresolved = data.get("demo_unresolved_players") or []
    mapping_index = int(data.get("demo_mapping_index") or 0)
    if mapping_index >= len(unresolved):
        await _show_demo_preview(callback, state)
        await callback.answer()
        return

    current_player = unresolved[mapping_index]
    mappings = dict(data.get("demo_mappings") or {})
    mappings[current_player["steamid"]] = user_id
    await state.update_data(
        demo_mappings=mappings,
        demo_mapping_index=mapping_index + 1,
    )
    await _show_demo_mapping_prompt(callback, state)
    await callback.answer()


@router.callback_query(ManualMatchInput.demo_confirm, F.data == "manual_demo_confirm")
async def confirm_demo_import(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    players_stats = data.get("player_stats_list") or []
    map_name = data.get("current_map")
    team1_score = int(data.get("team1_score") or 0)
    team2_score = int(data.get("team2_score") or 0)
    if not players_stats or not map_name:
        await callback.answer("Нет данных для импорта.", show_alert=True)
        return
    if team1_score == team2_score:
        await callback.answer("Ничейный счет карты недопустим.", show_alert=True)
        return

    finished = await _after_cs2_map_saved(callback, state, map_name, team1_score, team2_score, players_stats)
    if not finished:
        await callback.answer()


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
        players = _fetch_players(data.get("team1_id"), data.get("team2_id"), data.get("tournament_id"))
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

    players = _fetch_players(data.get("team1_id"), data.get("team2_id"), data.get("tournament_id"))
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

    finished = await _after_cs2_map_saved(callback, state, map_name, team1_score, team2_score, players_stats)
    if not finished:
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
    request_site_sync(f"match_result_saved:{tournament_id}:{match_id}")

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
    request_site_sync(f"match_series_saved:{tournament_id}:{match_id}")

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
