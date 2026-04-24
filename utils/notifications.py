"""
Уведомления по матчам сетки: назначение, изменение, напоминание и результат.
"""
import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from razryad_arena_utils import (
    build_tournament_date_lines,
    format_utc_to_msk,
    get_bracket_match_by_id,
    get_bracket_matches_due_for_reminder,
    get_effective_tournament_captain_id,
    get_match_history_details,
    get_match_history_details_by_sport,
    get_sport_display_name,
    get_team_by_id,
    get_team_members,
    get_tournament_team_members,
    get_tournament_admin_notification_chat_ids,
    get_tournament_by_id,
    get_tournament_manager_chat_ids,
    get_tournament_teams,
    get_user_by_id,
    list_tournaments_due_for_registration_deadline_notice,
    mark_tournament_registration_deadline_notified,
    mark_bracket_reminder_sent,
    normalize_sport_name,
)
from utils.veto_service import (
    ADMIN_ACTION_SCOPE_REGISTRATION_ENDED,
    send_tracked_admin_action_messages,
)

logger = logging.getLogger(__name__)


def get_registration_ended_action_key(tournament_id: int) -> str:
    return f"tournament:{int(tournament_id)}:registration-ended"


def _safe_team_name(value: str | None, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _round_label(match: dict[str, Any]) -> str:
    round_name = (match.get("round_name") or "").strip()
    if round_name:
        return round_name
    round_number = match.get("round_number")
    return f"Раунд {round_number}" if round_number else "Раунд"


def _collect_team_member_chat_ids(team1_id: int | None, team2_id: int | None, tournament_id: int | None = None) -> list[int]:
    chat_ids: set[int] = set()
    for team_id in (team1_id, team2_id):
        if not team_id:
            continue
        players = get_tournament_team_members(tournament_id, team_id) if tournament_id else get_team_members(team_id)
        for player in players:
            if isinstance(player, dict):
                telegram_id = player.get("telegram_id")
            else:
                telegram_id = player["telegram_id"] if "telegram_id" in player.keys() else None
            if telegram_id:
                chat_ids.add(int(telegram_id))
    return list(chat_ids)


def _chunk_text(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current).strip())
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current).strip())
    return chunks


async def _broadcast_text(bot: Bot, chat_ids: list[int], text: str):
    for chat_id in chat_ids:
        for chunk in _chunk_text(text):
            try:
                await bot.send_message(chat_id=chat_id, text=chunk)
            except Exception as exc:
                logger.warning("Не удалось отправить уведомление chat_id=%s: %s", chat_id, exc)


async def send_custom_broadcast(bot: Bot, chat_ids: list[int], text: str) -> tuple[int, int]:
    ok_count = 0
    fail_count = 0
    for chat_id in chat_ids:
        try:
            for chunk in _chunk_text(text):
                await bot.send_message(chat_id=chat_id, text=chunk)
            ok_count += 1
        except Exception as exc:
            logger.warning("Не удалось отправить custom-уведомление chat_id=%s: %s", chat_id, exc)
            fail_count += 1
    return ok_count, fail_count


def prepare_match_broadcast_payload(match_id: int, body_text: str = "") -> tuple[dict[str, Any] | None, str | None]:
    match = get_bracket_match_by_id(match_id)
    if not match:
        return None, "Матч не найден."
    match_dict = dict(match)
    if not match_dict.get("team1_id") or not match_dict.get("team2_id"):
        return None, "В этом матче еще нет пары команд."

    chat_ids = _collect_team_member_chat_ids(match_dict.get("team1_id"), match_dict.get("team2_id"), match_dict.get("tournament_id"))
    if not chat_ids:
        return None, "У участников этого матча нет доступных Telegram ID для рассылки."

    tournament_name = (match_dict.get("tournament_name") or "Турнир").strip()
    round_name = _round_label(match_dict)
    team1_name = _safe_team_name(match_dict.get("team1_name"), "Команда 1")
    team2_name = _safe_team_name(match_dict.get("team2_name"), "Команда 2")
    location = (match_dict.get("location") or "").strip()

    lines = [
        "📢 Сообщение по матчу",
        "",
        f"🏆 Турнир: {tournament_name}",
        f"📍 Раунд: {round_name}",
        f"⚔️ {team1_name} vs {team2_name}",
    ]
    if location:
        lines.extend(["", f"📌 Место: {location}"])
    if body_text:
        lines.extend(["", body_text.strip()])

    return {
        "scope": "match",
        "target_id": match_id,
        "recipient_ids": sorted(set(chat_ids)),
        "recipient_count": len(set(chat_ids)),
        "text": "\n".join(lines),
        "return_callback": f"bracket_match_{match_id}_{match_dict['tournament_id']}",
        "title": f"{team1_name} vs {team2_name}",
    }, None


