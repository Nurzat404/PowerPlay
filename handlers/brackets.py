"""
Обработчики для работы с турнирными сетками
Упрощённая логика: клик на матч → сразу ввод результата
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from razryad_arena_utils import (
    can_manage_bracket_match, can_manage_tournament, is_admin, get_tournament_by_id, get_bracket_matches,
    get_semifinal_matches, create_third_place_bracket_match,
    get_bracket_match_by_id, get_unscheduled_ready_bracket_matches,
    set_bracket_match_schedule, mark_bracket_schedule_notified,
    parse_msk_datetime_input, datetime_to_utc_storage, format_utc_to_msk,
    parse_utc_storage_datetime, mark_bracket_reminder_sent,
    clear_bracket_related_data, can_create_third_place_match, sync_tournament_match_format_rules,
    get_tournament_main_round_count,
)
from utils.bracket_utils import generate_bracket, get_round_name, get_semifinal_losers
from database import get_connection
from handlers.states import ManualMatchInput, BracketScheduleInput, TargetedBroadcast
from handlers.match_manual import start_manual_input_by_match
from utils.notifications import notify_bracket_match_scheduled, notify_bracket_match_reminder, prepare_match_broadcast_payload
from utils.site_sync import request_site_sync
from utils.notifications import get_registration_ended_action_key
from utils.veto_service import (
    ADMIN_ACTION_SCOPE_REGISTRATION_ENDED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    get_match_veto_details,
    resolve_admin_action_messages,
)

router = Router()
logger = logging.getLogger(__name__)


def _match_value(match, key: str, default=None):
    try:
        value = match[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


def _is_third_place_match(match) -> bool:
    if int(_match_value(match, "is_third_place", 0) or 0) == 1:
        return True
    round_name = str(_match_value(match, "round_name", "") or "").strip().lower()
    return round_name.startswith("матч за 3-е") or round_name.startswith("матч за 3е")


def _get_total_main_rounds(matches: list[dict]) -> int:
    main_rounds = [
        int(_match_value(match, "round_number", 0))
        for match in matches
        if not _is_third_place_match(match)
    ]
    return max(main_rounds) if main_rounds else 0


def _resolve_match_round_label(match: dict, total_main_rounds: int | None = None) -> str:
    if total_main_rounds is None:
        tournament_id = _match_value(match, "tournament_id")
        if tournament_id:
            total_main_rounds = get_tournament_main_round_count(int(tournament_id))
    return get_round_name(
        int(_match_value(match, "round_number", 0) or 0),
        total_main_rounds,
        is_third_place=_is_third_place_match(match),
    )


def _schedule_locked_by_veto(match_id: int) -> bool:
    details = get_match_veto_details(match_id)
    session = details.get("session") if details else None
    if not session:
        return False
    return session.get("status") in {STATUS_IN_PROGRESS, STATUS_COMPLETED}


def _schedule_state_defaults(tournament_id: int, match_ids: list[int], return_callback: str):
    return {
        "schedule_tournament_id": tournament_id,
        "schedule_match_ids": match_ids,
        "schedule_index": 0,
        "schedule_return_callback": return_callback,
        "schedule_datetime_utc": None,
    }


def _schedule_progress_text(idx: int, total: int) -> str:
    return f"Матч {idx + 1} из {total}"


def _schedule_missing(match: dict) -> bool:
    return not (match.get("scheduled_at_utc") and match.get("location"))


def _has_saved_bracket_map_results(match_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM match_map_results
        WHERE match_source='bracket' AND match_id=?
        LIMIT 1
        """,
        (match_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def _schedule_cancel_keyboard(tournament_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"bracket_schedule_cancel_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def _schedule_done_keyboard(return_callback: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Вернуться", callback_data=return_callback)
    builder.adjust(1)
    return builder.as_markup()


async def _send_text(target: CallbackQuery | Message, text: str, reply_markup=None):
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=reply_markup)
    else:
        await target.answer(text, reply_markup=reply_markup)


