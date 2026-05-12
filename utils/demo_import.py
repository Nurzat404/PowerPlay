from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from aiogram import Bot
from aiogram.types import Message

from utils.parse_demo import parse_demo


MAX_URL_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 240
MAX_PREPARE_SECONDS = 30
MAX_PARSE_SECONDS = 240
MAX_DOWNLOAD_ATTEMPTS = 3
REQUEST_CONNECT_TIMEOUT_SECONDS = 15
REQUEST_READ_TIMEOUT_SECONDS = 45
SUPPORTED_DEMO_SUFFIXES = {".dem", ".zip"}
SUPPORTED_SHARE_HOSTS = {"dropmefiles.com", "www.dropmefiles.com"}


class DemoImportError(ValueError):
    pass


def _normalize_steam_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 17:
        return text[:17]
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.strip("/").split("/")
        if path and path[0] == "profiles" and len(path) > 1 and path[1].isdigit():
            return path[1][:17]
    return text


def _steam_lookup_variants(value: Any) -> list[str]:
    normalized = _normalize_steam_id(value)
    if not normalized:
        return []

    raw = str(value).strip().rstrip("/") if value is not None else ""
    variants: list[str] = []
    for item in (normalized, raw):
        if item and item not in variants:
            variants.append(item)

    steam_id64 = normalized if normalized.isdigit() and len(normalized) >= 17 else None
    if steam_id64:
        canonical = f"https://steamcommunity.com/profiles/{steam_id64}"
        canonical_slash = canonical + "/"
        for item in (steam_id64[:17], canonical, canonical_slash):
            if item not in variants:
                variants.append(item)

    return variants


def _suffix_from_name(name: str | None) -> str:
    if not name:
        return ""
    return Path(name).suffix.lower()


