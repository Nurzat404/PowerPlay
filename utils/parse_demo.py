from __future__ import annotations

import argparse
import json
import math
import os
import sys
from bisect import bisect_left
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
from demoparser2 import DemoParser
from dotenv import load_dotenv


ROUND_TRACKER_DAMAGE_PROP = (
    "CCSPlayerController.CCSPlayerController_ActionTrackingServices"
    ".m_flTotalRoundDamageDealt"
)
TOTAL_ROUNDS_PROP = "total_rounds_played"
TEAM_PROPS = ["team_clan_name", "team_rounds_total"]


def parse_demo(demo_path: str, steam_api_key: str | None = None) -> dict[str, Any]:
    parser = DemoParser(str(demo_path))
    header = parser.parse_header()
    match_end_tick = _extract_match_end_tick(parser)
    rounds = _build_counted_rounds(parser, match_end_tick)
    fallback_names = _build_fallback_names(parser)

    player_info = parser.parse_player_info().copy()
    if player_info.empty:
        raise ValueError("No players found in demo.")
    player_info["steamid"] = player_info["steamid"].map(_normalize_steamid)
    player_info = player_info.dropna(subset=["steamid"]).drop_duplicates(subset=["steamid"])

    steamids = sorted(player_info["steamid"].tolist())
    display_names, steam_names = _resolve_player_names(steamids, fallback_names, steam_api_key)

    kills_df = _annotate_events_with_rounds(parser.parse_event("player_death").copy(), rounds)
    death_counts = _compute_death_counts(parser, kills_df)
    adr_by_player = _compute_adr_by_player(parser, rounds, steamids)
    assist_counts = _compute_assist_counts(parser, kills_df)
    player_team_map, team_scores = _extract_team_data(parser, rounds, match_end_tick)
    player_stats = _compute_player_stats(
        steamids=steamids,
        kills_df=kills_df,
        death_counts=death_counts,
        assist_counts=assist_counts,
        adr_by_player=adr_by_player,
        player_team_map=player_team_map,
        fallback_names=fallback_names,
        display_names=display_names,
        steam_names=steam_names,
    )

    team_results = _build_team_results(player_stats, team_scores)
    return {
        "map": _normalize_map_name(str(header.get("map_name", ""))),
        "rounds_played": len(rounds),
        "teams": team_results,
    }


def format_summary(result: dict[str, Any]) -> str:
    if not result["teams"]:
        return f"🗺 {result['map']}: 0:0"

    score_text = ":".join(str(team["score"]) for team in result["teams"])
    lines = [f"🗺 {result['map']}: {score_text}"]
    icons = ["🔵", "🔴"]
    for index, team in enumerate(result["teams"]):
        icon = icons[index] if index < len(icons) else "•"
        lines.append(f"{icon} {team['name']}:")
        for player in team["players"]:
            lines.append(
                "• "
                f"{player['name']} "
                f"[{player['steamid']}] "
                f"K:{player['kills']} D:{player['deaths']} A:{player['assists']} "
                f"| ADR:{player['adr']} HS:{player['hs_percent']}"
            )
    return "\n".join(lines)


def _extract_match_end_tick(parser: DemoParser) -> int:
    match_end_df = parser.parse_event("cs_win_panel_match")
    if match_end_df.empty:
        raise ValueError("Could not find cs_win_panel_match in demo.")
    return int(match_end_df["tick"].iloc[0])


