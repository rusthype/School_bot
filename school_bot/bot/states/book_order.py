from aiogram.fsm.state import State, StatesGroup


class BookOrderStates(StatesGroup):
    selecting_category = State()
    shopping_cart = State()
    checkout = State()
