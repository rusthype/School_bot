from aiogram.fsm.state import State, StatesGroup


class GroupManagementStates(StatesGroup):
    add_name = State()
    add_chat_id = State()
    edit_name = State()
    edit_chat_id = State()