def _build_counted_rounds(parser: DemoParser, match_end_tick: int) -> list[dict[str, int]]:
    round_end_df = parser.parse_event("round_officially_ended")
    if round_end_df.empty:
        raise ValueError("Could not find round_officially_ended events in demo.")

    checkpoint_ticks = sorted(
        {int(tick) for tick in round_end_df["tick"].tolist()} | {int(match_end_tick)}
    )
    checkpoints_df = parser.parse_ticks([TOTAL_ROUNDS_PROP], ticks=checkpoint_ticks)
    checkpoints_df = (
        checkpoints_df[["tick", TOTAL_ROUNDS_PROP]]
        .drop_duplicates(subset=["tick"])
        .sort_values("tick")
        .reset_index(drop=True)
    )

    rounds: list[dict[str, int]] = []
    if not checkpoints_df.empty:
        first_tick = int(checkpoints_df.iloc[0]["tick"])
        first_total = int(checkpoints_df.iloc[0][TOTAL_ROUNDS_PROP])
        if first_total >= 1:
            rounds.append(
                {
                    "round_no": 1,
                    "start_tick": _infer_first_round_start_tick(parser, first_tick),
                    "end_tick": first_tick,
                }
            )

    previous = None
    for row in checkpoints_df.itertuples(index=False):
        current_tick = int(row.tick)
        current_total = int(getattr(row, TOTAL_ROUNDS_PROP))
        if previous is not None:
            previous_tick, previous_total = previous
            if current_total == previous_total + 1:
                rounds.append(
                    {
                        "round_no": current_total,
                        "start_tick": previous_tick,
                        "end_tick": current_tick,
                    }
                )
        previous = (current_tick, current_total)

    if not rounds:
        raise ValueError("Could not derive counted rounds from demo.")
    return rounds


def _infer_first_round_start_tick(parser: DemoParser, first_checkpoint_tick: int) -> int:
    candidate_ticks: list[int] = []
    for event_name in (
        "round_freeze_end",
        "round_announce_match_start",
        "begin_new_match",
        "round_prestart",
        "round_poststart",
    ):
        event_df = parser.parse_event(event_name)
        if isinstance(event_df, pd.DataFrame) and not event_df.empty:
            candidate_ticks.extend(
                int(tick) for tick in event_df["tick"].tolist() if int(tick) < first_checkpoint_tick
            )

    if not candidate_ticks:
        return max(first_checkpoint_tick - 1, 0)

    candidate_ticks = sorted(set(candidate_ticks))
    warmup_df = parser.parse_ticks(["is_warmup_period"], ticks=candidate_ticks)
    warmup_df = (
        warmup_df[["tick", "is_warmup_period"]]
        .drop_duplicates(subset=["tick"])
        .sort_values("tick")
    )
    live_ticks = warmup_df.loc[~warmup_df["is_warmup_period"], "tick"].astype(int).tolist()
    if live_ticks:
        return max(live_ticks)
    return max(first_checkpoint_tick - 1, 0)


def _annotate_events_with_rounds(
    events_df: pd.DataFrame, rounds: list[dict[str, int]]
) -> pd.DataFrame:
    if events_df.empty:
        events_df["round_no"] = pd.Series(dtype="Int64")
        return events_df

    end_ticks = [round_info["end_tick"] for round_info in rounds]
    start_ticks = [round_info["start_tick"] for round_info in rounds]
    round_numbers = [round_info["round_no"] for round_info in rounds]

    assigned_rounds: list[int | None] = []
    for raw_tick in events_df["tick"].tolist():
        tick = int(raw_tick)
        index = bisect_left(end_ticks, tick)
        if index < len(end_ticks) and tick > start_ticks[index]:
            assigned_rounds.append(round_numbers[index])
        else:
            assigned_rounds.append(None)

    annotated_df = events_df.copy()
    annotated_df["round_no"] = assigned_rounds
    annotated_df = annotated_df.dropna(subset=["round_no"]).copy()
    annotated_df["round_no"] = annotated_df["round_no"].astype(int)
    return annotated_df


def _build_fallback_names(parser: DemoParser) -> dict[str, str]:
    fallback_names: dict[str, str] = {}

    connect_df = parser.parse_event("player_connect_full")
    if isinstance(connect_df, pd.DataFrame) and not connect_df.empty:
        connect_df = connect_df.copy()
        connect_df["user_steamid"] = connect_df["user_steamid"].map(_normalize_steamid)
        connect_df = connect_df.dropna(subset=["user_steamid"])
        connect_df = connect_df.sort_values("tick")
        for row in connect_df.itertuples(index=False):
            steamid = row.user_steamid
            user_name = str(row.user_name).strip()
            if steamid and user_name and steamid not in fallback_names:
                fallback_names[steamid] = user_name

    player_info_df = parser.parse_player_info().copy()
    if not player_info_df.empty:
        player_info_df["steamid"] = player_info_df["steamid"].map(_normalize_steamid)
        player_info_df = player_info_df.dropna(subset=["steamid"])
        for row in player_info_df.itertuples(index=False):
            steamid = row.steamid
            name = str(row.name).strip()
            if steamid and name and steamid not in fallback_names:
                fallback_names[steamid] = name

    return fallback_names


