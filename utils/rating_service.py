from __future__ import annotations

import sqlite3
from typing import Any

from database import get_connection
from utils.rating_rules import (
    ENTITY_PLAYER,
    ENTITY_TEAM,
    FORMAT_GENERAL,
    PLAYER_RATING_RULES,
    SCOPE_OVERALL,
    SCOPE_SEASONAL,
    SOURCE_BRACKET_MATCH,
    SOURCE_LEGACY_MATCH,
    SOURCE_MANUAL,
    SOURCE_TOURNAMENT,
    TEAM_RATING_RULES,
    get_format_options_for_sport,
    get_rating_multiplier,
    normalize_format_key,
    normalize_sport_key,
    resolve_cs2_format_from_team_size,
    round_rating_points,
    sport_supports_formats,
)
from utils.rating_seasons import (
    advance_to_next_rating_season,
    ensure_active_rating_season,
    ensure_active_rating_seasons_for_all_sports,
    get_active_rating_season,
    get_adjacent_season_ids,
    get_rating_season_by_id,
    list_rating_seasons,
)


def _dict_rows(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def ensure_rating_defaults() -> None:
    ensure_active_rating_seasons_for_all_sports()


def get_rating_format_for_tournament(tournament_id: int) -> str | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT sport, required_team_size FROM tournaments WHERE id=?", (tournament_id,))
    tournament = cur.fetchone()
    conn.close()
    if not tournament:
        return None
    return resolve_cs2_format_from_team_size(
        tournament["sport"],
        int(tournament["required_team_size"] or 0),
    )


def rebuild_entity_ratings(conn: sqlite3.Connection | None = None) -> None:
    owns_connection = conn is None
    if owns_connection:
        conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM entity_ratings")
    cur.execute(
        """
        INSERT INTO entity_ratings (
            entity_type,
            entity_id,
            sport_key,
            format_key,
            rating_scope,
            season_id,
            rating_value,
            matches_played,
            matches_won,
            tournaments_played,
            tournaments_won,
            second_places,
            third_places,
            mvp_matches_count,
            mvp_tournaments_count,
            manual_adjustment_total,
            last_manual_adjustment_at,
            updated_at
        )
        SELECT
            entity_type,
            entity_id,
            sport_key,
            format_key,
            rating_scope,
            season_id,
            COALESCE(SUM(delta), 0) AS rating_value,
            COALESCE(SUM(matches_played_delta), 0) AS matches_played,
            COALESCE(SUM(matches_won_delta), 0) AS matches_won,
            COALESCE(SUM(tournaments_played_delta), 0) AS tournaments_played,
            COALESCE(SUM(tournaments_won_delta), 0) AS tournaments_won,
            COALESCE(SUM(second_places_delta), 0) AS second_places,
            COALESCE(SUM(third_places_delta), 0) AS third_places,
            COALESCE(SUM(mvp_matches_count_delta), 0) AS mvp_matches_count,
            COALESCE(SUM(mvp_tournaments_count_delta), 0) AS mvp_tournaments_count,
            COALESCE(SUM(CASE WHEN source_type = ? THEN delta ELSE 0 END), 0) AS manual_adjustment_total,
            MAX(CASE WHEN source_type = ? THEN created_at ELSE NULL END) AS last_manual_adjustment_at,
            MAX(created_at) AS updated_at
        FROM rating_adjustments
        GROUP BY
            entity_type,
            entity_id,
            sport_key,
            format_key,
            rating_scope,
            season_id
        """,
        (SOURCE_MANUAL, SOURCE_MANUAL),
    )
    if owns_connection:
        conn.commit()
        conn.close()


def _expand_automatic_targets(sport_key: str, format_key: str | None) -> list[dict[str, Any]]:
    season = get_active_rating_season(sport_key)
    targets = [
        {"rating_scope": SCOPE_OVERALL, "season_id": None, "format_key": None},
        {"rating_scope": SCOPE_SEASONAL, "season_id": int(season["id"]), "format_key": None},
    ]
    normalized_format = normalize_format_key(format_key, sport_key)
    if normalized_format:
        targets.extend(
            [
                {"rating_scope": SCOPE_OVERALL, "season_id": None, "format_key": normalized_format},
                {"rating_scope": SCOPE_SEASONAL, "season_id": int(season["id"]), "format_key": normalized_format},
            ]
        )
    return targets


def _insert_adjustment_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cur = conn.cursor()
    for row in rows:
        cur.execute(
            """
            INSERT OR IGNORE INTO rating_adjustments (
                entity_type,
                entity_id,
                sport_key,
                format_key,
                rating_scope,
                season_id,
                delta,
                matches_played_delta,
                matches_won_delta,
                tournaments_played_delta,
                tournaments_won_delta,
                second_places_delta,
                third_places_delta,
                mvp_matches_count_delta,
                mvp_tournaments_count_delta,
                reason,
                source_type,
                source_id,
                actor_user_id,
                event_key,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
            )
            """,
            (
                row["entity_type"],
                row["entity_id"],
                row["sport_key"],
                row.get("format_key"),
                row["rating_scope"],
                row.get("season_id"),
                row.get("delta", 0),
                row.get("matches_played_delta", 0),
                row.get("matches_won_delta", 0),
                row.get("tournaments_played_delta", 0),
                row.get("tournaments_won_delta", 0),
                row.get("second_places_delta", 0),
                row.get("third_places_delta", 0),
                row.get("mvp_matches_count_delta", 0),
                row.get("mvp_tournaments_count_delta", 0),
                row.get("reason"),
                row.get("source_type"),
                row.get("source_id"),
                row.get("actor_user_id"),
                row.get("event_key"),
            ),
        )


def _build_targeted_adjustment_rows(
    *,
    entity_type: str,
    entity_id: int,
    sport_key: str,
    format_key: str | None,
    delta: int,
    matches_played_delta: int = 0,
    matches_won_delta: int = 0,
    tournaments_played_delta: int = 0,
    tournaments_won_delta: int = 0,
    second_places_delta: int = 0,
    third_places_delta: int = 0,
    mvp_matches_count_delta: int = 0,
    mvp_tournaments_count_delta: int = 0,
    reason: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    actor_user_id: int | None = None,
    event_key_base: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in _expand_automatic_targets(sport_key, format_key):
        target_format = target.get("format_key")
        key_suffix = (
            f"{target['rating_scope']}:{target_format or FORMAT_GENERAL}:{target.get('season_id') or 0}"
        )
        rows.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "sport_key": sport_key,
                "format_key": target_format,
                "rating_scope": target["rating_scope"],
                "season_id": target.get("season_id"),
                "delta": int(delta),
                "matches_played_delta": int(matches_played_delta),
                "matches_won_delta": int(matches_won_delta),
                "tournaments_played_delta": int(tournaments_played_delta),
                "tournaments_won_delta": int(tournaments_won_delta),
                "second_places_delta": int(second_places_delta),
                "third_places_delta": int(third_places_delta),
                "mvp_matches_count_delta": int(mvp_matches_count_delta),
                "mvp_tournaments_count_delta": int(mvp_tournaments_count_delta),
                "reason": reason,
                "source_type": source_type,
                "source_id": source_id,
                "actor_user_id": actor_user_id,
                "event_key": f"{event_key_base}:{key_suffix}" if event_key_base else None,
            }
        )
    return rows


