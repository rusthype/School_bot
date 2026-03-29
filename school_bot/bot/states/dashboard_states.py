from aiogram.fsm.state import State, StatesGroup


class SearchStates(StatesGroup):
    waiting_for_query = State()


class AddStudentStates(StatesGroup):
    first_name = State()
    last_name = State()
    phone = State()
    school_selection = State()
    class_group = State()
    confirm = State()


class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_confirm = State()


class SendMessageStates(StatesGroup):
    waiting_for_target = State()
    waiting_for_text = State()


class PrivacySettingsStates(StatesGroup):
    waiting_for_days = State()
