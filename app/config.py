import os
from dataclasses import dataclass
from typing import Optional, Set

from dotenv import load_dotenv


@dataclass
class Settings:
    bot_token: str
    staff_chat_id: Optional[int]
    admin_user_ids: Set[int]
    database_path: str


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    staff_chat_id_env = os.getenv("STAFF_CHAT_ID")
    staff_chat_id = int(staff_chat_id_env) if staff_chat_id_env else None

    admin_ids_env = os.getenv("ADMIN_USER_IDS", "")
    admin_ids = set()
    for s in admin_ids_env.split(","):
        s = s.strip()
        if s:
            try:
                admin_ids.add(int(s))
            except ValueError:
                pass

    db_path = os.getenv("DATABASE_PATH", "./data/bot.db")
    return Settings(
        bot_token=token,
        staff_chat_id=staff_chat_id,
        admin_user_ids=admin_ids,
        database_path=db_path,
    )