def _build_manual_adjustment_row(
    *,
    entity_type: str,
    entity_id: int,
    sport_key: str,
    format_key: str | None,
    rating_scope: str,
    season_id: int | None,
    delta: int,
    reason: str,
    actor_user_id: int | None,
) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "sport_key": normalize_sport_key(sport_key),
        "format_key": normalize_format_key(format_key, sport_key),
        "rating_scope": rating_scope,
        "season_id": season_id,
        "delta": int(delta),
        "matches_played_delta": 0,
        "matches_won_delta": 0,
        "tournaments_played_delta": 0,
        "tournaments_won_delta": 0,
        "second_places_delta": 0,
        "third_places_delta": 0,
        "mvp_matches_count_delta": 0,
        "mvp_tournaments_count_delta": 0,
        "reason": reason,
        "source_type": SOURCE_MANUAL,
        "source_id": None,
        "actor_user_id": actor_user_id,
        "event_key": None,
    }


def get_rating_value(
    entity_type: str,
    entity_id: int,
    sport_key: str,
    rating_scope: str,
    format_key: str | None = None,
    season_id: int | None = None,
) -> int:
    normalized_sport = normalize_sport_key(sport_key)
    normalized_format = normalize_format_key(format_key, normalized_sport)
    if rating_scope == SCOPE_SEASONAL and season_id is None:
        season_id = int(get_active_rating_season(normalized_sport)["id"])
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT rating_value
        FROM entity_ratings
        WHERE entity_type=? AND entity_id=? AND sport_key=? AND rating_scope=?
          AND COALESCE(format_key, '') = COALESCE(?, '')
          AND COALESCE(season_id, 0) = COALESCE(?, 0)
        """,
        (entity_type, entity_id, normalized_sport, rating_scope, normalized_format, season_id),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["rating_value"] or 0) if row else 0


def apply_manual_rating_adjustment(
    *,
    entity_type: str,
    entity_id: int,
    sport_key: str,
    rating_scope: str,
    format_key: str | None,
    delta: int,
    actor_user_id: int | None,
    season_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    normalized_sport = normalize_sport_key(sport_key)
    normalized_format = normalize_format_key(format_key, normalized_sport)
    if rating_scope == SCOPE_SEASONAL and season_id is None:
        season_id = int(get_active_rating_season(normalized_sport)["id"])
    current_value = get_rating_value(
        entity_type,
        entity_id,
        normalized_sport,
        rating_scope,
        normalized_format,
        season_id,
    )
    applied_delta = int(delta)
    if current_value + applied_delta < 0:
        applied_delta = -current_value
    if applied_delta == 0:
        return {"ok": False, "applied_delta": 0, "current_value": current_value}

    conn = get_connection()
    cur = conn.cursor()
    _insert_adjustment_rows(
        conn,
        [
            _build_manual_adjustment_row(
                entity_type=entity_type,
                entity_id=entity_id,
                sport_key=normalized_sport,
                format_key=normalized_format,
                rating_scope=rating_scope,
                season_id=season_id,
                delta=applied_delta,
                reason=(reason or "Ручная корректировка рейтинга").strip(),
                actor_user_id=actor_user_id,
            )
        ],
    )
    rebuild_entity_ratings(conn)
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "applied_delta": applied_delta,
        "new_value": current_value + applied_delta,
        "season_id": season_id,
    }


def clear_rating_bucket(
    *,
    entity_type: str,
    entity_id: int,
    sport_key: str,
    rating_scope: str,
    format_key: str | None,
    actor_user_id: int | None,
    season_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    normalized_sport = normalize_sport_key(sport_key)
    normalized_format = normalize_format_key(format_key, normalized_sport)
    if rating_scope == SCOPE_SEASONAL and season_id is None:
        season_id = int(get_active_rating_season(normalized_sport)["id"])
    current_value = get_rating_value(
        entity_type,
        entity_id,
        normalized_sport,
        rating_scope,
        normalized_format,
        season_id,
    )
    if current_value <= 0:
        return {"ok": False, "applied_delta": 0, "current_value": current_value, "season_id": season_id}
    return apply_manual_rating_adjustment(
        entity_type=entity_type,
        entity_id=entity_id,
        sport_key=normalized_sport,
        rating_scope=rating_scope,
        format_key=normalized_format,
        season_id=season_id,
        delta=-current_value,
        actor_user_id=actor_user_id,
        reason=reason or "Точечная очистка рейтинга",
    )


def _delete_adjustments_for_source(conn: sqlite3.Connection, source_type: str, source_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM rating_adjustments WHERE source_type=? AND source_id=?",
        (source_type, source_id),
    )


def replace_match_team_rating(
    *,
    source_type: str,
    match_id: int,
    sport_key: str,
    tournament_id: int | None,
    team1_id: int,
    team2_id: int,
    score1: int,
    score2: int,
    actor_user_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    owns_connection = conn is None
    if owns_connection:
        conn = get_connection()
    normalized_sport = normalize_sport_key(sport_key)
    format_key = get_rating_format_for_tournament(tournament_id) if tournament_id else None
    _delete_adjustments_for_source(conn, source_type, match_id)

    rows: list[dict[str, Any]] = []
    if score1 > score2:
        team_points = ((team1_id, TEAM_RATING_RULES["match_win"], 1), (team2_id, 0, 0))
    elif score2 > score1:
        team_points = ((team1_id, 0, 0), (team2_id, TEAM_RATING_RULES["match_win"], 1))
    else:
        team_points = (
            (team1_id, TEAM_RATING_RULES["match_draw"], 0),
            (team2_id, TEAM_RATING_RULES["match_draw"], 0),
        )

    for team_id, points, won in team_points:
        rows.extend(
            _build_targeted_adjustment_rows(
                entity_type=ENTITY_TEAM,
                entity_id=team_id,
                sport_key=normalized_sport,
                format_key=format_key,
                delta=points,
                matches_played_delta=1,
                matches_won_delta=won,
                reason="Начисление за матч",
                source_type=source_type,
                source_id=match_id,
                actor_user_id=actor_user_id,
                event_key_base=f"{source_type}:{match_id}:team:{team_id}",
            )
        )

    _insert_adjustment_rows(conn, rows)
    rebuild_entity_ratings(conn)
    if owns_connection:
        conn.commit()
        conn.close()


def _get_bracket_final_winner(cur, tournament_id: int) -> int | None:
    cur.execute(
        """
        SELECT winner_id
        FROM tournament_brackets
        WHERE tournament_id=?
          AND round_number = (
              SELECT MAX(round_number)
              FROM tournament_brackets
              WHERE tournament_id=? AND round_number <> 5
          )
          AND status='completed'
        ORDER BY match_number DESC, id DESC
        LIMIT 1
        """,
        (tournament_id, tournament_id),
    )
    row = cur.fetchone()
    return int(row["winner_id"]) if row and row["winner_id"] else None


def _get_bracket_second_place(cur, tournament_id: int) -> int | None:
    cur.execute(
        """
        SELECT team1_id, team2_id, winner_id
        FROM tournament_brackets
        WHERE tournament_id=?
          AND round_number = (
              SELECT MAX(round_number)
              FROM tournament_brackets
              WHERE tournament_id=? AND round_number <> 5
          )
          AND status='completed'
        ORDER BY match_number DESC, id DESC
        LIMIT 1
        """,
        (tournament_id, tournament_id),
    )
    row = cur.fetchone()
    if not row or not row["winner_id"]:
        return None
    if int(row["winner_id"]) == int(row["team1_id"] or 0):
        return int(row["team2_id"] or 0) or None
    return int(row["team1_id"] or 0) or None


def _get_bracket_third_place(cur, tournament_id: int) -> int | None:
    cur.execute(
        """
        SELECT winner_id
        FROM tournament_brackets
        WHERE tournament_id=? AND round_number=5 AND status='completed'
        ORDER BY id DESC
        LIMIT 1
        """,
        (tournament_id,),
    )
    row = cur.fetchone()
    return int(row["winner_id"]) if row and row["winner_id"] else None


def _get_completed_tournament_team_ids(cur, tournament_id: int) -> list[int]:
    cur.execute(
        """
        SELECT DISTINCT team_id
        FROM (
            SELECT team1_id AS team_id
            FROM tournament_brackets
            WHERE tournament_id=? AND status='completed' AND team1_id IS NOT NULL
            UNION ALL
            SELECT team2_id AS team_id
            FROM tournament_brackets
            WHERE tournament_id=? AND status='completed' AND team2_id IS NOT NULL
        )
        ORDER BY team_id
        """,
        (tournament_id, tournament_id),
    )
    return [int(row["team_id"]) for row in cur.fetchall()]


def _get_match_source_for_sport_stats(sport_key: str) -> str | None:
    sport = normalize_sport_key(sport_key)
    if sport == "Football":
        return "football_player_stats"
    if sport == "Basketball":
        return "basketball_player_stats"
    if sport == "Volleyball":
        return "volleyball_player_stats"
    return None


def _get_tournament_player_summaries(cur, tournament_id: int, sport_key: str) -> list[dict[str, Any]]:
    normalized_sport = normalize_sport_key(sport_key)
    if normalized_sport == "CS2":
        cur.execute(
            """
            WITH match_player_totals AS (
                SELECT
                    p.match_id,
                    p.user_id,
                    p.team_id,
                    SUM(COALESCE(p.kills, 0)) AS kills_total,
                    SUM(COALESCE(p.deaths, 0)) AS deaths_total,
                    AVG(COALESCE(p.adr, 0)) AS adr_avg,
                    AVG(COALESCE(p.rating_3_0, 0)) AS rating_avg
                FROM player_match_stats p
                WHERE COALESCE(p.match_source, 'bracket')='bracket'
                GROUP BY p.match_id, p.user_id, p.team_id
            )
            SELECT
                mpt.user_id,
                mpt.team_id,
                COUNT(DISTINCT mpt.match_id) AS matches_played,
                SUM(CASE WHEN b.winner_id = mpt.team_id THEN 1 ELSE 0 END) AS matches_won,
                SUM(COALESCE(mpt.kills_total, 0)) AS kills_total,
                SUM(COALESCE(mpt.deaths_total, 0)) AS deaths_total,
                AVG(COALESCE(mpt.adr_avg, 0)) AS adr_avg,
                AVG(COALESCE(mpt.rating_avg, 0)) AS rating_avg
            FROM match_player_totals mpt
            JOIN tournament_brackets b ON b.id = mpt.match_id
            WHERE b.tournament_id=? AND b.status='completed'
            GROUP BY mpt.user_id, mpt.team_id
            ORDER BY matches_played DESC, kills_total DESC, user_id
            """,
            (tournament_id,),
        )
        return _dict_rows(cur.fetchall())

    table_name = _get_match_source_for_sport_stats(normalized_sport)
    if not table_name:
        return []
    cur.execute(
        f"""
        SELECT
            s.user_id,
            s.team_id,
            COUNT(DISTINCT s.match_id) AS matches_played,
            SUM(CASE WHEN b.winner_id = s.team_id THEN 1 ELSE 0 END) AS matches_won,
            0 AS kills_total,
            0 AS deaths_total,
            0 AS adr_avg,
            0 AS rating_avg
        FROM {table_name} s
        JOIN tournament_brackets b ON b.id = s.match_id
        WHERE COALESCE(s.match_source, 'bracket')='bracket'
          AND b.tournament_id=? AND b.status='completed'
        GROUP BY s.user_id, s.team_id
        ORDER BY matches_played DESC, user_id
        """,
        (tournament_id,),
    )
    return _dict_rows(cur.fetchall())


def get_match_mvp_candidates(match_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.user_id,
            p.team_id,
            COALESCE(SUM(p.kills), 0) AS kills_total,
            COALESCE(SUM(p.deaths), 0) AS deaths_total,
            COALESCE(AVG(p.adr), 0) AS adr_avg,
            COALESCE(AVG(p.rating_3_0), 0) AS rating_avg,
            u.first_name,
            u.username
        FROM player_match_stats p
        JOIN users u ON u.id = p.user_id
        WHERE COALESCE(p.match_source, 'bracket')='bracket'
          AND p.match_id=?
        GROUP BY p.user_id, p.team_id, u.first_name, u.username
        ORDER BY kills_total DESC, deaths_total ASC, adr_avg DESC, p.user_id
        """,
        (match_id,),
    )
    rows = _dict_rows(cur.fetchall())
    conn.close()
    return rows


