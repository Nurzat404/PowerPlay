import logging
import re
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from database import get_connection
from razryad_arena_utils import get_sport_display_name
from utils.rating_rules import ENTITY_PLAYER, SCOPE_SEASONAL
from utils.rating_service import (
    get_active_rating_season,
    get_rating_leaderboard,
    get_rating_season_by_id,
    normalize_format_key,
    normalize_sport_key,
)

logger = logging.getLogger(__name__)

RATING_CHANNEL_POST_STATUS_ACTIVE = "active"
RATING_CHANNEL_POST_STATUS_DISABLED = "disabled"
RATING_CHANNEL_POST_LIMIT = 10


def _entity_title_label(entity_type: str) -> str:
    return "игроков" if entity_type == ENTITY_PLAYER else "команд"


def _effective_season_id(sport_key: str, rating_scope: str, season_id: int | None) -> int | None:
    if rating_scope != SCOPE_SEASONAL:
        return None
    if season_id:
        season = get_rating_season_by_id(int(season_id))
        if season and normalize_sport_key(season["sport_key"]) == normalize_sport_key(sport_key):
            return int(season["id"])
    active = get_active_rating_season(sport_key)
    return int(active["id"]) if active else None


def render_rating_channel_post(
    *,
    entity_type: str,
    sport_key: str,
    rating_scope: str,
    format_key: str | None = None,
    season_id: int | None = None,
) -> str:
    normalized_sport = normalize_sport_key(sport_key)
    normalized_format = normalize_format_key(format_key, normalized_sport)
    effective_season_id = _effective_season_id(normalized_sport, rating_scope, season_id)
    rows, _ = get_rating_leaderboard(
        entity_type=entity_type,
        sport_key=normalized_sport,
        rating_scope=rating_scope,
        format_key=normalized_format,
        season_id=effective_season_id,
        limit=RATING_CHANNEL_POST_LIMIT,
        offset=0,
    )

    if effective_season_id:
        season = get_rating_season_by_id(effective_season_id)
        title_target = season["name"] if season else get_sport_display_name(normalized_sport)
    else:
        title_target = get_sport_display_name(normalized_sport)

    if normalized_format:
        title = f'🏆 Рейтинг {_entity_title_label(entity_type)} {normalized_format} "{title_target}"'
    else:
        title = f'🏆 Общий рейтинг {_entity_title_label(entity_type)} "{title_target}"'

    lines = [title, ""]
    if not rows:
        lines.append("Пока рейтинг пуст.")
        return "\n".join(lines)

    for index, row in enumerate(rows, start=1):
        if entity_type == ENTITY_PLAYER:
            name = (row.get("first_name") or "Игрок").strip()
            username = (row.get("username") or "").strip()
            label = f"{name} (@{username})" if username else name
        else:
            label = (row.get("team_name") or "Команда").strip()
        lines.append(f"{index}. {label} — {int(row.get('rating_value') or 0)} очков")
    return "\n".join(lines)