def _resolve_player_names(
    steamids: list[str], fallback_names: dict[str, str], steam_api_key: str | None
) -> tuple[dict[str, str], dict[str, str]]:
    if steam_api_key is not None:
        api_key = steam_api_key.strip()
        if not api_key:
            api_key = ""
    else:
        _load_local_env()
        api_key = (os.getenv("STEAM_API_KEY") or "").strip()

    steam_names: dict[str, str] = {}
    if api_key:
        try:
            steam_names = _fetch_steam_persona_names(steamids, api_key)
        except Exception:
            steam_names = {}

    display_names = {
        steamid: steam_names.get(steamid) or fallback_names.get(steamid) or steamid
        for steamid in steamids
    }
    return display_names, steam_names


def _fetch_steam_persona_names(steamids: list[str], steam_api_key: str) -> dict[str, str]:
    if not steamids:
        return {}

    query = urlencode(
        {
            "key": steam_api_key,
            "steamids": ",".join(steamids),
        }
    )
    url = (
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?" + query
    )
    try:
        with urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("Failed to fetch Steam persona names from Steam API.") from exc

    players = payload.get("response", {}).get("players", [])
    return {
        _normalize_steamid(player.get("steamid")): str(player.get("personaname", "")).strip()
        for player in players
        if _normalize_steamid(player.get("steamid"))
    }


def _extract_team_data(
    parser: DemoParser, rounds: list[dict[str, int]], match_end_tick: int
) -> tuple[dict[str, str], dict[str, int]]:
    team_ticks = sorted({match_end_tick, *(round_info["end_tick"] for round_info in rounds)})
    team_df = parser.parse_ticks(TEAM_PROPS, ticks=team_ticks).copy()
    if team_df.empty:
        raise ValueError("Could not resolve team data from demo.")

    team_df["steamid"] = team_df["steamid"].map(_normalize_steamid)
    team_df = team_df.dropna(subset=["steamid"])
    team_df["team_clan_name"] = team_df["team_clan_name"].fillna("").map(str).str.strip()
    team_df = team_df[team_df["team_clan_name"] != ""].copy()

    player_team_map: dict[str, str] = {}
    team_df = team_df.sort_values("tick")
    for row in team_df.itertuples(index=False):
        player_team_map[row.steamid] = row.team_clan_name

    final_tick_df = team_df[team_df["tick"] == match_end_tick].copy()
    score_source_df = final_tick_df if not final_tick_df.empty else team_df
    team_scores = (
        score_source_df.groupby("team_clan_name")["team_rounds_total"]
        .max()
        .astype(int)
        .to_dict()
    )
    return player_team_map, team_scores


def _compute_assist_counts(parser: DemoParser, kills_df: pd.DataFrame) -> dict[str, int]:
    if kills_df.empty or "assister_steamid" not in kills_df.columns:
        return {}

    assists_df = kills_df.copy()
    for column_name in ("attacker_steamid", "user_steamid", "assister_steamid"):
        if column_name in assists_df.columns:
            assists_df[column_name] = assists_df[column_name].map(_normalize_steamid)

    assists_df = assists_df[assists_df["assister_steamid"].notna()].copy()
    if assists_df.empty:
        return {}

    assists_df = _annotate_kill_teams(parser, assists_df)

    # Ignore assists on pure world/self deaths and rows where the assister is not on the killer's side.
    assists_df = assists_df[
        ~(
            (assists_df["attacker_steamid"] == assists_df["user_steamid"])
            & assists_df["weapon"].fillna("").isin({"world", "worldent"})
        )
    ].copy()
    assists_df = assists_df[
        (assists_df["attacker_steamid"] == assists_df["user_steamid"])
        | (assists_df["assister_team"] == assists_df["attacker_team"])
    ].copy()

    return _group_event_counts(assists_df, "assister_steamid")


