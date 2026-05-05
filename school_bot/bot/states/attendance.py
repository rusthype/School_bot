from aiogram.fsm.state import State, StatesGroup


class TeacherAttendanceStates(StatesGroup):
    waiting_for_check_in_location = State()
    waiting_for_check_out_location = State()


class SuperadminAttendanceStates(StatesGroup):
    waiting_for_school_location = State()
    waiting_for_radius = State()


class StudentClassAttendanceStates(StatesGroup):
    choosing_date = State()
    waiting_for_photo_or_manual = State()
    confirming_result = State()
    marking_manual = State()
