from aiogram.fsm.state import State, StatesGroup


class FeedbackStates(StatesGroup):
    pick_shop = State()
    waiting_text = State()
