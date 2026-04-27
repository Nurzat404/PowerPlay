from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from keyboards import referral_link_card_keyboard, referral_menu_keyboard, referral_sport_picker_keyboard
from razryad_arena_utils import get_all_sports, get_sport_display_name, get_user
from utils.referral_service import (
    REFERRAL_LINK_STATUS_ACTIVE,
    build_referral_url,
    create_referral_link,
    disable_referral_link,
    get_referral_link,
    get_referral_link_stats,
    list_referral_link_referees,
    list_referral_links,
)

router = Router()


class ReferralCreate(StatesGroup):
    title = State()


def _referral_menu_text() -> str:
    return (
        "🎁 Реферальная система\n\n"
        "Создавайте ссылки по видам спорта и приглашайте новых пользователей.\n\n"
        "Начисление по ссылке:\n"
        "• +2 владельцу после первого одобренного турнира реферала\n"
        "• +3 владельцу после первого обычного матча реферала\n"
        "• +2 самому рефералу за его первый обычный матч\n\n"
        "Бонусы идут только в общий и текущий сезонный рейтинг выбранного спорта."
    )


def _format_referees_block(referees: list[dict]) -> str:
    if not referees:
        return "Пока никого не привязали."
    lines = []
    for idx, row in enumerate(referees[:15], 1):
        username = f"@{row['username']}" if row.get("username") else "без username"
        lines.append(f"{idx}. {row.get('first_name') or 'Пользователь'} ({username}) — {row['status_label']}")
    if len(referees) > 15:
        lines.append(f"… и еще {len(referees) - 15}")
    return "\n".join(lines)


async def _render_referrals_menu(target: CallbackQuery | Message) -> None:
    user = get_user(target.from_user.id)
    if not user:
        text = "Сначала завершите регистрацию."
        if isinstance(target, CallbackQuery):
            await target.answer(text, show_alert=True)
        else:
            await target.answer(text)
        return
    links = list_referral_links(int(user["id"]))
    text = _referral_menu_text()
    if links:
        text += f"\n\nВаших ссылок: {len(links)}"
    else:
        text += "\n\nУ вас пока нет ни одной ссылки."
    markup = referral_menu_keyboard(links)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


async def _render_referral_link_card(callback: CallbackQuery, link_id: int) -> None:
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала завершите регистрацию.", show_alert=True)
        return
    link = get_referral_link(link_id, int(user["id"]))
    if not link:
        await callback.answer("Ссылка не найдена.", show_alert=True)
        return

    me = await callback.bot.get_me()
    stats = get_referral_link_stats(link_id)
    referees = list_referral_link_referees(link_id)
    status = "Активна" if link["status"] == REFERRAL_LINK_STATUS_ACTIVE else "Отключена"
    text = (
        "🔗 Реферальная ссылка\n\n"
        f"Название: {link['title']}\n"
        f"Спорт: {get_sport_display_name(link['sport_key'])}\n"
        f"Статус: {status}\n"
        f"Создана: {link['created_at']}\n\n"
        f"Ссылка:\n{build_referral_url(me.username or '', link['token'])}\n\n"
        "Статистика:\n"
        f"• Привязано пользователей: {stats['attributed_users']}\n"
        f"• Выдано бонусов +2 владельцу: {stats['registration_bonus_count']}\n"
        f"• Выдано бонусов +3 владельцу: {stats['participation_bonus_count']}\n"
        f"• Всего очков принесла: {stats['owner_points_total']}\n\n"
        "Рефералы:\n"
        f"{_format_referees_block(referees)}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=referral_link_card_keyboard(link_id, link["status"] == REFERRAL_LINK_STATUS_ACTIVE),
    )
    await callback.answer()


@router.callback_query(F.data == "referrals")
async def referrals_menu(callback: CallbackQuery):
    await _render_referrals_menu(callback)


@router.callback_query(F.data == "referral_create")
async def referral_create_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Выберите вид спорта для реферальной ссылки:",
        reply_markup=referral_sport_picker_keyboard(get_all_sports()),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("referral_pick_sport_"))
async def referral_pick_sport(callback: CallbackQuery, state: FSMContext):
    sport_key = callback.data.replace("referral_pick_sport_", "")
    await state.set_state(ReferralCreate.title)
    await state.update_data(referral_sport_key=sport_key)
    await callback.message.edit_text(
        f"Введите название ссылки для {get_sport_display_name(sport_key)}.\n\n"
        "Например: Telegram пост, Discord, Личный инвайт",
    )
    await callback.answer()


@router.message(ReferralCreate.title)
async def referral_create_title(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Сначала завершите регистрацию.")
        return

    title = (message.text or "").strip()
    if not title:
        await message.answer("Название ссылки не может быть пустым.")
        return

    data = await state.get_data()
    sport_key = data.get("referral_sport_key")
    if not sport_key:
        await state.clear()
        await message.answer("Сессия создания ссылки устарела. Откройте раздел заново.")
        return

    link = create_referral_link(int(user["id"]), sport_key, title)
    await state.clear()

    me = await message.bot.get_me()
    text = (
        "✅ Ссылка создана\n\n"
        f"Название: {link['title']}\n"
        f"Спорт: {get_sport_display_name(link['sport_key'])}\n"
        f"Ссылка:\n{build_referral_url(me.username or '', link['token'])}"
    )
    await message.answer(
        text,
        reply_markup=referral_link_card_keyboard(int(link["id"]), True),
    )


@router.callback_query(F.data.startswith("referral_link_"))
async def referral_link_card(callback: CallbackQuery):
    link_id = int(callback.data.replace("referral_link_", ""))
    await _render_referral_link_card(callback, link_id)


@router.callback_query(F.data.startswith("referral_disable_"))
async def referral_disable(callback: CallbackQuery):
    link_id = int(callback.data.replace("referral_disable_", ""))
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала завершите регистрацию.", show_alert=True)
        return
    changed = disable_referral_link(link_id, int(user["id"]))
    if not changed:
        await callback.answer("Ссылка уже отключена или не найдена.", show_alert=True)
        return
    await _render_referral_link_card(callback, link_id)

