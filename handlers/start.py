from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import json
import os
from razryad_arena_utils import (
    get_all_sports, get_user, get_or_create_user, update_user,
    get_team_by_invite_token, get_tournament_by_invite_token,
    get_team_members_count, get_team_max_members, get_team_settings,
    get_user_by_id, get_sport_display_name, build_tournament_date_lines
)
from keyboards import main_menu_keyboard, sports_choice_keyboard, subscription_required_keyboard
from utils.referral_service import attach_user_to_referral

router = Router()

REQUIRED_CHANNEL_USERNAME = os.getenv("REQUIRED_CHANNEL_USERNAME", "razryadarena").strip().lstrip("@")


def _display_optional_text(value: str | None, fallback: str = "не указан") -> str:
    return (value or "").strip() or fallback


def _row_value(row, key: str, default=None):
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


class Registration(StatesGroup):
    name = State()
    email = State()
    city = State()
    age = State()
    sports = State()


async def _handle_team_invite(message: Message, token: str) -> bool:
    team = get_team_by_invite_token(token)
    if not team:
        await message.answer("Ссылка приглашения в команду недействительна или устарела.")
        return True

    captain = get_user_by_id(team['captain_id'])
    captain_name = "Неизвестно"
    if captain:
        captain_name = f"{captain['first_name']} (@{captain['username']})"

    settings = get_team_settings(team['id'])
    members_count = get_team_members_count(team['id'])
    max_members = get_team_max_members(team['id'])

    invite_mode = (team['invite_join_mode'] or 'request').strip().lower() if 'invite_join_mode' in team.keys() else 'request'
    mode_text = "⚡ Сразу вступать" if invite_mode == 'direct' else "📝 По заявке"

    text = (
        f"Приглашение в команду\n\n"
        f"Команда: {team['name']}\n"
        f"Вид спорта: {get_sport_display_name(team['sport'])}\n"
        f"Город: {_display_optional_text(team['city'])}\n"
        f"Капитан: {captain_name}\n"
        f"Участников: {members_count}/{max_members}\n"
        f"Набор: {'Открыт' if settings and settings['is_open'] else 'Закрыт'}\n"
    )

    action_text = "⚡ Вступить в команду" if invite_mode == 'direct' else "📝 Подать заявку"
    action_callback = f"team_join_direct_{team['id']}" if invite_mode == 'direct' else f"apply_team_{team['id']}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=action_text, callback_data=action_callback)],
        [InlineKeyboardButton(text="👤 К командам", callback_data="teams_menu")],
    ])
    await message.answer(text, reply_markup=kb)
    return True


async def _handle_tournament_invite(message: Message, token: str) -> bool:
    tournament = get_tournament_by_invite_token(token)
    if not tournament:
        await message.answer("Ссылка приглашения в турнир недействительна или устарела.")
        return True

    status_map = {
        'registration': 'Регистрация',
        'active': 'Идёт',
        'finished': 'Завершён',
    }
    status_display = status_map.get(tournament['status'], tournament['status'])

    text = (
        f"Приглашение в турнир\n\n"
        f"Турнир: {tournament['name']}\n"
        f"Вид спорта: {get_sport_display_name(tournament['sport'])}\n"
        f"Город: {tournament['city']}\n"
        f"{chr(10).join(build_tournament_date_lines(tournament))}\n"
        f"Статус: {status_display}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Открыть турнир", callback_data=f"tournament_{tournament['id']}")],
        [InlineKeyboardButton(text="🏆 К турнирам", callback_data="tournaments")],
    ])
    await message.answer(text, reply_markup=kb)
    return True


async def _handle_start_payload(message: Message, payload: str) -> bool:
    if payload.startswith("team_invite_"):
        token = payload[len("team_invite_"):].strip()
        if token:
            return await _handle_team_invite(message, token)
        await message.answer("Некорректная ссылка приглашения в команду.")
        return True

    if payload.startswith("tournament_invite_"):
        token = payload[len("tournament_invite_"):].strip()
        if token:
            return await _handle_tournament_invite(message, token)
        await message.answer("Некорректная ссылка приглашения в турнир.")
        return True

    if payload.startswith("ref_"):
        token = payload[len("ref_"):].strip()
        user = get_user(message.from_user.id)
        if not token:
            await message.answer("Некорректная реферальная ссылка.")
            return True
        if not user:
            await message.answer("Не удалось обработать реферальную ссылку.")
            return True
        if _row_value(user, "email"):
            await message.answer("Реферальная ссылка работает только для новых пользователей.")
            return True
        result = attach_user_to_referral(
            referred_user_id=int(user["id"]),
            referral_token=token,
            allow_for_unfinished_profile=True,
        )
        if result.get("ok"):
            link = result["link"]
            await message.answer(
                f"Вы пришли по приглашению в систему рефералов по {get_sport_display_name(link['sport_key'])}.\n"
                "После регистрации и участия в турнирах по этому спорту будут начисляться бонусы."
            )
            return True
        reason = result.get("reason")
        if reason == "self_referral":
            await message.answer("Нельзя использовать собственную реферальную ссылку.")
            return True
        if reason in {"already_attributed", "existing_registered_user"}:
            await message.answer("Реферальная привязка уже зафиксирована и не будет изменена.")
            return True
        if reason == "disabled":
            await message.answer("Эта реферальная ссылка отключена.")
            return True
        await message.answer("Реферальная ссылка недействительна.")
        return True

    return False


