from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import shutil
from datetime import datetime
from ftplib import FTP
from pathlib import Path
from sqlite3 import Row

from database import DB_PATH, get_connection
from utils.rating_service import build_site_rating_payload

logger = logging.getLogger(__name__)


STATUS_DISPLAY = {
    "registration": "Идёт набор",
    "active": "Идёт турнир",
    "finished": "Завершён",
    "scheduled": "Запланирован",
}

BOT_ROOT = Path(__file__).resolve().parents[1]
TEMP_EXPORT_DIR = BOT_ROOT / "temp" / "site_export"
LOCAL_DATA_DIR = TEMP_EXPORT_DIR / "assets" / "data"
LOCAL_BRACKETS_DIR = LOCAL_DATA_DIR / "brackets"

SYNC_REQUEST_QUEUE: asyncio.Queue[str] = asyncio.Queue(maxsize=32)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def site_sync_enabled() -> bool:
    return _env_flag("SITE_SYNC_ENABLED", False)


def sync_interval_seconds() -> int:
    raw = os.getenv("SITE_SYNC_INTERVAL_SECONDS", "3600").strip()
    try:
        value = int(raw)
    except ValueError:
        return 3600
    return max(300, value)


def get_bot_username() -> str:
    return os.getenv("RAZRYAD_BOT_USERNAME", "powerplaytestbot").strip() or "powerplaytestbot"


def get_remote_data_dir() -> str:
    value = os.getenv("SITE_SYNC_REMOTE_DIR", "").strip()
    if value:
        return value.rstrip("/")
    return "/home/c/cr780435/razryad-arena/public_html/assets/data"


def get_remote_brackets_dir() -> str:
    value = os.getenv("SITE_SYNC_REMOTE_BRACKETS_DIR", "").strip()
    if value:
        return value.rstrip("/")
    return f"{get_remote_data_dir()}/brackets"


def request_site_sync(reason: str) -> None:
    if not site_sync_enabled():
        return

    try:
        SYNC_REQUEST_QUEUE.put_nowait(reason)
    except asyncio.QueueFull:
        logger.warning("Site sync queue is full, skipping request: %s", reason)


def _dict_rows(rows: list[Row]) -> list[dict]:
    return [dict(row) for row in rows]


def _build_invite_url(token: str | None, username: str) -> str:
    clean = (token or "").strip()
    if not clean:
        return f"https://t.me/{username}"
    return f"https://t.me/{username}?start=tournament_invite_{clean}"


def _sport_display_map(conn) -> dict[str, str]:
    cur = conn.cursor()
    rows = cur.execute("SELECT name, display_name FROM sports ORDER BY display_name").fetchall()
    mapping: dict[str, str] = {}
    for row in rows:
        mapping[row["name"]] = row["display_name"] or row["name"]
    return mapping


def _copy_bracket_image(tournament_id: int, bracket_generated: int | None) -> str | None:
    source = BOT_ROOT / "temp" / "brackets" / f"bracket_{tournament_id}.png"

    if bracket_generated and not source.exists():
        try:
            from utils.bracket_visualizer import generate_bracket_png

            source.parent.mkdir(parents=True, exist_ok=True)
            result = generate_bracket_png(tournament_id, str(source))
            if not result:
                logger.warning("Bracket PNG was not generated for tournament %s", tournament_id)
        except Exception:
            logger.exception("Failed to generate bracket PNG for tournament %s", tournament_id)

    if not source.exists():
        return None

    LOCAL_BRACKETS_DIR.mkdir(parents=True, exist_ok=True)
    target = LOCAL_BRACKETS_DIR / source.name
    shutil.copy2(source, target)
    return f"../assets/data/brackets/{source.name}"


