from aiogram.fsm.state import State, StatesGroup


class AddShopStates(StatesGroup):
    name = State()
    address = State()
    working_hours = State()
    chain = State()
    cycle = State()
    anchor = State()
    price_start = State()
    price_step = State()
    description = State()
    photos = State()
    assign_admins = State()
    confirm = State()


class BroadcastStates(StatesGroup):
    text = State()
    media = State()
    audience = State()
    audience_list_upload = State()
    schedule = State()
    confirm = State()


class ChainStates(StatesGroup):
    name = State()
    default_cycle = State()
    parser_key = State()
    sort_order = State()
    edit_value = State()


class AdminDmStates(StatesGroup):
    text = State()


class FeedbackStates(StatesGroup):
    pick_shop = State()
    text = State()


class ParserScheduleStates(StatesGroup):
    cron = State()


class ShopSearchStates(StatesGroup):
    query = State()


class ShopPhotoStates(StatesGroup):
    upload = State()
