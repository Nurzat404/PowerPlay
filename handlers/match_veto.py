from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from razryad_arena_utils import apply_bracket_technical_result, can_manage_tournament, get_tournament_by_id, get_user
from utils.rating_channel_posts import refresh_rating_channel_posts
from utils.rating_rules import SOURCE_BRACKET_MATCH
from utils.rating_service import replace_match_team_rating
from utils.site_sync import request_site_sync
from utils.veto_service import (
    ADMIN_ACTION_SCOPE_VETO_READY,
    ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
    START_SOURCE_NOTIFICATION,
    START_SOURCE_PANEL,
    build_veto_keyboard_for_user,
    build_veto_timeout_keyboard,
    cancel_veto_session,
    format_veto_timeout_message,
    format_veto_text,
    get_match_veto_details,
    get_veto_ready_action_key,
    get_veto_session_summary,
    get_veto_timeout_action_key,
    is_veto_timeout_pending,
    notify_captains_veto_started,
    notify_veto_completed,
    perform_veto_action,
    refresh_veto_messages,
    reset_veto_session,
    resolve_admin_action_messages,
    start_veto_session,
    store_veto_message_target,
    close_veto_for_technical_result,
)

router = Router()


async def _show_veto(callback: CallbackQuery, match_id: int):
    summary = get_veto_session_summary(match_id)
    if not summary:
        await callback.answer("Сессия veto недоступна.", show_alert=True)
        return
    text = format_veto_text(summary)
    markup = build_veto_keyboard_for_user(callback.from_user.id, summary)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
        if callback.message.chat and callback.message.message_id:
            store_veto_message_target(match_id, callback.message.chat.id, callback.message.message_id)
        await callback.answer()
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            await callback.answer("Без изменений.")
            return
        raise


@router.callback_query(F.data.startswith("veto_open_"))
async def veto_open(callback: CallbackQuery):
    await _show_veto(callback, int(callback.data.split("_")[2]))


@router.callback_query(F.data.startswith("veto_admin_open_"))
async def veto_admin_open(callback: CallbackQuery):
    match_id = int(callback.data.split("_")[3])
    details = get_match_veto_details(match_id)
    if not details or not can_manage_tournament(callback.from_user.id, details["match"]["tournament_id"]):
        await callback.answer("Нет прав", show_alert=True)
        return
    await _show_veto(callback, match_id)


@router.callback_query(F.data.startswith("veto_admin_start_"))
async def veto_admin_start(callback: CallbackQuery):
    parts = callback.data.split("_")
    match_id = int(parts[-1])
    details = get_match_veto_details(match_id)
    if not details or not can_manage_tournament(callback.from_user.id, details["match"]["tournament_id"]):
        await callback.answer("Нет прав", show_alert=True)
        return

    ok, error = start_veto_session(
        match_id,
        actor_telegram_id=callback.from_user.id,
        start_source=START_SOURCE_NOTIFICATION if "_notify_" in callback.data else START_SOURCE_PANEL,
    )
    if not ok:
        await callback.answer(error or "Не удалось запустить pick/ban.", show_alert=True)
        return
    await notify_captains_veto_started(callback.bot, match_id)
    await resolve_admin_action_messages(
        callback.bot,
        text="✅ Pick/ban уже запущен другим администратором.",
        action_scope=ADMIN_ACTION_SCOPE_VETO_READY,
        action_key=get_veto_ready_action_key(match_id),
        exclude_target=(callback.message.chat.id, callback.message.message_id) if callback.message.chat and callback.message.message_id else None,
    )
    await _show_veto(callback, match_id)
    await refresh_veto_messages(callback.bot, match_id, exclude_chat_ids={callback.message.chat.id})