def _pick_auto_match_mvp(candidate_rows: list[dict[str, Any]]) -> int | None:
    if not candidate_rows:
        return None
    use_rating = any(float(row.get("rating_avg") or 0) > 0 for row in candidate_rows)
    if use_rating:
        candidate_rows.sort(
            key=lambda row: (
                float(row.get("rating_avg") or 0),
                int(row.get("kills_total") or 0),
                -int(row.get("deaths_total") or 0),
                float(row.get("adr_avg") or 0),
                -int(row.get("user_id") or 0),
            ),
            reverse=True,
        )
    else:
        candidate_rows.sort(
            key=lambda row: (
                int(row.get("kills_total") or 0),
                -int(row.get("deaths_total") or 0),
                float(row.get("adr_avg") or 0),
                -int(row.get("user_id") or 0),
            ),
            reverse=True,
        )
    return int(candidate_rows[0]["user_id"])


def get_effective_match_mvp(match_id: int, sport_key: str) -> int | None:
    if normalize_sport_key(sport_key) != "CS2":
        return None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id
        FROM rating_mvp_assignments
        WHERE source_type='match' AND source_id=?
        LIMIT 1
        """,
        (match_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row and row["user_id"]:
        return int(row["user_id"])
    return _pick_auto_match_mvp(get_match_mvp_candidates(match_id))


def set_match_mvp_override(match_id: int, user_id: int, sport_key: str, assigned_by: int | None = None) -> dict[str, Any]:
    if normalize_sport_key(sport_key) != "CS2":
        return {"ok": False, "reason": "unsupported_sport"}
    candidates = get_match_mvp_candidates(match_id)
    if not any(int(row["user_id"]) == int(user_id) for row in candidates):
        return {"ok": False, "reason": "invalid_user"}

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO rating_mvp_assignments (
            source_type, source_id, user_id, sport_key, assigned_mode, assigned_by, created_at, updated_at
        )
        VALUES ('match', ?, ?, ?, 'manual', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(source_type, source_id) DO UPDATE SET
            user_id=excluded.user_id,
            sport_key=excluded.sport_key,
            assigned_mode='manual',
            assigned_by=excluded.assigned_by,
            updated_at=CURRENT_TIMESTAMP
        """,
        (match_id, user_id, normalize_sport_key(sport_key), assigned_by),
    )
    cur.execute("SELECT tournament_id FROM tournament_brackets WHERE id=?", (match_id,))
    row = cur.fetchone()
    tournament_id = int(row["tournament_id"]) if row and row["tournament_id"] else None
    if tournament_id:
        cur.execute("SELECT status FROM tournaments WHERE id=?", (tournament_id,))
        tournament = cur.fetchone()
        if tournament and tournament["status"] == "finished":
            _delete_adjustments_for_source(conn, SOURCE_TOURNAMENT, tournament_id)
            conn.commit()
            conn.close()
            replace_tournament_rating_awards(tournament_id, actor_user_id=assigned_by)
            return {"ok": True, "recalculated": True, "tournament_id": tournament_id}
    conn.commit()
    conn.close()
    return {"ok": True, "recalculated": False, "tournament_id": tournament_id}


