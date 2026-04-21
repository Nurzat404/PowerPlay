from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards import ratings_entity_keyboard, ratings_format_keyboard, ratings_scope_keyboard, ratings_sport_picker_keyboard
from razryad_arena_utils import get_all_sports, get_sport_display_name
from utils.rating_rules import ENTITY_PLAYER, ENTITY_TEAM, SCOPE_OVERALL, SCOPE_SEASONAL, get_format_options_for_sport, sport_supports_formats
from utils.rating_service import (
    get_active_rating_season,
    get_adjacent_season_ids,
    get_rating_leaderboard,
    get_rating_season_by_id,
)

router = Router()
PAGE_SIZE = 10


async def _safe_edit_text(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


def _entity_from_token(token: str) -> str:
    return ENTITY_PLAYER if token == "p" else ENTITY_TEAM


def _entity_label(entity_type: str) -> str:
    return "Игроки" if entity_type == ENTITY_PLAYER else "Команды"


def _scope_from_token(token: str) -> str:
    return SCOPE_OVERALL if token == "o" else SCOPE_SEASONAL


def _scope_label(scope: str) -> str:
    return "Общий" if scope == SCOPE_OVERALL else "Сезонный"


def _format_from_token(token: str) -> str | None:
    return None if token == "g" else token


def _format_label(format_key: str | None) -> str:
    return "Общий" if not format_key else format_key


def _safe_season_id(sport_key: str, scope: str, season_id: int) -> int:
    if scope != SCOPE_SEASONAL:
        return 0
    if season_id:
        season = get_rating_season_by_id(season_id)
        if season and season["sport_key"] == sport_key:
            return int(season["id"])
    active = get_active_rating_season(sport_key)
    return int(active["id"])


def _rating_text(entity_type: str, sport_key: str, scope: str, format_key: str | None, season_id: int, rows: list[dict], offset: int, has_next: bool) -> str:
    sport_display = get_sport_display_name(sport_key)
    text = [
        f"📊 Рейтинг: {_entity_label(entity_type)}",
        f"🏅 Вид спорта: {sport_display}",
        f"🧭 Тип: {_scope_label(scope)}",
    ]
    if sport_supports_formats(sport_key):
        text.append(f"🗂 Формат: {_format_label(format_key)}")
    if scope == SCOPE_SEASONAL:
        season = get_rating_season_by_id(season_id)
        if season:
            text.append(f"🗓 Сезон: {season['name']}")

    text.append("")
    if not rows:
        text.append("Пока рейтинг пуст.")
        return "\n".join(text)

    for index, row in enumerate(rows, start=offset + 1):
        if entity_type == ENTITY_PLAYER:
            username = row.get("username") or "без_username"
            name = row.get("first_name") or "Игрок"
            line = f"{index}. {name} (@{username}) — {row['rating_value']} очков"
        else:
            line = f"{index}. {row['team_name']} — {row['rating_value']} очков"
        text.append(line)

    text.append("")
    shown_to = offset + len(rows)
    suffix = "+" if has_next else ""
    text.append(f"Показано {offset + 1}-{shown_to}{suffix}")
    return "\n".join(text)


def _rating_view_keyboard(entity_type: str, sport_key: str, scope: str, season_id: int, format_key: str | None, offset: int, has_next: bool) -> InlineKeyboardMarkup:
    entity_token = "p" if entity_type == ENTITY_PLAYER else "t"
    scope_token = "o" if scope == SCOPE_OVERALL else "s"
    format_token = "g" if not format_key else format_key
    builder = InlineKeyboardBuilder()

    nav = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"ratings_view_{entity_token}_{sport_key}_{scope_token}_{season_id}_{format_token}_{max(0, offset - PAGE_SIZE)}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"ratings_view_{entity_token}_{sport_key}_{scope_token}_{season_id}_{format_token}_{offset + PAGE_SIZE}",
            )
        )
    if nav:
        builder.row(*nav)

    if scope == SCOPE_SEASONAL:
        prev_season_id, next_season_id = get_adjacent_season_ids(sport_key, season_id)
        season_nav = []
        if prev_season_id:
            season_nav.append(
                InlineKeyboardButton(
                    text="⬅️ Предыдущий сезон",
                    callback_data=f"ratings_view_{entity_token}_{sport_key}_{scope_token}_{prev_season_id}_{format_token}_0",
                )
            )
        if next_season_id:
            season_nav.append(
                InlineKeyboardButton(
                    text="Следующий сезон ➡️",
                    callback_data=f"ratings_view_{entity_token}_{sport_key}_{scope_token}_{next_season_id}_{format_token}_0",
                )
            )
        if season_nav:
            builder.row(*season_nav)

    if sport_supports_formats(sport_key):
        if format_key:
            builder.button(
                text="🔙 К общему рейтингу",
                callback_data=f"ratings_view_{entity_token}_{sport_key}_{scope_token}_{season_id}_g_0",
            )
        else:
            builder.button(
                text="🗂 Форматы",
                callback_data=f"ratings_formats_{entity_token}_{sport_key}_{scope_token}_{season_id}",
            )
            builder.button(
                text="🔙 К выбору типа",
                callback_data=f"ratings_sport_{entity_token}_{sport_key}",
            )
    else:
        builder.button(
            text="🔙 К выбору типа",
            callback_data=f"ratings_sport_{entity_token}_{sport_key}",
        )
    builder.adjust(1)
    return builder.as_markup()


