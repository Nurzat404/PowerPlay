from aiogram.fsm.state import State, StatesGroup


class ManualMatchInput(StatesGroup):
    input_method = State()
    format = State()
    demo_input = State()
    demo_mapping = State()
    demo_confirm = State()
    map_select = State()
    custom_map_input = State()
    score_input = State()
    volleyball_sets = State()
    player_stats = State()


class BracketScheduleInput(StatesGroup):
    datetime_input = State()
    location_input = State()


class TargetedBroadcast(StatesGroup):
    text = State()


class TournamentRosterEdit(StatesGroup):
    username_input = State()
    confirm = State()


class BracketTechnicalResultInput(StatesGroup):
    reason_input = State()
    confirm = State()


class AdminRatingAdjustment(StatesGroup):
    points = State()


class AdminRatingChannelPublish(StatesGroup):
    channel = State()
