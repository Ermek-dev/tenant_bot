import asyncio
from contextlib import suppress
from typing import List, Optional, cast

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, ContentType, InlineKeyboardMarkup,
                           InputMediaPhoto, Message, MediaUnion)
from aiogram.fsm.context import FSMContext

from app.config import load_settings
from app import db
from app.states import ReportStates, CompleteStates, CompanyStates
from app.keyboards import (
    main_menu,
    skip_or_done_kb,
    staff_task_kb,
    send_completion_kb,
    CATEGORIES,
    categories_inline_kb,
    enter_company_code_kb,
    quick_start_kb,
    description_nav_kb,
)
from app.utils import user_display_name


async def on_startup() -> tuple[Bot, Dispatcher, int | None]:
    settings = load_settings()
    await db.init_db(settings.database_path)
    # Try to read staff_chat_id from DB if not set in env
    staff_chat_id = settings.staff_chat_id
    if staff_chat_id is None:
        saved = await db.get_setting("staff_chat_id")
        if saved:
            with suppress(ValueError):
                staff_chat_id = int(saved)
    else:
        # Persist env-provided staff chat id into settings for runtime use
        await db.set_setting("staff_chat_id", str(staff_chat_id))
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    register_handlers(dp, bot, admin_ids=settings.admin_user_ids)
    return bot, dp, staff_chat_id


