from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, MessageOriginChannel
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import get_connection
from aiogram import Bot
import json
from razryad_arena_utils import (
    is_admin, get_pending_applications, approve_application, reject_application, exclude_team_from_tournament,
    update_team_rating, get_all_users, search_users, get_user_by_id,
    update_user_role, toggle_user_ban,
    get_team_members, get_tournament_by_id, get_team_by_id,
    delete_tournament, get_all_sports,
    delete_team_admin, parse_russian_date, parse_russian_datetime,
    get_user, get_user_teams, get_team_application, get_approved_teams_count, get_all_users_count, search_users_count,
    get_team_members_count, get_team_max_members, get_team_settings, is_captain,
    get_sport_display_name, upsert_football_player_stat, upsert_basketball_player_stat,
    upsert_volleyball_player_stat, replace_volleyball_set_scores, map_sports_to_display,
    normalize_sport_name, allow_reapply_excluded_application, ensure_tournament_invite_token,
    get_active_user_telegram_ids, add_tournament_manager, can_manage_bracket_match, can_manage_tournament,
    get_tournament_manager_users, get_tournament_map_pool, is_tournament_creator_or_global_admin,
    get_tournament_teams,
    remove_tournament_manager, replace_tournament_map_pool, expand_stage_formats_to_round_rules,
    get_tournament_main_round_count, get_tournament_match_format_rules, get_tournament_stage_formats,
    replace_tournament_match_format_rules, ensure_tournament_team_roster, get_tournament_team_members,
    get_effective_tournament_captain_id, assign_tournament_team_captain,
    add_tournament_team_member, remove_tournament_team_member, replace_tournament_team_player,
    can_manage_tournament_team_roster, is_tournament_captain, get_user_by_username,
    create_tournament_roster_change_request, get_tournament_roster_change_request,
    accept_tournament_roster_change_request, decline_tournament_roster_change_request,
    update_tournament_roster_change_request_status,
    update_user_by_id, is_email_unique, update_user_steam_id,
    rename_team, set_team_city, update_team_fields,
    add_team_member_admin, remove_team_member_admin, block_team_member, unblock_team_member,
    get_team_member_blocks, is_team_member_blocked, update_team_max_members,
    set_team_open_status, set_team_notify_status, set_team_invite_join_mode, set_team_invite_enabled,
    get_admin_tournament_notifications_enabled, get_tournament_notification_override,
    set_admin_tournament_notifications_enabled, set_tournament_notification_override,
    build_tournament_date_lines,
)
from keyboards import (
    admin_menu_keyboard, back_to_main_keyboard,
    admin_rating_action_keyboard, admin_rating_entity_keyboard,
    admin_rating_entity_list_keyboard, admin_rating_format_picker_keyboard,
    admin_rating_scope_keyboard, admin_rating_sport_picker_keyboard, admin_rating_season_picker_keyboard,
    sports_choice_keyboard_single, sports_choice_keyboard
)
from datetime import datetime, timezone
import logging
from utils.cs2_maps import (
    CS2_MAPS,
    default_cs2_map_entries,
    get_cs2_map_name,
    parse_map_pool_text,
)
from utils.site_sync import request_site_sync
from utils.notifications import (
    get_registration_ended_action_key,
    prepare_match_broadcast_payload,
    prepare_tournament_broadcast_payload,
    send_custom_broadcast,
)
from utils.veto_service import (
    ADMIN_ACTION_SCOPE_REGISTRATION_ENDED,
    LAUNCH_ADMIN,
    LAUNCH_AUTO,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_READY,
    get_veto_status_label,
    list_tournament_veto_sessions,
    resolve_admin_action_messages,
    validate_veto_pool,
)
from handlers.states import AdminRatingAdjustment, AdminRatingChannelPublish, TargetedBroadcast, TournamentRosterEdit
from utils.rating_rules import (
    ENTITY_PLAYER,
    ENTITY_TEAM,
    FORMAT_GENERAL,
    SCOPE_OVERALL,
    SCOPE_SEASONAL,
    SOURCE_LEGACY_MATCH,
    get_format_options_for_sport,
    sport_supports_formats,
)
from utils.rating_service import (
    advance_to_next_rating_season,
    apply_manual_rating_adjustment,
    clear_rating_bucket,
    get_active_rating_season,
    list_rating_seasons,
    get_match_mvp_candidates,
    get_rating_leaderboard,
    get_rating_row,
    get_rating_season_by_id,
    get_tournament_mvp_candidates,
    replace_match_team_rating,
    set_match_mvp_override,
    set_tournament_mvp_override,
)
from utils.rating_channel_posts import parse_channel_target_text, publish_rating_channel_post, refresh_rating_channel_posts
from utils.steam_utils import parse_steam_link

logger = logging.getLogger(__name__)

MATCH_FORMAT_OPTIONS = [("bo1", "BO1"), ("bo3", "BO3"), ("bo5", "BO5")]
VETO_LAUNCH_OPTIONS = [
    (LAUNCH_ADMIN, "admin_start"),
    (LAUNCH_AUTO, "auto_start"),
]


def _is_cs2_sport(sport_name: str | None) -> bool:
    return normalize_sport_name(sport_name) == "CS2"


DATE_INPUT_HINT = (
    "в формате: день и сокращённое название месяца с точкой, например: 1 янв."
)
DATE_INPUT_MONTHS_HINT = (
    "Подсказка по месяцам:\n"
    "'янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'"
)


def _format_tournament_dates_for_text(tournament) -> str:
    return "\n".join(build_tournament_date_lines(tournament))


def _russian_date_order_key(value: str | None) -> tuple[int, int] | None:
    parsed = parse_russian_date((value or "").strip())
    if not parsed:
        return None
    day, month = parsed
    return month, day


def _is_optional_date_reset_text(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized in {"-", "нет"}


def _normalize_optional_date_input(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if _is_optional_date_reset_text(raw):
        return None
    return raw


def _normalize_map_pool_entries(raw_entries) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_entries or []:
        if isinstance(item, dict):
            map_key = str(item.get("map_key") or "").strip()
            map_name = str(item.get("map_name") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            map_key = str(item[0] or "").strip()
            map_name = str(item[1] or "").strip()
        else:
            map_key = str(item or "").strip()
            map_name = get_cs2_map_name(map_key)
        if not map_key or map_key in seen:
            continue
        seen.add(map_key)
        normalized.append({
            "map_key": map_key,
            "map_name": map_name or get_cs2_map_name(map_key),
        })
    return normalized


def _default_map_pool_entries() -> list[dict[str, str]]:
    return [{"map_key": map_key, "map_name": map_name} for map_key, map_name in default_cs2_map_entries()]


def _pool_entry_keys(raw_entries) -> list[str]:
    return [row["map_key"] for row in _normalize_map_pool_entries(raw_entries)]


def _map_pool_label(raw_entries) -> str:
    entries = _normalize_map_pool_entries(raw_entries)
    if not entries:
        return "не выбран"
    return ", ".join(row["map_name"] for row in entries)


def _build_match_format_keyboard(prefix: str, current: str | None = None, back_callback: str | None = None):
    builder = InlineKeyboardBuilder()
    current_value = (current or "").lower()
    for value, label in MATCH_FORMAT_OPTIONS:
        marker = "✅ " if current_value == value else ""
        builder.button(text=f"{marker}{label}", callback_data=f"{prefix}_{value}")
    if back_callback:
        builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def _format_stage_formats_text(stage_formats: dict) -> str:
    return (
        f"Ранние раунды: {(stage_formats['early_round_format'] or 'bo3').upper()}\n"
        f"Полуфинал: {(stage_formats['semifinal_format'] or 'bo3').upper()}\n"
        f"Финал: {(stage_formats['final_format'] or 'bo3').upper()}\n"
        f"Матч за 3-е место: {(stage_formats['semifinal_format'] or 'bo3').upper()} (наследует полуфинал)"
    )


def _expected_total_rounds_for_tournament_data(data: dict) -> int:
    team_count = max(int(data.get("max_teams") or 0), 2)
    rounds = 1
    slots = 2
    while slots < team_count:
        slots *= 2
        rounds += 1
    return rounds


def _round_rules_from_stage_formats(stage_formats: dict, total_rounds: int) -> list[tuple[int, str]]:
    return expand_stage_formats_to_round_rules(
        total_rounds,
        stage_formats["early_round_format"],
        stage_formats["semifinal_format"],
        stage_formats["final_format"],
    )


def _validate_veto_pool_for_stage_formats(stage_formats: dict, map_keys: list[str]) -> tuple[bool, str | None]:
    formats = {
        (stage_formats.get("early_round_format") or "bo3").lower(),
        (stage_formats.get("semifinal_format") or "bo3").lower(),
        (stage_formats.get("final_format") or "bo3").lower(),
    }
    if "bo5" in formats:
        return validate_veto_pool("bo5", map_keys)
    if "bo3" in formats:
        return validate_veto_pool("bo3", map_keys)
    return validate_veto_pool("bo1", map_keys)


def _save_tournament_stage_formats(tournament_id: int, stage_formats: dict):
    total_rounds = get_tournament_main_round_count(tournament_id)
    replace_tournament_match_format_rules(
        tournament_id,
        _round_rules_from_stage_formats(stage_formats, total_rounds),
    )
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tournaments SET match_format=? WHERE id=?",
        ((stage_formats["early_round_format"] or "bo3").lower(), tournament_id),
    )
    conn.commit()
    conn.close()


def _build_veto_toggle_keyboard(prefix: str, enabled: bool, back_callback: str | None = None):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"{'✅' if enabled else '⬜'} Включить map veto", callback_data=f"{prefix}_on")
    builder.button(text=f"{'✅' if not enabled else '⬜'} Выключить map veto", callback_data=f"{prefix}_off")
    if back_callback:
        builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def _build_launch_mode_keyboard(prefix: str, current: str | None = None, back_callback: str | None = None):
    builder = InlineKeyboardBuilder()
    current_value = (current or LAUNCH_ADMIN).strip()
    for value, label in VETO_LAUNCH_OPTIONS:
        marker = "✅ " if current_value == value else ""
        builder.button(text=f"{marker}{label}", callback_data=f"{prefix}_{value}")
    if back_callback:
        builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def _build_map_pool_keyboard(
    selected_entries,
    toggle_prefix: str,
    done_callback: str,
    back_callback: str,
):
    builder = InlineKeyboardBuilder()
    entries = _normalize_map_pool_entries(selected_entries)
    selected = {row["map_key"] for row in entries}
    allowed = {row["key"] for row in CS2_MAPS}
    for row in CS2_MAPS:
        marker = "✅" if row["key"] in selected else "⬜"
        builder.button(text=f"{marker} {row['name']}", callback_data=f"{toggle_prefix}_{row['key']}")
    custom_entries = [row for row in entries if row["map_key"] not in allowed]
    for row in custom_entries:
        builder.button(text=f"➖ {row['map_name']}", callback_data=f"{toggle_prefix}_{row['map_key']}")
    builder.button(text="✍️ Ввести вручную", callback_data=f"{toggle_prefix}_manual")
    builder.button(text="💾 Сохранить", callback_data=done_callback)
    builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(2)
    return builder.as_markup()


def _map_pool_prompt_text(raw_entries) -> str:
    entries = _normalize_map_pool_entries(raw_entries)
    return (
        "Выберите карты турнирного пула.\n"
        "Можно нажимать кнопки или отправить список карт сообщением: по одной на строку, через запятую или через ;.\n"
        "Допустимо от 1 до 10 уникальных карт.\n\n"
        f"Текущий пул: {_map_pool_label(entries)}"
    )


def _build_manager_label(user) -> str:
    username = user["username"] if "username" in user.keys() and user["username"] else None
    telegram_id = user["telegram_id"] if "telegram_id" in user.keys() else "?"
    first_name = user["first_name"] if "first_name" in user.keys() and user["first_name"] else "Пользователь"
    handle = f"@{username}" if username else f"id={telegram_id}"
    return f"{first_name} ({handle})"


def _notification_mode_label(mode: str) -> str:
    return {
        "inherit": "наследовать",
        "on": "включить",
        "off": "выключить",
    }.get((mode or "inherit").strip().lower(), "наследовать")


def _admin_notifications_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"{'✅' if enabled else '⬜'} Включены",
        callback_data="admin_notifications_global_on",
    )
    builder.button(
        text=f"{'⬜' if enabled else '✅'} Выключены",
        callback_data="admin_notifications_global_off",
    )
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def _tournament_notifications_keyboard(tournament_id: int, current_mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    modes = [
        ("inherit", "Наследовать"),
        ("on", "Включить"),
        ("off", "Выключить"),
    ]
    for mode_key, label in modes:
        marker = "✅" if current_mode == mode_key else "⬜"
        builder.button(
            text=f"{marker} {label}",
            callback_data=f"admin_tournament_notifications_set_{tournament_id}_{mode_key}",
        )
    builder.button(text="🔙 К турниру", callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


async def _render_tournament_notifications(callback: CallbackQuery, tournament_id: int) -> None:
    tournament = get_tournament_by_id(tournament_id)
    user = get_user(callback.from_user.id)
    if not tournament or not user:
        await callback.answer("Данные не найдены", show_alert=True)
        return
    current_mode = get_tournament_notification_override(tournament_id, int(user["id"]))
    global_enabled = get_admin_tournament_notifications_enabled(int(user["id"]))
    effective_text = "включены" if (current_mode == "on" or (current_mode == "inherit" and global_enabled)) else "выключены"
    await callback.message.edit_text(
        "🔔 Мои уведомления по турниру\n\n"
        f"Турнир: {tournament['name']}\n"
        f"Глобальная настройка: {'включены' if global_enabled else 'выключены'}\n"
        f"Режим для этого турнира: {_notification_mode_label(current_mode)}\n"
        f"Итог: {effective_text}",
        reply_markup=_tournament_notifications_keyboard(tournament_id, current_mode),
    )


def _has_in_progress_veto_sessions(tournament_id: int) -> bool:
    return bool(list_tournament_veto_sessions(tournament_id, [STATUS_IN_PROGRESS]))


def _build_veto_overview(tournament_id: int) -> str:
    sessions = list_tournament_veto_sessions(tournament_id)
    counters = {
        STATUS_READY: 0,
        STATUS_IN_PROGRESS: 0,
        STATUS_COMPLETED: 0,
        STATUS_CANCELLED: 0,
    }
    for session in sessions:
        status = session.get("status")
        if status in counters:
            counters[status] += 1
    return (
        f"готовы: {counters[STATUS_READY]}, "
        f"идут: {counters[STATUS_IN_PROGRESS]}, "
        f"завершены: {counters[STATUS_COMPLETED]}, "
        f"отменены: {counters[STATUS_CANCELLED]}"
    )


def _targeted_broadcast_keyboard(confirm_callback: str, cancel_callback: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data=confirm_callback)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_callback)],
    ])


def _targeted_broadcast_return_keyboard(return_callback: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=return_callback)],
    ])


def _can_manage_targeted_broadcast(telegram_id: int, scope: str, target_id: int) -> bool:
    if scope == "match":
        return can_manage_bracket_match(telegram_id, target_id)
    if scope == "tournament":
        return can_manage_tournament(telegram_id, target_id)
    return False


def _resolve_targeted_broadcast_payload(scope: str, target_id: int, body_text: str = ""):
    if scope == "match":
        return prepare_match_broadcast_payload(target_id, body_text)
    if scope == "tournament":
        return prepare_tournament_broadcast_payload(target_id, body_text)
    return None, "Неизвестный тип рассылки."

async def send_tournament_info(bot: Bot, chat_id: int, tournament_id: int, user_id: int):
    """Отправляет актуальную карточку турнира."""
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await bot.send_message(chat_id, "Турнир не найден.")
        return

    user = get_user(user_id)
    teams = get_user_teams(user['id']) if user else []
    approved_count = get_approved_teams_count(tournament_id)

    # Проверяем, может ли пользователь подать заявку
    can_apply = False
    if user and tournament['status'] == 'registration':
        for team in teams:
            if normalize_sport_name(team['sport']) == normalize_sport_name(tournament['sport']) and is_captain(user['id'], team['id']):
                status = get_team_application(tournament_id, team['id'])
                if status is None and approved_count < tournament['max_teams']:
                    can_apply = True
                    break

    status_map = {
        'registration': 'Регистрация',
        'active': 'Идёт',
        'finished': 'Завершён'
    }
    status_display = status_map.get(tournament['status'], tournament['status'])
    invite_token = ensure_tournament_invite_token(tournament_id, regenerate=False)
    bot_username = ""
    try:
        me = await bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""
    invite_url = f"https://t.me/{bot_username}?start=tournament_invite_{invite_token}" if (invite_token and bot_username) else "недоступно"

    # Возрастные ограничения
    age_text = ""
    if tournament['min_age'] is not None and tournament['max_age'] is not None:
        if tournament['min_age'] == 0 and tournament['max_age'] == 100:
            age_text = "Без ограничений"
        else:
            age_text = f"{tournament['min_age']}–{tournament['max_age']} лет"
    else:
        age_text = "Не указан"

    text = f"""
🏆 {tournament['name']}
Вид спорта: {get_sport_display_name(tournament['sport'])}
Требуемый размер команды: {tournament['required_team_size']} чел.
Город: {tournament['city']}
Возраст: {age_text}
{_format_tournament_dates_for_text(tournament)}
Макс. команд: {tournament['max_teams']}
Статус: {status_display}
Инвайт-ссылка: {invite_url}
"""
    if tournament['description'] and tournament['description'] != 'нет':
        text += f"\n📝 Описание: {tournament['description']}"

    builder = InlineKeyboardBuilder()
    if can_apply:
        builder.button(text="📝 Подать заявку",
                       callback_data=f"apply_{tournament_id}")
    builder.button(text="📋 Список команд",
                   callback_data=f"tournament_teams_{tournament_id}")

    # Кнопка просмотра сетки (если сгенерирована)
    if tournament['bracket_generated']:
        builder.button(text="📊 Управление сеткой",
                       callback_data=f"view_bracket_{tournament_id}")

    # Кнопка управления турниром (только админ)
    if user and can_manage_tournament(user['telegram_id'], tournament_id):
        builder.button(text="⚙️ Управление турниром",
                       callback_data=f"admin_tournament_manage_{tournament_id}")

    builder.button(
        text="🔙 Назад", callback_data=f"tournament_sport_{tournament['sport']}")
    builder.adjust(1)

    await bot.send_message(chat_id, text, reply_markup=builder.as_markup())
router = Router()
PAGE_SIZE = 10


def _build_team_roster_for_notification(members: list[dict]) -> str:
    if not members:
        return "—"

    lines = []
    for idx, member in enumerate(members, 1):
        username = member['username'] if 'username' in member.keys() and member['username'] else None
        first_name = member['first_name'] if 'first_name' in member.keys() and member['first_name'] else "Участник"
        username_text = f"@{username}" if username else "без username"
        lines.append(f"{idx}. {first_name} ({username_text})")
    return "\n".join(lines)


async def _notify_tournament_application_status(
    bot: Bot,
    tournament_id: int,
    team_id: int,
    action: str,
):
    tournament = get_tournament_by_id(tournament_id)
    team = get_team_by_id(team_id)
    members = get_team_members(team_id)
    if not tournament or not team:
        return

    action_text = {
        "approved": "✅ Заявка на турнир принята",
        "rejected": "❌ Заявка на турнир отклонена",
        "excluded": "🚫 Команда исключена из турнира",
    }.get(action, "ℹ️ Статус заявки на турнир изменён")

    text = (
        f"{action_text}\n\n"
        f"Турнир: {tournament['name']}\n"
        f"Команда: {team['name']}\n\n"
        "Состав:\n"
        f"{_build_team_roster_for_notification(members)}"
    )

    captain = get_user_by_id(team['captain_id']) if team['captain_id'] else None
    captain_chat_id = captain['telegram_id'] if captain and 'telegram_id' in captain.keys() else None
    if not captain_chat_id:
        return

    try:
        await bot.send_message(int(captain_chat_id), text)
    except Exception as exc:
        logger.warning(
            "Не удалось отправить уведомление капитану team_id=%s tournament_id=%s captain_chat_id=%s: %s",
            team_id, tournament_id, captain_chat_id, exc
        )

def _build_team_members_block(members):
    if not members:
        return "—"

    lines = []
    for idx, member in enumerate(members, 1):
        username = f"@{member['username']}" if member['username'] else "без username"
        age = member['age'] if member['age'] is not None else "не указан"
        steam = member['steam_id'] if member['steam_id'] else "❌ не указан"
        lines.append(
            f"{idx}. {member['first_name']} ({username}) | возраст: {age} | steam: {steam}")
    return "\n".join(lines)


def _build_roster_member_label(member: dict, *, is_captain_member: bool = False) -> str:
    username_value = _row_value(member, "username")
    username = f"@{username_value}" if username_value else "без username"
    prefix = "👑 " if is_captain_member else ""
    first_name = _row_value(member, "first_name", "Участник")
    return f"{prefix}{first_name or 'Участник'} ({username})"

def _build_tournament_compliance_block(tournament: dict, members: list[dict]) -> tuple[str, bool]:
    """Возвращает текст проверки и флаг полного соответствия."""
    checks = []
    has_errors = False

    required_size = tournament['required_team_size'] or 0
    actual_size = len(members)
    if actual_size == required_size:
        checks.append(f"✅ Размер состава: {actual_size}/{required_size}")
    else:
        has_errors = True
        checks.append(
            f"❌ Размер состава: {actual_size}/{required_size} (несоответствие)")

    min_age = tournament['min_age']
    max_age = tournament['max_age']
    if min_age is not None and max_age is not None:
        age_issues = []
        for member in members:
            age = member['age']
            username = f"@{member['username']}" if member['username'] else "без username"
            if age is None:
                age_issues.append(
                    f"{member['first_name']} ({username}) — возраст не указан")
            elif age < min_age or age > max_age:
                age_issues.append(
                    f"{member['first_name']} ({username}) — {age} лет (требуется {min_age}-{max_age})")

        if age_issues:
            has_errors = True
            checks.append("❌ Возрастные ограничения:\n" +
                          "\n".join(f"   - {item}" for item in age_issues))
        else:
            checks.append(f"✅ Возрастные ограничения: {min_age}-{max_age}")

    if normalize_sport_name(tournament['sport']) == 'CS2':
        steam_issues = []
        for member in members:
            if not member['steam_id']:
                username = f"@{member['username']}" if member['username'] else "без username"
                steam_issues.append(f"{member['first_name']} ({username})")
        if steam_issues:
            has_errors = True
            checks.append("❌ Steam обязателен для CS2:\n" +
                          "\n".join(f"   - {item}" for item in steam_issues))
        else:
            checks.append("✅ Steam профили указаны у всех участников")

    if not has_errors:
        checks.append("✅ Команда соответствует требованиям турнира")

    return "\n".join(checks), (not has_errors)

