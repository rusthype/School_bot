from aiogram.fsm.state import State, StatesGroup


class AddAdminStates(StatesGroup):
    waiting_for_user = State()
    waiting_for_role = State()


class RemoveAdminStates(StatesGroup):
    waiting_for_user = State()


class EditAdminRoleStates(StatesGroup):
    waiting_for_user = State()
    waiting_for_role = State()


class AddTeacherManualStates(StatesGroup):
    waiting_for_user = State()
    waiting_for_school = State()


class TeacherEditStates(StatesGroup):
    """Admin-side: editing a specific teacher's data."""
    choose_field = State()
    waiting_full_name = State()
    waiting_phone = State()
    waiting_role = State()


class TeacherSelfEditStates(StatesGroup):
    """Teacher-side: editing own profile fields."""
    choose_field = State()
    waiting_first_name = State()
    waiting_last_name = State()
    waiting_phone = State()
