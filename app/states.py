from aiogram.fsm.state import State, StatesGroup


class ReportStates(StatesGroup):
    choosing_category = State()
    typing_description = State()
    collecting_photos = State()


class CompleteStates(StatesGroup):
    waiting_text = State()
    collecting_photos = State()


class CompanyStates(StatesGroup):
    entering_code = State()
