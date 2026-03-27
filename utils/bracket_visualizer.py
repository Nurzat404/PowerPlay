"""
Интеграция tournament_bracket.py с ботом
"""
import os
import logging
from razryad_arena_utils import get_bracket_matches, get_tournament_by_id
from typing import Optional

logger = logging.getLogger(__name__)


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

        # Извлекаем реальные команды из 1-го раунда (без пустых слотов),
        # а пары/победителей подставляем через db_matches в draw_tournament_bracket.
        first_round_matches = [m for m in matches if m['round_number'] == 1]
        first_round_matches.sort(key=lambda m: m['match_number'])

        teams = []
        team_ids_seen = set()
        for match in first_round_matches:
            if match['team1_id'] and match['team1_id'] not in team_ids_seen:
                teams.append(match['team1_name'] or "???")
                team_ids_seen.add(match['team1_id'])
            if match['team2_id'] and match['team2_id'] not in team_ids_seen:
                teams.append(match['team2_name'] or "???")
                team_ids_seen.add(match['team2_id'])

        if not teams:
            logger.debug("[PNG] Нет данных команд для рендера")
            return None

        logger.debug("[PNG] Команд в первом раунде: %s", len(teams))

        # Динамические названия раундов (старый визуальный стиль).
        num_rounds = max(m['round_number'] for m in matches)
        logger.debug("[PNG] Раундов: %s", num_rounds)
        if num_rounds == 1:
            round_names = ["Финал"]
        elif num_rounds == 2:
            round_names = ["Полуфинал", "Финал"]
        elif num_rounds == 3:
            round_names = ["1/4 финала", "Полуфинал", "Финал"]
        elif num_rounds == 4:
            round_names = ["1/8 финала", "1/4 финала", "Полуфинал", "Финал"]
        else:
            round_names = [
                "1/8 финала",
                "1/4 финала",
                "Полуфинал",
                "Финал",
                "Матч за 3-е",
            ][:num_rounds]
            if len(round_names) < num_rounds:
                for idx in range(len(round_names) + 1, num_rounds + 1):
                    round_names.append(f"Раунд {idx}")

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

    for round_num in sorted(rounds.keys()):
        round_matches = rounds[round_num]
        round_name = f"Раунд {round_num}"

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