def _compute_death_counts(parser: DemoParser, kills_df: pd.DataFrame) -> dict[str, int]:
    if kills_df.empty:
        return {}

    deaths_df = kills_df.copy()
    for column_name in ("attacker_steamid", "user_steamid"):
        if column_name in deaths_df.columns:
            deaths_df[column_name] = deaths_df[column_name].map(_normalize_steamid)

    disconnect_df = parser.parse_event("player_disconnect")
    disconnect_keys: set[tuple[int, str]] = set()
    if isinstance(disconnect_df, pd.DataFrame) and not disconnect_df.empty:
        disconnect_df = disconnect_df.copy()
        disconnect_df["user_steamid"] = disconnect_df["user_steamid"].map(_normalize_steamid)
        disconnect_df = disconnect_df.dropna(subset=["user_steamid"])
        disconnect_df = disconnect_df[disconnect_df["reason"] == 2]
        disconnect_keys = {
            (int(row.tick), row.user_steamid)
            for row in disconnect_df.itertuples(index=False)
        }

    if disconnect_keys:
        deaths_df = deaths_df[
            ~deaths_df.apply(
                lambda row: (
                    row["attacker_steamid"] == row["user_steamid"]
                    and str(row.get("weapon", "")).strip() in {"world", "worldent"}
                    and (int(row["tick"]), row["user_steamid"]) in disconnect_keys
                ),
                axis=1,
            )
        ].copy()

    return _group_event_counts(deaths_df, "user_steamid")


def _annotate_kill_teams(parser: DemoParser, kills_df: pd.DataFrame) -> pd.DataFrame:
    if kills_df.empty:
        return kills_df

    team_ticks = sorted({int(tick) for tick in kills_df["tick"].tolist()})
    team_df = parser.parse_ticks(["team_clan_name"], ticks=team_ticks).copy()
    team_df["steamid"] = team_df["steamid"].map(_normalize_steamid)
    team_df = team_df.dropna(subset=["steamid"])
    team_df = team_df[["tick", "steamid", "team_clan_name"]].drop_duplicates()

    annotated_df = kills_df.copy()
    for steamid_column, team_column in (
        ("attacker_steamid", "attacker_team"),
        ("user_steamid", "user_team"),
        ("assister_steamid", "assister_team"),
    ):
        side_df = team_df.rename(columns={"steamid": steamid_column, "team_clan_name": team_column})
        annotated_df = annotated_df.merge(side_df, on=["tick", steamid_column], how="left")
    return annotated_df


def _compute_player_stats(
    steamids: list[str],
    kills_df: pd.DataFrame,
    death_counts: dict[str, int],
    assist_counts: dict[str, int],
    adr_by_player: dict[str, int],
    player_team_map: dict[str, str],
    fallback_names: dict[str, str],
    display_names: dict[str, str],
    steam_names: dict[str, str],
) -> list[dict[str, Any]]:
    counted_kills_df = kills_df.copy()
    if {"attacker_steamid", "user_steamid"}.issubset(counted_kills_df.columns):
        counted_kills_df["attacker_steamid"] = counted_kills_df["attacker_steamid"].map(
            _normalize_steamid
        )
        counted_kills_df["user_steamid"] = counted_kills_df["user_steamid"].map(
            _normalize_steamid
        )
        counted_kills_df = counted_kills_df[
            counted_kills_df["attacker_steamid"].notna()
            & (counted_kills_df["attacker_steamid"] != counted_kills_df["user_steamid"])
        ].copy()

    kill_counts = _group_event_counts(counted_kills_df, "attacker_steamid")
    headshot_counts = _group_event_counts(
        counted_kills_df[counted_kills_df["headshot"].fillna(False)], "attacker_steamid"
    )

    player_stats: list[dict[str, Any]] = []
    for steamid in steamids:
        kills = kill_counts.get(steamid, 0)
        headshots = headshot_counts.get(steamid, 0)
        hs_percent = math.floor((headshots / kills) * 100) if kills else 0

        player_stats.append(
            {
                "steamid": steamid,
                "name": display_names.get(steamid) or fallback_names.get(steamid) or steamid,
                "steam_name": steam_names.get(steamid),
                "demo_name": fallback_names.get(steamid),
                "name_source": _pick_name_source(steamid, steam_names, fallback_names),
                "team": player_team_map.get(steamid, ""),
                "kills": kills,
                "deaths": death_counts.get(steamid, 0),
                "assists": assist_counts.get(steamid, 0),
                "adr": adr_by_player.get(steamid, 0),
                "hs_percent": hs_percent,
            }
        )
    return player_stats


