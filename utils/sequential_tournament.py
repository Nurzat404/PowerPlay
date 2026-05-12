"""
Сервис для второго режима турнира — sequential (живая очередь без расписания).

В этом режиме:
- У турнира одна общая дата+время старта (`start_at_utc`) и общая локация (`default_location`).
- Матчи играются строго по одному. Текущий матч помечается `queue_state='active'`.
- После сохранения результата `advance_after_match_completed(...)` закрывает текущий и активирует следующий.
- За 1 час до старта рассылается общий broadcast, в момент старта — персональные сообщения каждой команде.
- Каждой команде отправляется/обновляется queue-сообщение со статусом очереди.
- Гейт `_match_due` в veto_service для sequential заменён на проверку `queue_state == 'active'`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from database import get_connection
from razryad_arena_utils import (
    get_bracket_match_by_id,
    get_tournament_by_id,
    get_tournament_team_members,
    get_tournament_teams,
    parse_utc_storage_datetime,
    format_utc_to_msk,
    get_sport_display_name,
)

logger = logging.getLogger(__name__)

SCHEDULE_MODE_FIXED = "fixed"
SCHEDULE_MODE_SEQUENTIAL = "sequential"

QUEUE_STATE_WAITING = "waiting"
QUEUE_STATE_ACTIVE = "active"
QUEUE_STATE_DONE = "done"


# ---------------------------------------------------------------------------
# Helpers / readers
# ---------------------------------------------------------------------------

def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _schedule_mode(tournament: Any) -> str:
    return (_row_get(tournament, "schedule_mode") or SCHEDULE_MODE_FIXED).strip() or SCHEDULE_MODE_FIXED


def is_sequential(tournament: Any) -> bool:
    return _schedule_mode(tournament) == SCHEDULE_MODE_SEQUENTIAL


def is_sequential_tournament(tournament_id: int | None) -> bool:
    if not tournament_id:
        return False
    tournament = get_tournament_by_id(int(tournament_id))
    return is_sequential(tournament) if tournament else False


def _round_label(match: Any) -> str:
    name = (_row_get(match, "round_name") or "").strip()
    if name:
        return name
    rn = _row_get(match, "round_number")
    return f"Раунд {rn}" if rn else "Раунд"


def _team_name(match: Any, side: int) -> str:
    fallback = "Команда 1" if side == 1 else "Команда 2"
    name = _row_get(match, f"team{side}_name")
    name = (name or "").strip() if isinstance(name, str) else ""
    return name or fallback


# ---------------------------------------------------------------------------
# Queue computation
# ---------------------------------------------------------------------------

def _list_main_bracket_matches(cur, tournament_id: int) -> list[dict[str, Any]]:
    """
    Возвращает все матчи сетки в правильном порядке очереди sequential-турнира:
    обычные матчи в порядке (round_number, match_number), а матч за 3-е место
    вставляется ПЕРЕД финалом (последним обычным матчем).
    """
    cur.execute(
        """
        SELECT b.*,
               t1.name AS team1_name,
               t2.name AS team2_name
        FROM tournament_brackets b
        LEFT JOIN teams t1 ON t1.id = b.team1_id
        LEFT JOIN teams t2 ON t2.id = b.team2_id
        WHERE b.tournament_id=?
        ORDER BY COALESCE(b.is_third_place, 0), b.round_number, b.match_number
        """,
        (tournament_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]

    normal: list[dict[str, Any]] = [r for r in rows if not int(r.get("is_third_place") or 0)]
    third_place: list[dict[str, Any]] = [r for r in rows if int(r.get("is_third_place") or 0)]

    if not third_place:
        return normal

    # Финал = последний обычный матч (max round_number, наименьший match_number в нём,
    # обычно один матч в финальном раунде). Вставляем матч(и) за 3-е место перед ним.
    if not normal:
        return third_place

    final_index = len(normal) - 1
    return normal[:final_index] + third_place + [normal[final_index]]


def recompute_queue(tournament_id: int) -> None:
    """
    Пересчитывает queue_position и queue_state для всех матчей сетки sequential-турнира.

    Правила:
    - queue_position идёт по порядку (round_number, match_number).
    - completed/bye матчи получают queue_state='done'.
    - Активная пара выбирается ТОЛЬКО если турнир уже стартовал
      (`sequential_started_at` заполнено). Иначе все pending — 'waiting'.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")

        # Запущен ли турнир?
        cur.execute("SELECT sequential_started_at FROM tournaments WHERE id=?", (tournament_id,))
        row = cur.fetchone()
        tournament_started = bool(row and row[0])

        matches = _list_main_bracket_matches(cur, tournament_id)
        if not matches:
            conn.commit()
            return

        position = 0
        chosen_active_id: int | None = None
        # First pass: position assignment + done states.
        for m in matches:
            position += 1
            new_state: str | None
            new_position = position

            status = (m.get("status") or "").lower()
            is_bye = bool(m.get("is_bye"))
            if status == "completed" or is_bye and status in ("completed", "bye"):
                new_state = QUEUE_STATE_DONE
            else:
                new_state = QUEUE_STATE_WAITING

            cur.execute(
                "UPDATE tournament_brackets SET queue_position=?, queue_state=? WHERE id=?",
                (new_position, new_state, m["id"]),
            )
            m["queue_position"] = new_position
            m["queue_state"] = new_state

        # Second pass: активную пару выбираем только если турнир уже запущен.
        if tournament_started:
            for m in matches:
                if m["queue_state"] != QUEUE_STATE_WAITING:
                    continue
                if (m.get("status") or "") != "pending":
                    continue
                if m.get("is_bye"):
                    continue
                if not m.get("team1_id") or not m.get("team2_id"):
                    continue
                chosen_active_id = m["id"]
                break

            if chosen_active_id:
                cur.execute(
                    "UPDATE tournament_brackets SET queue_state=? WHERE id=?",
                    (QUEUE_STATE_ACTIVE, chosen_active_id),
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_active_match(tournament_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.*, t1.name AS team1_name, t2.name AS team2_name
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            WHERE b.tournament_id=? AND b.queue_state=?
            ORDER BY b.queue_position
            LIMIT 1
            """,
            (tournament_id, QUEUE_STATE_ACTIVE),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_remaining_queue(tournament_id: int) -> list[dict[str, Any]]:
    """Все матчи, которые ещё не завершены (active + waiting), упорядоченные."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.*, t1.name AS team1_name, t2.name AS team2_name
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            WHERE b.tournament_id=?
              AND b.queue_state IN (?, ?)
              AND COALESCE(b.is_bye, 0) = 0
            ORDER BY b.queue_position
            """,
            (tournament_id, QUEUE_STATE_ACTIVE, QUEUE_STATE_WAITING),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_team_next_pending_match(tournament_id: int, team_id: int) -> dict[str, Any] | None:
    """Самый ранний (по queue_position) pending-матч команды с её участием."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.*, t1.name AS team1_name, t2.name AS team2_name
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            WHERE b.tournament_id=?
              AND COALESCE(b.is_bye, 0) = 0
              AND b.status='pending'
              AND b.queue_state IN (?, ?)
              AND (b.team1_id=? OR b.team2_id=?)
            ORDER BY b.queue_position
            LIMIT 1
            """,
            (tournament_id, QUEUE_STATE_ACTIVE, QUEUE_STATE_WAITING, team_id, team_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_team_first_round_for_team(tournament_id: int, team_id: int) -> int | None:
    """
    Возвращает наименьший round_number, в котором у команды есть слот
    (team1_id или team2_id равен team_id) среди не-завершённых матчей.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT MIN(round_number)
            FROM tournament_brackets
            WHERE tournament_id=?
              AND COALESCE(is_third_place, 0) = 0
              AND status='pending'
              AND COALESCE(is_bye, 0) = 0
              AND (team1_id=? OR team2_id=?)
            """,
            (tournament_id, team_id, team_id),
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])
    finally:
        conn.close()


def count_pending_before_round(tournament_id: int, round_number: int) -> int:
    """Сколько pending non-bye матчей в раундах < round_number ещё не сыграно."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM tournament_brackets
            WHERE tournament_id=?
              AND COALESCE(is_bye, 0) = 0
              AND status='pending'
              AND queue_state IN (?, ?)
              AND round_number < ?
              AND COALESCE(is_third_place, 0) = 0
            """,
            (tournament_id, QUEUE_STATE_ACTIVE, QUEUE_STATE_WAITING, round_number),
        )
        return int(cur.fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Approved teams & members helpers
# ---------------------------------------------------------------------------

def _approved_teams(tournament_id: int) -> list[dict[str, Any]]:
    teams = get_tournament_teams(tournament_id, status="approved") or []
    return [dict(t) for t in teams]


def _team_members(tournament_id: int, team_id: int) -> list[dict[str, Any]]:
    members = get_tournament_team_members(tournament_id, team_id) or []
    return [dict(m) for m in members]


# ---------------------------------------------------------------------------
# Queue message rendering
# ---------------------------------------------------------------------------

def format_queue_text_for_team(tournament_id: int, team_id: int) -> tuple[str, str]:
    """
    Возвращает (text, signature) для queue-сообщения команды.
    signature используется для throttling правок (если совпадает — не правим).
    """
    tournament = get_tournament_by_id(tournament_id)
    tournament_name = (_row_get(tournament, "name") or "Турнир").strip() if tournament else "Турнир"
    sport_display = get_sport_display_name(_row_get(tournament, "sport") or "")
    location = (_row_get(tournament, "default_location") or "").strip() if tournament else ""

    active = get_active_match(tournament_id)
    next_match = get_team_next_pending_match(tournament_id, team_id)

    lines: list[str] = [
        "📋 Состояние турнира",
        f"🏆 {tournament_name}",
        f"🎮 {sport_display}",
    ]
    if location:
        lines.append(f"📌 Локация: {location}")
    lines.append("")

    your_match_is_active = (
        active
        and (active.get("team1_id") == team_id or active.get("team2_id") == team_id)
    )
    if active and not your_match_is_active:
        active_team1 = _team_name(active, 1)
        active_team2 = _team_name(active, 2)
        active_round = _round_label(active)
        lines.append(f"🎯 Сейчас играют: {active_team1} vs {active_team2}")
        lines.append(f"   ({active_round})")
        lines.append("")
    elif not active:
        lines.append("⏳ Активный матч не выбран")
        lines.append("")
    # Если активна сама ваша команда — сводку «сейчас играют» не дублируем,
    # ниже всё равно будет «⚔️ Это ваш матч».

    sig_parts: list[str] = [
        f"act:{active.get('id') if active else 0}",
    ]

    if not next_match:
        # Возможные кейсы:
        # 1) Команда — будущий участник матча за 3-е место (проиграла полуфинал),
        #    но матч ещё не создан, потому что второй полуфинал не завершён.
        # 2) Команда выбыла из турнира насовсем или уже выиграла финал.
        third_place_sources = _team_awaits_third_place(tournament_id, team_id)
        if third_place_sources is not None:
            lines.append("🥉 Вы ждёте матч за 3-е место.")
            lines.append("📍 Этап: Матч за 3-е место")
            if third_place_sources:
                lines.append("")
                lines.append("🔮 Соперник определится после:")
                for src in third_place_sources:
                    src_team1 = _team_name(src, 1)
                    src_team2 = _team_name(src, 2)
                    src_round = _round_label(src)
                    lines.append(f"• {src_team1} vs {src_team2} ({src_round})")
            sig_parts.append("you-await-3rd")
            return "\n".join(lines), "|".join(sig_parts)
        lines.append("🏁 Все ваши матчи в этом турнире завершены.")
        sig_parts.append("done")
        return "\n".join(lines), "|".join(sig_parts)

    your_round = _round_label(next_match)
    has_team1 = bool(next_match.get("team1_id"))
    has_team2 = bool(next_match.get("team2_id"))
    opponent_known = has_team1 and has_team2

    if opponent_known:
        opponent_id = next_match["team2_id"] if next_match["team1_id"] == team_id else next_match["team1_id"]
        opponent_name = _team_name(next_match, 2) if next_match["team1_id"] == team_id else _team_name(next_match, 1)
        if next_match["queue_state"] == QUEUE_STATE_ACTIVE:
            lines.append("⚔️ Это ваш матч! Готовьтесь — он стартует прямо сейчас.")
            lines.append(f"👥 Соперник: {opponent_name}")
            lines.append(f"📍 Этап: {your_round}")
            sig_parts.append("you-active")
        else:
            ahead = _ahead_pairs_for_match(tournament_id, next_match)
            lines.append(f"👥 Ваш соперник: {opponent_name}")
            lines.append(f"📍 Ваш этап: {your_round}")
            lines.append(_format_ahead_line(ahead))
            sig_parts.append(f"you-wait:{next_match.get('queue_position') or 0}:{ahead}:{opponent_id}")
    else:
        # Соперник пока не определён: ждём предыдущий этап.
        round_number = int(next_match.get("round_number") or 0)
        if round_number > 0:
            ahead_total = count_pending_before_round(tournament_id, round_number)
            lines.append(f"⏳ Вы ждёте этап: {your_round}")
            if ahead_total > 0:
                lines.append(f"🕒 До вашего этапа осталось матчей: {ahead_total}")
            # Перечислим матчи предыдущего раунда, чьи победители играют против команды
            sources = _matches_feeding_into(tournament_id, next_match["id"])
            if sources:
                lines.append("")
                lines.append("🔮 Соперник определится после:")
                for src in sources:
                    src_team1 = _team_name(src, 1)
                    src_team2 = _team_name(src, 2)
                    src_round = _round_label(src)
                    lines.append(f"• {src_team1} vs {src_team2} ({src_round})")
            sig_parts.append(f"you-pending-round:{round_number}:{ahead_total}")
        else:
            lines.append("⏳ Ваш матч ещё не определён.")
            sig_parts.append("you-pending")

    return "\n".join(lines), "|".join(sig_parts)


def _team_awaits_third_place(tournament_id: int, team_id: int) -> list[dict[str, Any]] | None:
    """
    Возвращает:
    - список pending-полуфиналов, которые ещё не доиграны (если команда — будущий
      участник матча за 3-е место, но он пока не создан),
    - None — если команда не подходит под этот кейс.

    Команда подходит, если:
    - проиграла свой полуфинал (раунд = max_main_round - 1),
    - матч за 3-е место (is_third_place=1) ещё не существует.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Уже создан матч за 3-е место — этот хелпер не применим (next_match найдётся обычным путём).
        cur.execute(
            "SELECT 1 FROM tournament_brackets WHERE tournament_id=? AND COALESCE(is_third_place,0)=1 LIMIT 1",
            (tournament_id,),
        )
        if cur.fetchone():
            return None

        # Максимальный раунд основной сетки (финал).
        cur.execute(
            "SELECT MAX(round_number) FROM tournament_brackets WHERE tournament_id=? AND COALESCE(is_third_place,0)=0",
            (tournament_id,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        final_round = int(row[0])
        semifinal_round = final_round - 1
        if semifinal_round < 1:
            return None

        # Был ли у команды матч в полуфинале и проиграла ли она его?
        cur.execute(
            """
            SELECT id, winner_id, status, team1_id, team2_id
            FROM tournament_brackets
            WHERE tournament_id=? AND round_number=?
              AND COALESCE(is_third_place,0)=0
              AND (team1_id=? OR team2_id=?)
            """,
            (tournament_id, semifinal_round, team_id, team_id),
        )
        team_semi = cur.fetchone()
        if not team_semi:
            return None
        if (team_semi["status"] or "").lower() != "completed":
            return None
        if team_semi["winner_id"] == team_id:
            return None  # Команда выиграла полуфинал — она в финале, а не в матче за 3-е место.

        # Соберём полуфиналы, которые ещё не сыграны (источники для соперника).
        cur.execute(
            """
            SELECT b.*, t1.name AS team1_name, t2.name AS team2_name
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            WHERE b.tournament_id=? AND b.round_number=?
              AND COALESCE(b.is_third_place,0)=0
              AND b.status NOT IN ('completed','bye')
            ORDER BY b.match_number
            """,
            (tournament_id, semifinal_round),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _matches_feeding_into(tournament_id: int, match_id: int) -> list[dict[str, Any]]:
    """Возвращает матчи предыдущего раунда, чьи победители попадают в указанный матч."""
    match = get_bracket_match_by_id(match_id)
    if not match:
        return []
    round_number = int(_row_get(match, "round_number") or 0)
    match_number = int(_row_get(match, "match_number") or 0)
    if round_number <= 1 or match_number <= 0:
        return []
    prev_round = round_number - 1
    src_a = match_number * 2 - 1
    src_b = match_number * 2

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.*, t1.name AS team1_name, t2.name AS team2_name
            FROM tournament_brackets b
            LEFT JOIN teams t1 ON t1.id = b.team1_id
            LEFT JOIN teams t2 ON t2.id = b.team2_id
            WHERE b.tournament_id=?
              AND b.round_number=?
              AND b.match_number IN (?, ?)
              AND COALESCE(b.is_third_place, 0) = 0
            ORDER BY b.match_number
            """,
            (tournament_id, prev_round, src_a, src_b),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Queue message storage / send / update
# ---------------------------------------------------------------------------

def _save_queue_message(
    tournament_id: int,
    team_id: int,
    user_id: int,
    chat_id: int,
    message_id: int,
    signature: str,
) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tournament_queue_messages
                (tournament_id, team_id, user_id, chat_id, message_id, last_signature, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(tournament_id, user_id) DO UPDATE SET
                team_id=excluded.team_id,
                chat_id=excluded.chat_id,
                message_id=excluded.message_id,
                last_signature=excluded.last_signature,
                updated_at=CURRENT_TIMESTAMP
            """,
            (tournament_id, team_id, user_id, chat_id, message_id, signature),
        )
        conn.commit()
    finally:
        conn.close()


def _list_queue_messages(tournament_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tournament_queue_messages WHERE tournament_id=?",
            (tournament_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _delete_queue_message(message_row_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tournament_queue_messages WHERE id=?", (message_row_id,))
        conn.commit()
    finally:
        conn.close()


async def send_or_create_queue_messages_for_team(
    bot: Bot,
    tournament_id: int,
    team_id: int,
) -> None:
    """Отправляет/создаёт queue-сообщение каждому участнику команды (один раз)."""
    members = _team_members(tournament_id, team_id)
    if not members:
        return
    text, signature = format_queue_text_for_team(tournament_id, team_id)
    existing = {row["user_id"]: row for row in _list_queue_messages(tournament_id)}

    for member in members:
        user_id = int(member.get("id") or member.get("user_id") or 0)
        telegram_id = member.get("telegram_id")
        if not user_id or not telegram_id:
            continue
        if user_id in existing:
            continue
        try:
            sent = await bot.send_message(chat_id=int(telegram_id), text=text)
            _save_queue_message(tournament_id, team_id, user_id, int(telegram_id), sent.message_id, signature)
        except Exception as exc:
            logger.warning("Не удалось отправить queue-сообщение user_id=%s: %s", user_id, exc)


def _team_state_kind(signature: str) -> str:
    """
    Возвращает «жанр» состояния команды из подписи queue-сообщения.

    Если жанр поменялся между двумя подписями — это значимое изменение
    (новый соперник / новый этап / стали активными / финиш и т.п.).
    Если поменялись только числовые подсчёты в рамках одного жанра
    (например, «впереди 3 пары» → «впереди 2 пары») — значимым не считается.
    """
    own_part = ""
    for part in (signature or "").split("|"):
        if part and not part.startswith("act:"):
            own_part = part
            break
    if not own_part:
        return ""
    if own_part.startswith("you-wait:"):
        # формат: you-wait:pos:ahead:opp_id — значим только opp_id (он меняется при смене этапа).
        bits = own_part.split(":")
        opp = bits[3] if len(bits) >= 4 else ""
        return f"you-wait:{opp}"
    if own_part.startswith("you-pending-round:"):
        # формат: you-pending-round:round:ahead_total — значим round.
        bits = own_part.split(":")
        rnd = bits[1] if len(bits) >= 2 else ""
        return f"you-pending-round:{rnd}"
    return own_part


async def _resend_queue_message_for_user(
    bot: Bot,
    tournament_id: int,
    team_id: int,
    user_id: int,
    chat_id: int,
    old_message_id: int | None,
    text: str,
    signature: str,
) -> None:
    """Удаляет старое queue-сообщение и присылает новое, чтобы оно появилось внизу чата."""
    if old_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_message_id)
        except Exception as exc:
            logger.debug("Не удалось удалить старое queue-сообщение msg=%s: %s", old_message_id, exc)
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text)
        _save_queue_message(tournament_id, team_id, user_id, chat_id, sent.message_id, signature)
    except Exception as exc:
        logger.warning("Не удалось переслать queue-сообщение user_id=%s: %s", user_id, exc)


async def _edit_queue_message(
    bot: Bot,
    tournament_id: int,
    team_id: int,
    user_id: int,
    chat_id: int,
    message_id: int,
    text: str,
    signature: str,
) -> None:
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        _save_queue_message(tournament_id, team_id, user_id, chat_id, message_id, signature)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            _save_queue_message(tournament_id, team_id, user_id, chat_id, message_id, signature)
        elif "message to edit not found" in str(exc).lower() or "message can't be edited" in str(exc).lower():
            # Сообщение пропало (удалили), пришлём заново.
            await _resend_queue_message_for_user(
                bot, tournament_id, team_id, user_id, chat_id, None, text, signature,
            )
        else:
            logger.warning("Не удалось обновить queue-сообщение msg=%s: %s", message_id, exc)
    except Exception as exc:
        logger.warning("Не удалось обновить queue-сообщение msg=%s: %s", message_id, exc)


async def refresh_queue_messages(
    bot: Bot,
    tournament_id: int,
    *,
    resend_on_significant_change: bool = False,
) -> None:
    """
    Перерисовывает queue-сообщения у всех approved-команд (с учётом throttling).

    Если `resend_on_significant_change=True`, у команд, для которых сменился
    «жанр» состояния (новый этап, появился соперник, стали активными, и т.п.),
    старое сообщение удаляется и присылается новое в конец чата — чтобы его
    не загораживали присланные между делом сообщения (например, пик/бан карт).
    Для остальных команд продолжает работать обычный edit.
    """
    teams = _approved_teams(tournament_id)
    rendered: dict[int, tuple[str, str]] = {}
    for team in teams:
        team_id = int(team["id"])
        rendered[team_id] = format_queue_text_for_team(tournament_id, team_id)

    existing_rows = _list_queue_messages(tournament_id)
    for row in existing_rows:
        team_id = int(row["team_id"])
        if team_id not in rendered:
            continue
        text, signature = rendered[team_id]
        old_signature = row.get("last_signature") or ""
        if old_signature == signature:
            continue

        user_id = int(row["user_id"])
        chat_id = int(row["chat_id"])
        message_id = int(row["message_id"])

        kind_changed = _team_state_kind(old_signature) != _team_state_kind(signature)
        if resend_on_significant_change and kind_changed:
            await _resend_queue_message_for_user(
                bot, tournament_id, team_id, user_id, chat_id, message_id, text, signature,
            )
        else:
            await _edit_queue_message(
                bot, tournament_id, team_id, user_id, chat_id, message_id, text, signature,
            )


# ---------------------------------------------------------------------------
# Notifications: kickoff (1h) / start / opponent resolved / match active
# ---------------------------------------------------------------------------

async def _broadcast(bot: Bot, chat_ids: list[int], text: str) -> None:
    seen: set[int] = set()
    for chat_id in chat_ids:
        if not chat_id or chat_id in seen:
            continue
        seen.add(chat_id)
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            logger.warning("Не удалось отправить уведомление chat_id=%s: %s", chat_id, exc)


async def notify_kickoff_1h(bot: Bot, tournament_id: int) -> None:
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        return
    name = (_row_get(tournament, "name") or "Турнир").strip()
    sport_display = get_sport_display_name(_row_get(tournament, "sport") or "")
    location = (_row_get(tournament, "default_location") or "").strip()
    start_at = format_utc_to_msk(_row_get(tournament, "start_at_utc"))

    text_lines = [
        "⏰ Турнир стартует через 1 час",
        "",
        f"🏆 Турнир: {name}",
        f"🎮 Вид спорта: {sport_display}",
        f"🕒 Начало: {start_at} (МСК)",
    ]
    if location:
        text_lines.append(f"📌 Локация: {location}")
    text_lines.extend([
        "",
        "ℹ️ Матчи будут идти подряд один за другим.",
        "Когда подойдёт ваша очередь — придёт уведомление.",
    ])
    text = "\n".join(text_lines)

    chat_ids = _all_approved_teams_chat_ids(tournament_id)
    await _broadcast(bot, chat_ids, text)


def _all_approved_teams_chat_ids(tournament_id: int) -> list[int]:
    chat_ids: set[int] = set()
    for team in _approved_teams(tournament_id):
        for member in _team_members(tournament_id, int(team["id"])):
            tg = member.get("telegram_id")
            if tg:
                chat_ids.add(int(tg))
    return list(chat_ids)


def _team_member_chat_ids(tournament_id: int, team_id: int) -> list[int]:
    chat_ids: set[int] = set()
    for member in _team_members(tournament_id, team_id):
        tg = member.get("telegram_id")
        if tg:
            chat_ids.add(int(tg))
    return list(chat_ids)


async def notify_tournament_started_per_team(bot: Bot, tournament_id: int) -> None:
    """Персональное стартовое уведомление каждой команде (без queue-сообщения — оно отдельно)."""
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        return
    name = (_row_get(tournament, "name") or "Турнир").strip()

    teams = _approved_teams(tournament_id)
    for team in teams:
        team_id = int(team["id"])
        next_match = get_team_next_pending_match(tournament_id, team_id)
        active = get_active_match(tournament_id)

        lines = [
            "🚀 Турнир стартовал!",
            "",
            f"🏆 {name}",
        ]
        if active:
            lines.append(f"🎯 Первая пара: {_team_name(active, 1)} vs {_team_name(active, 2)}")
        lines.append("")

        if next_match:
            your_round = _round_label(next_match)
            opp_known = bool(next_match.get("team1_id")) and bool(next_match.get("team2_id"))
            if opp_known:
                opp_name = _team_name(next_match, 2) if next_match["team1_id"] == team_id else _team_name(next_match, 1)
                if next_match["queue_state"] == QUEUE_STATE_ACTIVE:
                    lines.append(f"⚔️ Ваш матч стартует первым! Соперник: {opp_name}")
                else:
                    ahead = _ahead_pairs_for_match(tournament_id, next_match)
                    lines.append(f"👥 Ваш соперник: {opp_name}")
                    lines.append(f"📍 Этап: {your_round}")
                    lines.append(_format_ahead_line(ahead))
            else:
                round_number = int(next_match.get("round_number") or 0)
                ahead = count_pending_before_round(tournament_id, round_number) if round_number else 0
                lines.append(f"⏳ Вы ждёте этап: {your_round}")
                if ahead:
                    lines.append(f"🕒 До вашего этапа: {ahead} матч(ей)")
        else:
            lines.append("ℹ️ Информация о вашем матче скоро появится.")

        text = "\n".join(lines)
        chat_ids = _team_member_chat_ids(tournament_id, team_id)
        await _broadcast(bot, chat_ids, text)


def _ahead_pairs_for_match(tournament_id: int, match: Any) -> int:
    """
    Сколько пар сыграет ДО вашего матча (включая текущую активную пару, если
    турнир уже идёт). Логика: если активная пара играет прямо сейчас, она тоже
    считается «парой перед вами», чтобы UX был понятным:
    - 1 → ваш матч после текущей активной пары,
    - 2 → перед вами активная + ещё одна,
    - и т.д.
    """
    your_pos = int(_row_get(match, "queue_position") or 0)
    if your_pos <= 0:
        return 0
    active = get_active_match(tournament_id)
    if active:
        active_pos = int(active.get("queue_position") or 0)
        # Включаем активную пару в счёт.
        return max(0, your_pos - active_pos)
    # Турнир ещё не стартовал: до тебя играют все пары с меньшей позицией.
    return max(0, your_pos - 1)


def _format_ahead_line(ahead: int) -> str:
    """
    Возвращает читаемую строку «вы играете после N пар» с правильным склонением.
    Семантика ahead — см. _ahead_pairs_for_match (включает активную пару).
    """
    if ahead <= 0:
        # Должно быть редко: если ahead<=0, команда обычно сама активна и
        # этот хелпер не вызывается. Оставим осмысленный fallback.
        return "⚔️ Ваш матч стартует следующим."
    n = ahead % 100
    if 11 <= n <= 14:
        suffix = "пар"
    else:
        last = n % 10
        suffix = "пары" if last == 1 else "пар"
    return f"⏭️ Вы играете после {ahead} {suffix}"


async def notify_opponent_resolved_for_match(bot: Bot, match_id: int) -> bool:
    """Если у матча определились обе команды — отправить уведомление обеим. Помечает в БД."""
    match = get_bracket_match_by_id(match_id)
    if not match:
        return False
    if (_row_get(match, "is_bye") or 0):
        return False
    if (_row_get(match, "status") or "") != "pending":
        return False
    if not (_row_get(match, "team1_id") and _row_get(match, "team2_id")):
        return False
    if _row_get(match, "opponents_resolved_notified_at"):
        return False
    tournament_id = int(_row_get(match, "tournament_id") or 0)
    if not tournament_id:
        return False

    team1_id = int(_row_get(match, "team1_id") or 0)
    team2_id = int(_row_get(match, "team2_id") or 0)
    team1_name = _team_name(match, 1)
    team2_name = _team_name(match, 2)
    round_label = _round_label(match)
    tournament = get_tournament_by_id(tournament_id)
    tournament_name = (_row_get(tournament, "name") or "Турнир").strip() if tournament else "Турнир"
    start_at_msk = format_utc_to_msk(_row_get(tournament, "start_at_utc")) if tournament else "не указано"
    location = (_row_get(tournament, "default_location") or "").strip() if tournament else ""
    if not location:
        location = "не указано"

    is_third_place = bool(int(_row_get(match, "is_third_place") or 0))
    round_number = int(_row_get(match, "round_number") or 0)
    is_first_round = (round_number == 1) and not is_third_place

    if is_third_place:
        header = "🥉 Назначен матч за 3-е место"
    else:
        header = "⚔️ Назначен матч"

    text_lines = [
        header,
        "",
        f"🏆 Турнир: {tournament_name}",
    ]
    # Для матча за 3-е место строка с раундом дублирует заголовок — пропускаем.
    if not is_third_place:
        text_lines.append(f"📍 Раунд: {round_label}")
    text_lines.append(f"⚔️ {team1_name} vs {team2_name}")
    text_lines.append("")
    # Для матчей не первого раунда (и матча за 3-е место) убираем строки
    # «Время начала турнира» и «Перед вами N пар» — турнир уже идёт, очередь
    # меняется в реальном времени, queue-сообщение покажет актуальное состояние.
    if is_first_round:
        ahead = _ahead_pairs_for_match(tournament_id, match)
        ahead_line = _format_ahead_line(ahead)
        text_lines.append(f"🕒 Время начала турнира: {start_at_msk} (МСК)")
        text_lines.append(ahead_line)
    text_lines.append(f"📌 Место: {location}")
    text = "\n".join(text_lines)

    for team_id in (team1_id, team2_id):
        chat_ids = _team_member_chat_ids(tournament_id, team_id)
        await _broadcast(bot, chat_ids, text)

    _mark_opponents_resolved_notified(match_id)
    return True


def _mark_opponents_resolved_notified(match_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tournament_brackets SET opponents_resolved_notified_at=CURRENT_TIMESTAMP WHERE id=?",
            (match_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Lifecycle: kickoff / start / advance
# ---------------------------------------------------------------------------

def _set_tournament_kickoff_notified(tournament_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tournaments SET kickoff_notified_at=CURRENT_TIMESTAMP WHERE id=?",
            (tournament_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _set_tournament_started(tournament_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tournaments
            SET sequential_started_at = COALESCE(sequential_started_at, CURRENT_TIMESTAMP),
                status = CASE WHEN status='registration' THEN 'in_progress' ELSE status END
            WHERE id=?
            """,
            (tournament_id,),
        )
        conn.commit()
    finally:
        conn.close()


async def start_sequential_tournament(bot: Bot, tournament_id: int) -> bool:
    """
    Запускает sequential-турнир: переводит в in_progress, ставит queue_state=active первой паре,
    рассылает персональные уведомления и создаёт queue-сообщения.

    Идемпотентен: если уже запущен — только обновляет очередь и сообщения.
    """
    tournament = get_tournament_by_id(tournament_id)
    if not tournament or not is_sequential(tournament):
        return False

    already_started = bool(_row_get(tournament, "sequential_started_at"))
    _set_tournament_started(tournament_id)
    recompute_queue(tournament_id)

    if not already_started:
        await notify_tournament_started_per_team(bot, tournament_id)
        for team in _approved_teams(tournament_id):
            await send_or_create_queue_messages_for_team(bot, tournament_id, int(team["id"]))
    else:
        await refresh_queue_messages(bot, tournament_id)

    return True


async def handle_bracket_generated(bot: Bot, tournament_id: int) -> None:
    """
    Хук на момент генерации/перегенерации сетки sequential-турнира.
    Пересчитывает очередь, отправляет round-1 уведомления о соперниках,
    обновляет queue-сообщения если турнир уже запущен.
    """
    if not is_sequential_tournament(tournament_id):
        return
    recompute_queue(tournament_id)
    pending_matches = _all_pending_pairs_for_notification(tournament_id)
    for match in pending_matches:
        try:
            await notify_opponent_resolved_for_match(bot, int(match["id"]))
        except Exception as exc:
            logger.warning("opponent_resolved on bracket_generated failed match=%s: %s", match.get("id"), exc)

    tournament = get_tournament_by_id(tournament_id)
    if tournament and _row_get(tournament, "sequential_started_at"):
        try:
            await refresh_queue_messages(bot, tournament_id)
        except Exception as exc:
            logger.warning("refresh_queue_messages failed after bracket_generated: %s", exc)


async def advance_after_match_completed(
    bot: Bot,
    tournament_id: int,
    completed_match_id: int,
) -> None:
    """
    Хук после сохранения результата матча. Пересчитывает очередь, рассылает
    notify_opponent_resolved для матчей, где соперники только что определились,
    обновляет queue-сообщения. Для CS2 запуск веты для нового активного матча
    выполнит существующий dispatcher на следующем тике worker.
    """
    if not is_sequential_tournament(tournament_id):
        return

    recompute_queue(tournament_id)

    # Уведомляем матчи с только что собранными парами.
    pending_matches = _all_pending_pairs_for_notification(tournament_id)
    for match in pending_matches:
        try:
            await notify_opponent_resolved_for_match(bot, int(match["id"]))
        except Exception as exc:
            logger.warning(
                "Не удалось отправить opponent_resolved для match_id=%s: %s",
                match.get("id"),
                exc,
            )

    # Обновляем queue-сообщения у всех команд. Для тех, у кого сменился
    # «жанр» состояния (новый этап / появился соперник / стали активными /
    # завершили турнир), старое сообщение удаляем и шлём новое в конец чата —
    # чтобы оно не пряталось за пиками/банами карт CS2.
    try:
        await refresh_queue_messages(bot, tournament_id, resend_on_significant_change=True)
    except Exception as exc:
        logger.warning("refresh_queue_messages failed: %s", exc)


def _all_pending_pairs_for_notification(tournament_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, team1_id, team2_id
            FROM tournament_brackets
            WHERE tournament_id=?
              AND status='pending'
              AND COALESCE(is_bye, 0) = 0
              AND team1_id IS NOT NULL
              AND team2_id IS NOT NULL
              AND opponents_resolved_notified_at IS NULL
            ORDER BY queue_position
            """,
            (tournament_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Worker dispatcher
# ---------------------------------------------------------------------------

def _list_sequential_tournaments_due() -> list[dict[str, Any]]:
    """
    Возвращает sequential-турниры, для которых worker должен сделать что-то:
    либо отправить kickoff_1h, либо запустить сам турнир.

    Важно: после генерации сетки турнир получает status='active'
    (см. handlers/brackets.py), поэтому 'active' тоже должен быть в фильтре —
    иначе kickoff_1h и автостарт никогда не сработают.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM tournaments
            WHERE schedule_mode=?
              AND start_at_utc IS NOT NULL
              AND TRIM(start_at_utc) <> ''
              AND status IN ('registration', 'active', 'in_progress')
            """,
            (SCHEDULE_MODE_SEQUENTIAL,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


async def dispatch_sequential_lifecycle(bot: Bot) -> int:
    """
    Worker tick: отправка kickoff_1h за час до старта и автозапуск sequential-турниров.
    Также шлёт админам предупреждения, если сетка не сгенерирована к моменту старта.
    Возвращает число выполненных действий.
    """
    actions = 0
    now_utc = datetime.now(timezone.utc)
    for tournament in _list_sequential_tournaments_due():
        start_dt = parse_utc_storage_datetime(_row_get(tournament, "start_at_utc"))
        if not start_dt:
            continue
        tournament_id = int(tournament["id"])
        bracket_generated = int(_row_get(tournament, "bracket_generated") or 0) == 1

        # 1h до старта: команды получают kickoff_1h, ИЛИ админы получают warning,
        # если сетка не сгенерирована. В обоих случаях ставим kickoff_notified_at,
        # чтобы не повторяться.
        if (
            not _row_get(tournament, "kickoff_notified_at")
            and now_utc >= start_dt - timedelta(hours=1)
            and now_utc < start_dt
        ):
            try:
                if bracket_generated:
                    await notify_kickoff_1h(bot, tournament_id)
                else:
                    await _notify_admins_bracket_missing_1h(bot, tournament_id)
                _set_tournament_kickoff_notified(tournament_id)
                actions += 1
            except Exception as exc:
                logger.exception("kickoff_1h tick failed for tournament_id=%s: %s", tournament_id, exc)

        # Время старта прошло.
        if not _row_get(tournament, "sequential_started_at") and now_utc >= start_dt:
            if bracket_generated:
                try:
                    await start_sequential_tournament(bot, tournament_id)
                    actions += 1
                except Exception as exc:
                    logger.exception("auto-start failed for tournament_id=%s: %s", tournament_id, exc)
            else:
                # Сетки до сих пор нет — единожды бьём админам тревогу,
                # дальше тихо ждём ручного действия.
                if not _row_get(tournament, "bracket_missing_overdue_notified_at"):
                    try:
                        await _notify_admins_bracket_missing_overdue(bot, tournament_id)
                        _set_tournament_bracket_missing_overdue_notified(tournament_id)
                        actions += 1
                    except Exception as exc:
                        logger.exception(
                            "bracket_missing_overdue notify failed for tournament_id=%s: %s",
                            tournament_id, exc,
                        )
                logger.info("Sequential tournament %s due for start, but bracket not generated yet", tournament_id)

    return actions


def _set_tournament_bracket_missing_overdue_notified(tournament_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tournaments SET bracket_missing_overdue_notified_at=CURRENT_TIMESTAMP WHERE id=?",
            (tournament_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _admin_chat_ids_for_tournament(tournament_id: int) -> list[int]:
    """Чаты админов и менеджеров турнира (без участников команд)."""
    chat_ids: set[int] = set()
    try:
        from razryad_arena_utils import get_tournament_admin_notification_chat_ids
        for chat_id in get_tournament_admin_notification_chat_ids(tournament_id) or []:
            if chat_id:
                chat_ids.add(int(chat_id))
    except Exception as exc:
        logger.warning("admin chat ids lookup failed for tournament=%s: %s", tournament_id, exc)
    return list(chat_ids)


async def _notify_admins_bracket_missing_1h(bot: Bot, tournament_id: int) -> None:
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        return
    name = (_row_get(tournament, "name") or "Турнир").strip()
    sport_display = get_sport_display_name(_row_get(tournament, "sport") or "")
    start_at = format_utc_to_msk(_row_get(tournament, "start_at_utc"))
    text = (
        "⚠️ Sequential-турнир стартует через 1 час, но сетка ещё не сгенерирована\n\n"
        f"🏆 Турнир: {name}\n"
        f"🎮 Вид спорта: {sport_display}\n"
        f"🕒 Старт: {start_at} (МСК)\n\n"
        "Зайдите в админ-панель турнира и сгенерируйте сетку, иначе автозапуск не сработает "
        "и команды не получат своих позиций в очереди."
    )
    chat_ids = _admin_chat_ids_for_tournament(tournament_id)
    await _broadcast(bot, chat_ids, text)


async def _notify_admins_bracket_missing_overdue(bot: Bot, tournament_id: int) -> None:
    tournament = get_tournament_by_id(tournament_id)
    if not tournament:
        return
    name = (_row_get(tournament, "name") or "Турнир").strip()
    start_at = format_utc_to_msk(_row_get(tournament, "start_at_utc"))
    text = (
        "🚨 Время старта sequential-турнира прошло, но сетка не сгенерирована\n\n"
        f"🏆 Турнир: {name}\n"
        f"🕒 Запланированный старт: {start_at} (МСК)\n\n"
        "Турнир не запущен и команды ждут. Сгенерируйте сетку — бот стартует турнир "
        "автоматически в течение минуты после этого."
    )
    chat_ids = _admin_chat_ids_for_tournament(tournament_id)
    await _broadcast(bot, chat_ids, text)
