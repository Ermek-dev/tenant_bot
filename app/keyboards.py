from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


CATEGORIES = [
    ("🔧 Сантехника", "plumbing"),
    ("💡 Свет", "electricity"),
    ("📄 Документы", "documents"),
    ("❓ Другая", "other"),
]


def main_menu() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🆕 Новая заявка"), KeyboardButton(text="📋 Мои заявки")],
        [KeyboardButton(text="🔑 Привязать предприятие"), KeyboardButton(text="ℹ️ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def categories_inline_kb() -> InlineKeyboardMarkup:
    rows = []
    pair = []
    for title, code in CATEGORIES:
        pair.append(InlineKeyboardButton(text=title, callback_data=f"cat:{code}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_or_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_photos"),
         InlineKeyboardButton(text="✅ Отправить", callback_data="done_photos")],
    ])


def staff_task_kb(issue_id: int, *, assigned_to: str | None) -> InlineKeyboardMarkup:
    if assigned_to:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить", callback_data=f"complete:{issue_id}")]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Взяться", callback_data=f"claim:{issue_id}")]
        ])


def send_completion_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="send_completion")]
    ])


def enter_company_code_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Ввести код предприятия", callback_data="company:enter_code")]
    ])