async def _prompt_schedule_datetime(target: CallbackQuery | Message, state: FSMContext):
    data = await state.get_data()
    match_ids = data.get("schedule_match_ids", [])
    idx = data.get("schedule_index", 0)
    tournament_id = data.get("schedule_tournament_id")
    if idx >= len(match_ids):
        await _finish_schedule_queue(target, state)
        return

    match = get_bracket_match_by_id(match_ids[idx])
    if not match:
        await state.update_data(schedule_index=idx + 1)
        await _prompt_schedule_datetime(target, state)
        return
    match_dict = dict(match)

    if match_dict.get("status") != "pending" or not match_dict.get("team1_id") or not match_dict.get("team2_id"):
        await state.update_data(schedule_index=idx + 1)
        await _prompt_schedule_datetime(target, state)
        return

    team1 = match_dict.get("team1_name") or "Команда 1"
    team2 = match_dict.get("team2_name") or "Команда 2"
    current_time = format_utc_to_msk(match_dict.get("scheduled_at_utc"))
    current_location = (match_dict.get("location") or "не назначено").strip()
    round_label = _resolve_match_round_label(match_dict)

    text = (
        "📅 Назначение матча\n\n"
        f"{_schedule_progress_text(idx, len(match_ids))}\n"
        f"🏆 {match_dict.get('tournament_name') or 'Турнир'}\n"
        f"📍 {round_label}\n"
        f"⚔️ {team1} vs {team2}\n\n"
        f"Текущее время: {current_time} (МСК)\n"
        f"Текущее место: {current_location}\n\n"
        "Введите дату и время в формате:\n"
        "ДД.ММ.ГГГГ ЧЧ:ММ\n"
        "Пример: 28.03.2026 18:30"
    )
    await state.set_state(BracketScheduleInput.datetime_input)
    await _send_text(target, text, reply_markup=_schedule_cancel_keyboard(tournament_id))


async def _finish_schedule_queue(target: CallbackQuery | Message, state: FSMContext):
    data = await state.get_data()
    return_callback = data.get("schedule_return_callback") or f"view_bracket_{data.get('schedule_tournament_id')}"
    await state.clear()
    await _send_text(
        target,
        "✅ Расписание матчей сохранено.",
        reply_markup=_schedule_done_keyboard(return_callback),
    )


async def start_schedule_wizard_for_tournament(
    target: CallbackQuery | Message,
    state: FSMContext,
    tournament_id: int,
    match_ids: list[int] | None = None,
    return_callback: str | None = None,
) -> bool:
    """
    Публичный helper для запуска мастера назначения матчей.
    Возвращает True, если мастер запущен.
    """
    if match_ids is None:
        match_rows = get_unscheduled_ready_bracket_matches(tournament_id)
        match_ids = [row["id"] for row in match_rows]
    else:
        match_ids = [int(match_id) for match_id in match_ids]

    if not match_ids:
        return False

    await state.update_data(
        **_schedule_state_defaults(
            tournament_id=tournament_id,
            match_ids=match_ids,
            return_callback=return_callback or f"view_bracket_{tournament_id}",
        )
    )
    await _prompt_schedule_datetime(target, state)
    return True


@router.callback_query(F.data.startswith("bracket_schedule_cancel_"))
async def bracket_schedule_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tournament_id = data.get("schedule_tournament_id")
    if not tournament_id or not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    return_callback = data.get("schedule_return_callback") or callback.data.replace("bracket_schedule_cancel_", "view_bracket_")
    await state.clear()
    await callback.message.answer(
        "❌ Назначение матчей прервано.",
        reply_markup=_schedule_done_keyboard(return_callback),
    )
    await callback.answer()


@router.message(BracketScheduleInput.datetime_input)
async def bracket_schedule_datetime_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in {"отмена", "cancel", "/cancel"}:
        data = await state.get_data()
        return_callback = data.get("schedule_return_callback") or f"view_bracket_{data.get('schedule_tournament_id')}"
        await state.clear()
        await message.answer(
            "❌ Назначение матчей прервано.",
            reply_markup=_schedule_done_keyboard(return_callback),
        )
        return

    dt_msk = parse_msk_datetime_input(text)
    if not dt_msk:
        await message.answer(
            "❌ Неверный формат даты/времени.\n"
            "Используйте формат: ДД.ММ.ГГГГ ЧЧ:ММ\n"
            "Пример: 28.03.2026 18:30"
        )
        return

    await state.update_data(schedule_datetime_utc=datetime_to_utc_storage(dt_msk))
    await state.set_state(BracketScheduleInput.location_input)
    await message.answer("Введите место проведения матча:")


