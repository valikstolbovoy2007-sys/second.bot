from aiogram.fsm.state import State, StatesGroup


class QuestionStates(StatesGroup):
    waiting_text = State()
    confirm = State()
