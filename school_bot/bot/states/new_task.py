from aiogram.fsm.state import State, StatesGroup


class NewTaskStates(StatesGroup):
    group_selection = State()  # Guruh tanlash
    topic = State()            # Mavzu
    description = State()      # Vazifa
    photo = State()            # Rasm