@router.callback_query(F.data.startswith("veto_map_"))
async def veto_map_action(callback: CallbackQuery):
    _, _, match_id_str, map_key = callback.data.split("_", 3)
    match_id = int(match_id_str)
    ok, error = perform_veto_action(match_id, callback.from_user.id, map_key)
    if not ok:
        await callback.answer(error or "Ход не выполнен.", show_alert=True)
        return
    await resolve_admin_action_messages(
        callback.bot,
        text="⏱ Ход по veto уже сделан, это уведомление больше неактуально.",
        action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
        bracket_match_id=match_id,
    )
    await _show_veto(callback, match_id)
    await refresh_veto_messages(callback.bot, match_id, exclude_chat_ids={callback.message.chat.id})
    summary = get_veto_session_summary(match_id)
    if summary and summary["details"].get("session", {}).get("status") == "completed":
        await notify_veto_completed(callback.bot, match_id)


@router.callback_query(F.data.startswith("veto_admin_cancel_"))
async def veto_admin_cancel(callback: CallbackQuery):
    match_id = int(callback.data.split("_")[3])
    details = get_match_veto_details(match_id)
    if not details or not can_manage_tournament(callback.from_user.id, details["match"]["tournament_id"]):
        await callback.answer("Нет прав", show_alert=True)
        return
    ok, error = cancel_veto_session(match_id, callback.from_user.id)
    if not ok:
        await callback.answer(error or "Не удалось отменить pick/ban.", show_alert=True)
        return
    await resolve_admin_action_messages(
        callback.bot,
        text="⛔ Pick/ban отменен. Это уведомление больше неактуально.",
        action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
        bracket_match_id=match_id,
    )
    await _show_veto(callback, match_id)
    await refresh_veto_messages(callback.bot, match_id, exclude_chat_ids={callback.message.chat.id})


@router.callback_query(F.data.startswith("veto_admin_reset_"))
async def veto_admin_reset(callback: CallbackQuery):
    match_id = int(callback.data.split("_")[3])
    details = get_match_veto_details(match_id)
    if not details or not can_manage_tournament(callback.from_user.id, details["match"]["tournament_id"]):
        await callback.answer("Нет прав", show_alert=True)
        return
    ok, error = reset_veto_session(match_id, callback.from_user.id)
    if not ok:
        await callback.answer(error or "Не удалось сбросить pick/ban.", show_alert=True)
        return
    await resolve_admin_action_messages(
        callback.bot,
        text="♻️ Pick/ban сброшен. Это уведомление больше неактуально.",
        action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
        bracket_match_id=match_id,
    )
    await resolve_admin_action_messages(
        callback.bot,
        text="♻️ Pick/ban сброшен. Это уведомление больше неактуально.",
        action_scope=ADMIN_ACTION_SCOPE_VETO_READY,
        action_key=get_veto_ready_action_key(match_id),
    )
    await _show_veto(callback, match_id)
    await refresh_veto_messages(callback.bot, match_id, exclude_chat_ids={callback.message.chat.id})