def prepare_tournament_broadcast_payload(tournament_id: int, body_text: str = "") -> tuple[dict[str, Any] | None, str | None]:
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        return None, "Турнир не найден."
    tournament_dict = dict(tournament)

    chat_ids: set[int] = set()
    for team in get_tournament_teams(tournament_id, status="approved"):
        team_id = team["id"] if isinstance(team, dict) else team["id"]
        for member in get_tournament_team_members(tournament_id, team_id):
            telegram_id = member.get("telegram_id") if isinstance(member, dict) else (member["telegram_id"] if "telegram_id" in member.keys() else None)
            if telegram_id:
                chat_ids.add(int(telegram_id))

    for telegram_id in get_tournament_manager_chat_ids(tournament_id):
        if telegram_id:
            chat_ids.add(int(telegram_id))

    creator = get_user_by_id(tournament_dict.get("created_by")) if tournament_dict.get("created_by") else None
    if creator and creator.get("telegram_id"):
        chat_ids.add(int(creator["telegram_id"]))

    if not chat_ids:
        return None, "У этого турнира нет получателей для рассылки."

    lines = [
        "📢 Сообщение по турниру",
        "",
        f"🏆 Турнир: {(tournament_dict.get('name') or 'Турнир').strip()}",
        f"🎮 Вид спорта: {get_sport_display_name(tournament_dict.get('sport'))}",
    ]
    for line in build_tournament_date_lines(tournament_dict):
        lines.append(f"📅 {line}")
    if body_text:
        lines.extend(["", body_text.strip()])

    return {
        "scope": "tournament",
        "target_id": tournament_id,
        "recipient_ids": sorted(chat_ids),
        "recipient_count": len(chat_ids),
        "text": "\n".join(lines),
        "return_callback": f"admin_tournament_manage_{tournament_id}",
        "title": (tournament_dict.get("name") or "Турнир").strip(),
    }, None


async def notify_bracket_match_scheduled(
    bot: Bot,
    match: dict[str, Any],
    *,
    changed: bool = False,
    old_scheduled_at_utc: str | None = None,
    old_location: str | None = None,
):
    team1_name = _safe_team_name(match.get("team1_name"), "Команда 1")
    team2_name = _safe_team_name(match.get("team2_name"), "Команда 2")
    tournament_name = (match.get("tournament_name") or "Турнир").strip()
    round_name = _round_label(match)
    new_time = format_utc_to_msk(match.get("scheduled_at_utc"))
    new_location = (match.get("location") or "не указано").strip()

    if changed:
        old_time = format_utc_to_msk(old_scheduled_at_utc, fallback="не назначено")
        old_place = (old_location or "не назначено").strip()
        text = (
            "⚠️ Изменено расписание матча\n\n"
            f"🏆 Турнир: {tournament_name}\n"
            f"📍 Раунд: {round_name}\n"
            f"⚔️ {team1_name} vs {team2_name}\n\n"
            "Было:\n"
            f"🕒 {old_time}\n"
            f"📌 {old_place}\n\n"
            "Стало:\n"
            f"🕒 {new_time} (МСК)\n"
            f"📌 {new_location}"
        )
    else:
        text = (
            "⚔️ Назначен матч\n\n"
            f"🏆 Турнир: {tournament_name}\n"
            f"📍 Раунд: {round_name}\n"
            f"⚔️ {team1_name} vs {team2_name}\n\n"
            f"🕒 Время: {new_time} (МСК)\n"
            f"📌 Место: {new_location}"
        )

    chat_ids = _collect_team_member_chat_ids(match.get("team1_id"), match.get("team2_id"), match.get("tournament_id"))
    await _broadcast_text(bot, chat_ids, text)