def _normalize_host(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_supported_share_url(url: str) -> bool:
    parsed = urlparse(url)
    host = _normalize_host(parsed.netloc)
    if host in SUPPORTED_SHARE_HOSTS and parsed.path.strip("/"):
        return True
    return False


def is_demo_source_message(message: Message) -> bool:
    if message.document:
        return _suffix_from_name(message.document.file_name) in SUPPORTED_DEMO_SUFFIXES
    text = (message.text or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return False
    parsed = urlparse(text)
    return _suffix_from_name(parsed.path) in SUPPORTED_DEMO_SUFFIXES or _is_supported_share_url(text)


async def parse_demo_source_message(bot: Bot, message: Message, temp_root: str | None = None) -> dict[str, Any]:
    base_dir = Path(temp_root or os.path.abspath("temp/demo_imports"))
    base_dir.mkdir(parents=True, exist_ok=True)
    work_dir = tempfile.mkdtemp(
        prefix="demo_import_",
        dir=str(base_dir),
    )
    work_path = Path(work_dir)
    try:
        source_path, source_label = await asyncio.wait_for(
            _download_source(bot, message, work_path),
            timeout=MAX_DOWNLOAD_SECONDS,
        )
        demo_path = await asyncio.wait_for(
            asyncio.to_thread(_prepare_demo_file, source_path, work_path),
            timeout=MAX_PREPARE_SECONDS,
        )
        parsed = await asyncio.wait_for(
            asyncio.to_thread(parse_demo, str(demo_path), None),
            timeout=MAX_PARSE_SECONDS,
        )
        return {
            "source_label": source_label,
            "parsed_result": parsed,
        }
    except DemoImportError:
        raise
    except asyncio.TimeoutError as exc:
        raise DemoImportError(
            "Обработка демки заняла слишком много времени. "
            "Попробуйте прямой файл/ссылку побыстрее или демку меньшего размера."
        ) from exc
    except Exception as exc:
        exc_text = str(exc)
        if "UnknownFile" in exc_text:
            raise DemoImportError(
                "Не удалось распознать демку. "
                "Скорее всего ссылка отдала не сам .dem/.zip файл, а промежуточную страницу загрузки."
            ) from exc
        if "EntityNotFound" in exc_text or "Entity not found" in exc_text:
            raise DemoImportError(
                "Не удалось разобрать демку: парсер не нашёл сущность игрока "
                "(EntityNotFound). Обычно это бывает, если демка повреждена, "
                "обрезана, или версия CS2/HLTV несовместима с парсером.\n\n"
                "Что попробовать:\n"
                "• обновить парсер (demoparser2 в зависимостях)\n"
                "• использовать полную, не обрезанную демку\n"
                "• перекачать файл напрямую (.dem или .zip без оболочки)"
            ) from exc
        raise DemoImportError(exc_text) from exc
    finally:
        shutil.rmtree(work_path, ignore_errors=True)


async def _download_source(bot: Bot, message: Message, work_path: Path) -> tuple[Path, str]:
    if message.document:
        suffix = _suffix_from_name(message.document.file_name)
        if suffix not in SUPPORTED_DEMO_SUFFIXES:
            raise DemoImportError("Поддерживаются только файлы .dem или .zip.")
        target = work_path / f"source{suffix}"
        await bot.download(message.document, destination=target)
        return target, message.document.file_name or target.name

    url = (message.text or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise DemoImportError("Отправьте .dem/.zip файлом или публичную ссылку на скачивание.")

    try:
        target = await asyncio.to_thread(_download_from_url, url, work_path)
    except DemoImportError:
        raise
    return target, url


def _resolve_target_suffix(url: str, response: requests.Response) -> str:
    header_name = response.headers.get("Content-Disposition", "")
    for candidate in re.findall(r'filename\\*?=(?:UTF-8\'\')?\"?([^\";]+)\"?', header_name, flags=re.IGNORECASE):
        suffix = _suffix_from_name(candidate)
        if suffix in SUPPORTED_DEMO_SUFFIXES:
            return suffix

    suffix = _suffix_from_name(urlparse(str(response.url or url)).path)
    if suffix in SUPPORTED_DEMO_SUFFIXES:
        return suffix

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "zip" in content_type:
        return ".zip"
    return ".dem"


def _file_looks_like_html(path: Path) -> bool:
    try:
        head = path.read_bytes()[:512].lstrip()
    except OSError:
        return False
    lowered = head.lower()
    return lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html") or lowered.startswith(b"<head") or lowered.startswith(b"<body")


def _download_response_to_path(response: requests.Response, target: Path) -> Path:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_URL_DOWNLOAD_BYTES:
                raise DemoImportError("Файл слишком большой для импорта по ссылке.")
        except ValueError:
            pass

    downloaded = 0
    try:
        with target.open("wb") as file_obj:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_URL_DOWNLOAD_BYTES:
                    raise DemoImportError("Файл слишком большой для импорта по ссылке.")
                file_obj.write(chunk)
    except requests.RequestException as exc:
        raise DemoImportError("Соединение оборвалось во время скачивания демки.") from exc

    if downloaded <= 0:
        raise DemoImportError("По ссылке не удалось скачать файл демки.")
    return target


def _extract_dropmefiles_download_url(page_url: str, html: str) -> str | None:
    direct_patterns = [
        r'''data-href=["'](?P<url>https?://[^"']+)["']''',
        r'''data-href=["'](?P<url>/[^"']+)["']''',
        r'''href=["'](?P<url>https?://[^"']*dropmefile[^"']*/dl/[^"']+)["']''',
        r'''href=["'](?P<url>/dl/[^"']+)["']''',
    ]
    for pattern in direct_patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            candidate = match.group("url").replace("\\/", "/").strip()
            if candidate:
                return urljoin(page_url, candidate)

    downloadurl_match = re.search(
        r'''data-downloadurl=["'](?P<value>[^"']+)["']''',
        html,
        flags=re.IGNORECASE,
    )
    if downloadurl_match:
        raw_value = downloadurl_match.group("value").replace("\\/", "/").strip()
        if raw_value:
            if raw_value.startswith("http://") or raw_value.startswith("https://"):
                return raw_value
            if ":" in raw_value:
                candidate = raw_value.rsplit(":", 1)[-1].strip()
                if candidate.startswith("http://") or candidate.startswith("https://"):
                    return candidate

    patterns = [
        r'''href=["'](?P<url>[^"']+)["'][^>]*download''',
        r'''(?:data-url|data-download|data-href|downloadUrl|download_url)\s*[:=]\s*["'](?P<url>[^"']+)["']''',
        r'''["'](?P<url>/files/[^"']+)["']''',
        r'''["'](?P<url>/download/[^"']+)["']''',
        r'''["'](?P<url>https?://[^"']+)["']''',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            candidate = match.group("url").replace("\\/", "/").strip()
            if not candidate or candidate.startswith("#"):
                continue
            absolute = urljoin(page_url, candidate)
            parsed = urlparse(absolute)
            if _normalize_host(parsed.netloc) in SUPPORTED_SHARE_HOSTS or _suffix_from_name(parsed.path) in SUPPORTED_DEMO_SUFFIXES:
                return absolute
    return None


def _download_from_url(url: str, work_path: Path) -> Path:
    last_error: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Accept-Language": "ru,en-US;q=0.9,en;q=0.8",
            }
        )
        response: requests.Response | None = None
        try:
            response = session.get(
                url,
                stream=True,
                timeout=(REQUEST_CONNECT_TIMEOUT_SECONDS, REQUEST_READ_TIMEOUT_SECONDS),
                allow_redirects=True,
            )
            response.raise_for_status()

            parsed = urlparse(url)
            suffix = _suffix_from_name(parsed.path)
            content_type = (response.headers.get("Content-Type") or "").lower()
            is_html_page = "text/html" in content_type or (
                suffix not in SUPPORTED_DEMO_SUFFIXES and _is_supported_share_url(url)
            )
            if is_html_page and _normalize_host(parsed.netloc) in SUPPORTED_SHARE_HOSTS:
                try:
                    html = response.text
                except Exception as exc:
                    raise DemoImportError("Не удалось прочитать страницу загрузки.") from exc
                download_url = _extract_dropmefiles_download_url(str(response.url or url), html)
                if not download_url:
                    raise DemoImportError("Не удалось найти ссылку скачивания на DropMeFiles.")
                response.close()
                response = session.get(
                    download_url,
                    stream=True,
                    timeout=(REQUEST_CONNECT_TIMEOUT_SECONDS, REQUEST_READ_TIMEOUT_SECONDS),
                    allow_redirects=True,
                    headers={"Referer": str(url)},
                )
                response.raise_for_status()

            suffix = _resolve_target_suffix(url, response)
            if suffix not in SUPPORTED_DEMO_SUFFIXES:
                raise DemoImportError("По ссылке не найден .dem или .zip файл.")
            target = work_path / f"source{suffix}"
            return _download_response_to_path(response, target)
        except DemoImportError as exc:
            last_error = exc
            if attempt >= MAX_DOWNLOAD_ATTEMPTS:
                break
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= MAX_DOWNLOAD_ATTEMPTS:
                break
        finally:
            if response is not None:
                response.close()
            session.close()
        time.sleep(attempt)

    if isinstance(last_error, DemoImportError):
        raise DemoImportError(
            f"{last_error} Попробуйте повторить позже или использовать прямую ссылку/Telegram-файл."
        ) from last_error
    raise DemoImportError(
        "Не удалось стабильно скачать файл по ссылке. "
        "Файлообменник отвечает нестабильно: попробуйте повторить позже, прямую ссылку или Telegram-файл."
    ) from last_error


def _prepare_demo_file(source_path: Path, work_path: Path) -> Path:
    suffix = source_path.suffix.lower()
    if _file_looks_like_html(source_path):
        raise DemoImportError(
            "По ссылке скачалась HTML-страница вместо файла демки. "
            "Для DropMeFiles это обычно значит, что сервис отдал страницу загрузки/защиту, а не сам файл."
        )
    if suffix == ".dem":
        return source_path
    if suffix != ".zip":
        raise DemoImportError("Поддерживаются только .dem или .zip файлы.")

    with zipfile.ZipFile(source_path, "r") as archive:
        demo_members = [
            member for member in archive.infolist()
            if not member.is_dir() and Path(member.filename).suffix.lower() == ".dem"
        ]
        if not demo_members:
            raise DemoImportError("В архиве не найден .dem файл.")
        if len(demo_members) > 1:
            raise DemoImportError("В архиве должно быть ровно одно .dem.")

        member = demo_members[0]
        target = work_path / "archive_demo.dem"
        with archive.open(member, "r") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return target


def flatten_demo_players(parsed_result: dict[str, Any]) -> list[dict[str, Any]]:
    teams = parsed_result.get("teams") or []
    if len(teams) != 2:
        raise DemoImportError("Не удалось определить две команды в демке.")

    players: list[dict[str, Any]] = []
    for team_index, team in enumerate(teams):
        for player_index, player in enumerate(team.get("players") or []):
            steamid = _normalize_steam_id(player.get("steamid"))
            players.append(
                {
                    "demo_key": f"{team_index}:{player_index}:{steamid or 'unknown'}",
                    "steamid": steamid,
                    "name": player.get("name") or player.get("steam_name") or player.get("demo_name") or "Игрок",
                    "team_index": team_index,
                    "team_name": team.get("name") or f"Команда {team_index + 1}",
                    "kills": int(player.get("kills") or 0),
                    "deaths": int(player.get("deaths") or 0),
                    "assists": int(player.get("assists") or 0),
                    "adr": int(player.get("adr") or 0),
                    "hs": int(player.get("hs_percent") or 0),
                }
            )
    return players


def auto_match_demo_players(parsed_result: dict[str, Any], expected_players: list[dict[str, Any]]) -> dict[str, Any]:
    demo_players = flatten_demo_players(parsed_result)
    expected_rows = [dict(player) for player in expected_players]

    steam_to_user: dict[str, dict[str, Any]] = {}
    for player in expected_rows:
        for variant in _steam_lookup_variants(player.get("steam_id")):
            if variant not in steam_to_user:
                steam_to_user[variant] = player

    mappings: dict[str, int] = {}
    used_user_ids: set[int] = set()
    unresolved: list[dict[str, Any]] = []

    for demo_player in demo_players:
        matched_user = None
        for variant in _steam_lookup_variants(demo_player.get("steamid")):
            matched_user = steam_to_user.get(variant)
            if matched_user:
                break
        if matched_user and int(matched_user["id"]) not in used_user_ids:
            mappings[demo_player["demo_key"]] = int(matched_user["id"])
            used_user_ids.add(int(matched_user["id"]))
        else:
            unresolved.append(demo_player)

    return {
        "demo_players": demo_players,
        "expected_players": expected_rows,
        "mappings": mappings,
        "unresolved": unresolved,
    }


def finalize_demo_import_payload(
    parsed_result: dict[str, Any],
    expected_players: list[dict[str, Any]],
    mappings: dict[str, int],
    team1_id: int,
    team2_id: int,
) -> dict[str, Any]:
    demo_players = flatten_demo_players(parsed_result)
    expected_by_id = {int(player["id"]): dict(player) for player in expected_players}

    missing = [player for player in demo_players if player["demo_key"] not in mappings]
    if missing:
        raise DemoImportError("Не все игроки из демки сопоставлены с участниками матча.")

    team_resolution: dict[int, int] = {}
    for team_index in range(2):
        mapped_team_ids: list[int] = []
        for demo_player in demo_players:
            if demo_player["team_index"] != team_index:
                continue
            user_id = mappings.get(demo_player["demo_key"])
            if user_id is None:
                raise DemoImportError(f"Нет сопоставления для игрока {demo_player.get('name')} [{demo_player.get('steamid') or 'без SteamID'}].")
            user = expected_by_id.get(int(user_id))
            if not user:
                raise DemoImportError("Найдено сопоставление с несуществующим игроком матча.")
            mapped_team_ids.append(int(user["team_id"]))

        unique_team_ids = set(mapped_team_ids)
        if not unique_team_ids:
            raise DemoImportError("Не удалось определить соответствие команд из демки командам матча.")
        if len(unique_team_ids) > 1:
            raise DemoImportError("Сопоставление игроков смешало команды. Проверьте ручной маппинг.")
        team_resolution[team_index] = unique_team_ids.pop()

    if set(team_resolution.values()) != {team1_id, team2_id}:
        raise DemoImportError("Не удалось однозначно сопоставить команды из демки с матчем.")

    teams = parsed_result.get("teams") or []
    team1_score = 0
    team2_score = 0
    for team_index, team in enumerate(teams):
        resolved_team_id = team_resolution[team_index]
        if resolved_team_id == team1_id:
            team1_score = int(team.get("score") or 0)
        elif resolved_team_id == team2_id:
            team2_score = int(team.get("score") or 0)

    if team1_score == team2_score:
        raise DemoImportError("Из демки получен ничейный счет карты, импорт невозможен.")

    player_stats: list[dict[str, Any]] = []
    for demo_player in demo_players:
        user_id = mappings.get(demo_player["demo_key"])
        if user_id is None:
            raise DemoImportError(f"Нет сопоставления для игрока {demo_player.get('name')} [{demo_player.get('steamid') or 'без SteamID'}].")
        user = expected_by_id[user_id]
        player_stats.append(
            {
                "user_id": user_id,
                "user_name": user.get("first_name") or "Игрок",
                "username": user.get("username"),
                "team_id": int(user["team_id"]),
                "kills": demo_player["kills"],
                "deaths": demo_player["deaths"],
                "assists": demo_player["assists"],
                "adr": demo_player["adr"],
                "hs": demo_player["hs"],
                "demo_name": demo_player["name"],
                "demo_team_name": demo_player["team_name"],
                "steamid": demo_player["steamid"],
            }
        )

    return {
        "map_name": parsed_result.get("map") or "Unknown",
        "team1_score": team1_score,
        "team2_score": team2_score,
        "player_stats": player_stats,
        "team_resolution": team_resolution,
        "source_teams": teams,
    }