def _build_admin_team_card_text(team_id: int, tournament: dict | None = None, app_status: str | None = None) -> str | None:
    team = get_team_by_id(team_id)
    if not team:
        return None

    if tournament:
        ensure_tournament_team_roster(tournament["id"], team_id)
        members = list(get_tournament_team_members(tournament["id"], team_id))
        captain_id = get_effective_tournament_captain_id(tournament["id"], team_id)
    else:
        members = list(get_team_members(team_id))
        captain_id = team["captain_id"]
    captain = get_user_by_id(captain_id) if captain_id else None
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Неизвестно"
    members_count = len(members) if tournament else get_team_members_count(team_id)
    max_members = get_team_max_members(team_id)
    settings = get_team_settings(team_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE team_id=?", (team_id,))
    tournaments_total = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE team_id=? AND status='pending'", (team_id,))
    tournaments_pending = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM team_invites WHERE team_id=? AND type='request' AND status='pending'", (team_id,))
    join_requests_pending = cur.fetchone()[0]
    conn.close()
    blocked_members = get_team_member_blocks(team_id)

    created_at = team['created_at'] if 'created_at' in team.keys(
    ) and team['created_at'] else "н/д"

    text = (
        "⚙️ Карточка команды (админ)\n\n"
        f"Название: {team['name']}\n"
        f"Вид спорта: {get_sport_display_name(team['sport'])}\n"
        f"Город: {_display_optional_text(team['city'])}\n"
        f"{'Турнирный капитан' if tournament else 'Капитан'}: {captain_name}\n"
        f"Участники: {members_count}/{max_members}\n"
        f"Набор в команду: {'🔓 открыт' if settings['is_open'] else '🔒 закрыт'}\n"
        f"Уведомления капитану: {'🔔 включены' if settings['notify'] else '🔕 выключены'}\n"
        f"Создана: {created_at}\n"
        f"Активность: заявок в турниры={tournaments_total}, pending турниров={tournaments_pending}, pending вступлений={join_requests_pending}, блок-лист={len(blocked_members)}\n"
    )

    if app_status:
        status_map = {'pending': '⏳ На рассмотрении',
                      'approved': '✅ Одобрено', 'rejected': '❌ Отклонено'}
        text += f"Статус заявки: {status_map.get(app_status, app_status)}\n"

    text += "\nСостав:\n" + _build_team_members_block(members)

    if tournament:
        compliance_text, _ = _build_tournament_compliance_block(
            tournament, members)
        text += (
            "\n\n"
            f"🏆 Турнир: {tournament['name']}\n"
            f"Требования: размер={tournament['required_team_size']}, возраст={tournament['min_age']}-{tournament['max_age']}, спорт={get_sport_display_name(tournament['sport'])}\n\n"
            "Проверка соответствия:\n"
            f"{compliance_text}"
        )

    return text

def _get_tournament_application_details(app_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.tournament_id, a.team_id, a.status,
               t.name as team_name, tm.name as tournament_name
        FROM tournament_applications a
        JOIN teams t ON a.team_id = t.id
        JOIN tournaments tm ON a.tournament_id = tm.id
        WHERE a.id=?
    """, (app_id,))
    app = cur.fetchone()
    conn.close()
    return app


def _get_tournament_team_application(tournament_id: int, team_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM tournament_applications
        WHERE tournament_id=? AND team_id=?
    """, (tournament_id, team_id))
    row = cur.fetchone()
    conn.close()
    return row


def _build_tournament_roster_action_keyboard(
    tournament_id: int,
    team_id: int,
    *,
    can_replace: bool,
    can_assign_captain: bool,
    is_finished: bool,
    refresh_callback: str,
    back_callback: str,
):
    builder = InlineKeyboardBuilder()
    if can_replace and not is_finished:
        builder.button(text="🔁 Заменить игрока", callback_data=f"tournament_roster_replace_{tournament_id}_{team_id}")
    if can_assign_captain and not is_finished:
        builder.button(text="👑 Назначить турнирного капитана", callback_data=f"tournament_roster_captain_{tournament_id}_{team_id}")
    builder.button(text="🔄 Обновить", callback_data=refresh_callback)
    builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


async def _show_tournament_roster_screen(message, tournament_id: int, team_id: int, *, viewer_telegram_id: int, refresh_callback: str, back_callback: str, notice: str | None = None):
    tournament = get_tournament_by_id(tournament_id)
    team = get_team_by_id(team_id)
    app = _get_tournament_team_application(tournament_id, team_id)
    if not tournament or not team or not app:
        await message.edit_text(
            "Команда или турнир не найдены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)]])
        )
        return

    ensure_tournament_team_roster(tournament_id, team_id)
    members = list(get_tournament_team_members(tournament_id, team_id))
    captain_id = get_effective_tournament_captain_id(tournament_id, team_id)
    captain = get_user_by_id(captain_id) if captain_id else None
    captain_name = f"{captain['first_name']} (@{captain['username']})" if captain else "Не назначен"
    can_edit = can_manage_tournament_team_roster(viewer_telegram_id, tournament_id, team_id)
    is_manager = can_manage_tournament(viewer_telegram_id, tournament_id)
    is_team_captain = is_tournament_captain(viewer_telegram_id, tournament_id, team_id)
    can_assign_captain = is_manager
    replacements_enabled = int(tournament["replacements_enabled"] or 0) == 1
    can_replace = can_edit and replacements_enabled
    is_finished = tournament["status"] == "finished"

    lines = [
        "🔁 Замены состава",
        "",
        f"🏆 Турнир: {tournament['name']}",
        f"👥 Команда: {team['name']}",
        f"Турнирный капитан: {captain_name}",
        f"Игроков в составе: {len(members)}/{tournament['required_team_size']}",
        f"Замены: {'разрешены' if replacements_enabled else 'запрещены'}",
    ]
    if is_manager:
        lines.insert(4, f"Статус заявки: {app['status']}")
    if is_finished:
        lines.extend(["", "ℹ️ Турнир завершен. Доступен только просмотр состава."])
    elif is_manager:
        lines.extend(["", "ℹ️ Админ или ответственный может назначать турнирного капитана и управлять заменами."])
    elif is_team_captain:
        lines.extend(["", "ℹ️ Вы турнирный капитан этой команды и можете запрашивать замены состава."])
    elif not can_edit:
        lines.extend(["", "ℹ️ Изменять состав может админ/ответственный или текущий турнирный капитан."])
    elif not replacements_enabled:
        lines.extend(["", "ℹ️ В этом турнире замены отключены настройками турнира."])
    lines.extend(["", "Состав:"])
    if members:
        for idx, member in enumerate(members, 1):
            lines.append(f"{idx}. {_build_roster_member_label(dict(member), is_captain_member=int(member['id']) == captain_id)}")
    else:
        lines.append("—")
    if notice:
        lines = [notice, ""] + lines

    await message.edit_text(
        "\n".join(lines),
        reply_markup=_build_tournament_roster_action_keyboard(
            tournament_id,
            team_id,
            can_replace=can_replace,
            can_assign_captain=can_assign_captain,
            is_finished=is_finished,
            refresh_callback=refresh_callback,
            back_callback=back_callback,
        ),
    )

async def _render_admin_team_card_by_state(target, state: FSMContext):
    data = await state.get_data()
    team_id = data.get("admin_team_id")
    if not team_id:
        return
    text = _build_admin_team_card_text(int(team_id))
    if not text:
        return
    markup = _admin_team_manage_card_keyboard(int(team_id))
    if isinstance(target, CallbackQuery):
        await _safe_edit_admin_message(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _show_admin_team_members_screen(target, team_id: int):
    members = list(get_team_members(team_id))
    text = "👥 Управление составом команды\n\nВыберите участника:" if members else "👥 В команде пока нет участников."
    markup = _admin_team_members_keyboard(team_id, members)
    if isinstance(target, CallbackQuery):
        await _safe_edit_admin_message(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _show_admin_team_blocks_screen(target, team_id: int):
    blocks = list(get_team_member_blocks(team_id))
    text = "🚫 Заблокированные участники" + ("" if blocks else "\n\nСписок пуст.")
    markup = _admin_team_blocks_keyboard(team_id, blocks)
    if isinstance(target, CallbackQuery):
        await _safe_edit_admin_message(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _show_admin_team_edit_menu(target, team_id: int):
    team = get_team_by_id(team_id)
    if not team:
        return
    settings = get_team_settings(team_id)
    invite_mode = (team['invite_join_mode'] or 'request').strip().lower() if 'invite_join_mode' in team.keys() else 'request'
    invite_enabled = int((team['invite_enabled'] if 'invite_enabled' in team.keys() else 1) or 0) == 1
    text = (
        "✏️ Редактирование команды\n\n"
        f"Команда: {team['name']}\n"
        f"Город: {_display_optional_text(team['city'])}\n"
        f"Лимит: {get_team_max_members(team_id)}\n"
        f"Набор: {'открыт' if settings['is_open'] else 'закрыт'}\n"
        f"Уведомления: {'включены' if settings['notify'] else 'выключены'}\n"
        f"Режим ссылки: {'сразу вступление' if invite_mode == 'direct' else 'по заявке'}\n"
        f"Ссылка: {'включена' if invite_enabled else 'выключена'}\n\n"
        "Выберите действие:"
    )
    markup = _admin_team_edit_menu_keyboard(team_id)
    if isinstance(target, CallbackQuery):
        await _safe_edit_admin_message(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)

def _get_tournament_capacity_info(tournament_id: int) -> tuple[int, int | None]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT max_teams FROM tournaments WHERE id=?",
                (tournament_id,))
    tournament = cur.fetchone()
    if not tournament:
        conn.close()
        return 0, None

    cur.execute(
        "SELECT COUNT(*) FROM tournament_applications WHERE tournament_id=? AND status='approved'",
        (tournament_id,)
    )
    approved_count = cur.fetchone()[0]
    conn.close()
    return approved_count, tournament['max_teams']

def _get_overbooked_tournaments_map(tournament_ids: list[int]) -> dict[int, dict]:
    if not tournament_ids:
        return {}

    placeholders = ",".join(["?"] * len(tournament_ids))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            t.id,
            t.name,
            t.max_teams,
            COALESCE(SUM(CASE WHEN a.status='approved' THEN 1 ELSE 0 END), 0) AS approved_count
        FROM tournaments t
        LEFT JOIN tournament_applications a ON a.tournament_id = t.id
        WHERE t.id IN ({placeholders})
        GROUP BY t.id, t.name, t.max_teams
    """, tournament_ids)
    rows = cur.fetchall()
    conn.close()

    overbooked = {}
    for row in rows:
        max_teams = row['max_teams']
        approved_count = row['approved_count']
        if max_teams is not None and max_teams > 0 and approved_count > max_teams:
            overbooked[row['id']] = {
                "name": row['name'],
                "approved": approved_count,
                "max_teams": max_teams
            }
    return overbooked

TOURNAMENT_APPLICATION_SECTIONS = {
    "approved": {
        "title": "✅ Участники турнира",
        "status": "approved",
        "empty": "В этом турнире пока нет одобренных команд.",
        "description": "Здесь команды, которые уже допущены к турниру.",
        "action_hint": "Откройте карточку команды, чтобы посмотреть состав и при необходимости исключить её из турнира.",
        "order": "a.updated_at DESC, a.id DESC",
    },
    "pending": {
        "title": "⏳ Новые заявки",
        "status": "pending",
        "empty": "Новых заявок сейчас нет.",
        "description": "Здесь заявки, которые ждут решения администратора.",
        "action_hint": "Откройте карточку команды, чтобы проверить соответствие турниру и принять решение.",
        "order": "a.applied_at DESC, a.id DESC",
    },
    "excluded": {
        "title": "🚫 Исключенные",
        "status": "excluded",
        "empty": "Исключенных команд нет.",
        "description": "Здесь команды, исключенные из турнира после одобрения.",
        "action_hint": "Откройте карточку команды, если нужно вернуть ей право на повторную заявку.",
        "order": "a.updated_at DESC, a.id DESC",
    },
    "rejected": {
        "title": "📚 Отклоненные",
        "status": "rejected",
        "empty": "Отклоненных заявок пока нет.",
        "description": "Это архив отклоненных заявок без рабочих действий.",
        "action_hint": "Раздел только для просмотра истории решений.",
        "order": "a.updated_at DESC, a.id DESC",
    },
}


def _get_tournament_application_status_counts(tournament_id: int) -> dict[str, int]:
    counts = {key: 0 for key in TOURNAMENT_APPLICATION_SECTIONS}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT status, COUNT(*) AS total
        FROM tournament_applications
        WHERE tournament_id=?
        GROUP BY status
    """, (tournament_id,))
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        status = row["status"]
        if status in counts:
            counts[status] = row["total"]
    return counts


def _section_label(section: str) -> str:
    config = TOURNAMENT_APPLICATION_SECTIONS.get(section, {})
    return config.get("title", section)


def _section_back_callback(section: str, tournament_id: int, offset: int) -> str:
    return f"admin_tournament_section_page_{section}_{tournament_id}_{offset}"


def _build_tournament_hub_keyboard(tournament_id: int, counts: dict[str, int]):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"✅ Участники ({counts['approved']})",
        callback_data=f"admin_tournament_section_approved_{tournament_id}"
    )
    builder.button(
        text=f"⏳ Новые заявки ({counts['pending']})",
        callback_data=f"admin_tournament_section_pending_{tournament_id}"
    )
    builder.button(
        text=f"🚫 Исключенные ({counts['excluded']})",
        callback_data=f"admin_tournament_section_excluded_{tournament_id}"
    )
    builder.button(
        text=f"📚 Отклоненные ({counts['rejected']})",
        callback_data=f"admin_tournament_section_rejected_{tournament_id}"
    )
    builder.button(text="🔙 Назад к турниру",
                   callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()

def _build_approve_error_text(result: dict) -> str:
    reason = result.get("reason")
    if reason == "limit_reached":
        approved = result.get("approved")
        max_teams = result.get("max_teams")
        if approved is not None and max_teams is not None:
            return f"Лимит команд уже достигнут ({approved}/{max_teams}), заявка оставлена в pending."
        return "Лимит команд уже достигнут, заявка оставлена в pending."
    if reason == "not_registration":
        return "Одобрение доступно только на этапе регистрации."
    if reason == "already_processed":
        return "Заявка уже обработана другим админом."
    if reason == "member_conflict":
        conflicts = result.get("conflicts") or []
        if not conflicts:
            return "Участники команды уже есть в другой одобренной команде этого турнира."
        lines = ["Нельзя одобрить заявку: участники уже есть в другой одобренной команде:"]
        for conflict in conflicts[:5]:
            username = f"@{conflict['username']}" if conflict.get("username") else "без username"
            lines.append(
                f"- {conflict.get('first_name', 'Участник')} ({username}) -> «{conflict['conflict_team_name']}»"
            )
        return "\n".join(lines)
    if reason == "not_found":
        return "Заявка не найдена."
    return "Не удалось одобрить заявку. Попробуйте ещё раз."

def _build_exclude_error_text(result: dict) -> str:
    reason = result.get("reason")
    if reason == "not_registration":
        return "Исключение доступно только на этапе регистрации и до генерации сетки."
    if reason == "not_approved":
        return "Исключить можно только уже одобренную команду."
    if reason == "already_processed":
        return "Заявка уже была изменена другим админом."
    if reason == "not_found":
        return "Заявка не найдена."
    return "Не удалось исключить команду. Попробуйте ещё раз."

# ---------- Создание турнира ----------

class CreateTournament(StatesGroup):
    name = State()
    sport = State()
    city = State()
    registration_start_date = State()
    registration_end_date = State()
    event_start_date = State()
    event_end_date = State()
    max_teams = State()
    required_team_size = State()
    min_age = State()        # новое
    max_age = State()
    early_round_format = State()
    semifinal_format = State()
    final_format = State()
    map_veto_enabled = State()
    veto_launch_mode = State()
    map_pool = State()
    description = State()

@router.callback_query(F.data == "admin_create_tournament")
async def admin_create_tournament_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(CreateTournament.name)
    await callback.message.edit_text("Введите название турнира:")

@router.message(CreateTournament.name)
async def create_tournament_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(CreateTournament.sport)
    sports = get_all_sports()
    await message.answer("Выберите вид спорта:", reply_markup=sports_choice_keyboard_single(sports))

@router.callback_query(CreateTournament.sport, F.data.startswith("admin_tourn_sport_"))
async def create_tournament_sport_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    sport_name = callback.data.replace("admin_tourn_sport_", "")
    await state.update_data(sport=sport_name)
    await state.set_state(CreateTournament.city)
    await callback.message.edit_text("Введите город проведения:")

@router.message(CreateTournament.city)
async def create_tournament_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text)
    await state.set_state(CreateTournament.registration_start_date)
    await message.answer(f"Введите дату начала регистрации ({DATE_INPUT_HINT}):")


@router.message(CreateTournament.registration_start_date)
async def create_tournament_registration_start(message: Message, state: FSMContext):
    date_str = message.text.strip()
    if parse_russian_date(date_str) is None:
        await message.answer(
            "❌ Неверный формат. Введите дату регистрации "
            f"({DATE_INPUT_HINT}).\n{DATE_INPUT_MONTHS_HINT}"
        )
        return
    await state.update_data(registration_start_date=date_str)
    await state.set_state(CreateTournament.registration_end_date)
    await message.answer(f"Введите дату конца регистрации ({DATE_INPUT_HINT}):")


@router.message(CreateTournament.registration_end_date)
async def create_tournament_registration_end(message: Message, state: FSMContext):
    date_str = message.text.strip()
    if parse_russian_date(date_str) is None:
        await message.answer(
            "❌ Неверный формат. Введите дату конца регистрации "
            f"({DATE_INPUT_HINT}).\n{DATE_INPUT_MONTHS_HINT}"
        )
        return
    data = await state.get_data()
    start_key = _russian_date_order_key(data.get("registration_start_date"))
    end_key = _russian_date_order_key(date_str)
    if start_key and end_key and end_key < start_key:
        await message.answer("❌ Конец регистрации не может быть раньше начала регистрации.")
        return
    await state.update_data(registration_end_date=date_str)
    await state.set_state(CreateTournament.event_start_date)
    await message.answer(
        f"Введите дату начала проведения турнира ({DATE_INPUT_HINT})\n"
        "Если дата пока неизвестна, отправьте `-` или `нет`."
    )


@router.message(CreateTournament.event_start_date)
async def create_tournament_event_start(message: Message, state: FSMContext):
    raw_value = message.text.strip()
    date_str = _normalize_optional_date_input(raw_value)
    if date_str and parse_russian_date(date_str) is None:
        await message.answer(
            "❌ Неверный формат. Введите дату проведения "
            f"({DATE_INPUT_HINT}) или отправьте `-` / `нет`.\n{DATE_INPUT_MONTHS_HINT}"
        )
        return
    await state.update_data(start_date=date_str)
    await state.set_state(CreateTournament.event_end_date)
    await message.answer(
        f"Введите дату конца проведения турнира ({DATE_INPUT_HINT})\n"
        "Если дата пока неизвестна, отправьте `-` или `нет`."
    )


@router.message(CreateTournament.event_end_date)
async def create_tournament_event_end(message: Message, state: FSMContext):
    raw_value = message.text.strip()
    date_str = _normalize_optional_date_input(raw_value)
    if date_str and parse_russian_date(date_str) is None:
        await message.answer(
            "❌ Неверный формат. Введите дату конца проведения "
            f"({DATE_INPUT_HINT}) или отправьте `-` / `нет`.\n{DATE_INPUT_MONTHS_HINT}"
        )
        return
    data = await state.get_data()
    start_key = _russian_date_order_key(data.get("start_date"))
    end_key = _russian_date_order_key(date_str)
    if start_key and end_key and end_key < start_key:
        await message.answer("❌ Конец проведения не может быть раньше начала проведения.")
        return
    await state.update_data(end_date=date_str)
    await state.set_state(CreateTournament.max_teams)
    await message.answer("Введите максимальное количество команд (число):")

@router.message(CreateTournament.max_teams)
async def create_tournament_max(message: Message, state: FSMContext):
    try:
        max_teams = int(message.text)
    except ValueError:
        await message.answer("Введите число!")
        return
    await state.update_data(max_teams=max_teams)
    await state.set_state(CreateTournament.required_team_size)
    await message.answer("Введите требуемое количество игроков в команде для участия в турнире (от 1 до 10):")

@router.message(CreateTournament.required_team_size)
async def create_tournament_required_size(message: Message, state: FSMContext):
    try:
        size = int(message.text)
        if size < 1 or size > 10:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 1 до 10.")
        return
    await state.update_data(required_team_size=size)
    await state.set_state(CreateTournament.min_age)
    await message.answer("Введите минимальный возраст участников (или 0, если нет ограничений):")

@router.message(CreateTournament.min_age)
async def create_tournament_min_age(message: Message, state: FSMContext):
    try:
        min_age = int(message.text)
        if min_age < 0 or min_age > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 0 до 100.")
        return
    await state.update_data(min_age=min_age)
    await state.set_state(CreateTournament.max_age)
    await message.answer("Введите максимальный возраст участников (или 100, если нет ограничений):")

@router.message(CreateTournament.max_age)
async def create_tournament_max_age(message: Message, state: FSMContext):
    try:
        max_age = int(message.text)
        if max_age < 0 or max_age > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 0 до 100.")
        return
    data = await state.get_data()
    min_age = data.get('min_age')
    if min_age is not None and min_age > max_age:
        await message.answer("❌ Минимальный возраст не может быть больше максимального.")
        return
    await state.update_data(max_age=max_age)
    await state.set_state(CreateTournament.early_round_format)
    await message.answer(
        "Выберите формат ранних раундов:",
        reply_markup=_build_match_format_keyboard("create_tournament_early_format"),
    )


@router.callback_query(CreateTournament.early_round_format, F.data.startswith("create_tournament_early_format_"))
async def create_tournament_early_format(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    match_format = callback.data.replace("create_tournament_early_format_", "")
    await state.update_data(early_round_format=match_format)
    await state.set_state(CreateTournament.semifinal_format)
    await callback.message.edit_text(
        "Выберите формат полуфинала:",
        reply_markup=_build_match_format_keyboard("create_tournament_semifinal_format", match_format),
    )


@router.callback_query(CreateTournament.semifinal_format, F.data.startswith("create_tournament_semifinal_format_"))
async def create_tournament_semifinal_format(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    match_format = callback.data.replace("create_tournament_semifinal_format_", "")
    await state.update_data(semifinal_format=match_format)
    await state.set_state(CreateTournament.final_format)
    await callback.message.edit_text(
        "Выберите формат финала:",
        reply_markup=_build_match_format_keyboard("create_tournament_final_format", match_format),
    )


@router.callback_query(CreateTournament.final_format, F.data.startswith("create_tournament_final_format_"))
async def create_tournament_final_format(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    match_format = callback.data.replace("create_tournament_final_format_", "")
    data = await state.get_data()
    stage_formats = {
        "early_round_format": data.get("early_round_format", "bo1"),
        "semifinal_format": data.get("semifinal_format", "bo3"),
        "final_format": match_format,
    }
    await state.update_data(
        final_format=match_format,
        stage_formats=stage_formats,
        match_format=stage_formats["early_round_format"],
    )

    if not _is_cs2_sport(data.get("sport")):
        await state.update_data(map_veto_enabled=0, veto_launch_mode=LAUNCH_ADMIN, map_pool_entries=[])
        await state.set_state(CreateTournament.description)
        await callback.message.edit_text("Введите описание турнира (можно отправить 'нет', чтобы пропустить):")
        return

    await state.set_state(CreateTournament.map_veto_enabled)
    await callback.message.edit_text(
        "Включить map veto для этого турнира?",
        reply_markup=_build_veto_toggle_keyboard("create_tournament_veto", False),
    )


@router.callback_query(CreateTournament.map_veto_enabled, F.data.startswith("create_tournament_veto_"))
async def create_tournament_map_veto(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    enabled = callback.data.endswith("_on")
    await state.update_data(map_veto_enabled=1 if enabled else 0)

    if not enabled:
        await state.update_data(veto_launch_mode=LAUNCH_ADMIN, map_pool_entries=[])
        await state.set_state(CreateTournament.description)
        await callback.message.edit_text("Введите описание турнира (можно отправить 'нет', чтобы пропустить):")
        return

    selected = _default_map_pool_entries()
    await state.update_data(map_pool_entries=selected)
    await state.set_state(CreateTournament.map_pool)
    await callback.message.edit_text(
        _map_pool_prompt_text(selected),
        reply_markup=_build_map_pool_keyboard(
            selected,
            "create_tournament_pool_toggle",
            "create_tournament_pool_done",
            "create_tournament_veto_back",
        ),
    )


@router.callback_query(CreateTournament.map_pool, F.data.startswith("create_tournament_pool_toggle_"))
async def create_tournament_pool_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    map_key = callback.data.replace("create_tournament_pool_toggle_", "")
    if map_key == "manual":
        data = await state.get_data()
        current_entries = _normalize_map_pool_entries(data.get("map_pool_entries") or _default_map_pool_entries())
        await callback.message.edit_text(
            "Отправьте новый пул карт сообщением.\n"
            "Формат: по одной карте на строку, через запятую или через ;.\n"
            "Можно указывать стандартные и пользовательские карты.\n\n"
            f"Текущий пул: {_map_pool_label(current_entries)}",
            reply_markup=_build_map_pool_keyboard(
                current_entries,
                "create_tournament_pool_toggle",
                "create_tournament_pool_done",
                "create_tournament_veto_back",
            ),
        )
        return
    data = await state.get_data()
    entries = _normalize_map_pool_entries(data.get("map_pool_entries") or _default_map_pool_entries())
    selected_keys = {row["map_key"] for row in entries}
    if map_key in selected_keys:
        entries = [row for row in entries if row["map_key"] != map_key]
    else:
        entries.append({"map_key": map_key, "map_name": get_cs2_map_name(map_key)})
    await state.update_data(map_pool_entries=_normalize_map_pool_entries(entries))
    await callback.message.edit_text(
        _map_pool_prompt_text(entries),
        reply_markup=_build_map_pool_keyboard(
            entries,
            "create_tournament_pool_toggle",
            "create_tournament_pool_done",
            "create_tournament_veto_back",
        ),
    )


@router.callback_query(CreateTournament.map_pool, F.data == "create_tournament_veto_back")
async def create_tournament_pool_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(CreateTournament.map_veto_enabled)
    data = await state.get_data()
    await callback.message.edit_text(
        "Включить map veto для этого турнира?",
        reply_markup=_build_veto_toggle_keyboard("create_tournament_veto", bool(data.get("map_veto_enabled"))),
    )


@router.message(CreateTournament.map_pool)
async def create_tournament_pool_manual_input(message: Message, state: FSMContext):
    entries = _normalize_map_pool_entries(parse_map_pool_text(message.text))
    if not entries:
        await message.answer("Не удалось распознать карты. Отправьте список заново: по одной на строку, через запятую или через ;.")
        return
    await state.update_data(map_pool_entries=entries)
    await message.answer(
        "Пул карт обновлен.\n\n"
        f"{_map_pool_prompt_text(entries)}",
        reply_markup=_build_map_pool_keyboard(
            entries,
            "create_tournament_pool_toggle",
            "create_tournament_pool_done",
            "create_tournament_veto_back",
        ),
    )


@router.callback_query(CreateTournament.map_pool, F.data == "create_tournament_pool_done")
async def create_tournament_pool_done(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    entries = _normalize_map_pool_entries(data.get("map_pool_entries") or [])
    ok, error = _validate_veto_pool_for_stage_formats(data.get("stage_formats") or {}, _pool_entry_keys(entries))
    if not ok:
        await callback.answer(error or "Проверьте пул карт.", show_alert=True)
        return
    await state.update_data(map_pool_entries=entries)
    await state.set_state(CreateTournament.veto_launch_mode)
    await callback.message.edit_text(
        "Выберите режим запуска pick/ban:",
        reply_markup=_build_launch_mode_keyboard("create_tournament_launch_mode", LAUNCH_ADMIN),
    )


@router.callback_query(CreateTournament.veto_launch_mode, F.data.startswith("create_tournament_launch_mode_"))
async def create_tournament_launch_mode(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    launch_mode = callback.data.replace("create_tournament_launch_mode_", "")
    await state.update_data(veto_launch_mode=launch_mode)
    await state.set_state(CreateTournament.description)
    await callback.message.edit_text("Введите описание турнира (можно отправить 'нет', чтобы пропустить):")

@router.message(CreateTournament.description)
async def create_tournament_description(message: Message, state: FSMContext):
    data = await state.get_data()
    description = message.text if message.text.lower() != 'нет' else ''
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO tournaments (
        name, sport, city, registration_start_date, registration_end_date, start_date, end_date, max_teams, required_team_size,
        min_age, max_age, description, created_by, status, match_format,
        map_veto_enabled, veto_launch_mode
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'registration', ?, ?, ?)
    """, (
        data['name'], data['sport'], data['city'],
        data['registration_start_date'], data['registration_end_date'],
        data.get('start_date'), data.get('end_date'),
        data['max_teams'], data['required_team_size'], data['min_age'], data['max_age'],
        description, message.from_user.id, data.get('match_format', 'bo3'),
        int(data.get('map_veto_enabled') or 0), data.get('veto_launch_mode', LAUNCH_ADMIN)
    ))
    tournament_id = cur.lastrowid
    conn.commit()
    conn.close()
    stage_formats = data.get("stage_formats") or {
        "early_round_format": data.get("early_round_format", data.get("match_format", "bo3")),
        "semifinal_format": data.get("semifinal_format", data.get("match_format", "bo3")),
        "final_format": data.get("final_format", data.get("match_format", "bo3")),
    }
    replace_tournament_match_format_rules(
        tournament_id,
        _round_rules_from_stage_formats(
            stage_formats,
            get_tournament_main_round_count(tournament_id) or _expected_total_rounds_for_tournament_data(data),
        ),
    )
    if _is_cs2_sport(data.get("sport")) and int(data.get("map_veto_enabled") or 0) == 1:
        replace_tournament_map_pool(
            tournament_id,
            [(row["map_key"], row["map_name"]) for row in _normalize_map_pool_entries(data.get("map_pool_entries"))],
        )
    ensure_tournament_invite_token(tournament_id, regenerate=False)
    request_site_sync(f"tournament_created:{tournament_id}")
    await state.clear()
    await message.answer("✅ Турнир создан!")
    # Отправляем отдельное сообщение с управлением
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Управление турниром",
                              callback_data=f"admin_tournament_manage_{tournament_id}")]
    ])
    await message.answer("Нажмите кнопку ниже для управления:", reply_markup=kb)

# ---------- Редактирование турнира ----------

class EditTournament(StatesGroup):
    field = State()
    value = State()
    sport_choice = State()
    early_round_format_choice = State()
    semifinal_format_choice = State()
    final_format_choice = State()
    map_veto_choice = State()
    launch_mode_choice = State()
    map_pool_choice = State()
    manager_add_input = State()

class AdminBroadcast(StatesGroup):
    text = State()


class AdminUserEdit(StatesGroup):
    value = State()
    sports = State()


class AdminTeamEdit(StatesGroup):
    value = State()
    member_username = State()


async def _open_tournament_field_editor(
    callback: CallbackQuery,
    state: FSMContext,
    tournament_id: int,
    field: str,
    *,
    return_callback: str,
):
    await state.update_data(tournament_id=tournament_id, field=field, editor_return_callback=return_callback)
    tournament = get_tournament_by_id(tournament_id)
    if field in {"map_veto_enabled", "map_pool", "veto_launch_mode"} and tournament and not _is_cs2_sport(tournament["sport"]):
        await callback.answer("Map veto доступен только для CS2-турниров.", show_alert=True)
        return

    if field == "sport":
        sports = get_all_sports()
        await state.set_state(EditTournament.sport_choice)
        await callback.message.edit_text("Выберите новый вид спорта:", reply_markup=sports_choice_keyboard_single(sports))
    elif field in {"early_round_format", "semifinal_format", "final_format"}:
        stage_formats = get_tournament_stage_formats(tournament_id)
        if field == "early_round_format":
            await state.set_state(EditTournament.early_round_format_choice)
            title = "Выберите формат ранних раундов:"
            prefix = "edit_early_round_format"
            current_format = stage_formats["early_round_format"]
        elif field == "semifinal_format":
            await state.set_state(EditTournament.semifinal_format_choice)
            title = "Выберите формат полуфинала:"
            prefix = "edit_semifinal_format"
            current_format = stage_formats["semifinal_format"]
        else:
            await state.set_state(EditTournament.final_format_choice)
            title = "Выберите формат финала:"
            prefix = "edit_final_format"
            current_format = stage_formats["final_format"]
        await callback.message.edit_text(
            title,
            reply_markup=_build_match_format_keyboard(prefix, current_format, return_callback),
        )
    elif field == "map_veto_enabled":
        current = bool(tournament and int(tournament["map_veto_enabled"] or 0))
        await state.set_state(EditTournament.map_veto_choice)
        await callback.message.edit_text(
            "Настройте map veto:",
            reply_markup=_build_veto_toggle_keyboard("edit_veto_toggle", current, return_callback),
        )
    elif field == "veto_launch_mode":
        await state.set_state(EditTournament.launch_mode_choice)
        await callback.message.edit_text(
            "Выберите режим запуска pick/ban:",
            reply_markup=_build_launch_mode_keyboard(
                "edit_launch_mode",
                tournament["veto_launch_mode"] if tournament else LAUNCH_ADMIN,
                return_callback,
            ),
        )
    elif field == "map_pool":
        current_pool = [dict(row) for row in get_tournament_map_pool(tournament_id)] or _default_map_pool_entries()
        await state.set_state(EditTournament.map_pool_choice)
        await state.update_data(edit_map_pool_entries=current_pool)
        await callback.message.edit_text(
            _map_pool_prompt_text(current_pool),
            reply_markup=_build_map_pool_keyboard(
                current_pool,
                "edit_pool_toggle",
                "edit_pool_done",
                return_callback,
            ),
        )
    elif field == "managers":
        await state.clear()
        await show_tournament_managers_panel(callback.message, tournament_id, callback.from_user.id)
    else:
        await state.set_state(EditTournament.value)
        field_titles = {
            "name": "Название",
            "city": "Город",
            "registration_start_date": "Дата начала регистрации",
            "registration_end_date": "Дата конца регистрации",
            "start_date": "Дата начала проведения",
            "end_date": "Дата конца проведения",
            "max_teams": "Макс. команд",
            "required_team_size": "Размер команды",
            "min_age": "Мин. возраст",
            "max_age": "Макс. возраст",
            "description": "Описание",
        }
        if field in {"start_date", "end_date"}:
            prompt = (
                f"Введите новое значение для поля «{field_titles.get(field, field)}» "
                f"({DATE_INPUT_HINT}) или отправьте `-` / `нет`, чтобы очистить:"
            )
        elif field in {"registration_start_date", "registration_end_date"}:
            prompt = (
                f"Введите новое значение для поля «{field_titles.get(field, field)}» "
                f"({DATE_INPUT_HINT}):"
            )
        else:
            prompt = f"Введите новое значение для поля «{field_titles.get(field, field)}»:"
        await callback.message.edit_text(
            prompt
        )

@router.callback_query(F.data.startswith("admin_edit_tournament_"))
async def edit_tournament_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.update_data(tournament_id=tournament_id, editor_return_callback=f"admin_edit_tournament_{tournament_id}")
    builder = InlineKeyboardBuilder()
    fields = [
        ("name", "Название"),
        ("sport", "Вид спорта"),
        ("city", "Город"),
        ("registration_start_date", "Дата начала регистрации"),
        ("registration_end_date", "Дата конца регистрации"),
        ("start_date", "Дата начала проведения"),
        ("end_date", "Дата конца проведения"),
        ("max_teams", "Макс. команд"),
        ("required_team_size", "Размер команды"),
        ("min_age", "Мин. возраст"),
        ("max_age", "Макс. возраст"),
        ("description", "Описание"),
    ]
    for field_key, field_label in fields:
        builder.button(text=field_label, callback_data=f"edit_field_{field_key}")
    builder.button(
        text="🔙 Назад", callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(2)
    await callback.message.edit_text("Выберите поле для редактирования:", reply_markup=builder.as_markup())
    await state.set_state(EditTournament.field)

@router.callback_query(EditTournament.field, F.data.startswith("edit_field_"))
async def edit_tournament_field(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    field = callback.data.replace("edit_field_", "")
    data = await state.get_data()
    tournament_id = data["tournament_id"]
    await _open_tournament_field_editor(
        callback,
        state,
        tournament_id,
        field,
        return_callback=f"admin_edit_tournament_{tournament_id}",
    )

@router.message(EditTournament.value)
async def edit_tournament_value(message: Message, state: FSMContext):
    data = await state.get_data()
    tournament_id = data['tournament_id']
    if not can_manage_tournament(message.from_user.id, tournament_id):
        await message.answer("Нет прав.")
        await state.clear()
        return
    field = data['field']
    raw_value = message.text.strip()
    new_value = raw_value
    if field == "max_teams" or field == "required_team_size" or field == "min_age" or field == "max_age":
        try:
            new_value = int(new_value)
            if field == "required_team_size" and (new_value < 1 or new_value > 10):
                await message.answer("❌ Требуемый размер команды должен быть от 1 до 10.")
                return
            elif (field == "min_age" or field == "max_age") and (new_value < 0 or new_value > 100):
                await message.answer("❌ Введите целое число от 0 до 100.")
                return
        except ValueError:
            await message.answer("Введите число!")
            return

        # Проверки на согласованность возраста
        if field == "min_age":
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT max_age FROM tournaments WHERE id=?", (tournament_id,))
            row = cur.fetchone()
            current_max_age = row['max_age'] if row else None
            conn.close()
            if current_max_age is not None and new_value > current_max_age:
                await message.answer("❌ Минимальный возраст не может быть больше максимального.")
                return
        elif field == "max_age":
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT min_age FROM tournaments WHERE id=?", (tournament_id,))
            row = cur.fetchone()
            current_min_age = row['min_age'] if row else None
            conn.close()
            if current_min_age is not None and current_min_age > new_value:
                await message.answer("❌ Минимальный возраст не может быть больше максимального.")
                return
    elif field in {"registration_start_date", "registration_end_date", "start_date", "end_date"}:
        tournament = get_tournament_by_id(tournament_id)
        if not tournament:
            await message.answer("Турнир не найден.")
            await state.clear()
            return

        if field in {"registration_start_date", "registration_end_date"}:
            if parse_russian_date(raw_value) is None:
                await message.answer(
                    "❌ Неверный формат даты.\n"
                    f"Введите дату {DATE_INPUT_HINT}.\n{DATE_INPUT_MONTHS_HINT}"
                )
                return
            new_value = raw_value
        else:
            normalized_optional = _normalize_optional_date_input(raw_value)
            if normalized_optional and parse_russian_date(normalized_optional) is None:
                await message.answer(
                    "❌ Неверный формат даты.\n"
                    f"Введите дату {DATE_INPUT_HINT} или отправьте `-` / `нет`.\n{DATE_INPUT_MONTHS_HINT}"
                )
                return
            new_value = normalized_optional

        if field == "registration_start_date":
            current_end = tournament["registration_end_date"]
            start_key = _russian_date_order_key(new_value)
            end_key = _russian_date_order_key(current_end)
            if start_key and end_key and start_key > end_key:
                await message.answer("❌ Начало регистрации не может быть позже конца регистрации.")
                return
        elif field == "registration_end_date":
            current_start = tournament["registration_start_date"]
            start_key = _russian_date_order_key(current_start)
            end_key = _russian_date_order_key(new_value)
            if start_key and end_key and end_key < start_key:
                await message.answer("❌ Конец регистрации не может быть раньше начала регистрации.")
                return
        elif field == "start_date":
            current_end = tournament["end_date"]
            start_key = _russian_date_order_key(new_value)
            end_key = _russian_date_order_key(current_end)
            if start_key and end_key and start_key > end_key:
                await message.answer("❌ Начало проведения не может быть позже конца проведения.")
                return
        elif field == "end_date":
            current_start = tournament["start_date"]
            start_key = _russian_date_order_key(current_start)
            end_key = _russian_date_order_key(new_value)
            if start_key and end_key and end_key < start_key:
                await message.answer("❌ Конец проведения не может быть раньше начала проведения.")
                return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE tournaments SET {field}=? WHERE id=?", (new_value, tournament_id))
    conn.commit()
    conn.close()
    request_site_sync(f"tournament_updated:{tournament_id}:{field}")
    await state.clear()
    await message.answer("✅ Турнир обновлён!")
    # Отправляем отдельное сообщение с управлением
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Управление турниром",
                              callback_data=f"admin_tournament_manage_{tournament_id}")]
    ])
    await message.answer("Нажмите кнопку ниже для управления:", reply_markup=kb)

@router.callback_query(EditTournament.sport_choice, F.data.startswith("admin_tourn_sport_"))
async def edit_tournament_sport_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    new_sport = callback.data.replace("admin_tourn_sport_", "")
    data = await state.get_data()
    tournament_id = data['tournament_id']
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tournaments SET sport=? WHERE id=?",
                (new_sport, tournament_id))
    conn.commit()
    conn.close()
    request_site_sync(f"tournament_updated:{tournament_id}:sport")
    await state.clear()
    await send_tournament_info(callback.bot, callback.message.chat.id, tournament_id, callback.from_user.id)


async def _update_stage_format(callback: CallbackQuery, state: FSMContext, stage_key: str, selected_format: str):
    data = await state.get_data()
    tournament_id = data["tournament_id"]
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    if _has_in_progress_veto_sessions(tournament_id):
        await callback.answer("Нельзя менять форматы, пока идет активный pick/ban.", show_alert=True)
        return

    stage_formats = get_tournament_stage_formats(tournament_id)
    stage_formats[stage_key] = selected_format
    tournament = get_tournament_by_id(tournament_id)
    if tournament and _is_cs2_sport(tournament["sport"]) and int(tournament["map_veto_enabled"] or 0) == 1:
        pool_entries = [dict(row) for row in get_tournament_map_pool(tournament_id)] or _default_map_pool_entries()
        ok, error = _validate_veto_pool_for_stage_formats(stage_formats, _pool_entry_keys(pool_entries))
        if not ok:
            await callback.answer(error or "Проверьте пул карт.", show_alert=True)
            return

    _save_tournament_stage_formats(tournament_id, stage_formats)
    request_site_sync(f"tournament_updated:{tournament_id}:round_formats")
    await state.clear()
    await admin_tournament_veto_panel(callback, tournament_id=tournament_id)


@router.callback_query(EditTournament.early_round_format_choice, F.data.startswith("edit_early_round_format_"))
async def edit_tournament_early_round_format(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _update_stage_format(
        callback,
        state,
        "early_round_format",
        callback.data.replace("edit_early_round_format_", ""),
    )


@router.callback_query(EditTournament.semifinal_format_choice, F.data.startswith("edit_semifinal_format_"))
async def edit_tournament_semifinal_format(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _update_stage_format(
        callback,
        state,
        "semifinal_format",
        callback.data.replace("edit_semifinal_format_", ""),
    )


@router.callback_query(EditTournament.final_format_choice, F.data.startswith("edit_final_format_"))
async def edit_tournament_final_format(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _update_stage_format(
        callback,
        state,
        "final_format",
        callback.data.replace("edit_final_format_", ""),
    )


@router.callback_query(EditTournament.map_veto_choice, F.data.startswith("edit_veto_toggle_"))
async def edit_tournament_veto_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    tournament_id = data["tournament_id"]
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    if _has_in_progress_veto_sessions(tournament_id):
        await callback.answer("Нельзя менять настройки veto, пока идет активный pick/ban.", show_alert=True)
        return

    enabled = callback.data.endswith("_on")
    tournament = get_tournament_by_id(tournament_id)
    if enabled and not _is_cs2_sport(tournament["sport"] if tournament else None):
        await callback.answer("Map veto доступен только для CS2.", show_alert=True)
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tournaments SET map_veto_enabled=? WHERE id=?", (1 if enabled else 0, tournament_id))
    conn.commit()
    conn.close()
    if enabled and not get_tournament_map_pool(tournament_id):
        replace_tournament_map_pool(
            tournament_id,
            default_cs2_map_entries(),
        )
    request_site_sync(f"tournament_updated:{tournament_id}:map_veto_enabled")
    await state.clear()
    await admin_tournament_veto_panel(callback, tournament_id=tournament_id)


@router.callback_query(EditTournament.launch_mode_choice, F.data.startswith("edit_launch_mode_"))
async def edit_tournament_launch_mode(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    tournament_id = data["tournament_id"]
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    launch_mode = callback.data.replace("edit_launch_mode_", "")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tournaments SET veto_launch_mode=? WHERE id=?", (launch_mode, tournament_id))
    conn.commit()
    conn.close()
    request_site_sync(f"tournament_updated:{tournament_id}:veto_launch_mode")
    await state.clear()
    await admin_tournament_veto_panel(callback, tournament_id=tournament_id)


@router.callback_query(EditTournament.map_pool_choice, F.data.startswith("edit_pool_toggle_"))
async def edit_tournament_pool_toggle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    return_callback = data.get("editor_return_callback") or f"admin_tournament_veto_{data['tournament_id']}"
    map_key = callback.data.replace("edit_pool_toggle_", "")
    if map_key == "manual":
        current_entries = _normalize_map_pool_entries(data.get("edit_map_pool_entries") or _default_map_pool_entries())
        await callback.message.edit_text(
            "Отправьте новый пул карт сообщением.\n"
            "Формат: по одной карте на строку, через запятую или через ;.\n"
            "Можно указывать стандартные и пользовательские карты.\n\n"
            f"Текущий пул: {_map_pool_label(current_entries)}",
            reply_markup=_build_map_pool_keyboard(
                current_entries,
                "edit_pool_toggle",
                "edit_pool_done",
                return_callback,
            ),
        )
        return
    entries = _normalize_map_pool_entries(data.get("edit_map_pool_entries") or _default_map_pool_entries())
    selected_keys = {row["map_key"] for row in entries}
    if map_key in selected_keys:
        entries = [row for row in entries if row["map_key"] != map_key]
    else:
        entries.append({"map_key": map_key, "map_name": get_cs2_map_name(map_key)})
    await state.update_data(edit_map_pool_entries=_normalize_map_pool_entries(entries))
    tournament_id = data["tournament_id"]
    await callback.message.edit_text(
        _map_pool_prompt_text(entries),
        reply_markup=_build_map_pool_keyboard(
            entries,
            "edit_pool_toggle",
            "edit_pool_done",
            return_callback,
        ),
    )


@router.message(EditTournament.map_pool_choice)
async def edit_tournament_pool_manual_input(message: Message, state: FSMContext):
    entries = _normalize_map_pool_entries(parse_map_pool_text(message.text))
    if not entries:
        await message.answer("Не удалось распознать карты. Отправьте список заново: по одной на строку, через запятую или через ;.")
        return
    data = await state.get_data()
    tournament_id = data["tournament_id"]
    return_callback = data.get("editor_return_callback") or f"admin_tournament_veto_{tournament_id}"
    await state.update_data(edit_map_pool_entries=entries)
    await message.answer(
        "Пул карт обновлен.\n\n"
        f"{_map_pool_prompt_text(entries)}",
        reply_markup=_build_map_pool_keyboard(
            entries,
            "edit_pool_toggle",
            "edit_pool_done",
            return_callback,
        ),
    )


@router.callback_query(EditTournament.map_pool_choice, F.data == "edit_pool_done")
async def edit_tournament_pool_done(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    tournament_id = data["tournament_id"]
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    if _has_in_progress_veto_sessions(tournament_id):
        await callback.answer("Нельзя менять пул, пока идет активный pick/ban.", show_alert=True)
        return
    entries = _normalize_map_pool_entries(data.get("edit_map_pool_entries") or [])
    stage_formats = get_tournament_stage_formats(tournament_id)
    ok, error = _validate_veto_pool_for_stage_formats(stage_formats, _pool_entry_keys(entries))
    if not ok:
        await callback.answer(error or "Проверьте пул карт.", show_alert=True)
        return
    replace_tournament_map_pool(
        tournament_id,
        [(row["map_key"], row["map_name"]) for row in entries],
    )
    request_site_sync(f"tournament_updated:{tournament_id}:map_pool")
    await state.clear()
    await admin_tournament_veto_panel(callback, tournament_id=tournament_id)


async def show_tournament_managers_panel(message, tournament_id: int, actor_telegram_id: int):
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await message.edit_text("Турнир не найден.")
        return
    managers = list(get_tournament_manager_users(tournament_id))
    is_owner = is_tournament_creator_or_global_admin(actor_telegram_id, tournament_id)
    lines = [
        "Ответственные турнира",
        "",
        f"Турнир: {tournament['name']}",
    ]
    if managers:
        lines.append("")
        lines.append("Назначены:")
        for user in managers:
            lines.append(f"• {_build_manager_label(user)}")
    else:
        lines.append("")
        lines.append("Пока никто не назначен.")

    builder = InlineKeyboardBuilder()
    if is_owner:
        builder.button(text="➕ Добавить ответственного", callback_data=f"admin_tournament_managers_add_{tournament_id}")
        for user in managers:
            builder.button(
                text=f"➖ {_build_manager_label(user)}",
                callback_data=f"admin_tournament_manager_remove_{tournament_id}_{user['id']}",
            )
    builder.button(text="🔙 К турниру", callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(1)
    await message.edit_text("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_tournament_managers_add_"))
async def admin_tournament_managers_add(callback: CallbackQuery, state: FSMContext):
    tournament_id = int(callback.data.split("_")[4])
    if not is_tournament_creator_or_global_admin(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.update_data(tournament_id=tournament_id, editor_return_callback=f"admin_tournament_managers_{tournament_id}")
    await state.set_state(EditTournament.manager_add_input)
    await callback.message.edit_text(
        "Введите username, имя или Telegram ID пользователя, которого нужно назначить ответственным."
    )
    await callback.answer()


@router.message(EditTournament.manager_add_input)
async def admin_tournament_manager_add_input(message: Message, state: FSMContext):
    data = await state.get_data()
    tournament_id = data["tournament_id"]
    if not is_tournament_creator_or_global_admin(message.from_user.id, tournament_id):
        await message.answer("Нет прав.")
        await state.clear()
        return
    users = list(search_users((message.text or "").strip(), limit=10))
    if not users:
        await message.answer("Пользователи не найдены. Попробуйте другой запрос.")
        return
    builder = InlineKeyboardBuilder()
    for user in users:
        builder.button(
            text=_build_manager_label(user),
            callback_data=f"admin_tournament_manager_pick_{tournament_id}_{user['id']}",
        )
    builder.button(text="🔙 Назад", callback_data=f"admin_tournament_managers_{tournament_id}")
    builder.adjust(1)
    await message.answer("Выберите пользователя:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_tournament_manager_pick_"))
async def admin_tournament_manager_pick(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    user_id = int(parts[5])
    if not is_tournament_creator_or_global_admin(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    actor = get_user(callback.from_user.id)
    add_tournament_manager(tournament_id, user_id, actor["id"] if actor else None)
    request_site_sync(f"tournament_manager_added:{tournament_id}:{user_id}")
    await state.clear()
    await show_tournament_managers_panel(callback.message, tournament_id, callback.from_user.id)
    await callback.answer("Ответственный назначен.")


@router.callback_query(F.data.startswith("admin_tournament_manager_remove_"))
async def admin_tournament_manager_remove(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    user_id = int(parts[5])
    if not is_tournament_creator_or_global_admin(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    remove_tournament_manager(tournament_id, user_id)
    request_site_sync(f"tournament_manager_removed:{tournament_id}:{user_id}")
    await show_tournament_managers_panel(callback.message, tournament_id, callback.from_user.id)
    await callback.answer("Ответственный снят.")

# ---------- Заявки на турниры ----------

# ---------- Управление турнирами (список) ----------

@router.callback_query(F.data == "admin_tournaments_list")
async def admin_tournaments_list(callback: CallbackQuery, state: FSMContext):
    """Список турниров для выбора управления с пагинацией."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    await state.clear()
    await show_tournaments_page(callback, 0)

async def show_tournaments_page(callback: CallbackQuery | None, offset: int, message=None):
    """Показывает страницу списка турниров."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tournaments")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT id, name, sport, status, bracket_generated
        FROM tournaments
        ORDER BY created_at DESC
        LIMIT 10 OFFSET ?
    """, (offset,))
    tournaments = cur.fetchall()
    conn.close()

    if not tournaments:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать турнир",
                                  callback_data="admin_create_tournament")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        text = "Нет турниров.\n\nСоздайте новый турнир:"
        if callback:
            await callback.message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        return

    text = f"🏆 Управление турнирами ({offset + 1}-{min(offset + 10, total)} из {total})\n\nВыберите турнир:\n"
    builder = InlineKeyboardBuilder()

    for t in tournaments:
        status_icon = "🟢" if t['status'] == 'registration' else "🟡" if t['status'] == 'active' else "⚪"
        bracket_icon = "📊" if t['bracket_generated'] else ""
        text += f"{status_icon}{bracket_icon} {t['name']} ({get_sport_display_name(t['sport'])})\n"
        builder.button(
            text=f"⚙️ {t['name']}",
            callback_data=f"admin_tournament_manage_{t['id']}"
        )

    # Пагинация
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_tournaments_page_{offset - 10}"))
    if offset + 10 < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_tournaments_page_{offset + 10}"))

    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="➕ Создать турнир",
                   callback_data="admin_create_tournament")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)

    if callback:
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("admin_tournaments_page_"))
async def admin_tournaments_page_callback(callback: CallbackQuery):
    """Переключение страницы списка турниров."""
    offset = int(callback.data.split("_")[3])
    await show_tournaments_page(callback, offset)

# ---------- Управление конкретным турниром ----------


@router.callback_query(F.data.regexp(r"^admin_tournament_notice_open_\d+$"))
async def admin_tournament_notice_open(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await resolve_admin_action_messages(
        callback.bot,
        text="✅ Турнир уже открыт другим администратором.",
        action_scope=ADMIN_ACTION_SCOPE_REGISTRATION_ENDED,
        action_key=get_registration_ended_action_key(tournament_id),
        exclude_target=(callback.message.chat.id, callback.message.message_id) if callback.message.chat and callback.message.message_id else None,
    )
    await admin_tournament_manage(callback, tournament_id=tournament_id)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_tournament_manage_"))
async def admin_tournament_manage(callback: CallbackQuery, tournament_id: int | None = None):
    """Меню управления конкретным турниром для админа."""
    if tournament_id is None:
        tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)

    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    # Получаем количество команд и заявок
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM tournament_applications
        WHERE tournament_id=? AND status='approved'
    """, (tournament_id,))
    approved_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM tournament_applications
        WHERE tournament_id=? AND status='pending'
    """, (tournament_id,))
    pending_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM tournament_applications
        WHERE tournament_id=? AND status='excluded'
    """, (tournament_id,))
    excluded_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM tournament_applications
        WHERE tournament_id=? AND status='rejected'
    """, (tournament_id,))
    rejected_count = cur.fetchone()[0]

    conn.close()

    # Показываем только принятые команды (approved)
    teams_text = f"Команд: {approved_count} из {tournament['max_teams']}"

    status_map = {
        'registration': 'Регистрация',
        'active': 'Идёт',
        'finished': 'Завершён'
    }
    status_display = status_map.get(tournament['status'], tournament['status'])
    invite_token = ensure_tournament_invite_token(tournament_id, regenerate=False)
    bot_username = ""
    try:
        me = await callback.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""
    invite_url = f"https://t.me/{bot_username}?start=tournament_invite_{invite_token}" if (invite_token and bot_username) else "недоступно"

    # Возрастные ограничения
    age_text = ""
    if tournament['min_age'] is not None and tournament['max_age'] is not None:
        if tournament['min_age'] == 0 and tournament['max_age'] == 100:
            age_text = "Без ограничений"
        else:
            age_text = f"{tournament['min_age']}–{tournament['max_age']} лет"
    else:
        age_text = "Не указан"
    stage_formats = get_tournament_stage_formats(tournament_id)

    lines = [
        "⚙️ Управление турниром",
        "",
        f"🏆 {tournament['name']}",
        f"Вид спорта: {get_sport_display_name(tournament['sport'])}",
        f"Требуемый размер команды: {tournament['required_team_size']} чел.",
        f"Город: {tournament['city']}",
        f"Возраст: {age_text}",
        *_format_tournament_dates_for_text(tournament).splitlines(),
        teams_text,
        f"Новые заявки: {pending_count}",
        f"Исключенные: {excluded_count}",
        f"Отклоненные: {rejected_count}",
        f"Сетка: {'✅ Сгенерирована' if tournament['bracket_generated'] else '❌ Не сгенерирована'}",
        f"Статус: {status_display}",
    ]
    if _is_cs2_sport(tournament["sport"]):
        lines.extend(
            [
                "Форматы серий:",
                _format_stage_formats_text(stage_formats),
                f"Map veto: {'включен' if int(tournament['map_veto_enabled'] or 0) == 1 else 'выключен'}",
                f"Режим запуска veto: {tournament['veto_launch_mode'] or LAUNCH_ADMIN}",
            ]
        )
    lines.append(f"Замены состава: {'разрешены' if int(tournament['replacements_enabled'] or 0) == 1 else 'запрещены'}")
    text = "\n".join(lines)
    if tournament['description'] and tournament['description'] != 'нет':
        text += f"\n📝 Описание: {tournament['description']}"
    if _is_cs2_sport(tournament["sport"]) and int(tournament["map_veto_enabled"] or 0) == 1:
        pool = [dict(row) for row in get_tournament_map_pool(tournament_id)] or _default_map_pool_entries()
        text += f"\n🗺 Пул карт: {_map_pool_label(pool)}"
        text += f"\n📋 Veto: {_build_veto_overview(tournament_id)}"
    managers = list(get_tournament_manager_users(tournament_id))
    if managers:
        text += f"\n👤 Ответственные: {', '.join(_build_manager_label(user) for user in managers)}"

    builder = InlineKeyboardBuilder()

    # Генерация сетки
    if not tournament['bracket_generated']:
        builder.button(text="🔷 Сгенерировать сетку",
                       callback_data=f"admin_generate_bracket_{tournament_id}")
    else:
        builder.button(text="📊 Управление сеткой",
                       callback_data=f"view_bracket_{tournament_id}")
        if tournament['status'] != 'finished':
            builder.button(text="♻️ Перегенерировать сетку",
                           callback_data=f"admin_regenerate_bracket_{tournament_id}")

    # Завершение турнира (только если сетка сгенерирована и статус не 'finished')
    if tournament['bracket_generated'] and tournament['status'] != 'finished':
        builder.button(text="🏆 Завершить турнир",
                       callback_data=f"admin_finish_tournament_{tournament_id}")

    # Команды и заявки открываются через отдельный хаб со статусами.
    builder.button(text="👥 Команды и заявки",
                   callback_data=f"admin_tournament_teams_{tournament_id}")
    builder.button(
        text=f"🔁 Замены: {'Вкл' if int(tournament['replacements_enabled'] or 0) == 1 else 'Выкл'}",
        callback_data=f"admin_tournament_replacements_toggle_{tournament_id}"
    )
    builder.button(text="📢 Сообщение участникам турнира",
                   callback_data=f"admin_tournament_broadcast_{tournament_id}")
    if _is_cs2_sport(tournament["sport"]):
        builder.button(text="🗺 Панель veto", callback_data=f"admin_tournament_veto_{tournament_id}")
        builder.button(text="🏅 MVP", callback_data=f"admin_tournament_mvp_{tournament_id}")
    builder.button(text="🔔 Мои уведомления", callback_data=f"admin_tournament_notifications_{tournament_id}")

    # Редактирование турнира
    builder.button(text="✏️ Редактировать турнир",
                   callback_data=f"admin_edit_tournament_{tournament_id}")
    if is_tournament_creator_or_global_admin(callback.from_user.id, tournament_id):
        builder.button(text="👤 Ответственные", callback_data=f"admin_tournament_managers_{tournament_id}")
    builder.button(text="🔗 Ссылка-приглашение",
                   callback_data=f"admin_tournament_invite_menu_{tournament_id}")

    # Удаление турнира
    builder.button(text="🗑 Удалить турнир",
                   callback_data=f"admin_delete_tournament_{tournament_id}")

    # Назад к списку турниров
    back_callback = "admin_tournaments_list" if is_admin(callback.from_user.id) else f"tournament_{tournament_id}"
    builder.button(text="🔙 Назад",
                   callback_data=back_callback)
    builder.adjust(1)

    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    except Exception as e:
        if "there is no text" in str(e):
            # Если сообщение содержит фото, отправляем новое
            await callback.message.answer(text, reply_markup=builder.as_markup())
        else:
            raise
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_replacements_toggle_"))
async def admin_tournament_replacements_toggle(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return
    conn = get_connection()
    cur = conn.cursor()
    new_value = 0 if int(tournament["replacements_enabled"] or 0) == 1 else 1
    cur.execute("UPDATE tournaments SET replacements_enabled=? WHERE id=?", (new_value, tournament_id))
    conn.commit()
    conn.close()
    request_site_sync(f"tournament_updated:{tournament_id}:replacements_enabled")
    await admin_tournament_manage(callback, tournament_id=tournament_id)


@router.callback_query(F.data == "admin_notifications")
async def admin_notifications(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    enabled = get_admin_tournament_notifications_enabled(int(user["id"]))
    await callback.message.edit_text(
        "🔔 Мои турнирные уведомления\n\n"
        f"Сейчас: {'включены' if enabled else 'выключены'}\n\n"
        "Эта настройка влияет на уведомления для админов и ответственных по турнирам.\n"
        "Отдельно в карточке турнира можно задать override для конкретного турнира.",
        reply_markup=_admin_notifications_keyboard(enabled),
    )
    await callback.answer()


@router.callback_query(F.data.in_(["admin_notifications_global_on", "admin_notifications_global_off"]))
async def admin_notifications_global_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    enabled = callback.data.endswith("_on")
    set_admin_tournament_notifications_enabled(int(user["id"]), enabled)
    await callback.message.edit_text(
        "🔔 Мои турнирные уведомления\n\n"
        f"Сейчас: {'включены' if enabled else 'выключены'}\n\n"
        "Эта настройка влияет на уведомления для админов и ответственных по турнирам.\n"
        "Отдельно в карточке турнира можно задать override для конкретного турнира.",
        reply_markup=_admin_notifications_keyboard(enabled),
    )
    await callback.answer("Настройка обновлена")


@router.callback_query(F.data.regexp(r"^admin_tournament_notifications_\d+$"))
async def admin_tournament_notifications(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await _render_tournament_notifications(callback, tournament_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_notifications_set_"))
async def admin_tournament_notifications_set(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    mode = parts[5]
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    set_tournament_notification_override(tournament_id, int(user["id"]), mode)
    await _render_tournament_notifications(callback, tournament_id)
    await callback.answer("Настройка уведомлений обновлена")


async def _show_tournament_mvp_panel(message, tournament_id: int):
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await message.edit_text("Турнир не найден.", reply_markup=back_to_main_keyboard())
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ MVP матчей", callback_data=f"admin_tournament_mvp_matches_{tournament_id}")
    builder.button(text="🏆 MVP турнира", callback_data=f"admin_tournament_mvp_tournament_{tournament_id}")
    builder.button(text="🔙 К турниру", callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(1)
    await message.edit_text(
        f"🏅 MVP-панель\n\nТурнир: {tournament['name']}\nСпорт: {get_sport_display_name(tournament['sport'])}\n\nЗдесь можно вручную переопределить MVP матча и MVP турнира.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.regexp(r"^admin_tournament_mvp_\d+$"))
async def admin_tournament_mvp_panel(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if not tournament or not _is_cs2_sport(tournament["sport"]):
        await callback.answer("MVP доступен только для CS2-турниров.", show_alert=True)
        return
    await _show_tournament_mvp_panel(callback.message, tournament_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_mvp_matches_"))
async def admin_tournament_mvp_matches(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT b.id, b.round_number, b.match_number, tm1.name AS team1_name, tm2.name AS team2_name
        FROM tournament_brackets b
        LEFT JOIN teams tm1 ON tm1.id = b.team1_id
        LEFT JOIN teams tm2 ON tm2.id = b.team2_id
        WHERE b.tournament_id=? AND b.status='completed'
        ORDER BY b.round_number, b.match_number, b.id
        """,
        (tournament_id,),
    )
    matches = cur.fetchall()
    conn.close()
    builder = InlineKeyboardBuilder()
    for match in matches:
        label = f"Раунд {match['round_number']} / матч {match['match_number']}: {match['team1_name'] or 'TBD'} vs {match['team2_name'] or 'TBD'}"
        builder.button(text=label[:64], callback_data=f"admin_tournament_mvp_match_{tournament_id}_{match['id']}")
    builder.button(text="🔙 Назад", callback_data=f"admin_tournament_mvp_{tournament_id}")
    builder.adjust(1)
    await callback.message.edit_text("⭐ Выберите матч для ручного MVP:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_tournament_mvp_match_\d+_\d+$"))
async def admin_tournament_mvp_match(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    match_id = int(parts[5])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    candidates = get_match_mvp_candidates(match_id)
    builder = InlineKeyboardBuilder()
    for candidate in candidates:
        username = candidate.get("username") or "без_username"
        text = (
            f"{candidate.get('first_name') or 'Игрок'} (@{username}) | "
            f"K {candidate.get('kills', 0)} / D {candidate.get('deaths', 0)} / ADR {candidate.get('adr', 0)}"
        )
        builder.button(
            text=text[:64],
            callback_data=f"admin_tournament_mvp_match_pick_{match_id}_{candidate['user_id']}",
        )
    builder.button(text="🔙 Назад", callback_data=f"admin_tournament_mvp_matches_{tournament_id}")
    builder.adjust(1)
    await callback.message.edit_text("⭐ Выберите MVP матча:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_tournament_mvp_match_pick_\d+_\d+$"))
async def admin_tournament_mvp_match_pick(callback: CallbackQuery):
    parts = callback.data.split("_")
    match_id = int(parts[5])
    user_id = int(parts[6])
    actor = get_user(callback.from_user.id)
    result = set_match_mvp_override(match_id, user_id, "CS2", assigned_by=actor["id"] if actor else None)
    if not result.get("ok"):
        await callback.answer("Не удалось назначить MVP матча.", show_alert=True)
        return
    request_site_sync(f"match_mvp_override:{match_id}:{user_id}")
    await refresh_rating_channel_posts(callback.bot, sport_key="CS2")
    tournament_id = result.get("tournament_id")
    if tournament_id:
        await _show_tournament_mvp_panel(callback.message, int(tournament_id))
    await callback.answer("MVP матча обновлён.", show_alert=True)


@router.callback_query(F.data.regexp(r"^admin_tournament_mvp_tournament_\d+$"))
async def admin_tournament_mvp_tournament(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    candidates = get_tournament_mvp_candidates(tournament_id)
    builder = InlineKeyboardBuilder()
    for candidate in candidates:
        username = candidate.get("username") or "без_username"
        text = (
            f"{candidate.get('first_name') or 'Игрок'} (@{username}) | "
            f"MVP матчей {candidate.get('match_mvp_count', 0)} | K {candidate.get('kills', 0)}"
        )
        builder.button(
            text=text[:64],
            callback_data=f"admin_tournament_mvp_tournament_pick_{tournament_id}_{candidate['user_id']}",
        )
    builder.button(text="🔙 Назад", callback_data=f"admin_tournament_mvp_{tournament_id}")
    builder.adjust(1)
    await callback.message.edit_text("🏆 Выберите MVP турнира:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_tournament_mvp_tournament_pick_\d+_\d+$"))
async def admin_tournament_mvp_tournament_pick(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[5])
    user_id = int(parts[6])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    actor = get_user(callback.from_user.id)
    result = set_tournament_mvp_override(tournament_id, user_id, "CS2", assigned_by=actor["id"] if actor else None)
    if not result.get("ok"):
        await callback.answer("Не удалось назначить MVP турнира.", show_alert=True)
        return
    request_site_sync(f"tournament_mvp_override:{tournament_id}:{user_id}")
    await refresh_rating_channel_posts(callback.bot, sport_key="CS2")
    await _show_tournament_mvp_panel(callback.message, tournament_id)
    await callback.answer("MVP турнира обновлён.", show_alert=True)


@router.callback_query(F.data.startswith("admin_tournament_veto_"))
async def admin_tournament_veto_panel(callback: CallbackQuery, tournament_id: int | None = None):
    if tournament_id is None:
        tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return
    if not _is_cs2_sport(tournament["sport"]):
        await callback.answer("Панель veto доступна только для CS2-турниров.", show_alert=True)
        return

    sessions = list_tournament_veto_sessions(tournament_id)
    stage_formats = get_tournament_stage_formats(tournament_id)
    lines = [
        "🗺 Панель veto",
        "",
        f"Турнир: {tournament['name']}",
        "",
        "Форматы серий:",
        _format_stage_formats_text(stage_formats),
        "",
        f"Map veto: {'включен' if int(tournament['map_veto_enabled'] or 0) == 1 else 'выключен'}",
        f"Режим запуска: {tournament['veto_launch_mode'] or LAUNCH_ADMIN}",
        "",
        f"Сводка: {_build_veto_overview(tournament_id)}",
    ]
    if int(tournament["map_veto_enabled"] or 0) == 1:
        pool = [dict(row) for row in get_tournament_map_pool(tournament_id)] or _default_map_pool_entries()
        lines.extend(["", f"Пул карт: {_map_pool_label(pool)}"])
    builder = InlineKeyboardBuilder()
    builder.button(text="🧩 Ранние раунды", callback_data=f"admin_veto_field_{tournament_id}_early_round_format")
    builder.button(text="🥈 Полуфинал", callback_data=f"admin_veto_field_{tournament_id}_semifinal_format")
    builder.button(text="🏆 Финал", callback_data=f"admin_veto_field_{tournament_id}_final_format")
    builder.button(text="🗺 Map veto", callback_data=f"admin_veto_field_{tournament_id}_map_veto_enabled")
    builder.button(text="📚 Пул карт", callback_data=f"admin_veto_field_{tournament_id}_map_pool")
    builder.button(text="🚀 Старт pick/ban", callback_data=f"admin_veto_field_{tournament_id}_veto_launch_mode")
    if sessions:
        lines.append("")
        lines.append("Матчи:")
        for session in sessions[:20]:
            round_label = session.get("round_name") or f"Раунд {session.get('round_number') or '?'}"
            lines.append(
                f"• {round_label}: {session.get('team1_name') or 'TBD'} vs {session.get('team2_name') or 'TBD'} "
                f"— {get_veto_status_label(session.get('status'))}"
            )
            builder.button(
                text=f"🎮 {round_label}: {session.get('team1_name') or 'TBD'} vs {session.get('team2_name') or 'TBD'}",
                callback_data=f"veto_admin_open_{session['bracket_match_id']}",
            )
    else:
        lines.append("")
        lines.append("Подходящих матчей для pick/ban пока нет.")
    builder.button(text="🔙 К турниру", callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_veto_field_"))
async def admin_veto_field(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, _, tournament_id_str, field = callback.data.split("_", 4)
    tournament_id = int(tournament_id_str)
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if not tournament or not _is_cs2_sport(tournament["sport"]):
        await callback.answer("Настройки veto доступны только для CS2-турниров.", show_alert=True)
        return
    await _open_tournament_field_editor(
        callback,
        state,
        tournament_id,
        field,
        return_callback=f"admin_tournament_veto_{tournament_id}",
    )


@router.callback_query(F.data.regexp(r"^admin_tournament_managers_\d+$"))
async def admin_tournament_managers(callback: CallbackQuery, state: FSMContext):
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.clear()
    await show_tournament_managers_panel(callback.message, tournament_id, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_broadcast_"))
async def admin_tournament_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    payload, error = _resolve_targeted_broadcast_payload("tournament", tournament_id)
    if not payload:
        await callback.answer(error or "Не удалось подготовить рассылку.", show_alert=True)
        return

    await state.clear()
    await state.set_state(TargetedBroadcast.text)
    await state.update_data(
        broadcast_scope="tournament",
        broadcast_target_id=tournament_id,
        broadcast_return_callback=payload["return_callback"],
    )
    await callback.message.edit_text(
        "📢 Сообщение участникам турнира\n\n"
        f"Турнир: {payload['title']}\n"
        f"Получателей: {payload['recipient_count']}\n\n"
        "Введите текст сообщения. Бот добавит шапку турнира автоматически.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="targeted_broadcast_cancel")]
        ]),
    )


@router.message(TargetedBroadcast.text)
async def targeted_broadcast_preview(message: Message, state: FSMContext):
    data = await state.get_data()
    scope = data.get("broadcast_scope")
    target_id = data.get("broadcast_target_id")
    if not scope or not target_id:
        await state.clear()
        await message.answer("Контекст рассылки потерян.")
        return
    if not _can_manage_targeted_broadcast(message.from_user.id, scope, int(target_id)):
        await state.clear()
        await message.answer("Нет прав.")
        return

    body_text = (message.text or "").strip()
    if not body_text:
        await message.answer("Текст рассылки не должен быть пустым.")
        return

    payload, error = _resolve_targeted_broadcast_payload(scope, int(target_id), body_text)
    if not payload:
        await state.clear()
        await message.answer(error or "Не удалось подготовить предпросмотр.", reply_markup=_targeted_broadcast_return_keyboard(data.get("broadcast_return_callback", "admin_menu")))
        return

    await state.update_data(broadcast_text=body_text)
    await message.answer(
        "Предпросмотр рассылки:\n\n"
        f"{payload['text']}\n\n"
        f"Получателей: {payload['recipient_count']}\n\n"
        "Отправить это сообщение?",
        reply_markup=_targeted_broadcast_keyboard("targeted_broadcast_confirm", "targeted_broadcast_cancel"),
    )


@router.callback_query(F.data == "targeted_broadcast_confirm")
async def targeted_broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    scope = data.get("broadcast_scope")
    target_id = data.get("broadcast_target_id")
    return_callback = data.get("broadcast_return_callback", "admin_menu")
    body_text = (data.get("broadcast_text") or "").strip()
    if not scope or not target_id or not body_text:
        await state.clear()
        await callback.message.edit_text("Нет данных для рассылки.", reply_markup=_targeted_broadcast_return_keyboard(return_callback))
        return
    if not _can_manage_targeted_broadcast(callback.from_user.id, scope, int(target_id)):
        await state.clear()
        await callback.message.edit_text("Нет прав.", reply_markup=_targeted_broadcast_return_keyboard(return_callback))
        return

    payload, error = _resolve_targeted_broadcast_payload(scope, int(target_id), body_text)
    if not payload:
        await state.clear()
        await callback.message.edit_text(error or "Не удалось подготовить рассылку.", reply_markup=_targeted_broadcast_return_keyboard(return_callback))
        return

    ok_count, fail_count = await send_custom_broadcast(callback.bot, payload["recipient_ids"], payload["text"])
    await state.clear()
    await callback.message.edit_text(
        "📢 Рассылка завершена.\n\n"
        f"Успешно: {ok_count}\n"
        f"Ошибок: {fail_count}",
        reply_markup=_targeted_broadcast_return_keyboard(payload["return_callback"]),
    )


@router.callback_query(F.data == "targeted_broadcast_cancel")
async def targeted_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    return_callback = data.get("broadcast_return_callback", "admin_menu")
    await state.clear()
    await callback.message.edit_text(
        "Отправка сообщения отменена.",
        reply_markup=_targeted_broadcast_return_keyboard(return_callback),
    )


@router.callback_query(F.data.startswith("admin_tournament_invite_menu_"))
async def admin_tournament_invite_menu(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    token = ensure_tournament_invite_token(tournament_id, regenerate=False)
    if not token:
        await callback.answer("Не удалось получить ссылку.", show_alert=True)
        return

    bot_username = ""
    try:
        me = await callback.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    link = f"https://t.me/{bot_username}?start=tournament_invite_{token}" if bot_username else "недоступна"

    text = (
        "🔗 Ссылка-приглашение турнира\n\n"
        f"Текущая ссылка:\n{link}\n\n"
        "Вы можете показать ссылку отдельным сообщением или обновить её."
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Показать ссылку", callback_data=f"admin_tournament_invite_show_{tournament_id}")
    builder.button(text="♻️ Обновить ссылку", callback_data=f"admin_tournament_invite_reset_{tournament_id}")
    builder.button(text="🔙 Назад к турниру", callback_data=f"admin_tournament_manage_{tournament_id}")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("admin_tournament_invite_show_"))
async def admin_tournament_invite_show(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    token = ensure_tournament_invite_token(tournament_id, regenerate=False)
    if not token:
        await callback.answer("Не удалось получить ссылку.", show_alert=True)
        return

    bot_username = ""
    try:
        me = await callback.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""
    if not bot_username:
        await callback.answer("Не удалось определить username бота.", show_alert=True)
        return

    link = f"https://t.me/{bot_username}?start=tournament_invite_{token}"
    await callback.answer()
    await callback.message.answer(f"Ссылка-приглашение в турнир:\n{link}")

@router.callback_query(F.data.startswith("admin_tournament_invite_reset_"))
async def admin_tournament_invite_reset(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    token = ensure_tournament_invite_token(tournament_id, regenerate=True)
    if not token:
        await callback.answer("Не удалось обновить ссылку.", show_alert=True)
        return

    bot_username = ""
    try:
        me = await callback.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""
    if not bot_username:
        await callback.answer("Ссылка обновлена, но username бота не определён.", show_alert=True)
        return

    link = f"https://t.me/{bot_username}?start=tournament_invite_{token}"
    await callback.answer("Ссылка турнира обновлена.", show_alert=True)
    await callback.message.answer(f"Новая ссылка турнира:\n{link}")

# ---------- Завершение турнира ----------
@router.callback_query(F.data.startswith("admin_finish_tournament_"))
async def admin_finish_tournament_confirm(callback: CallbackQuery):
    """Подтверждение завершения турнира."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)

    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, завершить",
              callback_data=f"confirm_finish_tournament_{tournament_id}")
    kb.button(text="❌ Нет",
              callback_data=f"admin_tournament_manage_{tournament_id}")
    kb.adjust(1)

    await callback.message.edit_text(
        f"Завершить турнир «{tournament['name']}»?\n\n"
        f"🏅 Будут начислены очки за места:\n"
        f"🥇 1 место: 20 очков\n"
        f"🥈 2 место: 15 очков\n"
        f"🥉 3 место: 10 очков\n\n"
        f"Статус турнира изменится на 'Завершён'.",
        reply_markup=kb.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_finish_tournament_"))
async def confirm_finish_tournament(callback: CallbackQuery):
    """Завершение турнира с начислением очков."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    # Импортируем функцию завершения
    from razryad_arena_utils import finish_tournament_with_awards, get_team_by_id

    first, second, third = finish_tournament_with_awards(tournament_id)

    if first is None:
        await callback.answer("❌ Ошибка при завершении турнира", show_alert=True)
        return

    # Получаем названия команд
    first_team = get_team_by_id(first) if first else None
    second_team = get_team_by_id(second) if second else None
    third_team = get_team_by_id(third) if third else None

    first_name = first_team['name'] if first_team else "???"
    second_name = second_team['name'] if second_team else "???"
    third_name = third_team['name'] if third_team else "???"

    await callback.answer(f"✅ Турнир завершён!", show_alert=True)

    tournament = get_tournament_by_id(tournament_id)
    if tournament:
        await refresh_rating_channel_posts(callback.bot, sport_key=tournament["sport"])

    await callback.message.answer(
        f"🏆 Турнир завершён!\n\n"
        f"🥇 1 место: {first_name} (+20 очков)\n"
        f"🥈 2 место: {second_name} (+15 очков)\n"
        f"🥉 3 место: {third_name} (+10 очков)"
    )

# ---------- Заявки на турниры (общие) ----------

@router.callback_query(F.data == "admin_applications")
async def admin_applications(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_admin_applications_list(callback.message)

async def show_admin_applications_list(message):
    """Показывает общий список pending-заявок на турниры."""
    apps = get_pending_applications()
    if not apps:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await message.edit_text("Нет заявок.", reply_markup=kb)
        return

    tournament_ids = sorted({app['tournament_id'] for app in apps})
    overbooked_map = _get_overbooked_tournaments_map(tournament_ids)

    text = "Заявки на турниры:\n"
    builder = InlineKeyboardBuilder()
    for app in apps:
        text += f"\nID {app['id']}: {app['team_name']} -> {app['tournament_name']}"
        builder.button(text=f"ℹ️ {app['id']}",
                       callback_data=f"admin_pending_team_info_{app['id']}")
        builder.button(text=f"✅ Одобрить",
                       callback_data=f"approve_{app['id']}")
        builder.button(text=f"❌ Отклонить",
                       callback_data=f"reject_{app['id']}")

    if overbooked_map:
        text += "\n\n⚠️ Внимание: есть переполненные турниры (approved > max):"
        for tournament_id in sorted(overbooked_map.keys()):
            meta = overbooked_map[tournament_id]
            text += f"\n- {meta['name']}: {meta['approved']}/{meta['max_teams']}"

    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(3)
    await message.edit_text(text, reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("admin_pending_team_info_"))
async def admin_pending_team_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    app_id = int(callback.data.split("_")[4])
    app = _get_tournament_application_details(app_id)
    if not app:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    tournament = get_tournament_by_id(app['tournament_id'])
    text = _build_admin_team_card_text(
        app['team_id'],
        tournament=tournament,
        app_status=app['status']
    )
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if app['status'] == 'pending':
        builder.button(text="✅ Одобрить", callback_data=f"approve_{app['id']}")
        builder.button(text="❌ Отклонить", callback_data=f"reject_{app['id']}")
    builder.button(text="🔙 Назад к заявкам",
                   callback_data="admin_applications")
    builder.adjust(2)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.regexp(r"^approve_\d+(?:_\d+)?(?:_\d+)?(?:_[a-z]+)?$"))
async def approve_app(callback: CallbackQuery):
    """Одобрение заявки."""
    parts = callback.data.split("_")
    app_id = int(parts[1])
    tournament_id = int(parts[2]) if len(parts) > 2 else None
    page_offset = int(parts[3]) if len(parts) > 3 else 0
    section = parts[4] if len(parts) > 4 else None
    app = _get_tournament_application_details(app_id)
    target_tournament_id = tournament_id or (app["tournament_id"] if app else None)
    if not target_tournament_id or not can_manage_tournament(callback.from_user.id, target_tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    result = approve_application(app_id)
    if result.get("ok"):
        request_site_sync(f"application_approved:{app_id}")
        if result.get("referral_sport_key"):
            await refresh_rating_channel_posts(callback.bot, sport_key=result["referral_sport_key"])
        if app:
            await _notify_tournament_application_status(
                callback.bot,
                app['tournament_id'],
                app['team_id'],
                "approved",
            )
        referral_note = ""
        if result.get("referral_awards"):
            referral_note = f"\nРеферальных начислений: {result['referral_awards']}"
        await callback.answer(f"Заявка одобрена!{referral_note}")
    else:
        await callback.answer(_build_approve_error_text(result), show_alert=True)

    if tournament_id:
        if section in TOURNAMENT_APPLICATION_SECTIONS:
            await show_tournament_application_section(callback.message, tournament_id, section, offset=page_offset)
        else:
            await show_tournament_applications_hub(callback.message, tournament_id)
    else:
        await show_admin_applications_list(callback.message)

@router.callback_query(F.data.regexp(r"^reject_\d+(?:_\d+)?(?:_\d+)?(?:_[a-z]+)?$"))
async def reject_app(callback: CallbackQuery):
    """Отклонение заявки."""
    parts = callback.data.split("_")
    app_id = int(parts[1])
    tournament_id = int(parts[2]) if len(parts) > 2 else None
    page_offset = int(parts[3]) if len(parts) > 3 else 0
    section = parts[4] if len(parts) > 4 else None
    app = _get_tournament_application_details(app_id)
    target_tournament_id = tournament_id or (app["tournament_id"] if app else None)
    if not target_tournament_id or not can_manage_tournament(callback.from_user.id, target_tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    reject_application(app_id)
    request_site_sync(f"application_rejected:{app_id}")
    if app:
        await _notify_tournament_application_status(
            callback.bot,
            app['tournament_id'],
            app['team_id'],
            "rejected",
        )
    await callback.answer("Заявка отклонена!")

    if tournament_id:
        if section in TOURNAMENT_APPLICATION_SECTIONS:
            await show_tournament_application_section(callback.message, tournament_id, section, offset=page_offset)
        else:
            await show_tournament_applications_hub(callback.message, tournament_id)
    else:
        await show_admin_applications_list(callback.message)

@router.callback_query(F.data.regexp(r"^exclude_\d+_\d+$"))
async def exclude_app(callback: CallbackQuery):
    """Legacy callback: запрашивает подтверждение исключения."""
    parts = callback.data.split("_")
    app_id = int(parts[1])
    tournament_id = int(parts[2])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_exclusion_confirm(callback.message, tournament_id, app_id, offset=0)
    await callback.answer()

# ---------- Заявки на конкретный турнир ----------

async def show_tournament_applications_hub(message, tournament_id: int):
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await message.answer("Турнир не найден.")
        return

    counts = _get_tournament_application_status_counts(tournament_id)
    approved_count, max_teams = _get_tournament_capacity_info(tournament_id)

    text = (
        "👥 Команды и заявки турнира\n\n"
        f"Турнир: {tournament['name']}\n"
        f"✅ Участники: {counts['approved']}"
    )
    if max_teams is not None:
        text += f" из {max_teams}"
    text += (
        f"\n⏳ Новые заявки: {counts['pending']}\n"
        f"🚫 Исключенные: {counts['excluded']}\n"
        f"📚 Отклоненные: {counts['rejected']}"
    )

    if max_teams is not None and max_teams > 0 and approved_count > max_teams:
        text += f"\n\n⚠️ Переполнение: одобрено {approved_count}/{max_teams}"

    text += "\n\nВыберите раздел:"
    await message.edit_text(
        text,
        reply_markup=_build_tournament_hub_keyboard(tournament_id, counts)
    )


async def show_tournament_application_section(message, tournament_id: int, section: str, offset: int = 0):
    config = TOURNAMENT_APPLICATION_SECTIONS.get(section)
    tournament = get_tournament_by_id(tournament_id)

    if not config or not tournament:
        await message.answer("Турнир или раздел не найден.")
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM tournament_applications
        WHERE tournament_id=? AND status=?
    """, (tournament_id, config["status"]))
    total = cur.fetchone()[0]

    if total > 0 and offset >= total:
        offset = ((total - 1) // PAGE_SIZE) * PAGE_SIZE
    if offset < 0:
        offset = 0

    cur.execute(f"""
        SELECT a.id, a.team_id, a.status, t.name AS team_name
        FROM tournament_applications a
        JOIN teams t ON a.team_id = t.id
        WHERE a.tournament_id=? AND a.status=?
        ORDER BY {config["order"]}
        LIMIT ? OFFSET ?
    """, (tournament_id, config["status"], PAGE_SIZE, offset))
    apps = cur.fetchall()
    conn.close()

    if not apps:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад к разделам",
                       callback_data=f"admin_tournament_teams_{tournament_id}")
        builder.adjust(1)
        await message.edit_text(
            f"{config['title']}\n\nТурнир: {tournament['name']}\n\n{config['empty']}",
            reply_markup=builder.as_markup()
        )
        return

    end_pos = min(offset + PAGE_SIZE, total)
    text = (
        f"{config['title']} ({offset + 1}-{end_pos} из {total})\n"
        f"Турнир: {tournament['name']}\n\n"
        f"{config['description']}\n\n"
    )

    for index, app in enumerate(apps, start=offset + 1):
        text += f"{index}. {app['team_name']}\n"

    text += f"\n{config['action_hint']}"

    if section == "approved" and (tournament['status'] != 'registration' or tournament['bracket_generated']):
        text += "\n\nℹ️ Исключение недоступно: турнир уже не на этапе регистрации или сетка уже сгенерирована."

    builder = InlineKeyboardBuilder()
    for app in apps:
        builder.button(
            text=f"ℹ️ {app['team_name']}",
            callback_data=f"admin_tournament_team_info_{section}_{tournament_id}_{app['team_id']}_{app['id']}_{offset}"
        )

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_tournament_section_page_{section}_{tournament_id}_{offset-PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_tournament_section_page_{section}_{tournament_id}_{offset+PAGE_SIZE}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="🔙 Назад к разделам",
                   callback_data=f"admin_tournament_teams_{tournament_id}")
    builder.adjust(1)

    await message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_tournament_section_page_"))
async def admin_tournament_section_page(callback: CallbackQuery):
    parts = callback.data.split("_")
    section = parts[4]
    tournament_id = int(parts[5])
    offset = int(parts[6])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_tournament_application_section(callback.message, tournament_id, section, offset=offset)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_tournament_section_(approved|pending|excluded|rejected)_\d+$"))
async def admin_tournament_section(callback: CallbackQuery):
    parts = callback.data.split("_")
    section = parts[3]
    tournament_id = int(parts[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_tournament_application_section(callback.message, tournament_id, section, offset=0)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin_tournament_teams_\d+$"))
async def admin_tournament_teams(callback: CallbackQuery):
    """Хаб раздела команд и заявок конкретного турнира."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_tournament_applications_hub(callback.message, tournament_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tournament_teams_page_"))
async def admin_tournament_teams_page_legacy(callback: CallbackQuery):
    """Устаревшая пагинация общего списка: перенаправляет в новый хаб."""
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_tournament_applications_hub(callback.message, tournament_id)
    await callback.answer()

async def show_tournament_exclusions_list(message, tournament_id, offset: int = 0):
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await message.answer("Турнир не найден.")
        return
    if tournament['status'] != 'registration' or tournament['bracket_generated']:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К разделам",
                                  callback_data=f"admin_tournament_teams_{tournament_id}")]
        ])
        await message.edit_text(
            "Исключение команд доступно только на этапе регистрации и до генерации сетки.",
            reply_markup=kb
        )
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM tournament_applications
        WHERE tournament_id=? AND status='approved'
    """, (tournament_id,))
    total = cur.fetchone()[0]

    if total > 0 and offset >= total:
        offset = ((total - 1) // PAGE_SIZE) * PAGE_SIZE
    if offset < 0:
        offset = 0

    cur.execute("""
        SELECT a.id, t.name as team_name
        FROM tournament_applications a
        JOIN teams t ON t.id = a.team_id
        WHERE a.tournament_id=? AND a.status='approved'
        ORDER BY a.updated_at DESC, a.id DESC
        LIMIT ? OFFSET ?
    """, (tournament_id, PAGE_SIZE, offset))
    apps = cur.fetchall()
    conn.close()

    if not apps:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К участникам",
                                  callback_data=f"admin_tournament_section_approved_{tournament_id}")]
        ])
        await message.edit_text("Нет одобренных команд для исключения.", reply_markup=kb)
        return

    end_pos = min(offset + PAGE_SIZE, total)
    text = (
        f"🚫 Исключение команд из турнира ({offset + 1}-{end_pos} из {total})\n"
        f"Турнир: {tournament['name']}\n\n"
        "Выберите команду для исключения:"
    )

    builder = InlineKeyboardBuilder()
    for app in apps:
        builder.button(
            text=f"🚫 {app['team_name']}",
            callback_data=f"admin_tournament_exclude_pick_{tournament_id}_{app['id']}_{offset}"
        )

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_tournament_exclusions_page_{tournament_id}_{offset-PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_tournament_exclusions_page_{tournament_id}_{offset+PAGE_SIZE}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="🔙 К участникам",
                   callback_data=f"admin_tournament_section_approved_{tournament_id}")
    builder.adjust(1)
    await message.edit_text(text, reply_markup=builder.as_markup())

async def show_exclusion_confirm(message, tournament_id: int, app_id: int, offset: int, return_callback: str | None = None):
    app = _get_tournament_application_details(app_id)
    tournament = get_tournament_by_id(tournament_id)
    if return_callback is None:
        return_callback = f"admin_tournament_exclusions_page_{tournament_id}_{offset}"
    if not app or not tournament or app['tournament_id'] != tournament_id:
        await message.edit_text("Заявка не найдена.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад",
                                  callback_data=return_callback)]
        ]))
        return

    text = (
        f"⚠️ Подтвердите исключение\n\n"
        f"Турнир: {tournament['name']}\n"
        f"Команда: {app['team_name']}\n\n"
        "Команда будет исключена из турнира (статус заявки станет «Исключено»)."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, исключить",
                              callback_data=f"admin_tournament_exclude_confirm_{tournament_id}_{app_id}_{offset}_approved")],
        [InlineKeyboardButton(text="❌ Нет",
                              callback_data=return_callback)]
    ])
    await message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data.regexp(r"^admin_tournament_exclusions_\d+$"))
