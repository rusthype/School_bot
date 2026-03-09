from aiogram.fsm.state import State, StatesGroup


class BookAddStates(StatesGroup):
    select_category = State()
    select_predefined_book = State()
    cover = State()
    confirm = State()


class BookEditStates(StatesGroup):
    select_category = State()
    select_book = State()
    edit_field = State()
    edit_value = State()
    select_new_category = State()
    title = State()
    author = State()
    description = State()
    cover = State()
    availability = State()


class BookDeleteStates(StatesGroup):
    select_category = State()
    select_book = State()
    confirm = State()


class CategoryAddStates(StatesGroup):
    waiting_for_name = State()