async def notify_bracket_match_reminder(bot: Bot, match: dict[str, Any]):
    team1_name = _safe_team_name(match.get("team1_name"), "Команда 1")
    team2_name = _safe_team_name(match.get("team2_name"), "Команда 2")
    tournament_name = (match.get("tournament_name") or "Турнир").strip()
    round_name = _round_label(match)
    match_time = format_utc_to_msk(match.get("scheduled_at_utc"))
    location = (match.get("location") or "не указано").strip()

    text = (
        "⏰ Напоминание: матч через 1 час\n\n"
        f"🏆 Турнир: {tournament_name}\n"
        f"📍 Раунд: {round_name}\n"
        f"⚔️ {team1_name} vs {team2_name}\n\n"
        f"🕒 Время: {match_time} (МСК)\n"
        f"📌 Место: {location}"
    )
    chat_ids = _collect_team_member_chat_ids(match.get("team1_id"), match.get("team2_id"), match.get("tournament_id"))
    await _broadcast_text(bot, chat_ids, text)


def _format_cs2_result(match: dict[str, Any], details: dict[str, Any]) -> str:
    team1_name = _safe_team_name(match.get("team1_name"), "Команда 1")
    team2_name = _safe_team_name(match.get("team2_name"), "Команда 2")
    score1 = match.get("score1") if match.get("score1") is not None else 0
    score2 = match.get("score2") if match.get("score2") is not None else 0
    lines = [
        f"📊 Счет серии: {team1_name} {score1}:{score2} {team2_name}",
        "",
    ]

    maps = details.get("maps", []) or []
    for one_map in maps:
        map_name = one_map.get("map_name") or f"Карта {one_map.get('map_number', 1)}"
        if one_map.get("has_score"):
            map_score = f"{one_map.get('team1_score', 0)}:{one_map.get('team2_score', 0)}"
        else:
            map_score = "нет данных"
        lines.append(f"🗺 {map_name}: {map_score}")
        lines.append(f"🔵 {team1_name}:")
        players_team1 = one_map.get("players_team1", []) or []
        if players_team1:
            for p in players_team1:
                username = f" @{p['username']}" if p.get("username") else ""
                lines.append(
                    f"• {p.get('first_name', 'Игрок')}{username}: "
                    f"K:{p.get('kills', 0)} D:{p.get('deaths', 0)} A:{p.get('assists', 0)} "
                    f"| ADR:{p.get('adr', 0)} HS:{p.get('hs', 0)}"
                )
        else:
            lines.append("• нет данных")

        lines.append(f"🔴 {team2_name}:")
        players_team2 = one_map.get("players_team2", []) or []
        if players_team2:
            for p in players_team2:
                username = f" @{p['username']}" if p.get("username") else ""
                lines.append(
                    f"• {p.get('first_name', 'Игрок')}{username}: "
                    f"K:{p.get('kills', 0)} D:{p.get('deaths', 0)} A:{p.get('assists', 0)} "
                    f"| ADR:{p.get('adr', 0)} HS:{p.get('hs', 0)}"
                )
        else:
            lines.append("• нет данных")
        lines.append("")

    return "\n".join(lines).strip()


def _format_non_cs2_result(sport: str, match: dict[str, Any], details: dict[str, Any]) -> str:
    team1_name = _safe_team_name(match.get("team1_name"), "Команда 1")
    team2_name = _safe_team_name(match.get("team2_name"), "Команда 2")
    score1 = match.get("score1") if match.get("score1") is not None else 0
    score2 = match.get("score2") if match.get("score2") is not None else 0

    lines = [f"📊 Счет матча: {team1_name} {score1}:{score2} {team2_name}", ""]

    if sport == "Football":
        lines.append("⚽ Голы и ассисты:")
    elif sport == "Basketball":
        lines.append("🏀 Очки и фолы:")
    else:
        set_scores = details.get("set_scores", []) or []
        lines.append("🏐 Счет по партиям:")
        if set_scores:
            for one_set in set_scores:
                lines.append(
                    f"• Партия {one_set.get('set_number')}: "
                    f"{one_set.get('team1_points', 0)}:{one_set.get('team2_points', 0)}"
                )
        else:
            lines.append("• нет данных")
        lines.append("")
        lines.append("⚡ Очки и эйсы:")

    lines.append("")
    lines.append(f"🔵 {team1_name}:")
    for p in details.get("players_team1", []) or []:
        username = f" @{p['username']}" if p.get("username") else ""
        if sport == "Football":
            lines.append(f"• {p.get('first_name', 'Игрок')}{username}: ⚽ {p.get('goals', 0)} | 🎯 {p.get('assists', 0)}")
        elif sport == "Basketball":
            lines.append(f"• {p.get('first_name', 'Игрок')}{username}: 🏀 {p.get('points', 0)} | 🚫 {p.get('fouls', 0)}")
        else:
            lines.append(f"• {p.get('first_name', 'Игрок')}{username}: 🏐 {p.get('points', 0)} | ⚡ {p.get('aces', 0)}")
    if not (details.get("players_team1") or []):
        lines.append("• нет данных")

    lines.append("")
    lines.append(f"🔴 {team2_name}:")
    for p in details.get("players_team2", []) or []:
        username = f" @{p['username']}" if p.get("username") else ""
        if sport == "Football":
            lines.append(f"• {p.get('first_name', 'Игрок')}{username}: ⚽ {p.get('goals', 0)} | 🎯 {p.get('assists', 0)}")
        elif sport == "Basketball":
            lines.append(f"• {p.get('first_name', 'Игрок')}{username}: 🏀 {p.get('points', 0)} | 🚫 {p.get('fouls', 0)}")
        else:
            lines.append(f"• {p.get('first_name', 'Игрок')}{username}: 🏐 {p.get('points', 0)} | ⚡ {p.get('aces', 0)}")
    if not (details.get("players_team2") or []):
        lines.append("• нет данных")

    return "\n".join(lines).strip()


