"""
Утилиты для работы с турнирными сетками (плей-офф).
"""
from database import get_connection
import logging

logger = logging.getLogger(__name__)


def get_main_round_name(round_number: int, total_rounds: int) -> str:
    """Возвращает каноничное название раунда основной сетки."""
    if total_rounds <= 0:
        return f"Раунд {round_number}"
    if round_number == total_rounds:
        return "Финал"
    if round_number == total_rounds - 1:
        return "Полуфинал"
    steps_to_final = total_rounds - round_number
    if steps_to_final >= 2:
        denominator = 2 ** steps_to_final
        return f"1/{denominator} финала"
    return f"Раунд {round_number}"


def get_round_name(round_number: int, total_rounds: int | None = None, *, is_third_place: bool = False) -> str:
    """Возвращает название этапа сетки."""
    if is_third_place:
        return "Матч за 3-е место"
    if total_rounds is None:
        return f"Раунд {round_number}"
    return get_main_round_name(round_number, total_rounds)


def calculate_total_rounds(num_teams: int) -> int:
    """Рассчитывает количество раундов для заданного числа команд."""
    if num_teams <= 1:
        return 0
    next_power = 1
    rounds = 0
    while next_power < num_teams:
        next_power *= 2
        rounds += 1
    return rounds


def calculate_bye_count(num_teams: int) -> int:
    """Рассчитывает количество BYE-слотов до ближайшей степени двойки."""
    next_power = 1
    while next_power < num_teams:
        next_power *= 2
    return next_power - num_teams


def _next_power_of_two(value: int) -> int:
    size = 1
    while size < value:
        size *= 2
    return size


def _select_bye_match_indexes(first_round_matches: int, bye_count: int) -> set[int]:
    """
    Возвращает индексы матчей 1-го раунда (0-based), где будет BYE.
    BYE распределяются максимально равномерно по сетке.
    """
    if bye_count <= 0 or first_round_matches <= 0:
        return set()

    indexes = set()
    for i in range(bye_count):
        idx = int((i + 0.5) * first_round_matches / bye_count)
        if idx >= first_round_matches:
            idx = first_round_matches - 1

        while idx in indexes:
            idx = (idx + 1) % first_round_matches

        indexes.add(idx)

    return indexes


def _build_seeded_slots(team_ids: list[int], bracket_size: int) -> list[int | None]:
    """
    Строит слоты первого раунда полной сетки:
    - без пар None vs None;
    - с равномерным распределением BYE между матчами.
    """
    first_round_matches = bracket_size // 2
    bye_count = bracket_size - len(team_ids)
    bye_match_indexes = _select_bye_match_indexes(first_round_matches, bye_count)

    slots: list[int | None] = []
    team_idx = 0

    for match_idx in range(first_round_matches):
        team1_id = team_ids[team_idx]
        team_idx += 1

        if match_idx in bye_match_indexes:
            team2_id = None
        else:
            team2_id = team_ids[team_idx]
            team_idx += 1

        slots.extend([team1_id, team2_id])

    return slots


def _propagate_winner(conn, match: dict, winner_id: int) -> None:
    """Переносит победителя в следующий матч сетки."""
    if not winner_id:
        return

    cur = conn.cursor()
    next_round = match["round_number"] + 1
    next_match_number = (match["match_number"] + 1) // 2

    cur.execute(
        """
        SELECT id FROM tournament_brackets
        WHERE tournament_id=? AND round_number=? AND match_number=?
        """,
        (match["tournament_id"], next_round, next_match_number),
    )
    next_match = cur.fetchone()
    if not next_match:
        return

    position = "team1_id" if match["match_number"] % 2 == 1 else "team2_id"
    cur.execute(
        f"""
        UPDATE tournament_brackets
        SET {position}=?
        WHERE id=?
        """,
        (winner_id, next_match["id"]),
    )


def _auto_advance_ready_matches(conn, tournament_id: int) -> None:
    """
    Автоматически закрывает матчи, где есть только одна команда,
    и проталкивает победителя дальше.
    """
    cur = conn.cursor()
    changed = True
    while changed:
        changed = False
        cur.execute(
            """
            SELECT *
            FROM tournament_brackets
            WHERE tournament_id=? AND COALESCE(is_third_place, 0)=0
            ORDER BY round_number, match_number
            """,
            (tournament_id,),
        )
        matches = cur.fetchall()

        for match in matches:
            if match["status"] == "completed":
                continue
            if not match["is_bye"]:
                continue

            team1_present = bool(match["team1_id"])
            team2_present = bool(match["team2_id"])
            if team1_present == team2_present:
                continue

            if team1_present:
                winner_id = match["team1_id"]
            else:
                winner_id = match["team2_id"]

            cur.execute(
                """
                UPDATE tournament_brackets
                SET winner_id=?, status='completed', is_bye=1
                WHERE id=?
                """,
                (winner_id, match["id"]),
            )
            _propagate_winner(conn, match, winner_id)
            changed = True