@router.message(BracketScheduleInput.location_input)
async def bracket_schedule_location_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()

    if text.lower() in {"отмена", "cancel", "/cancel"}:
        return_callback = data.get("schedule_return_callback") or f"view_bracket_{data.get('schedule_tournament_id')}"
        await state.clear()
        await message.answer(
            "❌ Назначение матчей прервано.",
            reply_markup=_schedule_done_keyboard(return_callback),
        )
        return

    if not text:
        await message.answer("❌ Место не может быть пустым. Введите место проведения:")
        return

    match_ids = data.get("schedule_match_ids", [])
    idx = data.get("schedule_index", 0)
    dt_utc = data.get("schedule_datetime_utc")
    if idx >= len(match_ids) or not dt_utc:
        await state.clear()
        await message.answer("❌ Сессия назначения устарела. Откройте сетку заново.")
        return

    result = set_bracket_match_schedule(match_ids[idx], dt_utc, text)
    if not result.get("ok"):
        await message.answer("⚠️ Не удалось сохранить расписание для этого матча. Переходим к следующему.")
    else:
        changed = bool(result.get("is_changed")) and not bool(result.get("is_new"))
        match_payload = result["match"]
        if result.get("is_changed"):
            await notify_bracket_match_scheduled(
                message.bot,
                match_payload,
                changed=changed,
                old_scheduled_at_utc=result["old"]["scheduled_at_utc"],
                old_location=result["old"]["location"],
            )
            mark_bracket_schedule_notified(match_payload["id"])
            action_text = "изменено" if changed else "назначено"
            await message.answer(
                f"✅ Расписание {action_text}: "
                f"{format_utc_to_msk(match_payload.get('scheduled_at_utc'))} (МСК), "
                f"место: {match_payload.get('location')}"
            )
        else:
            await message.answer("ℹ️ Изменений нет, уведомление не отправлено.")

    await state.update_data(schedule_index=idx + 1, schedule_datetime_utc=None)
    await _prompt_schedule_datetime(message, state)


@router.callback_query(F.data.startswith("admin_generate_bracket_"))
async def admin_generate_bracket_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение генерации сетки турнира."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)

    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    await state.update_data(tournament_id=tournament_id)

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, сгенерировать",
              callback_data="confirm_generate_bracket")
    kb.button(text="❌ Нет", callback_data=f"tournament_{tournament_id}")
    kb.adjust(1)

    await callback.message.edit_text(
        f"Вы уверены, что хотите сгенерировать сетку для турнира «{tournament['name']}»?\n\n"
        f"⚠️ Все одобренные команды будут распределены случайным образом.",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_generate_bracket")
async def confirm_generate_bracket(callback: CallbackQuery, state: FSMContext):
    """Генерация сетки турнира."""
    data = await state.get_data()
    tournament_id = data.get('tournament_id')
    if not tournament_id or not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        await state.clear()
        return

    success = generate_bracket(tournament_id)

    if success:
        sync_tournament_match_format_rules(tournament_id)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE tournaments SET bracket_generated=1, status='active' WHERE id=?", (tournament_id,))
        conn.commit()
        conn.close()
        request_site_sync(f"bracket_generated:{tournament_id}")
        await resolve_admin_action_messages(
            callback.bot,
            text="✅ Турнир уже переведен в активную стадию. Это уведомление больше неактуально.",
            action_scope=ADMIN_ACTION_SCOPE_REGISTRATION_ENDED,
            action_key=get_registration_ended_action_key(tournament_id),
        )

        await state.clear()
        await callback.answer("✅ Сетка сгенерирована! Статус турнира изменён на 'Идёт'.", show_alert=True)
        await callback.message.answer("Теперь обязательно назначьте время и место для новых пар.")
        started = await start_schedule_wizard_for_tournament(
            callback,
            state,
            tournament_id=tournament_id,
            return_callback=f"admin_tournament_manage_{tournament_id}",
        )
        if not started:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Управление турниром",
                                      callback_data=f"admin_tournament_manage_{tournament_id}")]
            ])
            await callback.message.answer("Нажмите кнопку ниже для управления:", reply_markup=kb)
    else:
        await callback.answer("❌ Ошибка при генерации сетки", show_alert=True)
        await state.clear()


