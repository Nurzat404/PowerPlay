"""
Интеграция tournament_bracket.py с ботом
"""
import os
import logging
from razryad_arena_utils import get_bracket_matches, get_tournament_by_id
from typing import Optional
from utils.bracket_utils import get_round_name

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


def _total_main_rounds(matches: list) -> int:
    main_rounds = [
        int(_match_value(match, "round_number", 0))
        for match in matches
        if not _is_third_place_match(match)
    ]
    return max(main_rounds) if main_rounds else 0


def _resolve_round_name(match: dict, total_main_rounds: int) -> str:
    return get_round_name(
        int(_match_value(match, "round_number", 0) or 0),
        total_main_rounds,
        is_third_place=_is_third_place_match(match),
    )


def generate_bracket_png(tournament_id: int, output_path: str) -> Optional[str]:
    """
    Генерирует PNG изображение турнирной сетки используя tournament_bracket.py
    """
    try:
        logger.debug("[PNG] Начинаем генерацию для турнира %s", tournament_id)

        matches = get_bracket_matches(tournament_id)
        logger.debug("[PNG] Получено матчей: %s", len(matches))

        if not matches:
            logger.debug("[PNG] Нет матчей")
            return None

        tournament = get_tournament_by_id(tournament_id)
        tournament_name = tournament['name'] if tournament else "Турнир"
        logger.debug("[PNG] Турнир: %s", tournament_name)

        # Возвращаем старый стабильный рендерер, который визуально выглядел лучше,
        # и подаем ему только корректные названия этапов.
        first_round_matches = [m for m in matches if int(_match_value(m, "round_number", 0) or 0) == 1]
        first_round_matches.sort(key=lambda m: _match_value(m, "match_number", 0))

        teams = []
        team_ids_seen = set()
        for match in first_round_matches:
            team1_id = _match_value(match, "team1_id")
            team2_id = _match_value(match, "team2_id")
            if team1_id and team1_id not in team_ids_seen:
                teams.append(_match_value(match, "team1_name", "???") or "???")
                team_ids_seen.add(team1_id)
            if team2_id and team2_id not in team_ids_seen:
                teams.append(_match_value(match, "team2_name", "???") or "???")
                team_ids_seen.add(team2_id)

        if not teams:
            logger.debug("[PNG] Нет данных команд для рендера")
            return None

        logger.debug("[PNG] Команд в первом раунде: %s", len(teams))

        main_rounds = _total_main_rounds(matches)
        round_names = [get_round_name(i, main_rounds) for i in range(1, main_rounds + 1)]
        logger.debug("[PNG] Названия раундов: %s", round_names)

        from tournament_bracket import draw_tournament_bracket

        original_cwd = os.getcwd()

        try:
            os.chdir(original_cwd)

            temp_filename = f"temp_bracket_{tournament_id}.png"
            draw_tournament_bracket(
                teams,
                tournament_name,
                round_names,
                temp_filename,
                db_matches=matches,
            )

            if os.path.exists(temp_filename):
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.rename(temp_filename, output_path)
                logger.debug("[PNG] PNG сохранён: %s", output_path)
                return output_path
            else:
                logger.warning("[PNG] Файл не создан для турнира %s", tournament_id)
                return None
        finally:
            os.chdir(original_cwd)

    except Exception as e:
        logger.exception("Ошибка генерации PNG для турнира %s: %s", tournament_id, e)
        return None


def generate_bracket_ascii(tournament_id: int) -> str:
    """Генерирует ASCII-версию сетки (запасной вариант)."""
    matches = get_bracket_matches(tournament_id)

    if not matches:
        return "Нет матчей для отображения"

    result = []
    result.append("=" * 80)
    result.append("ТУРНИРНАЯ СЕТКА")
    result.append("=" * 80)

    rounds = {}
    for match in matches:
        round_num = match['round_number']
        if round_num not in rounds:
            rounds[round_num] = []
        rounds[round_num].append(match)

    total_main_rounds = _total_main_rounds(matches)

    for round_num in sorted(rounds.keys()):
        round_matches = rounds[round_num]
        round_name = _resolve_round_name(round_matches[0], total_main_rounds) if round_matches else f"Раунд {round_num}"

        result.append("")
        result.append(f"📍 {round_name}")
        result.append("-" * 40)

        for match in round_matches:
            team1 = match['team1_name'] or "???"
            team2 = match['team2_name'] or "???"

            if match['winner_id'] == match['team1_id']:
                team1 = f"[W] {team1}"
            elif match['winner_id'] == match['team2_id']:
                team2 = f"[W] {team2}"

            status = match['status']
            if status == 'bye':
                status_icon = "⚡"
            elif status == 'completed':
                status_icon = "✅"
            else:
                status_icon = "⏳"

            result.append(f"{status_icon} {team1} vs {team2}")

    result.append("")
    result.append("=" * 80)

    return "\n".join(result)
