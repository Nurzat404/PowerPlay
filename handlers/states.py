from aiogram.fsm.state import State, StatesGroup


class ManualMatchInput(StatesGroup):
    format = State()
    map_select = State()
    score_input = State()
    volleyball_sets = State()
    player_stats = State()


class BracketScheduleInput(StatesGroup):
    datetime_input = State()
    location_input = State()
