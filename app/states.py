from aiogram.fsm.state import State, StatesGroup


class ReportStates(StatesGroup):
    choosing_category = State()
    creating_report = State()


class CompleteStates(StatesGroup):
    waiting_text = State()
    collecting_photos = State()


class CompanyStates(StatesGroup):
    entering_code = State()
    creating_name = State()
    creating_code = State()


class ClaimStates(StatesGroup):
    choosing_deadline = State()
    entering_custom_deadline = State()
