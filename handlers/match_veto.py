from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from razryad_arena_utils import can_manage_tournament
from utils.veto_service import (
    START_SOURCE_NOTIFICATION,
    START_SOURCE_PANEL,
    build_veto_keyboard_for_user,
    cancel_veto_session,
    format_veto_text,
    get_match_veto_details,
    get_veto_session_summary,
    notify_captains_veto_started,
    notify_veto_completed,
    perform_veto_action,
    refresh_veto_messages,
    reset_veto_session,
    start_veto_session,
    store_veto_message_target,
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
    await _show_veto(callback, match_id)
    await refresh_veto_messages(callback.bot, match_id, exclude_chat_ids={callback.message.chat.id})