@router.callback_query(F.data.startswith("admin_regenerate_bracket_"))
async def admin_regenerate_bracket_confirm(callback: CallbackQuery):
    """Подтверждение перегенерации существующей сетки."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM tournament_brackets
        WHERE tournament_id=? AND status='completed' AND COALESCE(is_bye, 0)=0
    """, (tournament_id,))
    played_matches = cur.fetchone()[0]
    conn.close()

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, перегенерировать",
              callback_data=f"confirm_regenerate_bracket_{tournament_id}")
    kb.button(text="❌ Нет",
              callback_data=f"admin_tournament_manage_{tournament_id}")
    kb.adjust(1)

    warning = "⚠️ Старые пары сетки будут удалены и созданы заново."
    if played_matches > 0:
        warning += f"\n⚠️ Уже завершено матчей: {played_matches}. Эти результаты будут сброшены."

    await callback.message.edit_text(
        f"Перегенерировать сетку турнира «{tournament['name']}»?\n\n{warning}",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_regenerate_bracket_"))
async def confirm_regenerate_bracket(callback: CallbackQuery, state: FSMContext):
    """Перегенерация сетки с предупреждением о сбросе уже сыгранных матчей."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return
    if tournament['status'] == 'finished':
        await callback.answer("Нельзя перегенерировать сетку завершённого турнира.", show_alert=True)
        return

    cleanup_result = clear_bracket_related_data(tournament_id)
    if not cleanup_result.get("ok"):
        await callback.answer("❌ Ошибка очистки старых данных сетки", show_alert=True)
        return

    success = generate_bracket(tournament_id)
    if not success:
        await callback.answer("❌ Ошибка при перегенерации сетки", show_alert=True)
        return

    sync_tournament_match_format_rules(tournament_id)
    request_site_sync(f"bracket_regenerated:{tournament_id}")
    await callback.answer("✅ Сетка перегенерирована.", show_alert=True)
    await callback.message.answer("Назначьте время и место для новых пар после перегенерации.")
    started = await start_schedule_wizard_for_tournament(
        callback,
        state,
        tournament_id=tournament_id,
        return_callback=f"admin_tournament_manage_{tournament_id}",
    )
    if not started:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Управление турниром",
                                  callback_data=f"admin_tournament_manage_{tournament_id}")],
            [InlineKeyboardButton(text="📊 Открыть сетку",
                                  callback_data=f"view_bracket_{tournament_id}")]
        ])
        await callback.message.edit_text("Сетка успешно перегенерирована.", reply_markup=kb)


@router.callback_query(F.data.startswith("view_bracket_refresh_"))
async def view_bracket_refresh(callback: CallbackQuery, state: FSMContext):
    """Обновление турнирной сетки (перегенерация PNG)."""
    # Парсим tournament_id из callback_data
    parts = callback.data.split("_")
    tournament_id = int(parts[3]) if len(parts) > 3 else None

    if not tournament_id:
        await callback.answer("❌ Ошибка: неверный ID турнира", show_alert=True)
        return

    # Вызываем view_bracket напрямую с тем же callback
    await view_bracket(callback, state=state, tournament_id=tournament_id)


@router.callback_query(F.data.startswith("view_bracket_"))
async def view_bracket(callback: CallbackQuery, state: FSMContext, tournament_id: int = None):
    """Просмотр турнирной сетки."""
    current_state = await state.get_state()
    if current_state and (
        "ManualMatchInput" in current_state
        or "BracketScheduleInput" in current_state
    ):
        await state.clear()

    if tournament_id is None:
        tournament_id = int(callback.data.split("_")[2]) if len(
            callback.data.split("_")) > 2 else None

    if not tournament_id:
        await callback.answer("❌ Ошибка: неверный ID турнира", show_alert=True)
        return

    tournament = get_tournament_by_id(tournament_id)

    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    matches = get_bracket_matches(tournament_id)

    if not matches:
        await callback.answer("Сетка ещё не сгенерирована", show_alert=True)
        return

    # Создаём папку для PNG (абсолютный путь)
    temp_dir = os.path.abspath("temp/brackets")
    os.makedirs(temp_dir, exist_ok=True)
    png_path = os.path.join(temp_dir, f"bracket_{tournament_id}.png")

    # Генерируем PNG
    from utils.bracket_visualizer import generate_bracket_png, generate_bracket_ascii
    png_result = generate_bracket_png(tournament_id, png_path)

    builder = InlineKeyboardBuilder()

    user_is_admin = can_manage_tournament(callback.from_user.id, tournament_id)

    if user_is_admin:
        # Сортируем матчи по round_number, match_number (как в PNG)
        sorted_matches = sorted(matches, key=lambda m: (
            m['round_number'], m['match_number']))

        # Группируем матчи по раундам для кнопок
        matches_by_round = {}
        for match in sorted_matches:
            round_num = match['round_number']
            if round_num not in matches_by_round:
                matches_by_round[round_num] = []
            matches_by_round[round_num].append(match)

        total_rounds = _get_total_main_rounds(matches)

        for round_num in sorted(matches_by_round.keys()):
            round_matches = matches_by_round[round_num]

            for i, match in enumerate(round_matches, 1):
                # Пропускаем матчи где нет команд (team1_id и team2_id не установлены)
                if not match['team1_id'] and not match['team2_id']:
                    continue

                round_name = _resolve_match_round_label(dict(match), total_rounds)

                # Показываем только матчи где есть хотя бы одна команда или статус completed
                if match['status'] == 'pending':
                    # Проверяем есть ли команды в матче
                    if not match['team1_name'] and not match['team2_name']:
                        continue

                    team1_name = match['team1_name'] if match['team1_name'] else '???'
                    team2_name = match['team2_name'] if match['team2_name'] else '???'
                    schedule_icon = "🕒" if (match["scheduled_at_utc"] and match["location"]) else "⏳"

                    builder.button(
                        text=f"{schedule_icon} {round_name} #{i}: {team1_name} vs {team2_name}",
                        callback_data=f"bracket_match_{match['id']}_{tournament_id}"
                    )
                elif match['status'] == 'completed':
                    # Уже завершённые матчи тоже показываем
                    team1_name = match['team1_name'] if match['team1_name'] else '???'
                    team2_name = match['team2_name'] if match['team2_name'] else '???'

                    # Добавляем [W] для победителя
                    if match['winner_id'] == match['team1_id']:
                        team1_name = f"[W] {team1_name}"
                    elif match['winner_id'] == match['team2_id']:
                        team2_name = f"[W] {team2_name}"

                    builder.button(
                        text=f"✅ {round_name} #{i}: {team1_name} vs {team2_name}",
                        callback_data=f"bracket_match_{match['id']}_{tournament_id}"
                    )

        semifinals = get_semifinal_matches(tournament_id)
        if len(semifinals) >= 2:
            all_completed = all(m['status'] in ('completed', 'bye')
                                for m in semifinals)
            if all_completed:
                third_place_exists = any(
                    _is_third_place_match(m) for m in matches)
                if not third_place_exists:
                    builder.button(
                        text="⚔️ Создать матч за 3-е место",
                        callback_data=f"create_third_place_{tournament_id}"
                    )

    builder.button(text="🔄 Обновить",
                   callback_data=f"view_bracket_refresh_{tournament_id}")
    builder.button(text="🔙 К турниру",
                   callback_data=f"tournament_{tournament_id}")

    if user_is_admin:
        builder.button(text="🔙 В управление турниром",
                       callback_data=f"admin_tournament_manage_{tournament_id}")

    builder.adjust(1)

    # Отправляем PNG картинку с кнопками
    if png_result and os.path.exists(png_path):
        from aiogram.types import FSInputFile
        try:
            # Отправляем как новое фото (не редактируем старое)
            await callback.message.answer_photo(
                photo=FSInputFile(png_path),
                caption=f"🏆 {tournament['name']}",
                reply_markup=builder.as_markup()
            )
            await callback.answer("✅ Сетка обновлена!")
        except Exception as e:
            logger.warning("Ошибка отправки фото сетки: %s", e)
            # Если не удалось отправить фото, показываем ASCII
            ascii_bracket = generate_bracket_ascii(tournament_id)
            await callback.message.answer(
                f"🏆 {tournament['name']}\n\n{ascii_bracket}",
                reply_markup=builder.as_markup()
            )
            await callback.answer()
    else:
        # Показываем ASCII версию
        ascii_bracket = generate_bracket_ascii(tournament_id)
        await callback.message.answer(
            f"🏆 {tournament['name']}\n\n{ascii_bracket}",
            reply_markup=builder.as_markup()
        )
        await callback.answer()


@router.callback_query(F.data.startswith("bracket_match_broadcast_"))
async def bracket_match_broadcast_start(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    match_id = int(parts[3])
    tournament_id = int(parts[4])
    if not can_manage_bracket_match(callback.from_user.id, match_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    payload, error = prepare_match_broadcast_payload(match_id)
    if not payload:
        await callback.answer(error or "Не удалось подготовить рассылку.", show_alert=True)
        return

    await state.clear()
    await state.set_state(TargetedBroadcast.text)
    await state.update_data(
        broadcast_scope="match",
        broadcast_target_id=match_id,
        broadcast_return_callback=f"bracket_match_{match_id}_{tournament_id}",
    )
    await callback.message.edit_text(
        "📢 Сообщение участникам матча\n\n"
        f"Матч: {payload['title']}\n"
        f"Получателей: {payload['recipient_count']}\n\n"
        "Введите текст сообщения. Бот добавит шапку матча автоматически.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="targeted_broadcast_cancel")]
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^bracket_match_\d+(?:_\d+)?$"))
async def bracket_match_menu(callback: CallbackQuery, state: FSMContext):
    """Карточка матча: ввод результата и редактирование расписания."""
    parts = callback.data.split("_")
    match_id = int(parts[2])
    tournament_id = int(parts[3]) if len(parts) > 3 else None
    if not can_manage_bracket_match(callback.from_user.id, match_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    match = get_bracket_match_by_id(match_id)
    if not match:
        await callback.answer("Матч не найден", show_alert=True)
        return
    match = dict(match)
    if not tournament_id:
        tournament_id = match.get("tournament_id")

    if not match.get("team1_id") or not match.get("team2_id"):
        await callback.answer("В этом матче еще нет пары команд.", show_alert=True)
        return
    if match.get("status") == "completed":
        await callback.answer("Этот матч уже завершён.", show_alert=True)
        return
    schedule_missing = _schedule_missing(match)
    match_time = format_utc_to_msk(match.get("scheduled_at_utc")) if match.get("scheduled_at_utc") else "не назначено"
    location = (match.get("location") or "не указано").strip() if match.get("location") else "не указано"
    round_name = _resolve_match_round_label(match)
    text = (
        "⚙️ Управление матчем\n\n"
        f"🏆 {match.get('tournament_name') or 'Турнир'}\n"
        f"📍 {round_name}\n"
        f"⚔️ {match.get('team1_name') or 'Команда 1'} vs {match.get('team2_name') or 'Команда 2'}\n\n"
        f"🕒 Время: {match_time} (МСК)\n"
        f"📌 Место: {location}"
    )
    if schedule_missing:
        text += "\n\nℹ️ Для обычного ввода результата сначала укажите время и место матча."
    schedule_locked = _schedule_locked_by_veto(match_id)
    if schedule_locked:
        text += "\n\n🔒 Время и место зафиксированы: pick/ban уже начался или завершен."
    builder = InlineKeyboardBuilder()
    if not schedule_missing:
        builder.button(text="✏️ Ввести результат", callback_data=f"manual_match_result_{match_id}_{tournament_id}")
    if _has_saved_bracket_map_results(match_id):
        builder.button(text="🧹 Очистить результаты", callback_data=f"manual_clear_saved_maps_{match_id}_{tournament_id}")
    builder.button(text="🚫 Тех.поражение", callback_data=f"manual_match_technical_{match_id}_{tournament_id}")
    builder.button(text="📢 Сообщение участникам матча", callback_data=f"bracket_match_broadcast_{match_id}_{tournament_id}")
    if not schedule_locked:
        builder.button(
            text="🗓 Назначить время/место" if schedule_missing else "🗓 Изменить время/место",
            callback_data=f"bracket_schedule_edit_{match_id}_{tournament_id}",
        )
    tournament = get_tournament_by_id(tournament_id)
    if tournament and tournament["sport"] == "CS2" and int(tournament["map_veto_enabled"] or 0) == 1:
        builder.button(text="🗺 Пик / бан карт", callback_data=f"veto_admin_open_{match_id}")
    builder.button(text="🔙 К сетке", callback_data=f"view_bracket_{tournament_id}")
    builder.adjust(1)
    await callback.message.answer(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("bracket_schedule_edit_"))
async def bracket_schedule_edit(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    match_id = int(parts[3])
    tournament_id = int(parts[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    if _schedule_locked_by_veto(match_id):
        await callback.answer("Нельзя менять время/место: pick/ban уже начался или завершен.", show_alert=True)
        return
    await start_schedule_wizard_for_tournament(
        callback,
        state,
        tournament_id=tournament_id,
        match_ids=[match_id],
        return_callback=f"view_bracket_{tournament_id}",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("create_third_place_"))
async def create_third_place_match_confirm(callback: CallbackQuery):
    """Подтверждение создания матча за 3-е место."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    check = can_create_third_place_match(tournament_id)
    if not check.get('ok'):
        reason_map = {
            'already_exists': 'Матч за 3-е место уже создан.',
            'not_enough_semifinals': 'Недостаточно полуфиналов для матча за 3-е место.',
            'semifinals_not_completed': 'Сначала завершите оба полуфинала.',
            'not_enough_losers': 'Невозможно определить двух проигравших полуфиналов.',
        }
        await callback.answer(reason_map.get(check.get('reason'), 'Пока нельзя создать матч за 3-е место.'), show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, создать",
              callback_data=f"confirm_third_place_{tournament_id}")
    kb.button(text="❌ Нет", callback_data=f"view_bracket_{tournament_id}")
    kb.adjust(1)

    await callback.message.edit_text(
        "Создать матч за 3-е место между проигравшими в полуфиналах?\n\n"
        "Победитель получит +3 очка в рейтинг.",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_third_place_"))
async def confirm_create_third_place(callback: CallbackQuery, state: FSMContext):
    """Создание матча за 3-е место."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    check = can_create_third_place_match(tournament_id)
    if not check.get('ok'):
        reason_map = {
            'already_exists': 'Матч за 3-е место уже создан.',
            'not_enough_semifinals': 'Недостаточно полуфиналов для матча за 3-е место.',
            'semifinals_not_completed': 'Сначала завершите оба полуфинала.',
            'not_enough_losers': 'Невозможно определить двух проигравших полуфиналов.',
        }
        await callback.answer(reason_map.get(check.get('reason'), 'Пока нельзя создать матч за 3-е место.'), show_alert=True)
        return

    losers = check['losers']
    create_third_place_bracket_match(tournament_id, losers[0], losers[1])

    await callback.answer("✅ Матч за 3-е место создан!", show_alert=True)
    started = await start_schedule_wizard_for_tournament(
        callback,
        state,
        tournament_id=tournament_id,
        return_callback=f"view_bracket_{tournament_id}",
    )
    if not started:
        await view_bracket(callback, state=state, tournament_id=tournament_id)


@router.callback_query(F.data.startswith("bracket_svg_"))
async def show_bracket_svg(callback: CallbackQuery):
    """Показ SVG версии сетки."""
    tournament_id = int(callback.data.split("_")[2])

    # Создаём папку для временных файлов
    import os
    temp_dir = "temp/brackets"
    os.makedirs(temp_dir, exist_ok=True)

    output_path = f"{temp_dir}/bracket_{tournament_id}.svg"

    # Генерируем SVG
    from utils.bracket_visualizer import generate_bracket_svg
    from aiogram.types import FSInputFile

    result = generate_bracket_svg(tournament_id, output_path)

    if result and os.path.exists(result):
        # Отправляем файл
        try:
            await callback.message.answer_document(
                document=FSInputFile(result),
                caption=f"🏆 Турнирная сетка: {get_tournament_by_id(tournament_id)['name']}"
            )
            await callback.answer("✅ SVG сгенерирован!")
        except Exception as e:
            logger.warning("Ошибка отправки SVG: %s", e)
            await callback.answer("❌ Ошибка при генерации SVG", show_alert=True)
    else:
        await callback.answer("❌ Ошибка при генерации SVG", show_alert=True)
