from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
import json
from razryad_arena_utils import get_all_sports, get_user, get_or_create_user, update_user
from keyboards import main_menu_keyboard, sports_choice_keyboard

router = Router()


class Registration(StatesGroup):
    name = State()
    email = State()
    city = State()
    age = State()
    sports = State()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    username = message.from_user.username or ""

    user = get_or_create_user(telegram_id, first_name, last_name, username)

    if not user['email']:
        await state.set_state(Registration.name)
        await message.answer("Добро пожаловать в Разряд-Арена! Давай зарегистрируемся.\nКак тебя зовут?")
    else:
        await message.answer("Главное меню:", reply_markup=main_menu_keyboard())


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
    await callback.message.answer("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()