def generate_bracket(tournament_id: int) -> bool:
    """
    Генерирует турнирную сетку для указанного турнира.
    Возвращает True при успехе.
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,))
        tournament = cur.fetchone()
        if not tournament:
            logger.info("Турнир не найден")
            return False

        cur.execute(
            """
            SELECT t.*
            FROM teams t
            JOIN tournament_applications a ON t.id = a.team_id
            WHERE a.tournament_id=? AND a.status='approved'
            ORDER BY RANDOM()
            """,
            (tournament_id,),
        )
        teams = cur.fetchall()
        if len(teams) < 2:
            logger.info("Недостаточно команд для генерации сетки (минимум 2)")
            return False

        team_ids = [team["id"] for team in teams]
        bracket_size = _next_power_of_two(len(team_ids))
        total_rounds = calculate_total_rounds(bracket_size)
        first_round_matches = bracket_size // 2

        # Чистим прошлую сетку и строим заново.
        cur.execute("DELETE FROM tournament_brackets WHERE tournament_id=?", (tournament_id,))

        slots = _build_seeded_slots(team_ids, bracket_size)
        for match_number in range(1, first_round_matches + 1):
            idx = (match_number - 1) * 2
            team1_id = slots[idx]
            team2_id = slots[idx + 1]
            is_bye = int(bool(team1_id) ^ bool(team2_id))
            status = "bye" if is_bye else "pending"

            cur.execute(
                """
                INSERT INTO tournament_brackets
                (tournament_id, round_number, round_name, match_number, team1_id, team2_id, is_bye, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tournament_id,
                    1,
                    get_main_round_name(1, total_rounds),
                    match_number,
                    team1_id,
                    team2_id,
                    is_bye,
                    status,
                ),
            )

        prev_round_matches = first_round_matches
        for round_number in range(2, total_rounds + 1):
            matches_in_round = prev_round_matches // 2
            for match_number in range(1, matches_in_round + 1):
                cur.execute(
                    """
                    INSERT INTO tournament_brackets
                    (tournament_id, round_number, round_name, match_number, status)
                    VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (
                        tournament_id,
                        round_number,
                        get_main_round_name(round_number, total_rounds),
                        match_number,
                    ),
                )
            prev_round_matches = matches_in_round

        _auto_advance_ready_matches(conn, tournament_id)
        conn.commit()
        return True
    except Exception as e:
        logger.info(f"Ошибка генерации сетки: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def advance_team_bye(conn, match_id: int, team_id: int):
    """Автоматически продвигает команду после BYE-матча."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM tournament_brackets WHERE id=?", (match_id,))
    match = cur.fetchone()
    if not match:
        return

    cur.execute(
        """
        UPDATE tournament_brackets
        SET winner_id=?, status='completed', is_bye=1
        WHERE id=?
        """,
        (team_id, match_id),
    )
    _propagate_winner(conn, match, team_id)
    _auto_advance_ready_matches(conn, match["tournament_id"])
    conn.commit()


def advance_winner(match_id: int, winner_id: int) -> bool:
    """Продвигает победителя в следующий раунд сетки."""
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT * FROM tournament_brackets WHERE id=?", (match_id,))
        match = cur.fetchone()
        if not match:
            logger.info("Матч не найден")
            return False

        cur.execute(
            """
            UPDATE tournament_brackets
            SET winner_id=?, status='completed'
            WHERE id=?
            """,
            (winner_id, match_id),
        )
        _propagate_winner(conn, match, winner_id)
        _auto_advance_ready_matches(conn, match["tournament_id"])
        conn.commit()
        return True
    except Exception as e:
        logger.info(f"Ошибка продвижения победителя: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_semifinal_losers(semifinals: list) -> list:
    """Возвращает список проигравших в полуфиналах."""
    losers = []
    for match in semifinals:
        if match["status"] not in ("completed", "bye"):
            continue
        team1_id = match["team1_id"]
        team2_id = match["team2_id"]
        winner_id = match["winner_id"]
        if winner_id == team1_id and team2_id:
            losers.append(team2_id)
        elif winner_id == team2_id and team1_id:
            losers.append(team1_id)
    return losers