async def _consume_pending_start_payload(target_message: Message, telegram_id: int) -> bool:
    user = get_user(telegram_id)
    if not user:
        return False
    payload = str(_row_value(user, "pending_start_payload", "") or "").strip()
    if not payload:
        return False
    handled = await _handle_start_payload(target_message, payload)
    update_user(telegram_id, pending_start_payload=None)
    return handled


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    username = message.from_user.username or ""

    payload = (command.args or "").strip() if command else ""
    existing_user = get_user(telegram_id)
    user = get_or_create_user(telegram_id, first_name, last_name, username)

    if payload and payload.startswith("ref_"):
        await _handle_start_payload(message, payload)
        update_user(telegram_id, pending_start_payload=None)

    if user and user['email'] and payload and not payload.startswith("ref_"):
        handled = await _handle_start_payload(message, payload)
        if handled:
            update_user(telegram_id, pending_start_payload=None)
            return

    if not user['email']:
        await state.set_state(Registration.name)
        if payload and not payload.startswith("ref_"):
            await message.answer(
                "Для перехода по ссылке сначала завершите регистрацию. "
                "После регистрации снова откройте ссылку."
            )
        elif payload.startswith("ref_") and existing_user and _row_value(existing_user, "email"):
            await message.answer("Реферальная ссылка работает только для новых пользователей.")
        await message.answer("Добро пожаловать в Разряд-Арена! Давай зарегистрируемся.\nКак тебя зовут?")
    else:
        await message.answer("Главное меню:", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery, state: FSMContext):
    channel = f"@{REQUIRED_CHANNEL_USERNAME}"
    try:
        member = await callback.bot.get_chat_member(channel, callback.from_user.id)
    except Exception:
        member = None

    if member and getattr(member, 'status', None) in {'member', 'administrator', 'creator'}:
        await callback.answer("Подписка подтверждена")

        telegram_id = callback.from_user.id
        first_name = callback.from_user.first_name or ""
        last_name = callback.from_user.last_name or ""
        username = callback.from_user.username or ""
        user = get_or_create_user(telegram_id, first_name, last_name, username)

        if not user or not user['email']:
            await state.set_state(Registration.name)
            await callback.message.answer("Добро пожаловать в Разряд-Арена! Давай зарегистрируемся.\nКак тебя зовут?")
        else:
            await _consume_pending_start_payload(callback.message, telegram_id)
            await callback.message.answer("Главное меню:", reply_markup=main_menu_keyboard())
        return

    await callback.answer("Подписка пока не найдена", show_alert=True)
    await callback.message.answer(
        f"Для использования бота подпишитесь на канал @{REQUIRED_CHANNEL_USERNAME} и нажмите проверку.",
        reply_markup=subscription_required_keyboard(REQUIRED_CHANNEL_USERNAME)
    )


@router.message(Registration.name)
async def reg_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(Registration.email)
    await message.answer("Введи свой email:")


@router.message(Registration.email)
async def reg_email(message: Message, state: FSMContext):
    if "@" not in message.text or "." not in message.text:
        await message.answer("Некорректный email. Попробуй ещё раз:")
        return
    await state.update_data(email=message.text)
    await state.set_state(Registration.city)
    await message.answer("Из какого ты города?")


@router.message(Registration.city)
async def reg_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text)
    await state.set_state(Registration.age)
    await message.answer("Сколько вам лет? (введите число от 0 до 100)")


@router.message(Registration.age)
async def reg_age(message: Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        if age < 0 or age > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Некорректный возраст. Введите число от 0 до 100.")
        return
    await state.update_data(age=age)
    await state.set_state(Registration.sports)
    sports = get_all_sports()
    await message.answer(
        "Каким видом спорта занимаешься? (можно выбрать несколько)",
        reply_markup=sports_choice_keyboard(sports)
    )


@router.callback_query(Registration.sports, F.data.startswith("sport_"), F.data != "sport_done")
async def reg_sports_choice(callback: CallbackQuery, state: FSMContext):
    sport = callback.data.replace("sport_", "")
    data = await state.get_data()
    selected = data.get("selected_sports", [])
    if sport in selected:
        selected.remove(sport)
    else:
        selected.append(sport)
    await state.update_data(selected_sports=selected)
    sports = get_all_sports()
    sports_map = {item["name"]: item["display_name"] for item in sports}
    selected_display = [sports_map.get(item, item) for item in selected]
    await callback.answer(f"Выбрано: {', '.join(selected_display) if selected_display else 'ничего'}")
    await callback.message.edit_reply_markup(reply_markup=sports_choice_keyboard(sports, selected))


@router.callback_query(Registration.sports, F.data == "sport_done")
async def reg_sports_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_sports", [])
    if not selected:
        await callback.answer("Выбери хотя бы один вид спорта!", show_alert=True)
        return

    update_user(
        callback.from_user.id,
        first_name=data.get('name'),
        email=data['email'],
        city=data['city'],
        age=data['age'],
        favorite_sports=json.dumps(selected, ensure_ascii=False)
    )

    await state.clear()
    await callback.message.edit_text("Регистрация завершена! Добро пожаловать в Разряд-Арена.")
    await _consume_pending_start_payload(callback.message, callback.from_user.id)
    await callback.message.answer("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()
