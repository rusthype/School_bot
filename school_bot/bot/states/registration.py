from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    welcome = State()
    first_name = State()
    last_name = State()
    school = State()
    phone = State()
    confirm = State()
    class_group = State()
