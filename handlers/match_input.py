"""
Legacy-совместимость для старого потока ввода результата матча.

Важно: канонический поток реализован в handlers/match_manual.py.
Этот модуль больше не содержит собственной FSM-логики и безопасно
перенаправляет на актуальный сценарий.
"""
from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from razryad_arena_utils import is_admin

router = Router()


@router.callback_query(F.data.startswith("enter_result_match_"))
async def legacy_enter_result_match(callback: CallbackQuery):
    """Перенаправляет старый callback в новый ручной поток ввода."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Некорректный ID матча", show_alert=True)
        return

    try:
        match_id = int(parts[3])
        tournament_id = int(parts[4]) if len(parts) > 4 else 0
    except ValueError:
        await callback.answer("Некорректный формат callback", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✏️ Перейти к ручному вводу",
        callback_data=f"manual_match_result_{match_id}_{tournament_id}",
    )
    if tournament_id:
        builder.button(text="❌ Отмена", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)

    await callback.message.answer(
        "⚠️ Старый режим ввода отключён.\n"
        "Используйте новый поток ручного ввода результата:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("result_"))
async def legacy_result_callbacks(callback: CallbackQuery):
    """Защита от устаревших кнопок result_*."""
    await callback.answer(
        "Эта кнопка устарела. Откройте матч через текущий интерфейс сетки.",
        show_alert=True,
    )