def _format_technical_result(match: dict[str, Any]) -> str:
    team1_name = _safe_team_name(match.get("team1_name"), "Команда 1")
    team2_name = _safe_team_name(match.get("team2_name"), "Команда 2")
    score1 = match.get("score1") if match.get("score1") is not None else 0
    score2 = match.get("score2") if match.get("score2") is not None else 0
    winner_name = _safe_team_name(match.get("winner_name"), "Победитель")
    loser_name = team1_name if match.get("technical_loser_id") == match.get("team1_id") else team2_name
    lines = [
        f"🚫 Матч завершен тех.поражением",
        f"📊 Счет матча: {team1_name} {score1}:{score2} {team2_name}",
        f"🏆 Победитель: {winner_name}",
        f"❌ Тех.поражение: {loser_name}",
    ]
    reason = (match.get("technical_reason") or "").strip()
    if reason:
        lines.append(f"📝 Причина: {reason}")
    return "\n".join(lines)


async def notify_bracket_match_result(bot: Bot, match_id: int, sport: str):
    match = get_bracket_match_by_id(match_id)
    if not match:
        return

    match_dict = dict(match)
    team1_name = _safe_team_name(match_dict.get("team1_name"), "Команда 1")
    team2_name = _safe_team_name(match_dict.get("team2_name"), "Команда 2")
    tournament_name = (match_dict.get("tournament_name") or "Турнир").strip()
    round_name = _round_label(match_dict)
    sport_normalized = normalize_sport_name(sport or match_dict.get("tournament_sport"))
    sport_display = get_sport_display_name(sport_normalized) if sport_normalized else "Спорт"

    header = (
        "✅ Матч завершен\n\n"
        f"🏆 Турнир: {tournament_name}\n"
        f"📍 Раунд: {round_name}\n"
        f"🏅 Вид спорта: {sport_display}\n"
        f"⚔️ {team1_name} vs {team2_name}\n\n"
    )

    if (match_dict.get("result_type") or "regular") == "technical":
        body = _format_technical_result(match_dict)
    elif sport_normalized == "CS2":
        details = get_match_history_details("bracket", match_id) or {}
        body = _format_cs2_result(match_dict, details)
    else:
        details = get_match_history_details_by_sport(sport_normalized, "bracket", match_id) or {}
        body = _format_non_cs2_result(sport_normalized, match_dict, details)

    chat_ids = _collect_team_member_chat_ids(match_dict.get("team1_id"), match_dict.get("team2_id"), match_dict.get("tournament_id"))
    await _broadcast_text(bot, chat_ids, header + body)


async def dispatch_due_match_reminders(bot: Bot) -> int:
    """Ищет матчи с дедлайном напоминания и отправляет уведомления."""
    due_matches = get_bracket_matches_due_for_reminder()
    sent = 0
    for match in due_matches:
        try:
            match_dict = dict(match)
            await notify_bracket_match_reminder(bot, match_dict)
            mark_bracket_reminder_sent(match_dict["id"])
            sent += 1
        except Exception as exc:
            logger.exception("Ошибка отправки напоминания по матчу %s: %s", match["id"], exc)
    return sent


