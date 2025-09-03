from aiogram.types import Message


def user_display_name(message: Message) -> str:
    u = message.from_user
    if not u:
        return "Unknown"
    parts = [u.first_name or "", u.last_name or ""]
    name = " ".join([p for p in parts if p]).strip()
    if not name and u.username:
        name = f"@{u.username}"
    if not name:
        name = str(u.id)
    return name