def export_site_data() -> dict[str, list[Path] | Path]:
    if LOCAL_DATA_DIR.exists():
        shutil.rmtree(LOCAL_DATA_DIR)
    LOCAL_BRACKETS_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    conn.row_factory = Row
    cur = conn.cursor()

    sport_map = _sport_display_map(conn)
    sports = _dict_rows(cur.execute("SELECT name, display_name FROM sports ORDER BY display_name").fetchall())

    tournament_rows = cur.execute(
        """
        SELECT
          t.*,
          COALESCE(SUM(CASE WHEN a.status = 'approved' THEN 1 ELSE 0 END), 0) AS approved_teams
        FROM tournaments t
        LEFT JOIN tournament_applications a ON a.tournament_id = t.id
        GROUP BY t.id
        ORDER BY
          CASE t.status
            WHEN 'registration' THEN 1
            WHEN 'active' THEN 2
            WHEN 'finished' THEN 3
            ELSE 4
          END,
          t.created_at DESC
        """
    ).fetchall()

    bot_username = get_bot_username()
    tournaments: list[dict] = []
    for row in tournament_rows:
        item = dict(row)
        item["sport_display"] = sport_map.get(item["sport"], item["sport"])
        item["status_display"] = STATUS_DISPLAY.get(item["status"], item["status"])
        item["invite_url"] = _build_invite_url(item.get("invite_token"), bot_username)
        item["age_label"] = f"{item.get('min_age', 0)}–{item.get('max_age', 100)} лет"
        item["bracket_image"] = _copy_bracket_image(item["id"], item.get("bracket_generated"))
        item["teams"] = _dict_rows(
            cur.execute(
                """
                SELECT tm.id, tm.name, tm.city
                FROM teams tm
                JOIN tournament_applications a ON a.team_id = tm.id
                WHERE a.tournament_id = ? AND a.status = 'approved'
                ORDER BY tm.name
                """,
                (item["id"],),
            ).fetchall()
        )
        tournaments.append(item)

    rating_payload = build_site_rating_payload()

    bracket_matches = _dict_rows(
        cur.execute(
            """
            SELECT
              b.id,
              b.tournament_id,
              b.round_number,
              b.match_number,
              b.team1_id,
              b.team2_id,
              b.score1,
              b.score2,
              b.status,
              b.scheduled_at_utc,
              b.location,
              t.name AS tournament_name,
              t.sport,
              t.city,
              tm1.name AS team1_name,
              tm2.name AS team2_name
            FROM tournament_brackets b
            JOIN tournaments t ON t.id = b.tournament_id
            LEFT JOIN teams tm1 ON tm1.id = b.team1_id
            LEFT JOIN teams tm2 ON tm2.id = b.team2_id
            ORDER BY b.tournament_id, b.round_number, b.match_number
            """
        ).fetchall()
    )

    generated_at = datetime.now().isoformat(timespec="seconds")

    tournaments_path = LOCAL_DATA_DIR / "tournaments.json"
    tournaments_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "sports": sports, "items": tournaments},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ratings_path = LOCAL_DATA_DIR / "ratings.json"
    ratings_path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "groups": rating_payload.get("groups", []),
                "seasons": rating_payload.get("seasons", {}),
                "leaderboards": rating_payload.get("leaderboards", {}),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    matches_path = LOCAL_DATA_DIR / "matches.json"
    matches_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "items": bracket_matches},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    conn.close()

    bracket_files = sorted(LOCAL_BRACKETS_DIR.glob("*.png"))
    return {
        "data_dir": LOCAL_DATA_DIR,
        "json_files": [tournaments_path, ratings_path, matches_path],
        "bracket_files": bracket_files,
    }


def _connect_ftp() -> FTP:
    host = os.getenv("SITE_SYNC_HOST", "").strip()
    username = os.getenv("SITE_SYNC_USERNAME", "").strip()
    password = os.getenv("SITE_SYNC_PASSWORD", "").strip()
    port_raw = os.getenv("SITE_SYNC_PORT", "21").strip()

    if not host or not username or not password:
        raise RuntimeError("SITE_SYNC FTP credentials are not fully configured")

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError("SITE_SYNC_PORT must be an integer") from exc

    ftp = FTP()
    ftp.encoding = "utf-8"
    ftp.connect(host=host, port=port, timeout=30)
    ftp.login(user=username, passwd=password)
    ftp.set_pasv(True)
    return ftp


def _ensure_remote_dir(ftp: FTP, remote_dir: str) -> None:
    normalized = remote_dir.strip()
    if not normalized:
        return

    ftp.cwd("/")
    parts = [part for part in normalized.split("/") if part]
    current = ""
    for part in parts:
        current = posixpath.join(current, part) if current else f"/{part}"
        try:
            ftp.cwd(current)
        except Exception:
            try:
                ftp.mkd(current)
            except Exception:
                # The directory may have been created concurrently or already exists.
                pass
            ftp.cwd(current)


def _upload_file(ftp: FTP, local_path: Path, remote_dir: str) -> None:
    _ensure_remote_dir(ftp, remote_dir)
    ftp.cwd(remote_dir)
    with local_path.open("rb") as file_obj:
        ftp.storbinary(f"STOR {local_path.name}", file_obj)


def upload_exported_site_data(exported: dict[str, list[Path] | Path]) -> None:
    remote_data_dir = get_remote_data_dir()
    remote_brackets_dir = get_remote_brackets_dir()

    ftp = _connect_ftp()
    try:
        for json_file in exported["json_files"]:
            _upload_file(ftp, json_file, remote_data_dir)
        for bracket_file in exported["bracket_files"]:
            _upload_file(ftp, bracket_file, remote_brackets_dir)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def sync_site_data(reason: str = "manual") -> None:
    logger.info("Starting site sync: %s", reason)
    exported = export_site_data()
    upload_exported_site_data(exported)
    logger.info("Site sync finished: %s", reason)


async def site_sync_worker() -> None:
    if not site_sync_enabled():
        logger.info("Site sync worker is disabled")
        return

    interval = sync_interval_seconds()
    logger.info("Site sync worker started, interval=%s", interval)
    request_site_sync("startup")

    while True:
        reason = "hourly"
        try:
            reason = await asyncio.wait_for(SYNC_REQUEST_QUEUE.get(), timeout=interval)
            batched_reasons = [reason]
            while True:
                try:
                    batched_reasons.append(SYNC_REQUEST_QUEUE.get_nowait())
                except asyncio.QueueEmpty:
                    break
            reason = ", ".join(batched_reasons[:5])
        except asyncio.TimeoutError:
            reason = "hourly"

        try:
            await asyncio.to_thread(sync_site_data, reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Site sync failed: %s", reason)