async def dispatch_due_tournament_registration_deadlines(bot: Bot) -> int:
    sent = 0
    for tournament in list_tournaments_due_for_registration_deadline_notice():
        tournament_id = int(tournament["id"])
        chat_ids = get_tournament_admin_notification_chat_ids(tournament_id)
        mark_tournament_registration_deadline_notified(tournament_id)
        if not chat_ids:
            continue

        tournament_name = (tournament["name"] or "Турнир").strip()
        lines = [
            "⏳ Регистрация завершилась",
            "",
            f"🏆 Турнир: {tournament_name}",
            f"🎮 Вид спорта: {get_sport_display_name(tournament['sport'])}",
            f"📅 Регистрация: {(tournament['registration_start_date'] or '').strip() or 'не указана'} - {(tournament['registration_end_date'] or '').strip() or 'не указана'}",
            "",
            "Пора начать турнир и перейти в управление.",
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Открыть турнир",
                callback_data=f"admin_tournament_notice_open_{tournament_id}",
            )
        ]])
        await send_tracked_admin_action_messages(
            bot,
            chat_ids=chat_ids,
            text="\n".join(lines),
            reply_markup=markup,
            action_scope=ADMIN_ACTION_SCOPE_REGISTRATION_ENDED,
            action_key=get_registration_ended_action_key(tournament_id),
            tournament_id=tournament_id,
            bracket_match_id=None,
        )
        sent += 1
    return sent


# ---- Совместимость старых импортов ----

async def notify_match_scheduled(
    bot: Bot,
    match_id: int,
    team1_id: int,
    team2_id: int,
    tournament_name: str,
    round_name: str,
    match_date: str,
    location: str,
):
    team1 = get_team_by_id(team1_id)
    team2 = get_team_by_id(team2_id)
    await notify_bracket_match_scheduled(
        bot,
        {
            "id": match_id,
            "team1_id": team1_id,
            "team2_id": team2_id,
            "team1_name": _safe_team_name(team1["name"] if team1 else None, "Команда 1"),
            "team2_name": _safe_team_name(team2["name"] if team2 else None, "Команда 2"),
            "tournament_name": tournament_name,
            "round_name": round_name,
            "scheduled_at_utc": match_date,
            "location": location,
        },
        changed=False,
    )


async def notify_match_reminder(
    bot: Bot,
    match_id: int,
    team1_id: int,
    team2_id: int,
    tournament_name: str,
    round_name: str,
    match_date: str,
    location: str,
):
    team1 = get_team_by_id(team1_id)
    team2 = get_team_by_id(team2_id)
    await notify_bracket_match_reminder(
        bot,
        {
            "id": match_id,
            "team1_id": team1_id,
            "team2_id": team2_id,
            "team1_name": _safe_team_name(team1["name"] if team1 else None, "Команда 1"),
            "team2_name": _safe_team_name(team2["name"] if team2 else None, "Команда 2"),
            "tournament_name": tournament_name,
            "round_name": round_name,
            "scheduled_at_utc": match_date,
            "location": location,
        },
    )


async def notify_match_changed(
    bot: Bot,
    match_id: int,
    team1_id: int,
    team2_id: int,
    tournament_name: str,
    round_name: str,
    old_date: str,
    new_date: str,
    old_location: str,
    new_location: str,
):
    team1 = get_team_by_id(team1_id)
    team2 = get_team_by_id(team2_id)
    await notify_bracket_match_scheduled(
        bot,
        {
            "id": match_id,
            "team1_id": team1_id,
            "team2_id": team2_id,
            "team1_name": _safe_team_name(team1["name"] if team1 else None, "Команда 1"),
            "team2_name": _safe_team_name(team2["name"] if team2 else None, "Команда 2"),
            "tournament_name": tournament_name,
            "round_name": round_name,
            "scheduled_at_utc": new_date,
            "location": new_location,
        },
        changed=True,
        old_scheduled_at_utc=old_date,
        old_location=old_location,
    )


async def notify_match_result(
    bot: Bot,
    match_id: int,
    team1_id: int,
    team2_id: int,
    score1: int,
    score2: int,
    stats: list[dict[str, Any]],
    tournament_name: str,
    round_name: str,
):
    # Совместимый no-op для старого API: используем новый путь по match_id, если возможно.
    match = get_bracket_match_by_id(match_id)
    sport = match["tournament_sport"] if match else "CS2"
    await notify_bracket_match_result(bot, match_id, sport)
