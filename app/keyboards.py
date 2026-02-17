from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


CATEGORIES = [
    ("Сантехника", "plumbing"),
    ("Электрика", "electricity"),
    ("Техническая неисправность", "technical_issue"),
    ("Уборка помещений", "cleaning"),
    ("IT обслуживание", "it_service"),
    ("Документооборот", "document_flow"),
    ("Другое (опишите проблему)", "other"),
]


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Create main menu keyboard.
    
    Args:
        is_admin: If True, show 'Все заявки' instead of 'Мои заявки'.
    """
    issues_button = "📋 Все заявки" if is_admin else "📋 Мои заявки"
    buttons = [
        [KeyboardButton(text="🆕 Новая заявка"), KeyboardButton(text=issues_button)],
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
        [
            InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_photos"),
            InlineKeyboardButton(text="✅ Отправить", callback_data="done_photos"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_description")],
        [InlineKeyboardButton(text="⛔️ Отмена", callback_data="cancel_flow")],
    ])


def staff_task_kb(issue_id: int, *, assigned_to: str | None) -> InlineKeyboardMarkup:
    if assigned_to:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Присоединиться", callback_data=f"join:{issue_id}"),
                InlineKeyboardButton(text="✅ Завершить", callback_data=f"complete:{issue_id}"),
            ]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Взяться", callback_data=f"claim:{issue_id}")]
        ])


def send_completion_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="send_completion")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_description")],
        [InlineKeyboardButton(text="⛔️ Отмена", callback_data="cancel_flow")],
    ])


def enter_company_code_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Ввести код предприятия", callback_data="company:enter_code")]
    ])


def quick_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Создать заявку", callback_data="new_issue")]
    ])


def description_nav_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="back_to_categories")],
        [InlineKeyboardButton(text="⛔️ Отмена", callback_data="cancel_flow")],
    ])


def cancel_company_create_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛔️ Отменить создание", callback_data="cancel_company_create")],
    ])


def deadline_choice_kb(issue_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ В течение часа", callback_data=f"deadline:{issue_id}:1hour")],
        [InlineKeyboardButton(text="📅 В течение дня", callback_data=f"deadline:{issue_id}:1day")],
        [InlineKeyboardButton(text="✏️ Указать срок", callback_data=f"deadline:{issue_id}:custom")],
        [InlineKeyboardButton(text="⛔️ Отмена", callback_data=f"cancel_claim:{issue_id}")],
    ])


def send_issue_kb() -> InlineKeyboardMarkup:
    """Упрощённая клавиатура для отправки заявки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отправить заявку", callback_data="send_issue"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_categories"),
            InlineKeyboardButton(text="⛔️ Отмена", callback_data="cancel_flow"),
        ],
    ])


def all_issues_page_kb(issues: list, page: int, total_pages: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Create inline keyboard for paginated all issues list.
    
    Args:
        issues: List of issue rows with id, status, assignee_name
        page: Current page number (0-indexed)
        total_pages: Total number of pages
        is_admin: If True, show reassign buttons for assigned issues
    """
    rows = []
    
    # Action buttons for each issue (2 per row for compactness)
    action_row = []
    for issue in issues:
        issue_id = issue["id"]
        if issue["status"] == "open":
            btn = InlineKeyboardButton(
                text=f"🛠 #{issue_id}", 
                callback_data=f"confirm_claim:{issue_id}"
            )
        else:  # assigned
            btn = InlineKeyboardButton(
                text=f"✅ #{issue_id}", 
                callback_data=f"confirm_complete:{issue_id}"
            )
        action_row.append(btn)
        if len(action_row) == 3:  # 3 buttons per row
            rows.append(action_row)
            action_row = []
    if action_row:
        rows.append(action_row)
    
    # Admin reassign buttons for assigned issues
    if is_admin:
        reassign_row = []
        for issue in issues:
            if issue["status"] == "assigned":
                reassign_row.append(InlineKeyboardButton(
                    text=f"🔄 #{issue['id']}",
                    callback_data=f"confirm_reassign:{issue['id']}"
                ))
                if len(reassign_row) == 3:
                    rows.append(reassign_row)
                    reassign_row = []
        if reassign_row:
            rows.append(reassign_row)
    
    # Navigation row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"all_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"all_page:{page + 1}"))
    
    if nav_row:
        rows.append(nav_row)
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_action_kb(action: str, issue_id: int) -> InlineKeyboardMarkup:
    """Create confirmation keyboard for claim/complete actions.
    
    Args:
        action: 'claim' or 'complete'
        issue_id: Issue ID to act on
    """
    if action == "claim":
        confirm_text = "✅ Да, взяться"
        confirm_data = f"claim:{issue_id}"
    else:
        confirm_text = "✅ Да, завершить"
        confirm_data = f"complete:{issue_id}"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=confirm_text, callback_data=confirm_data),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_confirm"),
        ]
    ])


def confirm_reassign_kb(issue_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard for admin reassignment."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, переназначить", callback_data=f"reassign:{issue_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_confirm"),
        ]
    ])
