from aiogram.fsm.state import State, StatesGroup


class BookOrderStates(StatesGroup):
    book_name = State()  # Kitob nomi
    book_author = State()  # Muallif (ixtiyoriy)
    book_quantity = State()  # Soni
    book_notes = State()  # Qo'shimcha ma'lumot (ixtiyoriy)
    confirm = State()  # Tasdiqlash