def register_handlers(dp: Dispatcher, bot: Bot, admin_ids: set[int]):
    # /start
    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        # Check company binding
        if message.from_user is None:
            return
        company = await db.get_user_company(message.from_user.id)
        if not company:
            await message.answer(
                "Здравствуйте!\n"
                "Вас приветствует чат-бот Сервис РТС ЛТД.\n"
                "Чтобы начать, привяжите своё предприятие.\n"
                "Попросите у администратора код и отправьте его: /company_join <код>",
                reply_markup=enter_company_code_kb(),
            )
            await message.answer("Либо используйте меню ниже.", reply_markup=main_menu())
            return
        await message.answer(
            "Добрый день!\n"
            "Вас приветствует чат-бот Сервис РТС ЛТД.\n"
            "С какой проблемой вы столкнулись?",
            reply_markup=quick_start_kb(),
        )
        await message.answer("Выберите действие:", reply_markup=main_menu())

    # Category selection via text buttons
    @dp.message(ReportStates.choosing_category)
    async def choose_category(message: Message, state: FSMContext):
        text = (message.text or "").strip().lower()
        mapping = {title.lower(): code for title, code in CATEGORIES}
        if text not in mapping:
            await message.answer("Пожалуйста, выберите категорию из списка ниже:", reply_markup=None)
            await message.answer("Категории:", reply_markup=categories_inline_kb())
            return
        await state.update_data(category=mapping[text])
        await message.answer("Опишите проблему текстом (что случилось, где именно).", reply_markup=description_nav_kb())
        await state.set_state(ReportStates.typing_description)

    # Category selection via inline buttons
    @dp.callback_query(F.data.startswith("cat:"))
    async def choose_category_inline(cb: CallbackQuery, state: FSMContext):
        if not cb.data:
            await cb.answer()
            return
        code = cb.data.split(":", 1)[1]
        await state.set_state(ReportStates.typing_description)
        await state.update_data(category=code)
        await cb.answer()
        if cb.message:
            await cb.message.answer("Опишите проблему текстом (что случилось, где именно).", reply_markup=description_nav_kb())

    # Description capture
    @dp.message(ReportStates.typing_description)
    async def type_description(message: Message, state: FSMContext):
        desc = (message.text or "").strip()
        if not desc:
            await message.answer("Пожалуйста, опишите проблему текстом.")
            return
        await state.update_data(description=desc, photos=[])
        await message.answer(
            "Можно приложить фото(а). Отправьте одно или несколько.\n"
            "Когда закончите — нажмите '✅ Отправить' или '⏭️ Пропустить'.",
            reply_markup=skip_or_done_kb(),
        )
        await state.set_state(ReportStates.collecting_photos)

    # Collect photos
    @dp.message(ReportStates.collecting_photos, F.content_type == ContentType.PHOTO)
    async def collect_photos(message: Message, state: FSMContext):
        data = await state.get_data()
        photos: List[str] = data.get("photos", [])
        # take highest resolution
        if not message.photo:
            return
        file_id = message.photo[-1].file_id
        photos.append(file_id)
        await state.update_data(photos=photos)
        await message.answer(f"Добавлено фото. Всего: {len(photos)}")

    @dp.callback_query(ReportStates.collecting_photos, F.data.in_({"skip_photos", "done_photos"}))
    async def done_photos(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        data = await state.get_data()
        category_any = data.get("category")
        description = data.get("description", "")
        photos: List[str] = data.get("photos", [])

        # Create issue in DB
        # Reporter is the user who pressed the button in PM
        reporter_user = cb.from_user
        reporter_name = (reporter_user.full_name or reporter_user.username or str(reporter_user.id)) if reporter_user else ""
        # Determine company
        if cb.from_user is None or cb.message is None:
            return
        company = await db.get_user_company(cb.from_user.id)
        if not company:
            await cb.message.answer("Сначала привяжите предприятие: используйте /company_join <код>.")
            await state.clear()
            return
        if not isinstance(category_any, str):
            await cb.message.answer("Произошла ошибка: категория не выбрана. Пожалуйста, начните заново.")
            await state.clear()
            return
        category = category_any
        issue_id = await db.create_issue(
            user_id=cb.from_user.id,
            user_name=reporter_name,
            category=category,
            description=description,
            tenant_chat_id=cb.from_user.id,
            company_id=company["id"],
        )
        for fid in photos:
            await db.add_issue_photo(issue_id, fid, is_completion=False, uploader_user_id=cb.from_user.id)

        # Send to staff chat
        staff_chat_id = None
        # prefer env/setting loaded on demand
        setting_chat = await db.get_setting("staff_chat_id")
        if setting_chat:
            with suppress(ValueError):
                staff_chat_id = int(setting_chat)
        if staff_chat_id is None:
            # fall back to message’s chat, but this is PM; so notify user
            await cb.message.answer(
                f"Благодарим за обращение! Ваша заявка принята. Номер: #{issue_id}.",
                reply_markup=main_menu(),
            )
        else:
            text = (
                f"🆕 Заявка #{issue_id}\n"
                f"🏢 Предприятие: {company['name']} (ID {company['id']})\n"
                f"Категория: {human_category(category)}\n"
                f"От: {reporter_name} (id {cb.from_user.id})\n\n"
                f"Описание:\n{description}"
            )
            # If there are photos, send them together with the text by using the
            # caption on the first media item (Telegram only allows caption on
            # media, not on media groups' separate messages). After that, send
            # a separate message with the staff action buttons and store its id
            # in DB so the bot can edit it later.
            if photos:
                if len(photos) == 1:
                    # Send single photo with the full staff text as caption
                    await bot.send_photo(staff_chat_id, photos[0], caption=text)
                else:
                    # Send media group; put the staff text as caption on the
                    # first media element (only the first caption is shown).
                    media = [InputMediaPhoto(media=fid) for fid in photos[:10]]  # media group limit
                    # attach caption to first media
                    media[0].caption = text
                    await bot.send_media_group(staff_chat_id, cast(List[MediaUnion], media))

                # Send a follow-up message that contains the action buttons and
                # store that message id for later edits.
                staff_msg = await bot.send_message(chat_id=staff_chat_id, text=f"Заявка #{issue_id}", reply_markup=staff_task_kb(issue_id, assigned_to=None))
                await db.set_staff_message(issue_id, staff_chat_id, staff_msg.message_id)
            else:
                # No photos: send plain text message with buttons as before
                staff_msg = await bot.send_message(chat_id=staff_chat_id, text=text, reply_markup=staff_task_kb(issue_id, assigned_to=None))
                await db.set_staff_message(issue_id, staff_chat_id, staff_msg.message_id)

            await cb.message.answer(
                f"Благодарим за обращение! Ваша заявка принята. Номер: #{issue_id}.",
                reply_markup=main_menu(),
            )

        await state.clear()

    # Staff: set staff chat id
    @dp.message(Command("setstaffchat"))
    async def set_staff_chat(message: Message):
        # Allow only in groups and from admins
        if message.chat.type not in {"group", "supergroup"}:
            await message.answer("Выполните эту команду в группе сотрудников.")
            return
        if message.from_user is None:
            return
        if admin_ids and (message.from_user.id not in admin_ids):
            await message.answer("Только администраторы могут выполнять эту команду.")
            return
        await db.set_setting("staff_chat_id", str(message.chat.id))
        await message.answer(f"Группа сотрудников зарегистрирована: {message.chat.id}")

    # Admin: create company
    @dp.message(Command("company_create"))
    async def company_create(message: Message):
        if message.from_user is None:
            return
        if not (admin_ids and message.from_user.id in admin_ids):
            await message.answer("Только администраторы могут создавать предприятия.")
            return
        args = (message.text or "").split(maxsplit=2)
        if len(args) < 2:
            await message.answer("Использование: /company_create <название> [код]")
            return
        name = args[1]
        code = args[2] if len(args) > 2 else None
        company_id = await db.create_company(name, code)
        # Retrieve to show final code
        comp = await db.get_company(company_id)
        if not comp:
            await message.answer(f"Создано предприятие #{company_id}.")
        else:
            await message.answer(f"Создано предприятие #{company_id}: {comp['name']}\nКод приглашения: {comp['invite_code']}")

    # Admin: list companies
    @dp.message(Command("company_list"))
    async def company_list(message: Message):
        if message.from_user is None:
            return
        if not (admin_ids and message.from_user.id in admin_ids):
            await message.answer("Доступно только администраторам.")
            return
        comps = await db.list_companies()
        if not comps:
            await message.answer("Список предприятий пуст.")
            return
        lines = []
        for c in comps:
            cnt = await db.company_member_count(c["id"])
            lines.append(f"#{c['id']} — {c['name']} (код: {c['invite_code'] or '—'}, пользователей: {cnt})")
        await message.answer("Список предприятий:\n" + "\n".join(lines))

    # Tenant: join company by invite code
    @dp.message(Command("company_join"))
    async def company_join(message: Message):
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("Использование: /company_join <код>")
            return
        code = args[1].strip()
        comp = await db.get_company_by_invite(code)
        if not comp:
            await message.answer("Неверный код предприятия. Проверьте и попробуйте снова.")
            return
        if message.from_user is None:
            return
        await db.set_user_company(message.from_user.id, comp["id"])
        await message.answer(f"Успешно привязано предприятие: {comp['name']} (#{comp['id']}).")
        await message.answer("Теперь можете создать заявку: нажмите '🆕 Новая заявка'.", reply_markup=main_menu())

    # Tenant: enter company code via UI
    @dp.callback_query(F.data == "company:enter_code")
    async def company_enter_code_cb(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.set_state(CompanyStates.entering_code)
        if cb.message:
            await cb.message.answer("Введите код предприятия (получите у администратора):")

    @dp.message(CompanyStates.entering_code)
    async def company_enter_code_message(message: Message, state: FSMContext):
        code = (message.text or "").strip()
        if not code:
            await message.answer("Код не должен быть пустым. Повторите ввод.")
            return
        comp = await db.get_company_by_invite(code)
        if not comp:
            await message.answer("Неверный код. Проверьте и попробуйте снова.")
            return
        if message.from_user is None:
            return
        await db.set_user_company(message.from_user.id, comp["id"])
        await state.clear()
        await message.answer(f"Привязано предприятие: {comp['name']} (#{comp['id']}).", reply_markup=main_menu())

    # New issue entrypoint via menu
    @dp.message(F.text == "🆕 Новая заявка")
    async def menu_new_issue(message: Message, state: FSMContext):
        if message.from_user is None:
            return
        company = await db.get_user_company(message.from_user.id)
        if not company:
            await message.answer("Сначала привяжите предприятие: /company_join <код>.", reply_markup=enter_company_code_kb())
            return
        await state.set_state(ReportStates.choosing_category)
        await message.answer("Выберите категорию:", reply_markup=categories_inline_kb())

    # Quick start: begin new issue via inline button
    @dp.callback_query(F.data == "new_issue")
    async def cb_new_issue(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        if cb.from_user is None:
            return
        company = await db.get_user_company(cb.from_user.id)
        if not company:
            if cb.message:
                await cb.message.answer("Сначала привяжите предприятие: /company_join <код>.", reply_markup=enter_company_code_kb())
            return
        await state.set_state(ReportStates.choosing_category)
        if cb.message:
            await cb.message.answer("Выберите категорию:", reply_markup=categories_inline_kb())

    # My issues
    @dp.message(Command("my"))
    @dp.message(F.text == "📋 Мои заявки")
    async def my_issues(message: Message):
        if message.from_user is None:
            return
        rows = await db.user_issues(message.from_user.id, limit=5)
        if not rows:
            await message.answer("У вас пока нет заявок.")
            return
        lines = []
        for r in rows:
            status_emoji = {"open": "🟡", "assigned": "🟠", "closed": "🟢"}.get(r["status"], "⚪️")
            lines.append(f"{status_emoji} #{r['id']} — {human_category(r['category'])}")
        await message.answer("Ваши последние заявки:\n" + "\n".join(lines))

    # Bind company via menu
    @dp.message(F.text == "🔑 Привязать предприятие")
    async def menu_bind_company(message: Message, state: FSMContext):
        await state.set_state(CompanyStates.entering_code)
        await message.answer("Введите код предприятия:")

    # Help via menu
    @dp.message(F.text == "ℹ️ Помощь")
    async def menu_help(message: Message):
        await cmd_help(message)

    # Cancel any flow
    @dp.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=main_menu())

    # Utility: get chat id and user id for configuration
    @dp.message(Command("chatid"))
    async def cmd_chatid(message: Message):
        if message.from_user is None:
            return
        await message.answer(
            f"Тип чата: {message.chat.type}\n"
            f"Chat ID: {message.chat.id}\n"
            f"Ваш User ID: {message.from_user.id}"
        )

    # Staff: claim
    @dp.callback_query(F.data.startswith("claim:"))
    async def cb_claim(cb: CallbackQuery):
        if not cb.data:
            await cb.answer()
            return
        issue_id = int(cb.data.split(":", 1)[1])
        issue = await db.get_issue(issue_id)
        if not issue:
            await cb.answer("Заявка не найдена", show_alert=True)
            return
        if issue["status"] != "open":
            await cb.answer("Заявка уже взята кем-то другим", show_alert=True)
            return
        if cb.from_user is None:
            return
        ok = await db.claim_issue(issue_id, cb.from_user.id, display_from(cb))
        if not ok:
            await cb.answer("Не удалось взять заявку", show_alert=True)
            return
        # edit staff message to show assignee and change buttons
        comp = await db.get_company(issue["company_id"]) if issue["company_id"] else None
        text = staff_message_text(issue, override_assignee=display_from(cb), override_status="assigned", company_name=(comp["name"] if comp else None))
        try:
            if isinstance(cb.message, Message):
                await cb.message.edit_text(text, reply_markup=staff_task_kb(issue_id, assigned_to=display_from(cb)))
            else:
                raise Exception("Inaccessible message")
        except Exception:
            # message might be not editable; send a follow-up
            if cb.message:
                await cb.message.answer(text, reply_markup=staff_task_kb(issue_id, assigned_to=display_from(cb)))
        await cb.answer("Вы назначены ответственным")

    # Staff: complete flow start
    @dp.callback_query(F.data.startswith("complete:"))
    async def cb_complete(cb: CallbackQuery, state: FSMContext):
        if not cb.data:
            await cb.answer()
            return
        issue_id = int(cb.data.split(":", 1)[1])
        issue = await db.get_issue(issue_id)
        if not issue:
            await cb.answer("Заявка не найдена", show_alert=True)
            return
        if cb.from_user is None or issue["assignee_user_id"] != cb.from_user.id:
            await cb.answer("Только ответственный может завершить.", show_alert=True)
            return
        await state.update_data(complete_issue_id=issue_id, completion_text="", completion_photos=[])
        await state.set_state(CompleteStates.waiting_text)
        if cb.message:
            await cb.message.answer(f"Завершение заявки #{issue_id}. Пришлите текстовый комментарий (или '-' чтобы пропустить).")
        await cb.answer()

    @dp.message(CompleteStates.waiting_text)
    async def completion_text(message: Message, state: FSMContext):
        text = (message.text or "").strip()
        if text == "-":
            text = ""
        await state.update_data(completion_text=text, completion_photos=[])
        await state.set_state(CompleteStates.collecting_photos)
        await message.answer("Пришлите фотоотчёт (можно несколько). Когда готово — нажмите кнопку ниже.", reply_markup=send_completion_kb())

    @dp.message(CompleteStates.collecting_photos, F.content_type == ContentType.PHOTO)
    async def completion_collect_photos(message: Message, state: FSMContext):
        data = await state.get_data()
        photos: List[str] = data.get("completion_photos", [])
        if not message.photo:
            return
        photos.append(message.photo[-1].file_id)
        await state.update_data(completion_photos=photos)
        await message.answer(f"Добавлено фото. Всего: {len(photos)}")

    @dp.callback_query(CompleteStates.collecting_photos, F.data == "send_completion")
    async def send_completion(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        data = await state.get_data()
        issue_id_val = data.get("complete_issue_id")
        if not isinstance(issue_id_val, int):
            if cb.message:
                await cb.message.answer("Произошла ошибка: не удалось определить номер заявки.")
            await state.clear()
            return
        issue_id: int = issue_id_val
        text: str = data.get("completion_text", "")
        photos: List[str] = data.get("completion_photos", [])

        issue = await db.get_issue(issue_id)
        if not issue:
            if cb.message:
                await cb.message.answer("Заявка не найдена")
            await state.clear()
            return
        # Store completion photos
        if cb.from_user is None:
            return
        for fid in photos:
            await db.add_issue_photo(issue_id, fid, is_completion=True, uploader_user_id=cb.from_user.id)
        await db.complete_issue(issue_id)

        # Notify tenant
        tenant_chat_id = issue["tenant_chat_id"]
        notify_text = (
            f"Ваша заявка #{issue_id} выполнена.\n"
            f"Комментарий исполнителя: {text or 'без комментария'}"
        )
        await bot.send_message(tenant_chat_id, notify_text)
        if photos:
            if len(photos) == 1:
                await bot.send_photo(tenant_chat_id, photos[0], caption=f"Фотоотчёт по заявке #{issue_id}")
            else:
                media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
                await bot.send_media_group(tenant_chat_id, cast(List[MediaUnion], media))
                await bot.send_message(tenant_chat_id, f"Фотоотчёт по заявке #{issue_id}")

        # Update staff message
        try:
            comp = await db.get_company(issue["company_id"]) if issue["company_id"] else None
            text_staff = staff_message_text(issue, override_status="closed", company_name=(comp["name"] if comp else None))
            await bot.edit_message_text(
                chat_id=issue["staff_chat_id"],
                message_id=issue["staff_message_id"],
                text=text_staff,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
        except Exception:
            pass

        if cb.message:
            await cb.message.answer(f"Заявка #{issue_id} отмечена как выполненная.", reply_markup=main_menu())
        await state.clear()

    # Inline cancel for any active flow
    @dp.callback_query(F.data == "cancel_flow")
    async def cancel_flow(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.answer()
        if cb.message:
            await cb.message.answer("Действие отменено.", reply_markup=main_menu())

    # Back: from description to categories
    @dp.callback_query(F.data == "back_to_categories")
    async def back_to_categories(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.set_state(ReportStates.choosing_category)
        if cb.message:
            await cb.message.answer("Выберите категорию:", reply_markup=categories_inline_kb())

    # Back: from photos to description
    @dp.callback_query(F.data == "back_to_description")
    async def back_to_description(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.set_state(ReportStates.typing_description)
        if cb.message:
            await cb.message.answer("Опишите проблему текстом (что случилось, где именно).", reply_markup=description_nav_kb())

    # Fallback help
    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(
            "/start — главное меню\n"
            "/my — мои последние заявки\n"
            "/chatid — показать chat id и ваш user id\n"
            "/setstaffchat — установить группу сотрудников (выполнить в группе)\n"
            "/company_create — создать предприятие (админ)\n"
            "/company_list — список предприятий (админ)\n"
            "/company_join <код> — привязать предприятие"
        )

    # Ignore other photos outside states to avoid confusion
    @dp.message(F.content_type == ContentType.PHOTO)
    async def generic_photo(message: Message):
        await message.answer("Чтобы приложить фото к заявке, начните с '🆕 Новая заявка' и следуйте шагам.")


def display_from(cb: CallbackQuery) -> str:
    u = cb.from_user
    name = (u.full_name or u.username or str(u.id)) if u else "Unknown"
    return name


def human_category(code: str) -> str:
    mapping = {code: title for title, code in CATEGORIES}
    return mapping.get(code, code)


def staff_message_text(issue_row, *, override_assignee: Optional[str] = None, override_status: Optional[str] = None, company_name: Optional[str] = None) -> str:
    status = override_status or issue_row["status"]
    assignee = override_assignee or issue_row["assignee_name"]
    text = (
        f"Заявка #{issue_row['id']}\n"
        f"🏢 Предприятие: {company_name or '—'}\n"
        f"Категория: {human_category(issue_row['category'])}\n"
        f"От: {issue_row['user_name']} (id {issue_row['user_id']})\n\n"
        f"Описание:\n{issue_row['description']}\n\n"
        f"Статус: {status}"
    )
    if assignee:
        text += f"\nОтветственный: {assignee}"
    return text


async def main():
    bot, dp, _ = await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