@router.callback_query(F.data.regexp(r"^veto_timeout_tech_\d+_\d+$"))
async def veto_timeout_tech(callback: CallbackQuery):
    _, _, _, match_id_token, step_index_token = callback.data.split("_")
    match_id = int(match_id_token)
    step_index = int(step_index_token)
    details = get_match_veto_details(match_id)
    if not details or not can_manage_tournament(callback.from_user.id, details["match"]["tournament_id"]):
        await callback.answer("Нет прав", show_alert=True)
        return
    session = details.get("session")
    if not session or not is_veto_timeout_pending(session, expected_step_index=step_index):
        await callback.answer("Это уведомление уже неактуально.", show_alert=True)
        await resolve_admin_action_messages(
            callback.bot,
            text="✅ Ситуация уже обработана другим администратором.",
            action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
            action_key=get_veto_timeout_action_key(match_id, step_index),
        )
        return
    await callback.message.edit_text(
        format_veto_timeout_message({**details["match"], **session}, confirm=True),
        reply_markup=build_veto_timeout_keyboard(match_id, step_index, confirm=True),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^veto_timeout_tech_confirm_\d+_\d+$"))
async def veto_timeout_tech_confirm(callback: CallbackQuery):
    _, _, _, _, match_id_token, step_index_token = callback.data.split("_")
    match_id = int(match_id_token)
    step_index = int(step_index_token)
    details = get_match_veto_details(match_id)
    if not details or not can_manage_tournament(callback.from_user.id, details["match"]["tournament_id"]):
        await callback.answer("Нет прав", show_alert=True)
        return
    session = details.get("session")
    if not session or not is_veto_timeout_pending(session, expected_step_index=step_index):
        await callback.answer("Это уведомление уже неактуально.", show_alert=True)
        await resolve_admin_action_messages(
            callback.bot,
            text="✅ Ситуация уже обработана другим администратором.",
            action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
            action_key=get_veto_timeout_action_key(match_id, step_index),
        )
        return

    actor = get_user(callback.from_user.id)
    loser_team_id = int(session["current_team_id"])
    result = apply_bracket_technical_result(
        match_id,
        loser_team_id,
        actor_user_id=actor["id"] if actor else None,
        reason="Не выполнен пик/бан карт в течение 5 минут",
    )
    if not result.get("ok"):
        await resolve_admin_action_messages(
            callback.bot,
            text="✅ Ситуация уже обработана другим администратором.",
            action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
            action_key=get_veto_timeout_action_key(match_id, step_index),
        )
        await callback.answer("Не удалось выдать тех.поражение.", show_alert=True)
        return

    match_payload = details["match"]
    tournament = get_tournament_by_id(match_payload["tournament_id"])
    if tournament:
        replace_match_team_rating(
            source_type=SOURCE_BRACKET_MATCH,
            match_id=match_id,
            sport_key=tournament["sport"],
            tournament_id=match_payload["tournament_id"],
            team1_id=match_payload["team1_id"],
            team2_id=match_payload["team2_id"],
            score1=result["score1"],
            score2=result["score2"],
            actor_user_id=actor["id"] if actor else None,
        )
        request_site_sync(f"match_technical_result_saved:{match_payload['tournament_id']}:{match_id}")
        await refresh_rating_channel_posts(callback.bot, sport_key=tournament["sport"], entity_type="team")

    close_veto_for_technical_result(match_id, actor_telegram_id=callback.from_user.id)
    await refresh_veto_messages(callback.bot, match_id)
    await resolve_admin_action_messages(
        callback.bot,
        text="🚫 Тех.поражение уже присуждено. Это уведомление больше неактуально.",
        action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
        bracket_match_id=match_id,
        exclude_target=(callback.message.chat.id, callback.message.message_id) if callback.message.chat and callback.message.message_id else None,
    )
    await callback.message.edit_text(
        "🚫 Тех.поражение присуждено за бездействие в map veto.",
        reply_markup=None,
    )
    await callback.answer("Тех.поражение сохранено.", show_alert=True)


@router.callback_query(F.data.regexp(r"^veto_timeout_dismiss_\d+_\d+$"))
async def veto_timeout_dismiss(callback: CallbackQuery):
    _, _, _, match_id_token, step_index_token = callback.data.split("_")
    match_id = int(match_id_token)
    step_index = int(step_index_token)
    details = get_match_veto_details(match_id)
    if not details or not can_manage_tournament(callback.from_user.id, details["match"]["tournament_id"]):
        await callback.answer("Нет прав", show_alert=True)
        return
    session = details.get("session")
    if not session or session.get("status") != "in_progress" or int(session.get("current_step_index") or 0) != step_index:
        await callback.answer("Это уведомление уже неактуально.", show_alert=True)
        await resolve_admin_action_messages(
            callback.bot,
            text="✅ Ситуация уже обработана другим администратором.",
            action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
            action_key=get_veto_timeout_action_key(match_id, step_index),
        )
        return
    await resolve_admin_action_messages(
        callback.bot,
        text="✅ Менеджер решил не присуждать тех.поражение по этому таймауту.",
        action_scope=ADMIN_ACTION_SCOPE_VETO_TIMEOUT_TECH,
        action_key=get_veto_timeout_action_key(match_id, step_index),
        exclude_target=(callback.message.chat.id, callback.message.message_id) if callback.message.chat and callback.message.message_id else None,
    )
    await callback.message.edit_text(
        "✅ По этому таймауту решено не присуждать тех.поражение.",
        reply_markup=None,
    )
    await callback.answer("Тех.поражение не будет присуждено по этому таймауту.", show_alert=True)