def get_tournament_mvp_candidates(tournament_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT sport FROM tournaments WHERE id=?", (tournament_id,))
    tournament = cur.fetchone()
    if not tournament or normalize_sport_key(tournament["sport"]) != "CS2":
        conn.close()
        return []
    summaries = _get_tournament_player_summaries(cur, tournament_id, "CS2")
    match_mvps = get_tournament_match_mvp_counts(tournament_id)
    for row in summaries:
        row["match_mvp_count"] = int(match_mvps.get(int(row["user_id"]), 0))
    summaries.sort(
        key=lambda row: (
            int(row.get("match_mvp_count") or 0),
            int(row.get("kills_total") or 0),
            -int(row.get("deaths_total") or 0),
            float(row.get("adr_avg") or 0),
            -int(row.get("user_id") or 0),
        ),
        reverse=True,
    )
    if summaries:
        user_ids = [int(row["user_id"]) for row in summaries]
        placeholders = ",".join("?" for _ in user_ids)
        cur.execute(
            f"""
            SELECT id, first_name, username
            FROM users
            WHERE id IN ({placeholders})
            """,
            tuple(user_ids),
        )
        user_map = {int(row["id"]): dict(row) for row in cur.fetchall()}
        for row in summaries:
            user = user_map.get(int(row["user_id"]), {})
            row["first_name"] = user.get("first_name")
            row["username"] = user.get("username")
    conn.close()
    return summaries


def get_tournament_match_mvp_counts(tournament_id: int) -> dict[int, int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id
        FROM tournament_brackets
        WHERE tournament_id=? AND status='completed'
        ORDER BY round_number, match_number, id
        """,
        (tournament_id,),
    )
    match_ids = [int(row["id"]) for row in cur.fetchall()]
    conn.close()
    counts: dict[int, int] = {}
    for match_id in match_ids:
        user_id = get_effective_match_mvp(match_id, "CS2")
        if user_id:
            counts[user_id] = counts.get(user_id, 0) + 1
    return counts


def get_effective_tournament_mvp(tournament_id: int, sport_key: str) -> int | None:
    if normalize_sport_key(sport_key) != "CS2":
        return None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id
        FROM rating_mvp_assignments
        WHERE source_type='tournament' AND source_id=?
        LIMIT 1
        """,
        (tournament_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row and row["user_id"]:
        return int(row["user_id"])
    candidates = get_tournament_mvp_candidates(tournament_id)
    return int(candidates[0]["user_id"]) if candidates else None


def set_tournament_mvp_override(tournament_id: int, user_id: int, sport_key: str, assigned_by: int | None = None) -> dict[str, Any]:
    if normalize_sport_key(sport_key) != "CS2":
        return {"ok": False, "reason": "unsupported_sport"}
    candidates = get_tournament_mvp_candidates(tournament_id)
    if not any(int(row["user_id"]) == int(user_id) for row in candidates):
        return {"ok": False, "reason": "invalid_user"}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO rating_mvp_assignments (
            source_type, source_id, user_id, sport_key, assigned_mode, assigned_by, created_at, updated_at
        )
        VALUES ('tournament', ?, ?, ?, 'manual', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(source_type, source_id) DO UPDATE SET
            user_id=excluded.user_id,
            sport_key=excluded.sport_key,
            assigned_mode='manual',
            assigned_by=excluded.assigned_by,
            updated_at=CURRENT_TIMESTAMP
        """,
        (tournament_id, user_id, normalize_sport_key(sport_key), assigned_by),
    )
    cur.execute("SELECT status FROM tournaments WHERE id=?", (tournament_id,))
    tournament = cur.fetchone()
    conn.commit()
    conn.close()
    if tournament and tournament["status"] == "finished":
        replace_tournament_rating_awards(tournament_id, actor_user_id=assigned_by)
        return {"ok": True, "recalculated": True}
    return {"ok": True, "recalculated": False}


def replace_tournament_rating_awards(tournament_id: int, actor_user_id: int | None = None) -> dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, sport, required_team_size FROM tournaments WHERE id=?",
        (tournament_id,),
    )
    tournament = cur.fetchone()
    if not tournament:
        conn.close()
        return {"ok": False, "reason": "not_found"}

    sport_key = normalize_sport_key(tournament["sport"])
    format_key = resolve_cs2_format_from_team_size(sport_key, int(tournament["required_team_size"] or 0))
    first_place_id = _get_bracket_final_winner(cur, tournament_id)
    second_place_id = _get_bracket_second_place(cur, tournament_id)
    third_place_id = _get_bracket_third_place(cur, tournament_id)
    team_ids = _get_completed_tournament_team_ids(cur, tournament_id)
    player_summaries = _get_tournament_player_summaries(cur, tournament_id, sport_key)
    match_mvp_counts = get_tournament_match_mvp_counts(tournament_id) if sport_key == "CS2" else {}
    tournament_mvp_user_id = get_effective_tournament_mvp(tournament_id, sport_key) if sport_key == "CS2" else None

    _delete_adjustments_for_source(conn, SOURCE_TOURNAMENT, tournament_id)
    rows: list[dict[str, Any]] = []

    for team_id in team_ids:
        delta = 0
        tournaments_won_delta = 0
        second_places_delta = 0
        third_places_delta = 0
        if first_place_id and int(team_id) == int(first_place_id):
            delta = TEAM_RATING_RULES["first_place"]
            tournaments_won_delta = 1
        elif second_place_id and int(team_id) == int(second_place_id):
            delta = TEAM_RATING_RULES["second_place"]
            second_places_delta = 1
        elif third_place_id and int(team_id) == int(third_place_id):
            delta = TEAM_RATING_RULES["third_place"]
            third_places_delta = 1
        rows.extend(
            _build_targeted_adjustment_rows(
                entity_type=ENTITY_TEAM,
                entity_id=int(team_id),
                sport_key=sport_key,
                format_key=format_key,
                delta=delta,
                tournaments_played_delta=1,
                tournaments_won_delta=tournaments_won_delta,
                second_places_delta=second_places_delta,
                third_places_delta=third_places_delta,
                reason="Начисление за завершение турнира",
                source_type=SOURCE_TOURNAMENT,
                source_id=tournament_id,
                actor_user_id=actor_user_id,
                event_key_base=f"tournament:{tournament_id}:team:{team_id}",
            )
        )

    for summary in player_summaries:
        user_id = int(summary["user_id"])
        team_id = int(summary["team_id"])
        matches_played = int(summary.get("matches_played") or 0)
        matches_won = int(summary.get("matches_won") or 0)
        if matches_played <= 0:
            continue
        base_points = PLAYER_RATING_RULES["participation"] + matches_won * PLAYER_RATING_RULES["match_win"]
        tournaments_won_delta = 0
        second_places_delta = 0
        third_places_delta = 0
        if first_place_id and team_id == int(first_place_id):
            base_points += PLAYER_RATING_RULES["first_place"]
            tournaments_won_delta = 1
        elif second_place_id and team_id == int(second_place_id):
            base_points += PLAYER_RATING_RULES["second_place"]
            second_places_delta = 1
        elif third_place_id and team_id == int(third_place_id):
            base_points += PLAYER_RATING_RULES["third_place"]
            third_places_delta = 1

        match_mvp_count = int(match_mvp_counts.get(user_id, 0))
        tournament_mvp_count = 1 if tournament_mvp_user_id and user_id == int(tournament_mvp_user_id) else 0
        mvp_bonus_raw = (
            match_mvp_count * PLAYER_RATING_RULES["match_mvp"]
            + tournament_mvp_count * PLAYER_RATING_RULES["tournament_mvp"]
        )
        mvp_bonus = min(mvp_bonus_raw, PLAYER_RATING_RULES["mvp_cap"])
        multiplier = get_rating_multiplier(sport_key, format_key)
        total_points = round_rating_points((base_points + mvp_bonus) * multiplier)
        rows.extend(
            _build_targeted_adjustment_rows(
                entity_type=ENTITY_PLAYER,
                entity_id=user_id,
                sport_key=sport_key,
                format_key=format_key,
                delta=total_points,
                matches_played_delta=matches_played,
                matches_won_delta=matches_won,
                tournaments_played_delta=1,
                tournaments_won_delta=tournaments_won_delta,
                second_places_delta=second_places_delta,
                third_places_delta=third_places_delta,
                mvp_matches_count_delta=match_mvp_count,
                mvp_tournaments_count_delta=tournament_mvp_count,
                reason="Начисление игроку за завершение турнира",
                source_type=SOURCE_TOURNAMENT,
                source_id=tournament_id,
                actor_user_id=actor_user_id,
                event_key_base=f"tournament:{tournament_id}:player:{user_id}",
            )
        )

    _insert_adjustment_rows(conn, rows)
    rebuild_entity_ratings(conn)
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "first_place_id": first_place_id,
        "second_place_id": second_place_id,
        "third_place_id": third_place_id,
        "season_id": int(get_active_rating_season(sport_key)["id"]),
    }


def get_rating_leaderboard(
    *,
    entity_type: str,
    sport_key: str,
    rating_scope: str,
    format_key: str | None = None,
    season_id: int | None = None,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    normalized_sport = normalize_sport_key(sport_key)
    normalized_format = normalize_format_key(format_key, normalized_sport)
    if rating_scope == SCOPE_SEASONAL and season_id is None:
        season_id = int(get_active_rating_season(normalized_sport)["id"])
    conn = get_connection()
    cur = conn.cursor()
    join_sql = (
        """
        LEFT JOIN users u ON u.id = r.entity_id
        """
        if entity_type == ENTITY_PLAYER
        else """
        LEFT JOIN teams t ON t.id = r.entity_id
        """
    )
    select_sql = (
        """
        u.first_name AS first_name,
        u.username AS username,
        NULL AS team_name
        """
        if entity_type == ENTITY_PLAYER
        else """
        NULL AS first_name,
        NULL AS username,
        t.name AS team_name
        """
    )
    cur.execute(
        f"""
        SELECT
            r.entity_id,
            r.rating_value,
            r.matches_played,
            r.matches_won,
            r.tournaments_played,
            r.tournaments_won,
            r.second_places,
            r.third_places,
            r.mvp_matches_count,
            r.mvp_tournaments_count,
            {select_sql}
        FROM entity_ratings r
        {join_sql}
        WHERE r.entity_type=? AND r.sport_key=? AND r.rating_scope=?
          AND COALESCE(r.format_key, '') = COALESCE(?, '')
          AND COALESCE(r.season_id, 0) = COALESCE(?, 0)
        ORDER BY r.rating_value DESC, r.tournaments_won DESC, r.matches_won DESC, r.entity_id ASC
        LIMIT ? OFFSET ?
        """,
        (entity_type, normalized_sport, rating_scope, normalized_format, season_id, limit + 1, offset),
    )
    rows = _dict_rows(cur.fetchall())
    conn.close()
    has_next = len(rows) > limit
    return rows[:limit], has_next


def get_rating_row(
    *,
    entity_type: str,
    entity_id: int,
    sport_key: str,
    rating_scope: str,
    format_key: str | None = None,
    season_id: int | None = None,
) -> dict[str, Any] | None:
    normalized_sport = normalize_sport_key(sport_key)
    normalized_format = normalize_format_key(format_key, normalized_sport)
    if rating_scope == SCOPE_SEASONAL and season_id is None:
        season_id = int(get_active_rating_season(normalized_sport)["id"])
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM entity_ratings
        WHERE entity_type=? AND entity_id=? AND sport_key=? AND rating_scope=?
          AND COALESCE(format_key, '') = COALESCE(?, '')
          AND COALESCE(season_id, 0) = COALESCE(?, 0)
        LIMIT 1
        """,
        (entity_type, entity_id, normalized_sport, rating_scope, normalized_format, season_id),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def build_site_rating_payload() -> dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, display_name FROM sports ORDER BY display_name, name")
    sports = _dict_rows(cur.fetchall())
    payload: dict[str, Any] = {"groups": [], "seasons": {}, "leaderboards": {}}
    for sport in sports:
        sport_key = normalize_sport_key(sport["name"])
        overall_team_rows, _ = get_rating_leaderboard(
            entity_type=ENTITY_TEAM,
            sport_key=sport_key,
            rating_scope=SCOPE_OVERALL,
            format_key=None,
            limit=100,
            offset=0,
        )
        payload["groups"].append(
            {
                "sport": sport_key,
                "sport_display": sport["display_name"],
                "month": None,
                "items": [
                    {
                        "id": row["entity_id"],
                        "name": row["team_name"],
                        "city": None,
                        "points": row["rating_value"],
                    }
                    for row in overall_team_rows
                ],
            }
        )
        payload["seasons"][sport_key] = list_rating_seasons(sport_key)
        payload["leaderboards"][sport_key] = {
            "teams_overall": overall_team_rows,
            "players_overall": get_rating_leaderboard(
                entity_type=ENTITY_PLAYER,
                sport_key=sport_key,
                rating_scope=SCOPE_OVERALL,
                format_key=None,
                limit=100,
                offset=0,
            )[0],
            "formats": get_format_options_for_sport(sport_key),
        }
    return payload


__all__ = [
    "advance_to_next_rating_season",
    "apply_manual_rating_adjustment",
    "build_site_rating_payload",
    "clear_rating_bucket",
    "ensure_rating_defaults",
    "get_active_rating_season",
    "get_adjacent_season_ids",
    "get_effective_match_mvp",
    "get_effective_tournament_mvp",
    "get_format_options_for_sport",
    "get_rating_format_for_tournament",
    "get_rating_leaderboard",
    "get_rating_row",
    "get_rating_season_by_id",
    "get_rating_value",
    "get_tournament_match_mvp_counts",
    "get_tournament_mvp_candidates",
    "list_rating_seasons",
    "normalize_format_key",
    "normalize_sport_key",
    "replace_match_team_rating",
    "replace_tournament_rating_awards",
    "set_match_mvp_override",
    "set_tournament_mvp_override",
    "sport_supports_formats",
]