def _compute_adr_by_player(
    parser: DemoParser, rounds: list[dict[str, int]], steamids: list[str]
) -> dict[str, int]:
    hurt_df = parser.parse_event("player_hurt")
    if hurt_df.empty:
        return {steamid: 0 for steamid in steamids}

    hurt_ticks = sorted({int(tick) for tick in hurt_df["tick"].tolist()})
    damage_df = parser.parse_ticks([ROUND_TRACKER_DAMAGE_PROP], ticks=hurt_ticks).copy()
    damage_df["steamid"] = damage_df["steamid"].map(_normalize_steamid)
    damage_df = damage_df.dropna(subset=["steamid"])
    damage_df = _annotate_events_with_rounds(damage_df, rounds)
    if damage_df.empty:
        return {steamid: 0 for steamid in steamids}

    round_damage = (
        damage_df.groupby(["steamid", "round_no"])[ROUND_TRACKER_DAMAGE_PROP]
        .max()
        .groupby(level=0)
        .sum()
        .to_dict()
    )
    rounds_played = len(rounds)
    return {
        steamid: math.floor(float(round_damage.get(steamid, 0.0)) / rounds_played)
        for steamid in steamids
    }


def _group_event_counts(events_df: pd.DataFrame, column_name: str) -> dict[str, int]:
    if column_name not in events_df.columns:
        return {}
    grouped_df = events_df.copy()
    grouped_df[column_name] = grouped_df[column_name].map(_normalize_steamid)
    grouped_df = grouped_df.dropna(subset=[column_name])
    if grouped_df.empty:
        return {}
    return grouped_df.groupby(column_name).size().astype(int).to_dict()


def _build_team_results(
    player_stats: list[dict[str, Any]], team_scores: dict[str, int]
) -> list[dict[str, Any]]:
    teams: dict[str, list[dict[str, Any]]] = {}
    for player in player_stats:
        team_name = player.pop("team")
        teams.setdefault(team_name, []).append(player)

    team_results = []
    for team_name, players in teams.items():
        players.sort(key=lambda item: (-item["kills"], item["deaths"], item["name"].lower()))
        team_results.append(
            {
                "name": team_name,
                "score": int(team_scores.get(team_name, 0)),
                "players": players,
            }
        )

    team_results.sort(key=lambda item: (item["score"], item["name"].lower()))
    return team_results


def _normalize_map_name(raw_map_name: str) -> str:
    map_name = raw_map_name.strip().lower()
    if map_name.startswith("de_"):
        map_name = map_name[3:]

    for suffix in ("_wingman", "_scrimmagemap", "_se", "_night"):
        if map_name.endswith(suffix):
            map_name = map_name[: -len(suffix)]

    return " ".join(part.capitalize() for part in map_name.split("_") if part)


def _normalize_steamid(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def _pick_name_source(
    steamid: str, steam_names: dict[str, str], fallback_names: dict[str, str]
) -> str:
    if steam_names.get(steamid):
        return "steam_api"
    if fallback_names.get(steamid):
        return "demo_fallback"
    return "steamid"


def _load_local_env() -> None:
    env_path = Path(__file__).resolve().with_name(".env")
    load_dotenv(dotenv_path=env_path, override=False)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse a CS2 demo into match stats.")
    parser.add_argument("demo_path", help="Path to the .dem file")
    parser.add_argument(
        "--steam-api-key",
        dest="steam_api_key",
        default=None,
        help="Optional Steam Web API key. Falls back to STEAM_API_KEY env var.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print parsed data as JSON instead of formatted text.",
    )
    return parser


def main() -> int:
    cli = _build_cli()
    args = cli.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = parse_demo(args.demo_path, steam_api_key=args.steam_api_key)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
