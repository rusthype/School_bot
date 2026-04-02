from aiogram.fsm.state import State, StatesGroup


class RoleSelectStates(StatesGroup):
    waiting_role = State()


class RegistrationStates(StatesGroup):
    welcome = State()
    first_name = State()
    last_name = State()
    school = State()
    phone = State()
    confirm = State()
    class_group = State()


class PostRoleRegistrationStates(StatesGroup):
    """Simple two-step flow that runs after role selection (name → school → pending)."""
    waiting_name = State()
    waiting_school = State()
