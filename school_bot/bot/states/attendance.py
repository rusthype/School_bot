from aiogram.fsm.state import State, StatesGroup


class TeacherAttendanceStates(StatesGroup):
    waiting_for_check_in_location = State()
    waiting_for_check_out_location = State()


class SuperadminAttendanceStates(StatesGroup):
    waiting_for_school_location = State()
    waiting_for_radius = State()