async def _render_scope_or_view(callback: CallbackQuery, entity_token: str, sport_key: str, scope_token: str):
    await _render_view(callback, entity_token, sport_key, scope_token, 0, "g", 0)


async def _render_view(callback: CallbackQuery, entity_token: str, sport_key: str, scope_token: str, season_id: int, format_token: str, offset: int):
    entity_type = _entity_from_token(entity_token)
    scope = _scope_from_token(scope_token)
    safe_season_id = _safe_season_id(sport_key, scope, season_id)
    format_key = _format_from_token(format_token)
    rows, has_next = get_rating_leaderboard(
        entity_type=entity_type,
        sport_key=sport_key,
        rating_scope=scope,
        format_key=format_key,
        season_id=safe_season_id or None,
        limit=PAGE_SIZE,
        offset=offset,
    )
    text = _rating_text(entity_type, sport_key, scope, format_key, safe_season_id, rows, offset, has_next)
    await _safe_edit_text(
        callback,
        text,
        reply_markup=_rating_view_keyboard(entity_type, sport_key, scope, safe_season_id, format_key, offset, has_next),
    )


async def _render_format_picker(callback: CallbackQuery, entity_token: str, sport_key: str, scope_token: str, season_id: int):
    await _safe_edit_text(
        callback,
        f"Выберите формат рейтинга по {get_sport_display_name(sport_key)}:",
        reply_markup=ratings_format_keyboard(entity_token, sport_key, scope_token, season_id, get_format_options_for_sport(sport_key)),
    )


@router.callback_query(F.data == "ratings")
async def ratings_menu(callback: CallbackQuery):
    await _safe_edit_text(
        callback,
        "Выберите, чей рейтинг хотите посмотреть:",
        reply_markup=ratings_entity_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.in_(["ratings_entity_p", "ratings_entity_t"]))
async def ratings_entity_pick(callback: CallbackQuery):
    entity_token = callback.data.rsplit("_", 1)[1]
    sports = get_all_sports()
    await _safe_edit_text(
        callback,
        "Выберите вид спорта:",
        reply_markup=ratings_sport_picker_keyboard(entity_token, sports),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ratings_sport_"))
async def ratings_sport_pick(callback: CallbackQuery):
    parts = callback.data.split("_", 3)
    entity_token = parts[2]
    sport_key = parts[3]
    await _safe_edit_text(
        callback,
        f"Выберите тип рейтинга по {get_sport_display_name(sport_key)}:",
        reply_markup=ratings_scope_keyboard(entity_token, sport_key),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ratings_scope_"))
async def ratings_scope_pick(callback: CallbackQuery):
    parts = callback.data.split("_", 4)
    entity_token = parts[2]
    sport_key = parts[3]
    scope_token = parts[4]
    await _render_scope_or_view(callback, entity_token, sport_key, scope_token)
    await callback.answer()


@router.callback_query(F.data.startswith("ratings_formats_"))
async def ratings_formats(callback: CallbackQuery):
    parts = callback.data.split("_", 4)
    entity_token = parts[2]
    sport_key = parts[3]
    rest = parts[4]
    scope_token, season_token = rest.split("_", 1)
    season_id = int(season_token)
    await _render_format_picker(callback, entity_token, sport_key, scope_token, season_id)
    await callback.answer()


@router.callback_query(F.data.startswith("ratings_view_"))
async def ratings_view(callback: CallbackQuery):
    parts = callback.data.split("_", 6)
    entity_token = parts[2]
    sport_key = parts[3]
    scope_token = parts[4]
    season_id = int(parts[5])
    format_token, offset = parts[6].rsplit("_", 1)
    await _render_view(callback, entity_token, sport_key, scope_token, season_id, format_token, int(offset))
    await callback.answer()


@router.callback_query(F.data.startswith("rating_sport_"))
async def legacy_rating_sport_redirect(callback: CallbackQuery):
    sport_key = callback.data.replace("rating_sport_", "")
    await _render_view(callback, "t", sport_key, "o", 0, "g", 0)
    await callback.answer()
