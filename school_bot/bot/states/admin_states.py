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