def _fetch_posts(
    *,
    status: str | None = None,
    sport_key: str | None = None,
    entity_type: str | None = None,
    rating_scope: str | None = None,
) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    sql = """
        SELECT *
        FROM rating_channel_posts
        WHERE 1=1
    """
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if sport_key:
        sql += " AND sport_key=?"
        params.append(normalize_sport_key(sport_key))
    if entity_type:
        sql += " AND entity_type=?"
        params.append(entity_type)
    if rating_scope:
        sql += " AND rating_scope=?"
        params.append(rating_scope)
    sql += " ORDER BY id"
    cur.execute(sql, params)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _fetch_post_by_rating_key(
    *,
    entity_type: str,
    sport_key: str,
    rating_scope: str,
    season_id: int | None,
    format_key: str | None,
) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM rating_channel_posts
        WHERE entity_type=?
          AND sport_key=?
          AND rating_scope=?
          AND COALESCE(season_id, 0) = COALESCE(?, 0)
          AND COALESCE(format_key, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (
            entity_type,
            normalize_sport_key(sport_key),
            rating_scope,
            season_id,
            normalize_format_key(format_key, sport_key),
        ),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _store_post(
    *,
    entity_type: str,
    sport_key: str,
    rating_scope: str,
    season_id: int | None,
    format_key: str | None,
    chat_id: int,
    message_id: int,
    created_by: int | None,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO rating_channel_posts (
            entity_type, sport_key, rating_scope, season_id, format_key,
            chat_id, message_id, status, created_by, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT DO UPDATE SET
            chat_id=excluded.chat_id,
            message_id=excluded.message_id,
            status=?,
            created_by=COALESCE(excluded.created_by, rating_channel_posts.created_by),
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            entity_type,
            normalize_sport_key(sport_key),
            rating_scope,
            season_id,
            normalize_format_key(format_key, sport_key),
            int(chat_id),
            int(message_id),
            RATING_CHANNEL_POST_STATUS_ACTIVE,
            created_by,
            RATING_CHANNEL_POST_STATUS_ACTIVE,
        ),
    )
    conn.commit()
    conn.close()


def _disable_post(post_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE rating_channel_posts
        SET status=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (RATING_CHANNEL_POST_STATUS_DISABLED, int(post_id)),
    )
    conn.commit()
    conn.close()


async def publish_rating_channel_post(
    bot: Bot,
    *,
    chat_id: int,
    entity_type: str,
    sport_key: str,
    rating_scope: str,
    season_id: int | None,
    format_key: str | None,
    created_by: int | None = None,
) -> dict[str, Any]:
    normalized_sport = normalize_sport_key(sport_key)
    stored_season_id = None if rating_scope == SCOPE_SEASONAL else season_id
    normalized_format = normalize_format_key(format_key, normalized_sport)
    text = render_rating_channel_post(
        entity_type=entity_type,
        sport_key=normalized_sport,
        rating_scope=rating_scope,
        season_id=stored_season_id,
        format_key=normalized_format,
    )
    existing = _fetch_post_by_rating_key(
        entity_type=entity_type,
        sport_key=normalized_sport,
        rating_scope=rating_scope,
        season_id=stored_season_id,
        format_key=normalized_format,
    )

    reused_existing = False
    if existing and int(existing["chat_id"]) == int(chat_id) and existing["status"] == RATING_CHANNEL_POST_STATUS_ACTIVE:
        try:
            await bot.edit_message_text(
                chat_id=int(existing["chat_id"]),
                message_id=int(existing["message_id"]),
                text=text,
            )
            _store_post(
                entity_type=entity_type,
                sport_key=normalized_sport,
                rating_scope=rating_scope,
                season_id=stored_season_id,
                format_key=normalized_format,
                chat_id=int(existing["chat_id"]),
                message_id=int(existing["message_id"]),
                created_by=created_by,
            )
            reused_existing = True
            return {
                "ok": True,
                "chat_id": int(existing["chat_id"]),
                "message_id": int(existing["message_id"]),
                "reused_existing": reused_existing,
                "text": text,
            }
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if "message to edit not found" in lowered or "message can't be edited" in lowered:
                _disable_post(int(existing["id"]))
            elif "message is not modified" in lowered:
                reused_existing = True
                return {
                    "ok": True,
                    "chat_id": int(existing["chat_id"]),
                    "message_id": int(existing["message_id"]),
                    "reused_existing": reused_existing,
                    "text": text,
                }
            else:
                raise

    sent = await bot.send_message(chat_id=chat_id, text=text)
    _store_post(
        entity_type=entity_type,
        sport_key=normalized_sport,
        rating_scope=rating_scope,
        season_id=stored_season_id,
        format_key=normalized_format,
        chat_id=int(sent.chat.id),
        message_id=int(sent.message_id),
        created_by=created_by,
    )
    return {
        "ok": True,
        "chat_id": int(sent.chat.id),
        "message_id": int(sent.message_id),
        "reused_existing": reused_existing,
        "text": text,
    }


async def refresh_rating_channel_posts(
    bot: Bot,
    *,
    sport_key: str | None = None,
    entity_type: str | None = None,
    rating_scope: str | None = None,
) -> None:
    for post in _fetch_posts(
        status=RATING_CHANNEL_POST_STATUS_ACTIVE,
        sport_key=sport_key,
        entity_type=entity_type,
        rating_scope=rating_scope,
    ):
        try:
            await bot.edit_message_text(
                chat_id=int(post["chat_id"]),
                message_id=int(post["message_id"]),
                text=render_rating_channel_post(
                    entity_type=post["entity_type"],
                    sport_key=post["sport_key"],
                    rating_scope=post["rating_scope"],
                    season_id=post.get("season_id"),
                    format_key=post.get("format_key"),
                ),
            )
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if "message is not modified" in lowered:
                continue
            if (
                "message to edit not found" in lowered
                or "message can't be edited" in lowered
                or "chat not found" in lowered
                or "bot is not a member" in lowered
                or "have no rights" in lowered
                or "not enough rights" in lowered
            ):
                _disable_post(int(post["id"]))
                continue
            logger.warning(
                "TelegramBadRequest при обновлении live-поста рейтинга id=%s chat_id=%s: %s",
                post["id"],
                post["chat_id"],
                exc,
            )
            continue
        except Exception as exc:
            logger.warning(
                "Не удалось обновить live-пост рейтинга id=%s chat_id=%s: %s",
                post["id"],
                post["chat_id"],
                exc,
            )


def parse_channel_target_text(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("https://t.me/") or raw.startswith("http://t.me/"):
        slug = raw.split("t.me/", 1)[1].strip().strip("/")
        slug = slug.split("/", 1)[0]
        return f"@{slug}" if slug else None
    if raw.startswith("@"):
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return raw
    return None


__all__ = [
    "parse_channel_target_text",
    "publish_rating_channel_post",
    "refresh_rating_channel_posts",
    "render_rating_channel_post",
]