async def admin_tournament_exclusions(callback: CallbackQuery):
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_tournament_application_section(callback.message, tournament_id, "approved", offset=0)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_tournament_exclusions_page_"))
async def admin_tournament_exclusions_page(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    offset = int(parts[5])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_tournament_exclusions_list(callback.message, tournament_id, offset=offset)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_tournament_exclude_pick_"))
async def admin_tournament_exclude_pick(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    app_id = int(parts[5])
    offset = int(parts[6])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_exclusion_confirm(
        callback.message,
        tournament_id,
        app_id,
        offset,
        return_callback=f"admin_tournament_section_page_approved_{tournament_id}_{offset}"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("admin_tournament_exclude_confirm_"))
async def admin_tournament_exclude_confirm(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    app_id = int(parts[5])
    offset = int(parts[6])
    section = parts[7] if len(parts) > 7 else None
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    app = _get_tournament_application_details(app_id)

    result = exclude_team_from_tournament(app_id)
    if result.get("ok"):
        request_site_sync(f"application_excluded:{app_id}")
        if app:
            await _notify_tournament_application_status(
                callback.bot,
                app['tournament_id'],
                app['team_id'],
                "excluded",
            )
        await callback.answer("Команда исключена из турнира.")
    else:
        await callback.answer(_build_exclude_error_text(result), show_alert=True)

    if section in TOURNAMENT_APPLICATION_SECTIONS:
        await show_tournament_application_section(callback.message, tournament_id, section, offset=offset)
    else:
        await show_tournament_exclusions_list(callback.message, tournament_id, offset=offset)

@router.callback_query(F.data.startswith("admin_tournament_allow_reapply_"))
async def admin_tournament_allow_reapply(callback: CallbackQuery):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    app_id = int(parts[5])
    offset = int(parts[6])
    section = parts[7] if len(parts) > 7 else None
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    result = allow_reapply_excluded_application(app_id)
    if result.get("ok"):
        request_site_sync(f"application_reapply_allowed:{app_id}")
        await callback.answer("Повторная заявка разрешена: статус переведён в pending.")
    else:
        reason = result.get("reason")
        if reason == "not_registration":
            await callback.answer("Разрешение повторной заявки доступно только на этапе регистрации.", show_alert=True)
        elif reason == "not_excluded":
            await callback.answer("Это действие доступно только для статуса excluded.", show_alert=True)
        elif reason == "not_found":
            await callback.answer("Заявка не найдена.", show_alert=True)
        elif reason == "already_processed":
            await callback.answer("Заявка уже была изменена другим админом.", show_alert=True)
        else:
            await callback.answer("Не удалось изменить статус заявки.", show_alert=True)

    if section in TOURNAMENT_APPLICATION_SECTIONS:
        await show_tournament_application_section(callback.message, tournament_id, section, offset=offset)
    else:
        await show_tournament_applications_hub(callback.message, tournament_id)

@router.callback_query(F.data.startswith("admin_tournament_team_info_"))
async def admin_tournament_team_info(callback: CallbackQuery):
    """Подробная информация о команде в контексте заявки на конкретный турнир."""
    parts = callback.data.split("_")
    if len(parts) > 8 and parts[4] in TOURNAMENT_APPLICATION_SECTIONS:
        section = parts[4]
        tournament_id = int(parts[5])
        team_id = int(parts[6])
        app_id = int(parts[7])
        offset = int(parts[8]) if len(parts) > 8 else 0
    else:
        tournament_id = int(parts[4])
        team_id = int(parts[5])
        app_id = int(parts[6])
        offset = int(parts[7]) if len(parts) > 7 else 0
        app = _get_tournament_application_details(app_id)
        section = app['status'] if app and app['status'] in TOURNAMENT_APPLICATION_SECTIONS else 'pending'
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return

    tournament = get_tournament_by_id(tournament_id)
    app = _get_tournament_application_details(app_id)
    if not tournament or not app or app['tournament_id'] != tournament_id or app['team_id'] != team_id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    text = _build_admin_team_card_text(
        team_id,
        tournament=tournament,
        app_status=app['status']
    )
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    can_exclude = tournament['status'] == 'registration' and not tournament['bracket_generated']

    if section == 'pending' and app['status'] == 'pending':
        builder.button(text="✅ Одобрить",
                       callback_data=f"approve_{app_id}_{tournament_id}_{offset}_{section}")
        builder.button(text="❌ Отклонить",
                       callback_data=f"reject_{app_id}_{tournament_id}_{offset}_{section}")
    elif section == 'approved' and app['status'] == 'approved' and can_exclude:
        builder.button(text="🚫 Исключить из турнира",
                       callback_data=f"admin_tournament_exclude_pick_{tournament_id}_{app_id}_{offset}")
    elif section == 'excluded' and app['status'] == 'excluded':
        builder.button(text="♻️ Разрешить повторную заявку",
                       callback_data=f"admin_tournament_allow_reapply_{tournament_id}_{app_id}_{offset}_{section}")

    if app['status'] == 'approved':
        builder.button(
            text="🔁 Замены состава",
            callback_data=f"admin_tournament_roster_{tournament_id}_{team_id}_{app_id}_{section}_{offset}"
        )

    builder.button(text=f"🔙 Назад в раздел: {_section_label(section)}",
                   callback_data=_section_back_callback(section, tournament_id, offset))
    builder.adjust(2)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


def _build_tournament_roster_member_picker(
    tournament_id: int,
    team_id: int,
    *,
    callback_prefix: str,
    back_callback: str,
    include_captain_marker: bool = False,
):
    members = list(get_tournament_team_members(tournament_id, team_id))
    captain_id = get_effective_tournament_captain_id(tournament_id, team_id)
    builder = InlineKeyboardBuilder()
    for member in members:
        member_dict = dict(member)
        builder.button(
            text=_build_roster_member_label(member_dict, is_captain_member=include_captain_marker and int(member_dict["id"]) == captain_id),
            callback_data=f"{callback_prefix}_{member_dict['id']}",
        )
    builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data.startswith("admin_tournament_roster_"))
async def admin_tournament_roster_open(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[3])
    team_id = int(parts[4])
    app_id = int(parts[5])
    section = parts[6]
    offset = int(parts[7])
    if not can_manage_tournament_team_roster(callback.from_user.id, tournament_id, team_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    refresh_callback = callback.data
    back_callback = f"admin_tournament_team_info_{section}_{tournament_id}_{team_id}_{app_id}_{offset}"
    await state.clear()
    await state.update_data(
        roster_refresh_callback=refresh_callback,
        roster_back_callback=back_callback,
        tournament_id=tournament_id,
        team_id=team_id,
    )
    await _show_tournament_roster_screen(
        callback.message,
        tournament_id,
        team_id,
        viewer_telegram_id=callback.from_user.id,
        refresh_callback=refresh_callback,
        back_callback=back_callback,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_roster_open_"))
async def tournament_roster_open(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[3])
    team_id = int(parts[4])
    if not can_manage_tournament_team_roster(callback.from_user.id, tournament_id, team_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        roster_refresh_callback=callback.data,
        roster_back_callback=f"tournament_{tournament_id}",
        tournament_id=tournament_id,
        team_id=team_id,
    )
    await _show_tournament_roster_screen(
        callback.message,
        tournament_id,
        team_id,
        viewer_telegram_id=callback.from_user.id,
        refresh_callback=callback.data,
        back_callback=f"tournament_{tournament_id}",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_roster_from_list_"))
async def tournament_roster_from_list(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    team_id = int(parts[5])
    if not can_manage_tournament_team_roster(callback.from_user.id, tournament_id, team_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        roster_refresh_callback=callback.data,
        roster_back_callback=f"tournament_roster_manage_{tournament_id}",
        tournament_id=tournament_id,
        team_id=team_id,
    )
    await _show_tournament_roster_screen(
        callback.message,
        tournament_id,
        team_id,
        viewer_telegram_id=callback.from_user.id,
        refresh_callback=callback.data,
        back_callback=f"tournament_roster_manage_{tournament_id}",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_roster_manage_"))
async def tournament_roster_manage(callback: CallbackQuery, state: FSMContext):
    tournament_id = int(callback.data.split("_")[3])
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return
    teams = []
    for team in get_tournament_teams(tournament_id, status="approved"):
        team_id = team["id"] if isinstance(team, dict) else team["id"]
        if can_manage_tournament_team_roster(callback.from_user.id, tournament_id, team_id):
            teams.append(team)
    if not teams:
        await callback.answer("У вас нет доступа к заменам в этом турнире.", show_alert=True)
        return
    await state.clear()
    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(text=f"👥 {team['name']}", callback_data=f"tournament_roster_from_list_{tournament_id}_{team['id']}")
    builder.button(text="🔙 К турниру", callback_data=f"tournament_{tournament_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"🔁 Замены состава\n\n🏆 Турнир: {tournament['name']}\n\nВыберите команду:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^tournament_roster_add_\d+_\d+$"))
async def tournament_roster_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отдельное добавление отключено. Используйте замену игрока.", show_alert=True)


@router.callback_query(F.data.regexp(r"^tournament_roster_remove_\d+_\d+$"))
async def tournament_roster_remove_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отдельное удаление отключено. Используйте замену игрока.", show_alert=True)


@router.callback_query(F.data.regexp(r"^tournament_roster_remove_pick_\d+_\d+_\d+$"))
async def tournament_roster_remove_pick(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отдельное удаление отключено. Используйте замену игрока.", show_alert=True)


@router.callback_query(F.data.startswith("tournament_roster_captain_"))
async def tournament_roster_captain_start(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[3])
    team_id = int(parts[4])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Назначать турнирного капитана может только админ или ответственный турнира.", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if tournament and tournament["status"] == "finished":
        await callback.answer("Турнир завершен. Изменения недоступны.", show_alert=True)
        return
    context = await state.get_data()
    await callback.message.edit_text(
        "Выберите нового турнирного капитана:",
        reply_markup=_build_tournament_roster_member_picker(
            tournament_id,
            team_id,
            callback_prefix=f"tournament_roster_assign_{tournament_id}_{team_id}",
            back_callback=context.get("roster_refresh_callback") or f"tournament_roster_open_{tournament_id}_{team_id}",
            include_captain_marker=True,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_roster_assign_"))
async def tournament_roster_assign(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[3])
    team_id = int(parts[4])
    user_id = int(parts[5])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Назначать турнирного капитана может только админ или ответственный турнира.", show_alert=True)
        return
    context = await state.get_data()
    actor = get_user(callback.from_user.id)
    ok, error = assign_tournament_team_captain(tournament_id, team_id, user_id, actor["id"] if actor else None)
    if not ok:
        await callback.answer(error or "Не удалось назначить турнирного капитана.", show_alert=True)
        return
    await _show_tournament_roster_screen(
        callback.message,
        tournament_id,
        team_id,
        viewer_telegram_id=callback.from_user.id,
        refresh_callback=context.get("roster_refresh_callback") or f"tournament_roster_open_{tournament_id}_{team_id}",
        back_callback=context.get("roster_back_callback") or f"tournament_roster_manage_{tournament_id}",
        notice="✅ Турнирный капитан назначен.",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^tournament_roster_replace_\d+_\d+$"))
async def tournament_roster_replace_start(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[3])
    team_id = int(parts[4])
    if not can_manage_tournament_team_roster(callback.from_user.id, tournament_id, team_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if tournament and tournament["status"] == "finished":
        await callback.answer("Турнир завершен. Изменения недоступны.", show_alert=True)
        return
    context = await state.get_data()
    await callback.message.edit_text(
        "Выберите игрока, которого нужно заменить:",
        reply_markup=_build_tournament_roster_member_picker(
            tournament_id,
            team_id,
            callback_prefix=f"tournament_roster_replace_pick_{tournament_id}_{team_id}",
            back_callback=context.get("roster_refresh_callback") or f"tournament_roster_open_{tournament_id}_{team_id}",
            include_captain_marker=True,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_roster_replace_pick_"))
async def tournament_roster_replace_pick(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    tournament_id = int(parts[4])
    team_id = int(parts[5])
    old_user_id = int(parts[6])
    context = await state.get_data()
    if not can_manage_tournament_team_roster(callback.from_user.id, tournament_id, team_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        roster_mode="replace",
        tournament_id=tournament_id,
        team_id=team_id,
        old_user_id=old_user_id,
        roster_refresh_callback=context.get("roster_refresh_callback") or f"tournament_roster_open_{tournament_id}_{team_id}",
        roster_back_callback=context.get("roster_back_callback") or f"tournament_roster_manage_{tournament_id}",
    )
    await state.set_state(TournamentRosterEdit.username_input)
    await callback.message.edit_text(
        "Введите username нового игрока для замены.\nНапример: `nickname` или `@nickname`",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=(context.get("roster_refresh_callback") or f"tournament_roster_open_{tournament_id}_{team_id}"),
            )
        ]]),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(TournamentRosterEdit.username_input)
async def tournament_roster_username_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите username игрока.")
        return
    username = text[1:] if text.startswith("@") else text
    user = get_user_by_username(username)
    if not user:
        await message.answer("Пользователь с таким username не найден.")
        return
    data = await state.get_data()
    mode = data.get("roster_mode")
    tournament_id = int(data["tournament_id"])
    team_id = int(data["team_id"])
    team = get_team_by_id(team_id)
    old_user = get_user_by_id(data.get("old_user_id")) if data.get("old_user_id") else None
    preview = [
        "Подтвердите изменение состава",
        "",
        f"Команда: {team['name'] if team else team_id}",
    ]
    if mode == "replace" and old_user:
        preview.append(f"Замена: {old_user['first_name']} -> {user['first_name']}")
    preview.append("")
    preview.append("После подтверждения запрос уйдет игроку.")
    preview.append("Замена вступит в силу только после его согласия.")
    await state.update_data(pending_user_id=user["id"])
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="tournament_roster_confirm")
    builder.button(text="❌ Отмена", callback_data="tournament_roster_cancel")
    builder.adjust(1)
    await state.set_state(TournamentRosterEdit.confirm)
    await message.answer("\n".join(preview), reply_markup=builder.as_markup())


@router.callback_query(F.data == "tournament_roster_confirm")
async def tournament_roster_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mode = data.get("roster_mode")
    tournament_id = int(data["tournament_id"])
    team_id = int(data["team_id"])
    actor = get_user(callback.from_user.id)
    pending_user_id = int(data["pending_user_id"])
    refresh_callback = data.get("roster_refresh_callback") or f"tournament_roster_open_{tournament_id}_{team_id}"
    back_callback = data.get("roster_back_callback") or f"tournament_roster_manage_{tournament_id}"
    if mode != "replace":
        await callback.answer("Поддерживается только замена игрока.", show_alert=True)
        return

    request_id, error = create_tournament_roster_change_request(
        tournament_id,
        team_id,
        int(data["old_user_id"]),
        pending_user_id,
        actor["id"] if actor else None,
    )
    if not request_id:
        await callback.answer(error or "Не удалось подготовить запрос на замену.", show_alert=True)
        return

    request_row = get_tournament_roster_change_request(request_id)
    new_user = get_user_by_id(pending_user_id)
    old_user = get_user_by_id(int(data["old_user_id"]))
    current_captain_id = get_effective_tournament_captain_id(tournament_id, team_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Согласен", callback_data=f"tournament_roster_request_accept_{request_id}")
    builder.button(text="❌ Отказаться", callback_data=f"tournament_roster_request_decline_{request_id}")
    builder.adjust(1)
    request_text_lines = [
        "🔁 Вас приглашают на замену в турнире",
        "",
        f"🏆 Турнир: {request_row['tournament_name']}",
        f"👥 Команда: {request_row['team_name']}",
        f"Замена: {request_row['old_first_name']} -> {request_row['new_first_name']}",
    ]
    if current_captain_id == int(data["old_user_id"]):
        request_text_lines.extend(["", "👑 После подтверждения вы станете турнирным капитаном этой команды."])
    request_text_lines.extend(["", "Подтвердите участие, если готовы войти в состав на этот турнир."])

    try:
        await callback.bot.send_message(
            new_user["telegram_id"],
            "\n".join(request_text_lines),
            reply_markup=builder.as_markup(),
        )
    except Exception:
        update_tournament_roster_change_request_status(request_id, "cancelled")
        await callback.answer("Не удалось отправить запрос игроку. Пусть он сначала запустит бота и нажмет /start.", show_alert=True)
        return

    await state.clear()
    await _show_tournament_roster_screen(
        callback.message,
        tournament_id,
        team_id,
        viewer_telegram_id=callback.from_user.id,
        refresh_callback=refresh_callback,
        back_callback=back_callback,
        notice="✅ Запрос на замену отправлен игроку. Состав обновится после его подтверждения.",
    )
    await callback.answer()


@router.callback_query(F.data == "tournament_roster_cancel")
async def tournament_roster_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tournament_id = int(data.get("tournament_id") or 0)
    team_id = int(data.get("team_id") or 0)
    refresh_callback = data.get("roster_refresh_callback") or f"tournament_roster_open_{tournament_id}_{team_id}"
    back_callback = data.get("roster_back_callback") or f"tournament_roster_manage_{tournament_id}"
    await state.clear()
    await _show_tournament_roster_screen(
        callback.message,
        tournament_id,
        team_id,
        viewer_telegram_id=callback.from_user.id,
        refresh_callback=refresh_callback,
        back_callback=back_callback,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tournament_roster_request_accept_"))
async def tournament_roster_request_accept(callback: CallbackQuery):
    request_id = int(callback.data.split("_")[4])
    user = get_user(callback.from_user.id)
    ok, error, request_row = accept_tournament_roster_change_request(request_id, user["id"] if user else 0)
    if not ok:
        await callback.answer(error or "Не удалось подтвердить замену.", show_alert=True)
        return
    requester = get_user_by_id(int(request_row["requested_by_user_id"])) if request_row["requested_by_user_id"] else None
    if requester and requester["telegram_id"]:
        try:
            await callback.bot.send_message(
                requester["telegram_id"],
                f"✅ Игрок {request_row['new_first_name']} подтвердил замену в команде «{request_row['team_name']}» на турнире «{request_row['tournament_name']}».",
            )
        except Exception:
            pass
    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 К турниру", callback_data=f"tournament_{request_row['tournament_id']}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"✅ Вы подтвердили участие в турнире «{request_row['tournament_name']}» за команду «{request_row['team_name']}».",
        reply_markup=builder.as_markup(),
    )
    await callback.answer("Замена подтверждена.")


@router.callback_query(F.data.startswith("tournament_roster_request_decline_"))
async def tournament_roster_request_decline(callback: CallbackQuery):
    request_id = int(callback.data.split("_")[4])
    user = get_user(callback.from_user.id)
    ok, error, request_row = decline_tournament_roster_change_request(request_id, user["id"] if user else 0)
    if not ok:
        await callback.answer(error or "Не удалось отклонить запрос.", show_alert=True)
        return
    requester = get_user_by_id(int(request_row["requested_by_user_id"])) if request_row["requested_by_user_id"] else None
    if requester and requester["telegram_id"]:
        try:
            await callback.bot.send_message(
                requester["telegram_id"],
                f"❌ Игрок {request_row['new_first_name']} отказался от замены в команде «{request_row['team_name']}» на турнире «{request_row['tournament_name']}».",
            )
        except Exception:
            pass
    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 К турниру", callback_data=f"tournament_{request_row['tournament_id']}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"❌ Вы отказались от участия в турнире «{request_row['tournament_name']}» за команду «{request_row['team_name']}».",
        reply_markup=builder.as_markup(),
    )
    await callback.answer("Запрос отклонен.")

@router.callback_query(F.data.startswith("admin_tournament_applications_"))
async def admin_tournament_applications(callback: CallbackQuery):
    """Устаревший callback: открывает новый хаб раздела турнира."""
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_tournament_applications_hub(callback.message, tournament_id)
    await callback.answer()

# ---------- Создание матча ----------

class CreateMatch(StatesGroup):
    tournament = State()
    team1 = State()
    team2 = State()
    date = State()
    location = State()

@router.callback_query(F.data == "admin_create_match")
async def admin_create_match_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM tournaments WHERE status IN ('registration', 'active')")
    tournaments = cur.fetchall()
    conn.close()
    if not tournaments:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Нет доступных турниров.", reply_markup=kb)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for t in tournaments:
        builder.button(
            text=t['name'], callback_data=f"match_tournament_{t['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text("Выберите турнир:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("match_tournament_"))
async def match_choose_tournament(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tournament_id = int(callback.data.split("_")[2])
    await state.update_data(tournament_id=tournament_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.name FROM teams t
        JOIN tournament_applications a ON t.id = a.team_id
        WHERE a.tournament_id=? AND a.status='approved'
    """, (tournament_id,))
    teams = cur.fetchall()
    conn.close()
    if len(teams) < 2:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Недостаточно команд в турнире (нужно минимум 2).", reply_markup=kb)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(text=team['name'],
                       callback_data=f"match_team1_{team['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_create_match")
    builder.adjust(1)
    await callback.message.edit_text("Выберите ПЕРВУЮ команду:", reply_markup=builder.as_markup())
    await state.set_state(CreateMatch.team1)

@router.callback_query(CreateMatch.team1, F.data.startswith("match_team1_"))
async def match_choose_team1(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    team1_id = int(callback.data.split("_")[2])
    await state.update_data(team1_id=team1_id)
    data = await state.get_data()
    tournament_id = data['tournament_id']
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.name FROM teams t
        JOIN tournament_applications a ON t.id = a.team_id
        WHERE a.tournament_id=? AND a.status='approved' AND t.id != ?
    """, (tournament_id, team1_id))
    teams = cur.fetchall()
    conn.close()
    if not teams:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await callback.message.edit_text("Нет других команд в турнире.", reply_markup=kb)
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(text=team['name'],
                       callback_data=f"match_team2_{team['id']}")
    builder.button(text="🔙 Назад", callback_data="admin_create_match")
    builder.adjust(1)
    await callback.message.edit_text("Выберите ВТОРУЮ команду:", reply_markup=builder.as_markup())
    await state.set_state(CreateMatch.team2)

@router.callback_query(CreateMatch.team2, F.data.startswith("match_team2_"))
async def match_choose_team2(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    team2_id = int(callback.data.split("_")[2])
    await state.update_data(team2_id=team2_id)
    await state.set_state(CreateMatch.date)
    await callback.message.edit_text("Введите дату и время матча (например, 1 янв. 18:00):")

@router.message(CreateMatch.date)
async def match_enter_date(message: Message, state: FSMContext):
    datetime_str = message.text.strip()
    if parse_russian_datetime(datetime_str) is None:
        await message.answer("❌ Неверный формат. Введите дату и время в формате: день месяц часы:минуты (например: 1 янв. 18:00)\n"
                             "Часы и минуты должны быть двузначными (например, 09:05, 18:00)."
                             "Подсказка по месяцам:\n"
                             "'янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'"
                             )
        return
    await state.update_data(date=datetime_str)
    await state.set_state(CreateMatch.location)
    await message.answer("Введите место проведения:")

@router.message(CreateMatch.location)
async def match_enter_location(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO matches (tournament_id, team1_id, team2_id, match_date, location, status)
        VALUES (?, ?, ?, ?, ?, 'scheduled')
    """, (data['tournament_id'], data['team1_id'], data['team2_id'], data['date'], message.text))
    conn.commit()

    # ---------- Уведомления командам ----------
    members1 = get_team_members(data['team1_id'])
    members2 = get_team_members(data['team2_id'])
    tournament = get_tournament_by_id(data['tournament_id'])
    team1_name = get_team_by_id(data['team1_id'])['name']
    team2_name = get_team_by_id(data['team2_id'])['name']
    match_info = (f"⚔️ Новый матч в турнире {tournament['name']}!\n"
                  f"Команда: {team1_name} vs {team2_name}\n"
                  f"📅 Дата: {data['date']}\n"
                  f"📍 Место: {message.text}")
    for member in members1 + members2:
        try:
            await message.bot.send_message(member['telegram_id'], match_info)
        except Exception as e:
            logger.info(
                f"Не удалось отправить уведомление {member['telegram_id']}: {e}")

    conn.close()
    await state.clear()
    await message.answer("Матч создан!", reply_markup=admin_menu_keyboard())

# ---------- Ввод результата матча ----------

class EnterResult(StatesGroup):
    match = State()
    score1 = State()
    score2 = State()
    volleyball_sets = State()
    player_stats = State()

# Обработчик admin_enter_result больше не используется
# Ввод результата теперь через brackets.py → match_input.py

def _load_legacy_match_for_stats(match_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.*, t.name AS tournament_name, t.sport,
               tm1.name AS team1_name, tm2.name AS team2_name
        FROM matches m
        LEFT JOIN tournaments t ON t.id = m.tournament_id
        LEFT JOIN teams tm1 ON tm1.id = m.team1_id
        LEFT JOIN teams tm2 ON tm2.id = m.team2_id
        WHERE m.id=?
    """, (match_id,))
    row = cur.fetchone()
    conn.close()
    return row

def _fetch_players(team1_id: int, team2_id: int) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.id, u.first_name, u.username, tm.team_id
        FROM users u
        JOIN team_members tm ON tm.user_id = u.id
        WHERE tm.team_id IN (?, ?)
        ORDER BY tm.team_id, u.first_name
        """,
        (team1_id, team2_id),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

async def _prompt_next_legacy_player(message: Message, state: FSMContext):
    data = await state.get_data()
    players = data.get("legacy_players", [])
    idx = data.get("legacy_player_index", 0)
    if idx >= len(players):
        await _save_legacy_player_stats_and_finish(message, state)
        return

    player = players[idx]
    team1_id = data.get("team1_id")
    team_name = data.get("team1_name") if player["team_id"] == team1_id else data.get("team2_name")
    sport = normalize_sport_name(data.get("sport_mode"))

    if sport == "Football":
        prompt = "Введите goals:assists (пример: 1:0)"
    elif sport == "Basketball":
        prompt = "Введите points:fouls (пример: 18:3)"
    else:
        prompt = "Введите points:aces (пример: 14:2)"

    await message.answer(
        f"Игрок {idx + 1}/{len(players)}\n"
        f"Команда: {team_name}\n"
        f"Игрок: {player['first_name']} (@{player['username'] or 'без_username'})\n\n"
        f"{prompt}"
    )

async def _save_legacy_player_stats_and_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    sport = normalize_sport_name(data.get("sport_mode"))
    match_id = data.get("match_id")
    stats = data.get("legacy_player_stats", [])

    for item in stats:
        if sport == "Football":
            upsert_football_player_stat(
                "legacy",
                match_id,
                item["user_id"],
                item["team_id"],
                item.get("goals", 0),
                item.get("assists", 0),
            )
        elif sport == "Basketball":
            upsert_basketball_player_stat(
                "legacy",
                match_id,
                item["user_id"],
                item["team_id"],
                item.get("points", 0),
                item.get("fouls", 0),
            )
        elif sport == "Volleyball":
            upsert_volleyball_player_stat(
                "legacy",
                match_id,
                item["user_id"],
                item["team_id"],
                item.get("points", 0),
                item.get("aces", 0),
            )

    if sport == "Volleyball":
        replace_volleyball_set_scores("legacy", match_id, data.get("volleyball_sets", []))

    await state.clear()
    await message.answer("Результат и персональная статистика сохранены!", reply_markup=admin_menu_keyboard())

@router.callback_query(F.data.startswith("result_match_"))
async def result_choose_match(callback: CallbackQuery, state: FSMContext):
    match_id = int(callback.data.split("_")[2])
    await state.update_data(match_id=match_id)
    await state.set_state(EnterResult.score1)
    await callback.message.edit_text("Введите счёт первой команды (число):")
    await callback.answer()

@router.message(EnterResult.score1)
async def result_enter_score1(message: Message, state: FSMContext):
    try:
        score1 = int(message.text)
    except ValueError:
        await message.answer("Введите число!")
        return
    await state.update_data(score1=score1)
    await state.set_state(EnterResult.score2)
    await message.answer("Введите счёт второй команды (число):")

@router.message(EnterResult.score2)
async def result_enter_score2(message: Message, state: FSMContext):
    try:
        score2 = int(message.text)
    except ValueError:
        await message.answer("Введите число!")
        return
    data = await state.get_data()
    match_id = data['match_id']
    score1 = data['score1']
    score2_value = score2

    conn = get_connection()
    sport = "CS2"
    match_meta = None
    try:
        cur = conn.cursor()
        cur.execute("UPDATE matches SET score1=?, score2=?, status='completed' WHERE id=?",
                    (score1, score2, match_id))

        cur.execute(
            "SELECT team1_id, team2_id, tournament_id FROM matches WHERE id=?", (match_id,))
        match = cur.fetchone()
        if match:
            cur.execute("SELECT sport FROM tournaments WHERE id=?",
                        (match['tournament_id'],))
            tour = cur.fetchone()
            sport = normalize_sport_name(tour['sport']) if tour else "CS2"
            match_meta = match

            replace_match_team_rating(
                source_type=SOURCE_LEGACY_MATCH,
                match_id=match_id,
                sport_key=sport,
                tournament_id=match["tournament_id"],
                team1_id=match["team1_id"],
                team2_id=match["team2_id"],
                score1=score1,
                score2=score2,
                actor_user_id=None,
                conn=conn,
            )

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.info(f"Ошибка при сохранении результата: {e}")
        await message.answer("Произошла ошибка, попробуйте позже.")
        return
    finally:
        conn.close()

    if match_meta:
        await refresh_rating_channel_posts(message.bot, sport_key=sport, entity_type=ENTITY_TEAM)

    if match_meta and sport in ("Football", "Basketball", "Volleyball"):
        players = _fetch_players(match_meta["team1_id"], match_meta["team2_id"])
        if not players:
            await state.clear()
            await message.answer("Результат сохранён! В командах нет игроков для статистики.", reply_markup=admin_menu_keyboard())
            return

        await state.update_data(
            sport_mode=sport,
            team1_id=match_meta["team1_id"],
            team2_id=match_meta["team2_id"],
            score1=score1,
            score2=score2_value,
            legacy_players=players,
            legacy_player_index=0,
            legacy_player_stats=[],
        )

        t1 = get_team_by_id(match_meta["team1_id"])
        t2 = get_team_by_id(match_meta["team2_id"])
        await state.update_data(
            team1_name=t1["name"] if t1 else "Команда 1",
            team2_name=t2["name"] if t2 else "Команда 2",
        )

        if sport == "Volleyball":
            await state.set_state(EnterResult.volleyball_sets)
            await message.answer(
                "🏐 Результат матча сохранён.\n"
                "Введите счет по партиям в формате 25:20,23:25,15:13\n"
                "Количество выигранных партий должно совпадать с итоговым счетом."
            )
            return

        await state.set_state(EnterResult.player_stats)
        await _prompt_next_legacy_player(message, state)
        return

    await state.clear()
    await message.answer("Результат сохранён!", reply_markup=admin_menu_keyboard())

@router.message(EnterResult.volleyball_sets)
async def result_enter_volleyball_sets(message: Message, state: FSMContext):
    data = await state.get_data()
    raw = message.text.strip()
    try:
        sets_raw = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
        if not sets_raw:
            raise ValueError
        parsed = []
        team1_wins = 0
        team2_wins = 0
        for chunk in sets_raw:
            if ":" not in chunk:
                raise ValueError
            left, right = chunk.split(":", 1)
            p1 = int(left)
            p2 = int(right)
            if p1 < 0 or p2 < 0 or p1 == p2:
                raise ValueError
            parsed.append((p1, p2))
            if p1 > p2:
                team1_wins += 1
            else:
                team2_wins += 1
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: 25:20,23:25,15:13")
        return

    if team1_wins != data.get("score1") or team2_wins != data.get("score2"):
        await message.answer("❌ Партии не совпадают с итоговым счетом матча.")
        return

    await state.update_data(volleyball_sets=parsed)
    await state.set_state(EnterResult.player_stats)
    await _prompt_next_legacy_player(message, state)

@router.message(EnterResult.player_stats)
async def result_enter_player_stats(message: Message, state: FSMContext):
    data = await state.get_data()
    sport = normalize_sport_name(data.get("sport_mode"))
    players = data.get("legacy_players", [])
    idx = data.get("legacy_player_index", 0)
    stats = data.get("legacy_player_stats", [])

    if idx >= len(players):
        await _save_legacy_player_stats_and_finish(message, state)
        return

    player = players[idx]
    text = message.text.strip()
    try:
        parts = text.split(":")
        left = int(parts[0]) if len(parts) > 0 else 0
        right = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        await message.answer("❌ Неверный формат. Введите два числа через ':'.")
        return

    if left < 0 or right < 0:
        await message.answer("❌ Значения не могут быть отрицательными.")
        return

    payload = {
        "user_id": player["id"],
        "team_id": player["team_id"],
    }
    if sport == "Football":
        payload["goals"] = left
        payload["assists"] = right
    elif sport == "Basketball":
        payload["points"] = left
        payload["fouls"] = right
    else:
        payload["points"] = left
        payload["aces"] = right

    stats.append(payload)
    await state.update_data(legacy_player_stats=stats, legacy_player_index=idx + 1)
    await _prompt_next_legacy_player(message, state)

# ---------- Управление пользователями ----------


def _parse_favorite_sports_display(raw_value) -> str:
    if not raw_value:
        return "не указаны"
    try:
        raw_sports = json.loads(raw_value)
        if isinstance(raw_sports, list):
            return ", ".join(map_sports_to_display(raw_sports)) or "не указаны"
    except Exception:
        pass
    return str(raw_value)


def _build_admin_user_card_text(user: dict) -> str:
    age = _row_value(user, "age")
    age_str = str(age) if age is not None else "не указан"
    email = _display_optional_text(_row_value(user, 'email'))
    city = _display_optional_text(_row_value(user, 'city'))
    username_raw = _row_value(user, 'username')
    username = f"@{username_raw}" if username_raw else "не указан"
    return (
        f"👤 Пользователь #{user['id']}\n"
        f"Telegram ID: {user['telegram_id']}\n"
        f"Имя: {_display_optional_text(_row_value(user, 'first_name'), 'не указано')}\n"
        f"Фамилия: {_display_optional_text(_row_value(user, 'last_name'))}\n"
        f"Username: {username}\n"
        f"Email: {email}\n"
        f"Город: {city}\n"
        f"Возраст: {age_str}\n"
        f"Steam: {_display_optional_text(_row_value(user, 'steam_id'))}\n"
        f"Любимые виды спорта: {_parse_favorite_sports_display(_row_value(user, 'favorite_sports'))}\n"
        f"Роль: {user['role']}\n"
        f"Статус: {'🔴 Забанен' if user['is_banned'] else '🟢 Активен'}"
    )


def _admin_user_card_keyboard(user_id: int, source: str, offset: int = 0, query: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить данные", callback_data=f"admin_user_edit_menu_{user_id}")
    builder.button(text="🏅 Изменить рейтинг", callback_data=f"admin_user_rating_change_{user_id}")
    builder.button(text="🧹 Очистить рейтинг", callback_data=f"admin_user_rating_clear_{user_id}")
    builder.button(text="🔄 Сменить роль", callback_data=f"admin_user_changerole_{user_id}")
    builder.button(text="🔨 Бан/Разбан", callback_data=f"admin_user_toggleban_{user_id}")
    if source == "list":
        builder.button(text="🔙 Назад к списку", callback_data=f"admin_user_page_{offset}")
    elif source == "search":
        builder.button(text="🔙 К результатам поиска", callback_data=f"admin_search_page_{query}_{offset}")
    else:
        builder.button(text="🔙 Назад", callback_data="admin_users")
    builder.adjust(1)
    return builder.as_markup()


def _admin_user_edit_menu_keyboard(user_id: int, source: str = "direct", offset: int = 0, query: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for callback, label in [
        ("first_name", "Имя"),
        ("last_name", "Фамилия"),
        ("username", "Username"),
        ("email", "Email"),
        ("city", "Город"),
        ("age", "Возраст"),
        ("steam_id", "Steam"),
        ("favorite_sports", "Любимые виды спорта"),
    ]:
        builder.button(text=f"✏️ {label}", callback_data=f"admin_user_edit_field_{callback}_{user_id}")
    if source == "list":
        back_callback = f"admin_user_view_{user_id}_list_{offset}"
    elif source == "search":
        back_callback = f"admin_user_view_{user_id}_search_{query}_{offset}"
    else:
        back_callback = f"admin_user_view_{user_id}_direct"
    builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def _admin_team_manage_card_keyboard(team_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Данные", callback_data=f"admin_team_edit_menu_{team_id}")
    builder.button(text="👥 Состав", callback_data=f"admin_team_members_{team_id}")
    builder.button(text="🏅 Изменить рейтинг", callback_data=f"admin_team_rating_change_{team_id}")
    builder.button(text="🧹 Очистить рейтинг", callback_data=f"admin_team_rating_clear_{team_id}")
    builder.button(text="🗑 Удалить", callback_data=f"admin_team_delete_confirm_{team_id}")
    builder.button(text="🔙 К списку", callback_data="admin_teams")
    builder.adjust(1)
    return builder.as_markup()


def _admin_team_edit_menu_keyboard(team_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Название", callback_data=f"admin_team_edit_field_name_{team_id}")
    builder.button(text="🏙 Город", callback_data=f"admin_team_edit_field_city_{team_id}")
    builder.button(text="👤 Лимит участников", callback_data=f"admin_team_edit_field_max_members_{team_id}")
    builder.button(text="👑 Капитан", callback_data=f"admin_team_change_captain_{team_id}")
    builder.button(text="🔓/🔒 Набор", callback_data=f"admin_team_toggle_open_{team_id}")
    builder.button(text="🔔 Уведомления", callback_data=f"admin_team_toggle_notify_{team_id}")
    builder.button(text="🔗 Режим ссылки", callback_data=f"admin_team_toggle_invite_mode_{team_id}")
    builder.button(text="📨 Ссылка включена/выкл", callback_data=f"admin_team_toggle_invite_enabled_{team_id}")
    builder.button(text="🔙 Назад", callback_data=f"admin_team_manage_{team_id}")
    builder.adjust(1)
    return builder.as_markup()


def _admin_team_members_keyboard(team_id: int, members: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for member in members:
        builder.button(
            text=_format_user_label(member),
            callback_data=f"admin_team_member_actions_{team_id}_{member['id']}"
        )
    builder.button(text="➕ Добавить участника", callback_data=f"admin_team_member_add_{team_id}")
    builder.button(text="🚫 Заблокированные", callback_data=f"admin_team_blocks_{team_id}")
    builder.button(text="🔙 Назад", callback_data=f"admin_team_manage_{team_id}")
    builder.adjust(1)
    return builder.as_markup()


def _admin_team_member_actions_keyboard(team_id: int, user_id: int, is_captain_member: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not is_captain_member:
        builder.button(text="➖ Удалить из команды", callback_data=f"admin_team_member_remove_{team_id}_{user_id}")
        builder.button(text="⛔ Исключить и заблокировать", callback_data=f"admin_team_member_exclude_{team_id}_{user_id}")
        builder.button(text="👑 Сделать капитаном", callback_data=f"admin_team_make_captain_{team_id}_{user_id}")
    builder.button(text="🔙 Назад", callback_data=f"admin_team_members_{team_id}")
    builder.adjust(1)
    return builder.as_markup()


def _admin_team_blocks_keyboard(team_id: int, blocks: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in blocks:
        builder.button(
            text=f"{_format_user_label(row)}",
            callback_data=f"admin_team_unblock_{team_id}_{row['user_id']}"
        )
    builder.button(text="🔙 Назад", callback_data=f"admin_team_members_{team_id}")
    builder.adjust(1)
    return builder.as_markup()

class UserManagement(StatesGroup):
    searching = State()

@router.callback_query(F.data == "admin_users")
async def admin_users_menu(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список пользователей",
                   callback_data="admin_user_list")
    builder.button(text="🔍 Поиск", callback_data="admin_user_search")
    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text("Управление пользователями:", reply_markup=builder.as_markup())

@router.callback_query(F.data == "admin_user_list")
async def admin_user_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    users = get_all_users(0, 10)
    total = get_all_users_count()  # нужно добавить функцию в utils
    await show_user_list(callback.message, users, 0, total)
    await callback.answer()

async def show_user_list(message, users, offset, total):
    """
    Отображает список пользователей в виде кнопок.
    users – список пользователей для текущей страницы
    offset – текущее смещение
    total – общее количество пользователей
    """
    if not users:
        text = "Пользователи не найдены."
        kb = back_to_main_keyboard()
        await message.edit_text(text, reply_markup=kb)
        return

    builder = InlineKeyboardBuilder()
    for u in users:
        status_icon = "🟢" if not u['is_banned'] else "🔴"
        button_text = f"{status_icon} {u['id']}: {u['first_name']} @{u['username']} ({u['role']})"
        builder.button(
            text=button_text, callback_data=f"admin_user_view_{u['id']}_list_{offset}")
    builder.adjust(1)

    # Кнопки пагинации
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_user_page_{offset-10}"))
    if offset + 10 < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_user_page_{offset+10}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    # Кнопка назад в меню пользователей
    builder.row(InlineKeyboardButton(
        text="🔙 Назад", callback_data="admin_users"))

    await message.edit_text("Список пользователей:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("admin_user_page_"))
async def admin_user_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    offset = int(callback.data.split("_")[3])
    users = get_all_users(offset, 10)
    total = get_all_users_count()
    await show_user_list(callback.message, users, offset, total)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_user_view_"))
async def admin_user_view(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split("_")
    user_id = int(parts[3])
    source = parts[4] if len(parts) > 4 else "direct"
    offset = int(parts[5]) if source == "list" and len(parts) > 5 else 0
    query = parts[5] if source == "search" and len(parts) > 5 else ""

    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await state.update_data(
        admin_user_id=user_id,
        admin_user_source=source,
        admin_user_offset=offset,
        admin_user_query=query,
    )
    text = _build_admin_user_card_text(user)
    await callback.message.edit_text(text, reply_markup=_admin_user_card_keyboard(user_id, source, offset, query))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_user_changerole_"))
async def admin_user_changerole(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[3])
    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for role in ['player', 'captain', 'admin']:
        mark = "✅" if user['role'] == role else ""
        builder.button(
            text=f"{mark} {role}", callback_data=f"admin_user_setrole_{user_id}_{role}")
    data = await state.get_data()
    if data.get("admin_user_source") == "list":
        back_callback = f"admin_user_view_{user_id}_list_{int(data.get('admin_user_offset', 0))}"
    elif data.get("admin_user_source") == "search":
        back_callback = f"admin_user_view_{user_id}_search_{data.get('admin_user_query', '')}_{int(data.get('admin_user_offset', 0))}"
    else:
        back_callback = f"admin_user_view_{user_id}_direct"
    builder.button(text="🔙 Назад", callback_data=back_callback)
    builder.adjust(1)
    await callback.message.edit_text(f"Выберите новую роль для {user['first_name']}:", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("admin_user_setrole_"))
async def admin_user_setrole(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    user_id = int(parts[3])
    new_role = parts[4]
    update_user_role(user_id, new_role)
    await callback.answer(f"Роль изменена на {new_role}")
    await state.update_data(admin_user_id=user_id)
    await _render_admin_user_card_by_state(callback, state)

@router.callback_query(F.data.startswith("admin_user_toggleban_"))
async def admin_user_toggleban(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[3])
    new_status = toggle_user_ban(user_id)
    status_text = "забанен" if new_status else "разбанен"
    await callback.answer(f"Пользователь {status_text}")
    await state.update_data(admin_user_id=user_id)
    await _render_admin_user_card_by_state(callback, state)

@router.callback_query(F.data == "admin_user_search")
async def admin_user_search_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(UserManagement.searching)
    await callback.message.edit_text("Введите имя, username или Telegram ID для поиска:")
    await callback.answer()

@router.message(UserManagement.searching)
async def admin_user_search_results(message: Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()
    users = search_users(query)
    total = search_users_count(query)  # нужно добавить в utils
    await show_search_results(message, query, users, 0, total)

async def show_search_results(message, query, users, offset, total):
    if not users:
        kb = back_to_main_keyboard()
        await message.answer(f"По запросу «{query}» ничего не найдено.", reply_markup=kb)
        return

    builder = InlineKeyboardBuilder()
    for u in users:
        status_icon = "🟢" if not u['is_banned'] else "🔴"
        button_text = f"{status_icon} {u['id']}: {u['first_name']} @{u['username']} ({u['role']})"
        builder.button(
            text=button_text, callback_data=f"admin_user_view_{u['id']}_search_{query}_{offset}")
    builder.adjust(1)

    # Пагинация
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_search_page_{query}_{offset-10}"))
    if offset + 10 < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_search_page_{query}_{offset+10}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(
        text="🔙 Назад", callback_data="admin_users"))

    await message.answer(f"Результаты поиска по запросу «{query}»:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("admin_search_page_"))
async def admin_search_page(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    parts = callback.data.split("_")
    query = parts[3]
    offset = int(parts[4])
    # нужно изменить search_users, добавив offset/limit
    users = search_users(query, offset, 10)
    total = search_users_count(query)
    await show_search_results(callback.message, query, users, offset, total)


async def _render_admin_user_card_by_state(target, state: FSMContext, *, bot: Bot | None = None):
    data = await state.get_data()
    user_id = data.get("admin_user_id")
    if not user_id:
        return
    user = get_user_by_id(int(user_id))
    if not user:
        return
    text = _build_admin_user_card_text(user)
    markup = _admin_user_card_keyboard(
        int(user_id),
        data.get("admin_user_source", "direct"),
        int(data.get("admin_user_offset", 0)),
        data.get("admin_user_query", ""),
    )
    if isinstance(target, CallbackQuery):
        await _safe_edit_admin_message(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("admin_user_edit_menu_"))
async def admin_user_edit_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[4])
    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    data = await state.get_data()
    source = data.get("admin_user_source", "direct")
    offset = int(data.get("admin_user_offset", 0))
    query = data.get("admin_user_query", "")
    await callback.message.edit_text(
        f"✏️ Редактирование пользователя\n\n{_format_user_label(user)}\n\nВыберите поле:",
        reply_markup=_admin_user_edit_menu_keyboard(user_id, source, offset, query),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_edit_field_"))
async def admin_user_edit_field(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tail = callback.data.replace("admin_user_edit_field_", "")
    field_name, user_id_token = tail.rsplit("_", 1)
    user_id = int(user_id_token)
    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await state.update_data(admin_edit_user_id=user_id, admin_edit_user_field=field_name)
    if field_name == "favorite_sports":
        sports = get_all_sports()
        current_sports = json.loads(user['favorite_sports']) if _row_value(user, 'favorite_sports') else []
        await state.set_state(AdminUserEdit.sports)
        await state.update_data(admin_edit_user_sports=current_sports)
        await callback.message.edit_text(
            "Выберите любимые виды спорта пользователя:",
            reply_markup=sports_choice_keyboard(sports, current_sports),
        )
        await callback.answer()
        return

    prompts = {
        "first_name": "Введите новое имя:",
        "last_name": "Введите новую фамилию или '-' для очистки:",
        "username": "Введите новый username без @ или '-' для очистки:",
        "email": "Введите новый email:",
        "city": "Введите новый город или '-' для очистки:",
        "age": "Введите возраст от 0 до 100 или '-' для очистки:",
        "steam_id": "Введите SteamID/ссылку или '-' для очистки:",
    }
    await state.set_state(AdminUserEdit.value)
    await callback.message.edit_text(prompts.get(field_name, "Введите новое значение:"))
    await callback.answer()


@router.callback_query(AdminUserEdit.sports, F.data.startswith("sport_"), F.data != "sport_done")
async def admin_user_edit_sports_toggle(callback: CallbackQuery, state: FSMContext):
    sport = callback.data.replace("sport_", "")
    data = await state.get_data()
    selected = list(data.get("admin_edit_user_sports", []))
    if sport in selected:
        selected.remove(sport)
    else:
        selected.append(sport)
    await state.update_data(admin_edit_user_sports=selected)
    await callback.message.edit_reply_markup(reply_markup=sports_choice_keyboard(get_all_sports(), selected))
    await callback.answer()


@router.callback_query(AdminUserEdit.sports, F.data == "sport_done")
async def admin_user_edit_sports_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("admin_edit_user_id")
    selected = data.get("admin_edit_user_sports", [])
    if not selected:
        await callback.answer("Выберите хотя бы один вид спорта.", show_alert=True)
        return
    update_user_by_id(int(user_id), favorite_sports=json.dumps(selected, ensure_ascii=False))
    await state.set_state(None)
    await _render_admin_user_card_by_state(callback, state)
    await callback.answer("Данные обновлены")


@router.message(AdminUserEdit.value)
async def admin_user_edit_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return
    data = await state.get_data()
    user_id = data.get("admin_edit_user_id")
    field_name = data.get("admin_edit_user_field")
    if not user_id or not field_name:
        await state.clear()
        await message.answer("Сессия редактирования устарела.")
        return

    raw_value = (message.text or "").strip()
    clear_requested = raw_value == "-"
    if field_name == "email":
        if "@" not in raw_value or "." not in raw_value:
            await message.answer("Введите корректный email.")
            return
        user = get_user_by_id(int(user_id))
        if not is_email_unique(raw_value, exclude_telegram_id=user["telegram_id"]):
            await message.answer("Этот email уже используется.")
            return
        update_user_by_id(int(user_id), email=raw_value)
    elif field_name == "age":
        if clear_requested:
            update_user_by_id(int(user_id), age=None)
        else:
            try:
                age = int(raw_value)
            except ValueError:
                await message.answer("Введите число от 0 до 100.")
                return
            if age < 0 or age > 100:
                await message.answer("Введите число от 0 до 100.")
                return
            update_user_by_id(int(user_id), age=age)
    elif field_name == "steam_id":
        if clear_requested:
            update_user_by_id(int(user_id), steam_id=None)
        else:
            parsed = parse_steam_link(raw_value)
            if not parsed:
                await message.answer("Не удалось распознать SteamID или ссылку.")
                return
            update_user_steam_id(int(user_id), parsed)
    elif field_name in {"last_name", "username", "city"}:
        if field_name == "username" and not clear_requested:
            raw_value = raw_value.lstrip("@")
        update_user_by_id(int(user_id), **{field_name: None if clear_requested else raw_value})
    else:
        if not raw_value:
            await message.answer("Значение не может быть пустым.")
            return
        update_user_by_id(int(user_id), **{field_name: raw_value})

    await state.set_state(None)
    await _render_admin_user_card_by_state(message, state)


async def _start_rating_shortcut(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    entity_type: str,
    entity_id: int,
    entity_name: str,
    mode: str,
    origin: str,
):
    await state.update_data(
        rating_shortcut_origin=origin,
        rating_shortcut_mode=mode,
        rating_entity_type=entity_type,
        rating_entity_id=entity_id,
        rating_entity_name=entity_name,
        rating_sport_key=None,
        rating_format_key=None,
        rating_season_id=None,
    )
    await callback.message.edit_text(
        f"📊 Управление рейтингом\n\nВыбрано: {entity_name}\nРежим: {'Изменить рейтинг' if mode == 'change' else 'Очистить рейтинг'}\n\nВыберите тип рейтинга:",
        reply_markup=admin_rating_scope_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_user_rating_change_"))
async def admin_user_rating_change(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[4])
    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await state.update_data(admin_user_id=user_id)
    await _start_rating_shortcut(
        callback,
        state,
        entity_type=ENTITY_PLAYER,
        entity_id=user_id,
        entity_name=_format_user_label(user),
        mode="change",
        origin="user_card",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_rating_clear_"))
async def admin_user_rating_clear(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_id = int(callback.data.split("_")[4])
    user = get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await state.update_data(admin_user_id=user_id)
    await _start_rating_shortcut(
        callback,
        state,
        entity_type=ENTITY_PLAYER,
        entity_id=user_id,
        entity_name=_format_user_label(user),
        mode="clear",
        origin="user_card",
    )
    await callback.answer()


# ---------- Удаление турнира (кнопка в карточке турнира) ----------

@router.callback_query(F.data.startswith("admin_delete_tournament_"))
async def admin_delete_tournament_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tournament_id = int(callback.data.split("_")[3])
    if not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        await callback.answer("Турнир не найден", show_alert=True)
        return
    await state.update_data(tournament_id=tournament_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить",
                              callback_data="admin_confirm_delete_tournament")],
        [InlineKeyboardButton(
            text="❌ Нет", callback_data=f"admin_tournament_manage_{tournament_id}")]
    ])
    await callback.message.edit_text(f"Вы уверены, что хотите удалить турнир «{tournament['name']}»? Это действие нельзя отменить.", reply_markup=kb)
    await state.update_data(tournament_id=tournament_id, sport=tournament['sport'])

@router.callback_query(F.data == "admin_confirm_delete_tournament")
async def admin_delete_tournament_execute(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tournament_id = data.get('tournament_id')
    if not tournament_id or not can_manage_tournament(callback.from_user.id, tournament_id):
        await callback.answer("Нет прав", show_alert=True)
        return
    if not tournament_id:
        await callback.answer("Ошибка", show_alert=True)
        return

    tournament = get_tournament_by_id(tournament_id)
    sport = tournament['sport'] if tournament else None

    delete_tournament(tournament_id)
    request_site_sync(f"tournament_deleted:{tournament_id}")
    await state.clear()

    await callback.message.answer("✅ Турнир удалён.")
    # Отправляем отдельное сообщение с кнопкой
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Управление турнирами",
                              callback_data="admin_tournaments_list")]
    ])
    await callback.message.answer("Нажмите кнопку ниже:", reply_markup=kb)

# ---------- Управление рейтингом ----------

def _admin_rating_scope_display(scope: str) -> str:
    return "Общий" if scope == SCOPE_OVERALL else "Сезонный"


def _admin_rating_entity_display(entity_type: str) -> str:
    return "Игроки" if entity_type == ENTITY_PLAYER else "Команды"


def _admin_rating_format_display(format_key: str | None) -> str:
    return "Общий" if not format_key else format_key


def _display_optional_text(value: str | None, fallback: str = "не указан") -> str:
    return (value or "").strip() or fallback


def _row_value(row, key: str, default=None):
    if row is None:
        return default
    try:
        if hasattr(row, "keys"):
            keys = row.keys()
            if key in keys:
                value = row[key]
                return default if value is None else value
    except Exception:
        pass
    if isinstance(row, dict):
        value = row.get(key, default)
        return default if value is None else value
    return default


def _format_user_label(user: dict) -> str:
    username = str(_row_value(user, "username", "") or "").strip()
    name = str(_row_value(user, "first_name", "Игрок") or "Игрок").strip()
    return f"{name} (@{username})" if username else name


async def _safe_edit_admin_message(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


def _extract_forwarded_channel(message: Message) -> tuple[int, str | None] | None:
    origin = getattr(message, "forward_origin", None)
    if isinstance(origin, MessageOriginChannel):
        return int(origin.chat.id), origin.chat.title or origin.chat.username
    forwarded_chat = getattr(message, "forward_from_chat", None)
    if forwarded_chat and getattr(forwarded_chat, "type", None) == "channel":
        return int(forwarded_chat.id), forwarded_chat.title or forwarded_chat.username
    return None


async def _render_admin_rating_sports(callback: CallbackQuery, state: FSMContext):
    sports = get_all_sports()
    data = await state.get_data()
    scope = data.get("rating_scope", SCOPE_OVERALL)
    entity_type = data.get("rating_entity_type")
    entity_label = _admin_rating_entity_display(entity_type) if entity_type else "не выбрано"
    await callback.message.edit_text(
        f"📊 Управление рейтингом\n\nТип: {_admin_rating_scope_display(scope)}\nКто в рейтинге: {entity_label}\n\nВыберите вид спорта:",
        reply_markup=admin_rating_sport_picker_keyboard(sports),
    )


async def _render_admin_rating_seasons(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sport_key = data["rating_sport_key"]
    seasons = list_rating_seasons(sport_key)
    active_season = get_active_rating_season(sport_key)
    selected_season_id = int(data.get("rating_season_id") or active_season["id"])
    await state.update_data(rating_season_id=selected_season_id)
    await callback.message.edit_text(
        f"📊 Управление рейтингом\n\nТип: {_admin_rating_scope_display(data['rating_scope'])}\n"
        f"Кто в рейтинге: {_admin_rating_entity_display(data['rating_entity_type'])}\n"
        f"Спорт: {get_sport_display_name(sport_key)}\n\n"
        "Выберите сезон:",
        reply_markup=admin_rating_season_picker_keyboard(
            seasons,
            active_season_id=int(active_season["id"]) if active_season else None,
            allow_next_season=True,
        ),
    )


async def _render_admin_rating_format(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sport_key = data["rating_sport_key"]
    scope = data["rating_scope"]
    season = get_rating_season_by_id(int(data["rating_season_id"])) if scope == SCOPE_SEASONAL and data.get("rating_season_id") else None
    if scope == SCOPE_SEASONAL and not season:
        season = get_active_rating_season(sport_key)
        await state.update_data(rating_season_id=int(season["id"]) if season else None)
    season_text = f"\nСезон: {season['name']}" if season else ""
    await callback.message.edit_text(
        f"📊 Управление рейтингом\n\nТип: {_admin_rating_scope_display(scope)}\nКто в рейтинге: {_admin_rating_entity_display(data['rating_entity_type'])}\nСпорт: {get_sport_display_name(sport_key)}{season_text}\n\nВыберите формат:",
        reply_markup=admin_rating_format_picker_keyboard(
            get_format_options_for_sport(sport_key) if sport_supports_formats(sport_key) else [],
            allow_next_season=scope == SCOPE_SEASONAL and season and int(season["id"]) == int(get_active_rating_season(sport_key)["id"]),
        ),
    )


def _load_admin_rating_entities(
    entity_type: str,
    sport_key: str,
    rating_scope: str,
    format_key: str | None,
    season_id: int | None,
    offset: int,
) -> tuple[list[dict], bool, bool]:
    limit = 10
    rows, has_next = get_rating_leaderboard(
        entity_type=entity_type,
        sport_key=sport_key,
        rating_scope=rating_scope,
        format_key=format_key,
        season_id=season_id,
        limit=limit,
        offset=offset,
    )
    items: list[dict] = []
    for row in rows:
        item = {
            "id": int(row["entity_id"]),
            "rating_value": int(row.get("rating_value") or 0),
        }
        if entity_type == ENTITY_TEAM:
            item["name"] = row.get("team_name") or f"Команда #{row['entity_id']}"
        else:
            item["first_name"] = row.get("first_name") or "Игрок"
            item["username"] = row.get("username") or ""
        items.append(item)
    return items, offset > 0, has_next


async def _render_admin_rating_entities(callback: CallbackQuery, state: FSMContext, offset: int = 0):
    data = await state.get_data()
    items, has_prev, has_next = _load_admin_rating_entities(
        data["rating_entity_type"],
        data["rating_sport_key"],
        data["rating_scope"],
        data.get("rating_format_key"),
        data.get("rating_season_id"),
        offset,
    )
    season = None
    active_season = None
    if data["rating_scope"] == SCOPE_SEASONAL and data.get("rating_season_id"):
        season = get_rating_season_by_id(int(data["rating_season_id"]))
        active_season = get_active_rating_season(data["rating_sport_key"])
    season_text = f"\nСезон: {season['name']}" if season else ""
    format_label = _admin_rating_format_display(data.get("rating_format_key"))
    show_format_button = bool(sport_supports_formats(data["rating_sport_key"]))
    show_next_season_button = bool(
        data["rating_scope"] == SCOPE_SEASONAL
        and season
        and active_season
        and int(season["id"]) == int(active_season["id"])
    )
    await state.update_data(rating_entity_offset=offset)
    await _safe_edit_admin_message(
        callback,
        f"📊 Управление рейтингом\n\nТип: {_admin_rating_scope_display(data['rating_scope'])}\nКто в рейтинге: {_admin_rating_entity_display(data['rating_entity_type'])}\nСпорт: {get_sport_display_name(data['rating_sport_key'])}\nФормат: {format_label}{season_text}\n\nВыберите {'игрока' if data['rating_entity_type'] == ENTITY_PLAYER else 'команду'} для изменения очков:",
        reply_markup=admin_rating_entity_list_keyboard(
            items,
            entity_type=data["rating_entity_type"],
            offset=offset,
            has_prev=has_prev,
            has_next=has_next,
            show_format_button=show_format_button,
            show_next_season_button=show_next_season_button,
            show_publish_button=True,
            show_season_button=data["rating_scope"] == SCOPE_SEASONAL,
        ),
    )


async def _render_admin_rating_actions(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    row = get_rating_row(
        entity_type=data["rating_entity_type"],
        entity_id=int(data["rating_entity_id"]),
        sport_key=data["rating_sport_key"],
        rating_scope=data["rating_scope"],
        format_key=data.get("rating_format_key"),
        season_id=data.get("rating_season_id"),
    )
    current_value = int(row["rating_value"]) if row else 0
    season = None
    if data["rating_scope"] == SCOPE_SEASONAL and data.get("rating_season_id"):
        season = get_rating_season_by_id(int(data["rating_season_id"]))
    season_text = f"\nСезон: {season['name']}" if season else ""
    await callback.message.edit_text(
        f"📊 Управление рейтингом\n\nТип: {_admin_rating_scope_display(data['rating_scope'])}\nВыбрано: {data['rating_entity_name']}\nСпорт: {get_sport_display_name(data['rating_sport_key'])}\nФормат: {_admin_rating_format_display(data.get('rating_format_key'))}{season_text}\nТекущий рейтинг: {current_value}\n\nВыберите действие:",
        reply_markup=admin_rating_action_keyboard(),
    )


def _is_rating_shortcut(data: dict) -> bool:
    return bool(data.get("rating_shortcut_origin"))


async def _return_from_rating_shortcut(target, state: FSMContext):
    data = await state.get_data()
    origin = data.get("rating_shortcut_origin")
    if origin == "user_card":
        await _render_admin_user_card_by_state(target, state)
        return
    if origin == "team_card":
        team_id = int(data.get("admin_team_id") or data.get("rating_return_team_id") or 0)
        team_text = _build_admin_team_card_text(team_id) if team_id else None
        if team_text:
            markup = _admin_team_manage_card_keyboard(team_id)
            if isinstance(target, CallbackQuery):
                await _safe_edit_admin_message(target, team_text, markup)
            else:
                await target.answer(team_text, reply_markup=markup)


async def _handle_rating_target_shortcut(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not _is_rating_shortcut(data):
        return False
    if data.get("rating_shortcut_mode") == "clear":
        actor = get_user(callback.from_user.id)
        result = clear_rating_bucket(
            entity_type=data["rating_entity_type"],
            entity_id=int(data["rating_entity_id"]),
            sport_key=data["rating_sport_key"],
            rating_scope=data["rating_scope"],
            format_key=data.get("rating_format_key"),
            season_id=data.get("rating_season_id"),
            actor_user_id=actor["id"] if actor else None,
            reason="Точечная очистка рейтинга через админ-карточку",
        )
        if result.get("ok"):
            request_site_sync(
                f"rating_clear:{data['rating_scope']}:{data['rating_entity_type']}:{data['rating_sport_key']}:{data['rating_entity_id']}"
            )
            await refresh_rating_channel_posts(
                callback.bot,
                sport_key=data["rating_sport_key"],
                entity_type=data["rating_entity_type"],
            )
            await callback.answer("Рейтинг очищен.", show_alert=True)
        else:
            await callback.answer("Этот рейтинг уже равен нулю.", show_alert=True)
        await _return_from_rating_shortcut(callback, state)
        return True

    await _render_admin_rating_actions(callback, state)
    return True


@router.callback_query(F.data == "admin_rating")
async def admin_rating_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "📊 Управление рейтингом\n\nВыберите режим работы:",
        reply_markup=admin_rating_scope_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.in_(["admin_rating_scope_overall", "admin_rating_scope_seasonal"]))
async def admin_rating_scope_pick(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    scope = SCOPE_OVERALL if callback.data.endswith("overall") else SCOPE_SEASONAL
    await state.update_data(rating_scope=scope)
    data = await state.get_data()
    if _is_rating_shortcut(data):
        await _render_admin_rating_sports(callback, state)
    else:
        await callback.message.edit_text(
            f"📊 Управление рейтингом\n\nТип: {_admin_rating_scope_display(scope)}\n\nВыберите, чей рейтинг хотите изменить:",
            reply_markup=admin_rating_entity_keyboard(scope),
        )
    await callback.answer()


@router.callback_query(F.data.in_(["admin_rating_entity_player", "admin_rating_entity_team"]))
async def admin_rating_entity_pick(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    entity_type = ENTITY_PLAYER if callback.data.endswith("player") else ENTITY_TEAM
    await state.update_data(rating_entity_type=entity_type)
    await _render_admin_rating_sports(callback, state)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_back_entity")
async def admin_rating_back_entity(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    if _is_rating_shortcut(data):
        await _return_from_rating_shortcut(callback, state)
    else:
        await callback.message.edit_text(
            f"📊 Управление рейтингом\n\nТип: {_admin_rating_scope_display(data.get('rating_scope', SCOPE_OVERALL))}\n\nВыберите, чей рейтинг хотите изменить:",
            reply_markup=admin_rating_entity_keyboard(data.get("rating_scope", SCOPE_OVERALL)),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rating_pick_sport_"))
async def admin_rating_pick_sport(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    sport_key = callback.data.replace("admin_rating_pick_sport_", "")
    scope = (await state.get_data()).get("rating_scope")
    season = get_active_rating_season(sport_key) if scope == SCOPE_SEASONAL else None
    await state.update_data(
        rating_sport_key=sport_key,
        rating_format_key=None,
        rating_season_id=int(season["id"]) if season else None,
    )
    if scope == SCOPE_SEASONAL:
        await _render_admin_rating_seasons(callback, state)
    else:
        data = await state.get_data()
        if _is_rating_shortcut(data):
            if sport_supports_formats(sport_key):
                await _render_admin_rating_format(callback, state)
            else:
                await _handle_rating_target_shortcut(callback, state)
        else:
            await state.update_data(rating_entity_id=None)
            await _render_admin_rating_entities(callback, state, 0)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_back_sport")
async def admin_rating_back_sport(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await _render_admin_rating_sports(callback, state)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_open_formats")
async def admin_rating_open_formats(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    if not sport_supports_formats(data.get("rating_sport_key")):
        await callback.answer("Для этого спорта нет форматного рейтинга.", show_alert=True)
        return
    await _render_admin_rating_format(callback, state)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_choose_season")
async def admin_rating_choose_season(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    if data.get("rating_scope") != SCOPE_SEASONAL or not data.get("rating_sport_key"):
        await callback.answer("Сначала выберите сезонный рейтинг.", show_alert=True)
        return
    await _render_admin_rating_seasons(callback, state)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rating_pick_season_"))
async def admin_rating_pick_season(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    season_id = int(callback.data.replace("admin_rating_pick_season_", ""))
    season = get_rating_season_by_id(season_id)
    data = await state.get_data()
    if not season or (data.get("rating_sport_key") and season["sport_key"] != data["rating_sport_key"]):
        await callback.answer("Сезон не найден.", show_alert=True)
        return
    await state.update_data(rating_season_id=season_id, rating_format_key=None)
    data = await state.get_data()
    if _is_rating_shortcut(data):
        if sport_supports_formats(data["rating_sport_key"]):
            await _render_admin_rating_format(callback, state)
        else:
            await _handle_rating_target_shortcut(callback, state)
    else:
        await _render_admin_rating_entities(callback, state, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rating_pick_format_"))
async def admin_rating_pick_format(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    token = callback.data.replace("admin_rating_pick_format_", "")
    format_key = None if token == "general" else token
    await state.update_data(rating_format_key=format_key)
    data = await state.get_data()
    if _is_rating_shortcut(data):
        await _handle_rating_target_shortcut(callback, state)
    else:
        await _render_admin_rating_entities(callback, state, 0)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_pick_format_general")
async def admin_rating_pick_format_general_legacy(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.update_data(rating_format_key=None)
    data = await state.get_data()
    if _is_rating_shortcut(data):
        await _handle_rating_target_shortcut(callback, state)
    else:
        await _render_admin_rating_entities(callback, state, 0)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_next_season")
async def admin_rating_next_season_handler(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    sport_key = data.get("rating_sport_key")
    if not sport_key:
        await callback.answer("Сначала выберите спорт.", show_alert=True)
        return
    season = get_active_rating_season(sport_key)
    return_target = "season_picker" if "Выберите сезон" in ((callback.message.text or "") if callback.message else "") else "entities"
    await state.update_data(rating_next_season_return=return_target)
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="admin_rating_next_season_confirm")
    builder.button(text="❌ Отмена", callback_data="admin_rating_next_season_cancel")
    builder.adjust(1)
    await callback.message.edit_text(
        f"⚠️ Подтверждение перехода сезона\n\n"
        f"Спорт: {get_sport_display_name(sport_key)}\n"
        f"Текущий активный сезон: {season['name']}\n\n"
        "После подтверждения текущий сезон будет завершен, создастся новый пустой сезон, а общий рейтинг останется без изменений.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_rating_next_season_cancel")
async def admin_rating_next_season_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    if data.get("rating_next_season_return") == "season_picker":
        await _render_admin_rating_seasons(callback, state)
    else:
        await _render_admin_rating_entities(callback, state, int(data.get("rating_entity_offset", 0)))
    await callback.answer()


@router.callback_query(F.data == "admin_rating_next_season_confirm")
async def admin_rating_next_season_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    sport_key = data.get("rating_sport_key")
    if not sport_key:
        await callback.answer("Сначала выберите спорт.", show_alert=True)
        return
    result = advance_to_next_rating_season(sport_key)
    await state.update_data(rating_season_id=int(result["current"]["id"]))
    request_site_sync(f"rating_next_season:{sport_key}:{result['current']['id']}")
    await refresh_rating_channel_posts(callback.bot, sport_key=sport_key)
    await _render_admin_rating_entities(callback, state, 0)
    await callback.answer("Создан новый активный сезон.", show_alert=True)


@router.callback_query(F.data.startswith("admin_rating_page_"))
async def admin_rating_page(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    offset = int(callback.data.replace("admin_rating_page_", ""))
    await _render_admin_rating_entities(callback, state, max(0, offset))
    await callback.answer()


@router.callback_query(F.data == "admin_rating_back_format")
async def admin_rating_back_format(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await _render_admin_rating_sports(callback, state)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_back_entities_general")
async def admin_rating_back_entities_general(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.update_data(rating_format_key=None)
    data = await state.get_data()
    if _is_rating_shortcut(data):
        await _handle_rating_target_shortcut(callback, state)
    else:
        await _render_admin_rating_entities(callback, state, 0)
    await callback.answer()


@router.callback_query(F.data == "admin_rating_back_entities")
async def admin_rating_back_entities(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    if _is_rating_shortcut(data):
        await _return_from_rating_shortcut(callback, state)
    else:
        await _render_admin_rating_entities(callback, state, int(data.get("rating_entity_offset", 0)))
    await callback.answer()


@router.callback_query(F.data == "admin_rating_publish_channel")
async def admin_rating_publish_channel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("rating_sport_key") or not data.get("rating_entity_type") or not data.get("rating_scope"):
        await callback.answer("Сначала выберите рейтинг.", show_alert=True)
        return
    await state.set_state(AdminRatingChannelPublish.channel)
    await callback.message.answer(
        "Перешлите любое сообщение из канала или пришлите @channel_username.\n\n"
        "Чтобы отменить, отправьте: отмена"
    )
    await callback.answer()


@router.message(AdminRatingChannelPublish.channel)
async def admin_rating_publish_channel_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав.")
        return
    text = (message.text or "").strip()
    if text.lower() in {"отмена", "cancel", "/cancel"}:
        await state.set_state(None)
        await message.answer("Публикация в канал отменена.")
        return

    forwarded_channel = _extract_forwarded_channel(message)
    channel_ref: int | str | None = None
    channel_label: str | None = None
    if forwarded_channel:
        channel_ref, channel_label = forwarded_channel
    else:
        parsed_target = parse_channel_target_text(text)
        if not parsed_target:
            await message.answer("Пришлите пересланное сообщение из канала или @channel_username.")
            return
        channel_ref = parsed_target

    try:
        chat = await message.bot.get_chat(channel_ref)
    except Exception:
        await message.answer("Не удалось открыть канал. Проверьте, что бот добавлен в канал и username указан верно.")
        return

    if getattr(chat, "type", None) != "channel":
        await message.answer("Нужен именно канал Telegram, а не личный чат или группа.")
        return

    data = await state.get_data()
    actor = get_user(message.from_user.id)
    try:
        result = await publish_rating_channel_post(
            message.bot,
            chat_id=int(chat.id),
            entity_type=data["rating_entity_type"],
            sport_key=data["rating_sport_key"],
            rating_scope=data["rating_scope"],
            season_id=data.get("rating_season_id"),
            format_key=data.get("rating_format_key"),
            created_by=actor["id"] if actor else None,
        )
    except Exception as exc:
        logger.warning("Не удалось опубликовать рейтинг в канал chat_id=%s: %s", getattr(chat, "id", None), exc)
        await message.answer(
            "Не удалось опубликовать рейтинг в канал. Убедитесь, что бот есть в канале и имеет право отправлять сообщения."
        )
        return

    await state.set_state(None)
    target_name = channel_label or getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat.id)
    action_text = "Сообщение обновлено" if result.get("reused_existing") else "Live-пост опубликован"
    await message.answer(f"✅ {action_text} в канале: {target_name}")


@router.callback_query(F.data.startswith("admin_rating_pick_entity_"))
async def admin_rating_pick_entity(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    entity_id = int(callback.data.replace("admin_rating_pick_entity_", ""))
    data = await state.get_data()
    conn = get_connection()
    cur = conn.cursor()
    if data["rating_entity_type"] == ENTITY_TEAM:
        cur.execute("SELECT name FROM teams WHERE id=?", (entity_id,))
        row = cur.fetchone()
        entity_name = row["name"] if row else f"Команда #{entity_id}"
    else:
        cur.execute("SELECT first_name, username FROM users WHERE id=?", (entity_id,))
        row = cur.fetchone()
        if row:
            username = row["username"] or "без_username"
            entity_name = f"{row['first_name'] or 'Игрок'} (@{username})"
        else:
            entity_name = f"Игрок #{entity_id}"
    conn.close()
    await state.update_data(rating_entity_id=entity_id, rating_entity_name=entity_name)
    await _render_admin_rating_actions(callback, state)
    await callback.answer()


@router.callback_query(F.data.in_(["admin_rating_action_add", "admin_rating_action_sub"]))
async def admin_rating_action_pick(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    operation = "add" if callback.data.endswith("add") else "sub"
    await state.update_data(rating_operation=operation)
    label = "добавления" if operation == "add" else "снятия"
    await state.set_state(AdminRatingAdjustment.points)
    await callback.message.edit_text(f"Введите количество очков для {label}:")
    await callback.answer()


@router.message(AdminRatingAdjustment.points)
async def admin_rating_apply_points(message: Message, state: FSMContext):
    try:
        points = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите число.")
        return

    if points <= 0:
        await message.answer("Введите положительное число.")
        return

    data = await state.get_data()
    delta = points if data.get("rating_operation") == "add" else -points
    actor = get_user(message.from_user.id)
    result = apply_manual_rating_adjustment(
        entity_type=data["rating_entity_type"],
        entity_id=int(data["rating_entity_id"]),
        sport_key=data["rating_sport_key"],
        rating_scope=data["rating_scope"],
        format_key=data.get("rating_format_key"),
        season_id=data.get("rating_season_id"),
        delta=delta,
        actor_user_id=actor["id"] if actor else None,
        reason="Ручная корректировка через админ-панель",
    )
    if not result.get("ok"):
        await message.answer("Изменение не применилось: рейтинг уже равен нулю.")
        await state.clear()
        await message.answer("Панель администратора:", reply_markup=admin_menu_keyboard())
        return

    request_site_sync(
        f"rating_manual:{data['rating_scope']}:{data['rating_entity_type']}:{data['rating_sport_key']}:{data['rating_entity_id']}"
    )
    await refresh_rating_channel_posts(
        message.bot,
        sport_key=data["rating_sport_key"],
        entity_type=data["rating_entity_type"],
    )
    sign = "+" if result["applied_delta"] > 0 else ""
    if _is_rating_shortcut(data):
        await state.set_state(None)
        await message.answer(
            f"✅ Изменение применено.\n{data['rating_entity_name']}: {sign}{result['applied_delta']} очков\nНовый рейтинг: {result['new_value']}"
        )
        await _return_from_rating_shortcut(message, state)
    else:
        await state.clear()
        await message.answer(
            f"✅ Изменение применено.\n{data['rating_entity_name']}: {sign}{result['applied_delta']} очков\nНовый рейтинг: {result['new_value']}"
        )
        await message.answer("Панель администратора:", reply_markup=admin_menu_keyboard())

# ---------- Управление командами ----------

@router.callback_query(F.data == "admin_teams")
async def admin_teams_list(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await show_admin_teams_page(callback.message, 0)

async def show_admin_teams_page(message, offset: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM teams")
    total = cur.fetchone()[0]
    if total > 0 and offset >= total:
        offset = ((total - 1) // PAGE_SIZE) * PAGE_SIZE
    if offset < 0:
        offset = 0

    cur.execute("""
        SELECT id, name, sport, city
        FROM teams
        ORDER BY name
        LIMIT ? OFFSET ?
    """, (PAGE_SIZE, offset))
    teams = cur.fetchall()
    conn.close()

    if not teams:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        await message.edit_text("Нет команд.", reply_markup=kb)
        return

    end_pos = min(offset + PAGE_SIZE, total)
    text = f"Список команд ({offset + 1}-{end_pos} из {total}):"
    builder = InlineKeyboardBuilder()
    for team in teams:
        builder.button(
            text=f"⚙️ {team['name']} ({get_sport_display_name(team['sport'])})",
            callback_data=f"admin_team_manage_{team['id']}"
        )

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin_teams_page_{offset - PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav_buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin_teams_page_{offset + PAGE_SIZE}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.button(text="🔙 Назад", callback_data="admin_menu")
    builder.adjust(1)
    await message.edit_text(text, reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("admin_teams_page_"))
async def admin_teams_page(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    offset = int(callback.data.split("_")[3])
    await show_admin_teams_page(callback.message, offset)

@router.callback_query(F.data.startswith("admin_team_manage_"))
async def admin_team_manage_card(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[3])
    text = _build_admin_team_card_text(team_id)
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    await state.update_data(admin_team_id=team_id)
    await callback.message.edit_text(text, reply_markup=_admin_team_manage_card_keyboard(team_id))

@router.callback_query(F.data.startswith("admin_team_delete_confirm_"))
async def admin_team_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[4])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить",
                              callback_data=f"admin_team_delete_execute_{team_id}")],
        [InlineKeyboardButton(
            text="❌ Нет", callback_data=f"admin_team_manage_{team_id}")]
    ])
    await callback.message.edit_text(f"Вы уверены, что хотите удалить команду «{team['name']}»? Это действие нельзя отменить.", reply_markup=kb)

@router.callback_query(F.data.startswith("admin_team_delete_execute_"))
async def admin_team_delete_execute(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[4])
    delete_team_admin(team_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку команд",
                              callback_data="admin_teams")],
        [InlineKeyboardButton(text="🔙 В админ-меню",
                              callback_data="admin_menu")]
    ])
    await callback.message.edit_text("✅ Команда удалена.", reply_markup=kb)

@router.callback_query(F.data.startswith("admin_delete_team_"))
async def admin_delete_team_legacy_redirect(callback: CallbackQuery, state: FSMContext):
    """Legacy callback: перенаправляем старые кнопки в новый поток карточки команды."""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    team_id = int(callback.data.split("_")[3])
    text = _build_admin_team_card_text(team_id)
    if not text:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    await state.update_data(admin_team_id=team_id)
    await callback.message.edit_text(text, reply_markup=_admin_team_manage_card_keyboard(team_id))


@router.callback_query(F.data.startswith("admin_team_rating_change_"))
async def admin_team_rating_change(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[4])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    await state.update_data(admin_team_id=team_id, rating_return_team_id=team_id)
    await _start_rating_shortcut(
        callback,
        state,
        entity_type=ENTITY_TEAM,
        entity_id=team_id,
        entity_name=team["name"],
        mode="change",
        origin="team_card",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_team_rating_clear_"))
async def admin_team_rating_clear(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[4])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    await state.update_data(admin_team_id=team_id, rating_return_team_id=team_id)
    await _start_rating_shortcut(
        callback,
        state,
        entity_type=ENTITY_TEAM,
        entity_id=team_id,
        entity_name=team["name"],
        mode="clear",
        origin="team_card",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_team_edit_menu_"))
async def admin_team_edit_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[4])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    await state.update_data(admin_team_id=team_id)
    await _show_admin_team_edit_menu(callback, team_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_team_edit_field_"))
async def admin_team_edit_field(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    tail = callback.data.replace("admin_team_edit_field_", "")
    field_name, team_id_token = tail.rsplit("_", 1)
    team_id = int(team_id_token)
    await state.update_data(admin_team_id=team_id, admin_team_edit_field=field_name)
    prompts = {
        "name": "Введите новое название команды:",
        "city": "Введите новый город или '-' чтобы очистить:",
        "max_members": "Введите новый лимит участников от 1 до 10:",
    }
    await state.set_state(AdminTeamEdit.value)
    await callback.message.edit_text(prompts.get(field_name, "Введите новое значение:"))
    await callback.answer()


@router.message(AdminTeamEdit.value)
async def admin_team_edit_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return
    data = await state.get_data()
    team_id = data.get("admin_team_id")
    field_name = data.get("admin_team_edit_field")
    if not team_id or not field_name:
        await state.clear()
        await message.answer("Сессия редактирования устарела.")
        return
    raw_value = (message.text or "").strip()
    if field_name == "name":
        if len(raw_value) < 3 or len(raw_value) > 20:
            await message.answer("Название должно быть от 3 до 20 символов.")
            return
        rename_team(int(team_id), raw_value)
    elif field_name == "city":
        set_team_city(int(team_id), None if raw_value in {"", "-"} else raw_value)
    elif field_name == "max_members":
        try:
            value = int(raw_value)
        except ValueError:
            await message.answer("Введите число от 1 до 10.")
            return
        if value < 1 or value > 10:
            await message.answer("Введите число от 1 до 10.")
            return
        members_count = get_team_members_count(int(team_id))
        if value < members_count:
            await message.answer(f"Нельзя поставить лимит меньше текущего состава ({members_count}).")
            return
        update_team_max_members(int(team_id), value)
    await state.set_state(None)
    await _render_admin_team_card_by_state(message, state)


@router.callback_query(F.data.startswith("admin_team_toggle_open_"))
async def admin_team_toggle_open(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[4])
    settings = get_team_settings(team_id)
    set_team_open_status(team_id, not settings["is_open"])
    await _show_admin_team_edit_menu(callback, team_id)
    await callback.answer("Статус набора обновлен")


@router.callback_query(F.data.startswith("admin_team_toggle_notify_"))
async def admin_team_toggle_notify(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[4])
    settings = get_team_settings(team_id)
    set_team_notify_status(team_id, not settings["notify"])
    await _show_admin_team_edit_menu(callback, team_id)
    await callback.answer("Настройка уведомлений обновлена")


@router.callback_query(F.data.startswith("admin_team_toggle_invite_mode_"))
async def admin_team_toggle_invite_mode(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[5])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    current_mode = (team['invite_join_mode'] or 'request').strip().lower() if 'invite_join_mode' in team.keys() else 'request'
    set_team_invite_join_mode(team_id, 'request' if current_mode == 'direct' else 'direct')
    await _show_admin_team_edit_menu(callback, team_id)
    await callback.answer("Режим ссылки обновлен")


@router.callback_query(F.data.startswith("admin_team_toggle_invite_enabled_"))
async def admin_team_toggle_invite_enabled(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[5])
    team = get_team_by_id(team_id)
    if not team:
        await callback.answer("Команда не найдена", show_alert=True)
        return
    enabled = int((team['invite_enabled'] if 'invite_enabled' in team.keys() else 1) or 0) == 1
    set_team_invite_enabled(team_id, not enabled)
    await _show_admin_team_edit_menu(callback, team_id)
    await callback.answer("Статус ссылки обновлен")


@router.callback_query(F.data.startswith("admin_team_change_captain_"))
async def admin_team_change_captain(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[4])
    members = get_team_members(team_id)
    builder = InlineKeyboardBuilder()
    for member in members:
        builder.button(text=_format_user_label(member), callback_data=f"admin_team_make_captain_{team_id}_{member['id']}")
    builder.button(text="🔙 Назад", callback_data=f"admin_team_edit_menu_{team_id}")
    builder.adjust(1)
    await callback.message.edit_text("Выберите нового капитана:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("admin_team_make_captain_"))
async def admin_team_make_captain(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    _, _, _, _, team_id_token, user_id_token = callback.data.split("_")
    team_id = int(team_id_token)
    user_id = int(user_id_token)
    update_team_fields(team_id, captain_id=user_id)
    await state.update_data(admin_team_id=team_id)
    await _render_admin_team_card_by_state(callback, state)
    await callback.answer("Капитан обновлен")


@router.callback_query(F.data.startswith("admin_team_members_"))
async def admin_team_members_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[3])
    await state.update_data(admin_team_id=team_id)
    await _show_admin_team_members_screen(callback, team_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_team_member_actions_"))
async def admin_team_member_actions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    _, _, _, _, team_id_token, user_id_token = callback.data.split("_")
    team_id = int(team_id_token)
    user_id = int(user_id_token)
    team = get_team_by_id(team_id)
    is_captain_member = bool(team and int(team["captain_id"]) == user_id)
    user = get_user_by_id(user_id)
    await callback.message.edit_text(
        f"Участник: {_format_user_label(user or {'first_name': 'Игрок', 'username': ''})}",
        reply_markup=_admin_team_member_actions_keyboard(team_id, user_id, is_captain_member),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_team_member_remove_"))
async def admin_team_member_remove(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    _, _, _, _, team_id_token, user_id_token = callback.data.split("_")
    result = remove_team_member_admin(int(team_id_token), int(user_id_token))
    if not result.get("ok"):
        await callback.answer("Не удалось удалить участника.", show_alert=True)
        return
    await _show_admin_team_members_screen(callback, int(team_id_token))
    await callback.answer("Участник удален")


@router.callback_query(F.data.startswith("admin_team_member_exclude_"))
async def admin_team_member_exclude(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    _, _, _, _, team_id_token, user_id_token = callback.data.split("_")
    team_id = int(team_id_token)
    user_id = int(user_id_token)
    result = remove_team_member_admin(team_id, user_id)
    if not result.get("ok"):
        await callback.answer("Не удалось исключить участника.", show_alert=True)
        return
    actor = get_user(callback.from_user.id)
    block_team_member(team_id, user_id, blocked_by=actor["id"] if actor else None, reason="Исключен админом")
    await _show_admin_team_members_screen(callback, int(team_id_token))
    await callback.answer("Участник исключен и заблокирован")


@router.callback_query(F.data.startswith("admin_team_member_add_"))
async def admin_team_member_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[4])
    await state.update_data(admin_team_id=team_id)
    await state.set_state(AdminTeamEdit.member_username)
    await callback.message.edit_text("Введите username пользователя для добавления в команду:")
    await callback.answer()


@router.message(AdminTeamEdit.member_username)
async def admin_team_member_add_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return
    data = await state.get_data()
    team_id = int(data.get("admin_team_id") or 0)
    username = (message.text or "").strip().lstrip("@")
    user = get_user_by_username(username)
    if not user:
        await message.answer("Пользователь не найден.")
        return
    result = add_team_member_admin(team_id, int(user["id"]))
    if not result.get("ok"):
        reasons = {
            "already_member": "Пользователь уже в команде.",
            "blocked": "Пользователь исключен из команды и сейчас заблокирован.",
            "team_full": "В команде уже достигнут лимит участников.",
        }
        await message.answer(reasons.get(result.get("reason"), "Не удалось добавить участника."))
        return
    await state.set_state(None)
    await _render_admin_team_card_by_state(message, state)


@router.callback_query(F.data.startswith("admin_team_blocks_"))
async def admin_team_blocks(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    team_id = int(callback.data.split("_")[3])
    await _show_admin_team_blocks_screen(callback, team_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_team_unblock_"))
async def admin_team_unblock(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    _, _, _, team_id_token, user_id_token = callback.data.split("_")
    unblock_team_member(int(team_id_token), int(user_id_token))
    await _show_admin_team_blocks_screen(callback, int(team_id_token))
    await callback.answer("Блок снят")

@router.callback_query(F.data == "admin_confirm_delete_team")
async def admin_delete_team_execute_legacy(callback: CallbackQuery, state: FSMContext):
    """Legacy callback: поддержка старых сообщений с FSM-подтверждением удаления."""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    data = await state.get_data()
    team_id = data.get('team_id')
    if not team_id:
        await callback.answer("Старая кнопка устарела. Откройте «Управление командами».", show_alert=True)
        return

    delete_team_admin(team_id)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку команд",
                              callback_data="admin_teams")],
        [InlineKeyboardButton(text="🔙 В админ-меню",
                              callback_data="admin_menu")]
    ])
    await callback.message.edit_text("✅ Команда удалена.", reply_markup=kb)

@router.callback_query(F.data == "admin_broadcast_start")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    await state.set_state(AdminBroadcast.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast_cancel")]
    ])
    await callback.message.edit_text(
        "📢 Введите текст рассылки для всех активных пользователей:",
        reply_markup=kb
    )

@router.message(AdminBroadcast.text)
async def admin_broadcast_preview(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет прав")
        return

    broadcast_text = (message.text or "").strip()
    if not broadcast_text:
        await message.answer("Текст рассылки не должен быть пустым.")
        return

    await state.update_data(broadcast_text=broadcast_text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="admin_broadcast_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast_cancel")],
    ])
    await message.answer(
        "Предпросмотр рассылки:\n\n"
        f"{broadcast_text}\n\n"
        "Отправить это сообщение всем активным пользователям?",
        reply_markup=kb,
    )

@router.callback_query(F.data == "admin_broadcast_confirm")
async def admin_broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    data = await state.get_data()
    broadcast_text = (data.get("broadcast_text") or "").strip()
    if not broadcast_text:
        await callback.answer("Нет текста рассылки.", show_alert=True)
        return

    user_ids = get_active_user_telegram_ids()
    ok_count = 0
    fail_count = 0

    for telegram_id in user_ids:
        try:
            await callback.bot.send_message(telegram_id, broadcast_text)
            ok_count += 1
        except Exception:
            fail_count += 1

    await state.clear()
    await callback.message.answer(
        f"📢 Рассылка завершена.\n"
        f"Успешно: {ok_count}\n"
        f"Ошибок: {fail_count}"
    )
    await callback.message.answer("Панель администратора:", reply_markup=admin_menu_keyboard())

@router.callback_query(F.data == "admin_broadcast_cancel")
async def admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.message.edit_text("Панель администратора:", reply_markup=admin_menu_keyboard())

@router.callback_query(F.data == "admin_menu")
async def back_to_admin_menu(callback: CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.message.edit_text("Панель администратора:", reply_markup=admin_menu_keyboard())
