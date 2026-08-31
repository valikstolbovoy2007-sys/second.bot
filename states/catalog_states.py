from aiogram.fsm.state import State, StatesGroup


class CatalogStates(StatesGroup):
    searching = State()
