import asyncio
import logging
from contextlib import suppress
from typing import List, Optional, cast

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, ContentType, InlineKeyboardMarkup,
                           InputMediaPhoto, Message, MediaUnion, BotCommand,
                           BotCommandScopeDefault, BotCommandScopeChat)
from aiogram.fsm.context import FSMContext

from app.config import load_settings
from app import db
from app.states import ReportStates, CompleteStates, CompanyStates, ClaimStates
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
    deadline_choice_kb,
    send_issue_kb,
    all_issues_page_kb,
    confirm_action_kb,
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

    # Set up command menu (кнопка "/")
    await setup_bot_commands(bot, admin_ids=settings.admin_user_ids, staff_chat_id=staff_chat_id)
    
    register_handlers(dp, bot, admin_ids=settings.admin_user_ids)
    return bot, dp, staff_chat_id

async def setup_bot_commands(bot: Bot, admin_ids: set[int], staff_chat_id: int | None = None):
    """Настройка меню команд бота (кнопка '/').
    
    Args:
        bot: Bot instance
        admin_ids: Set of admin user IDs
        staff_chat_id: Staff chat group ID (optional)
    """
    try:
        commands = [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="my", description="Мои заявки"),
            BotCommand(command="help", description="Справка"),
            BotCommand(command="company_join", description="Привязать предприятие"),
            BotCommand(command="cancel", description="Отменить действие"),
            BotCommand(command="chatid", description="Показать ID чата"),
        ]
        
        # Устанавливаем команды для всех пользователей (по умолчанию)
        await bot.set_my_commands(commands)
        
        # Для администраторов добавляем дополнительные команды
        if admin_ids:
            admin_commands = commands + [
                BotCommand(command="all", description="Все невыполненные заявки (админ)"),
                BotCommand(command="company_create", description="Создать предприятие (админ)"),
                BotCommand(command="company_list", description="Список предприятий (админ)"),
                BotCommand(command="setstaffchat", description="Установить группу (админ)"),
            ]
            
            # Устанавливаем расширенный список для каждого администратора
            for admin_id in admin_ids:
                try:
                    await bot.set_my_commands(
                        admin_commands, 
                        scope=BotCommandScopeChat(chat_id=admin_id)
                    )
                except Exception:
                    # Игнорируем ошибки для пользователей, которые ещё не писали боту
                    pass
        
        # Для staff chat группы добавляем /all
        if staff_chat_id:
            staff_commands = [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="all", description="Все невыполненные заявки"),
                BotCommand(command="help", description="Справка"),
            ]
            try:
                await bot.set_my_commands(
                    staff_commands,
                    scope=BotCommandScopeChat(chat_id=staff_chat_id)
                )
            except Exception:
                # Группа может быть недоступна
                pass
    except Exception as e:
        # Логируем ошибку, но не прерываем работу бота
        logger.error(f"Ошибка при установке команд меню: {e}")


def register_handlers(dp: Dispatcher, bot: Bot, admin_ids: set[int]):
    
    def is_admin_user(user_id: int) -> bool:
        """Check if user is an admin."""
        return user_id in admin_ids
    
    # /start
    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        # Check company binding
        if message.from_user is None:
            return
        is_admin = is_admin_user(message.from_user.id)
        company = await db.get_user_company(message.from_user.id)
        if not company:
            await message.answer(
                "Здравствуйте!\n"
                "Вас приветствует чат-бот Сервис РТС ЛТД.\n"
                "Чтобы начать, привяжите своё предприятие.\n"
                "Попросите у администратора код и отправьте его: /company_join <код>",
                reply_markup=enter_company_code_kb(),
            )
            await message.answer("Либо используйте меню ниже.", reply_markup=main_menu(is_admin=is_admin))
            return
        await message.answer(
            "Добрый день!\n"
            "Вас приветствует чат-бот Сервис РТС ЛТД.\n"
            "С какой проблемой вы столкнулись?",
            reply_markup=quick_start_kb(),
        )
        await message.answer("Выберите действие:", reply_markup=main_menu(is_admin=is_admin))

    # Category selection via text buttons
    @dp.message(ReportStates.choosing_category)
    async def choose_category(message: Message, state: FSMContext):
        text = (message.text or "").strip()
        
        # Check if user pressed a menu button - reset state and handle
        if text == "🆕 Новая заявка":
            # Already in issue creation, just remind to select category
            await message.answer("Выберите категорию:", reply_markup=categories_inline_kb())
            return
        elif text == "📋 Мои заявки":
            await state.clear()
            await my_issues(message, state)
            return
        elif text == "📋 Все заявки":
            await state.clear()
            await all_issues_handler(message, state)
            return
        elif text == "🔑 Привязать предприятие":
            await state.clear()
            await menu_bind_company(message, state)
            return
        elif text == "ℹ️ Помощь":
            await state.clear()
            await menu_help(message, state)
            return
        
        mapping = {title.lower(): code for title, code in CATEGORIES}
        if text.lower() not in mapping:
            await message.answer("Пожалуйста, выберите категорию из списка ниже:", reply_markup=None)
            await message.answer("Категории:", reply_markup=categories_inline_kb())
            return

        await state.update_data(category=mapping[text.lower()], photos=[], description=None)
        await message.answer(
            "📝 Опишите проблему:\n"
            "• Отправьте фото с подписью\n"
            "• Или просто текст",
            reply_markup=description_nav_kb(),
        )
        await state.set_state(ReportStates.creating_report)



    

    # Simplified report creation - automatic preview with send button
    @dp.message(ReportStates.creating_report, F.content_type.in_({ContentType.PHOTO, ContentType.TEXT}))
    async def creating_report_input(message: Message, state: FSMContext):
        text = (message.text or "").strip()
        
        # Check if user pressed a menu button - reset state and handle
        if text == "🆕 Новая заявка":
            # Already in issue creation, remind current state
            await message.answer("Вы уже создаёте заявку. Продолжите или отправьте /cancel для отмены.")
            return
        elif text == "📋 Мои заявки":
            await state.clear()
            await my_issues(message, state)
            return
        elif text == "📋 Все заявки":
            await state.clear()
            await all_issues_handler(message, state)
            return
        elif text == "🔑 Привязать предприятие":
            await state.clear()
            await menu_bind_company(message, state)
            return
        elif text == "ℹ️ Помощь":
            await state.clear()
            await menu_help(message, state)
            return
        
        data = await state.get_data()
        photos: list[str] = list(data.get("photos", []))
        existing_description = (data.get("description") or "").strip()

        # Add photo if present
        if message.photo:
            file_id = message.photo[-1].file_id
            photos.append(file_id)

        # Get description from caption or text
        incoming_description = (message.caption or message.text or "").strip()
        description = incoming_description if incoming_description else existing_description

        if not description and not photos:
            await message.answer("❌ Не удалось распознать сообщение. Пришлите текст или фото с подписью.")
            return

        # Update state
        await state.update_data(photos=photos, description=description)

        # Show preview and send button if we have description
        if description:
            category = data.get("category")
            category_name = human_category(category) if category else "Неизвестная"
            
            # Show preview if this is new description or first time
            was_empty = not existing_description
            if was_empty or incoming_description:
                preview_text = (
                    f"📋 <b>Превью заявки</b>\n\n"
                    f"Категория: {category_name}\n"
                    f"Описание: {description[:100]}{'...' if len(description) > 100 else ''}\n"
                    f"Фото: {len(photos)} шт.\n\n"
                    f"✅ Готово к отправке!"
                )
                await message.answer(preview_text, parse_mode="HTML", reply_markup=send_issue_kb())
            elif message.photo:
                # Just adding more photos, minimal feedback
                await message.answer(f"✅ Фото добавлено. Всего: {len(photos)} шт. Используйте кнопку выше для отправки.")
        elif photos:
            # Only photos, no description yet
            await message.answer(
                f"✅ Фото добавлено ({len(photos)} шт.)\n"
                f"Добавьте описание (подпись к фото или отправьте текстом):",
                reply_markup=description_nav_kb()
            )
        else:
            await message.answer("❌ Не удалось распознать сообщение. Пришлите текст или фото с подписью.")
        
    @dp.callback_query(F.data.startswith("cat:"))
    async def choose_category_inline(cb: CallbackQuery, state: FSMContext):
        if not cb.data:
            await cb.answer()
            return
        
        # Check if user is still in category selection state
        current_state = await state.get_state()
        if current_state != ReportStates.choosing_category:
            await cb.answer("Выбор категории уже завершён", show_alert=False)
            return
        
        code = cb.data.split(":", 1)[1]
        category_name = human_category(code)
        
        # Скрываем кнопки категорий - редактируем сообщение
        if cb.message:
            try:
                await cb.message.edit_text(
                    f"✅ Категория выбрана: <b>{category_name}</b>",
                    parse_mode="HTML",
                    reply_markup=None
                )
            except Exception:
                # Если не удалось отредактировать, просто скрываем кнопки
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
        
        await cb.answer(f"Выбрано: {category_name}")
        
        await state.update_data(category=code, photos=[], description=None)
        if cb.message:
            await cb.message.answer(
                f"📝 Опишите проблему:\n"
                f"• Отправьте фото с подписью\n"
                f"• Или просто текст",
                reply_markup=description_nav_kb(),
            )
        await state.set_state(ReportStates.creating_report)

        
    @dp.callback_query(ReportStates.creating_report, F.data == "send_issue")
    async def send_issue_callback(cb: CallbackQuery, state: FSMContext):
        await finalize_report(cb, state)
    
    @dp.callback_query(ReportStates.creating_report, F.data.in_({"skip_photos", "done_photos"}))
    async def finalize_report(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        data = await state.get_data()
        category_any = data.get("category")
        description = (data.get("description") or "").strip()
        photos: List[str] = data.get("photos", [])

        # Проверки
        if cb.from_user is None or cb.message is None:
            return
        if not isinstance(category_any, str):
            await cb.message.answer("Произошла ошибка: категория не выбрана. Пожалуйста, начните заново.")
            await state.clear()
            return
        if not description:
            await cb.message.answer("Пожалуйста, добавьте описание (в подписи к фото или текстом).")
            return

        # Компания пользователя
        company = await db.get_user_company(cb.from_user.id)
        if not company:
            await cb.message.answer("Сначала привяжите предприятие: используйте /company_join <код>.")
            await state.clear()
            return

        # Создаём заявку
        reporter_user = cb.from_user
        reporter_name = (reporter_user.full_name or reporter_user.username or str(reporter_user.id)) if reporter_user else ""
        category = category_any
        issue_id = await db.create_issue(
            user_id=cb.from_user.id,
            user_name=reporter_name,
            category=category,
            description=description,
            tenant_chat_id=cb.from_user.id,
            company_id=company["id"],
        )
        for fid in photos[:10]:  # ограничимся безопасной десяткой
            await db.add_issue_photo(issue_id, fid, is_completion=False, uploader_user_id=cb.from_user.id)

        # staff_chat_id
        staff_chat_id = None
        setting_chat = await db.get_setting("staff_chat_id")
        if setting_chat:
            with suppress(ValueError):
                staff_chat_id = int(setting_chat)

        # Уведомления
        if staff_chat_id is None:
            # Hide preview buttons
            try:
                await cb.message.edit_text(
                    f"✅ Заявка #{issue_id} отправлена!",
                    reply_markup=None
                )
            except Exception:
                pass
            await cb.message.answer(
                f"Благодарим за обращение! Ваша заявка принята. Номер: #{issue_id}.",
                reply_markup=main_menu(is_admin=is_admin_user(cb.from_user.id)),
            )
        else:
            text = (
                f"🆕 Заявка #{issue_id}\n"
                f"🏢 Предприятие: {company['name']}\n"
                f"Категория: {human_category(category)}\n"
                f"От: {reporter_name}\n\n"
                f"Описание:\n{description}"
            )
            try:
                if photos:
                    if len(photos) == 1:
                        # Одно фото - отправляем с подписью и кнопкой в одном сообщении
                        staff_msg = await bot.send_photo(
                            staff_chat_id, 
                            photos[0], 
                            caption=text,
                            reply_markup=staff_task_kb(issue_id, assigned_to=None)
                        )
                        await db.set_staff_message(issue_id, staff_chat_id, staff_msg.message_id)
                    else:
                        # Несколько фото - сначала альбом, потом краткое сообщение с кнопкой
                        media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
                        media[0].caption = text
                        await bot.send_media_group(staff_chat_id, cast(List[MediaUnion], media))
                        # Краткое сообщение с кнопкой управления (ссылка на заявку)
                        staff_msg = await bot.send_message(
                            chat_id=staff_chat_id, 
                            text=f"👆 Заявка #{issue_id} | {human_category(category)} | {company['name']}", 
                            reply_markup=staff_task_kb(issue_id, assigned_to=None)
                        )
                        await db.set_staff_message(issue_id, staff_chat_id, staff_msg.message_id)
                else:
                    # Без фото - текст с кнопкой в одном сообщении
                    staff_msg = await bot.send_message(chat_id=staff_chat_id, text=text, reply_markup=staff_task_kb(issue_id, assigned_to=None))
                    await db.set_staff_message(issue_id, staff_chat_id, staff_msg.message_id)
            except Exception as e:
                # Staff chat may be unavailable, but issue is still created
                logger.error(f"Failed to send issue #{issue_id} to staff chat: {e}")

            # Hide preview buttons
            try:
                await cb.message.edit_text(
                    f"✅ Заявка #{issue_id} отправлена!",
                    reply_markup=None
                )
            except Exception:
                pass
            await cb.message.answer(
                f"Благодарим за обращение! Ваша заявка принята. Номер: #{issue_id}.",
                reply_markup=main_menu(is_admin=is_admin_user(cb.from_user.id)),
            )

        await state.clear()



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

    # Admin: create company - improved with examples
    @dp.message(Command("company_create"))
    async def company_create(message: Message, state: FSMContext):
        if message.from_user is None:
            return
        if not (admin_ids and message.from_user.id in admin_ids):
            await message.answer("❌ Только администраторы могут создавать предприятия.")
            return
        
        # Parse arguments
        args = (message.text or "").split(maxsplit=2)
        
        # If no arguments, start interactive mode
        if len(args) < 2:
            help_text = (
                "🏢 <b>Создание предприятия</b>\n\n"
                "Вы можете создать предприятие двумя способами:\n\n"
                "<b>Способ 1: Быстрое создание</b>\n"
                "Используйте команду с названием предприятия:\n"
                "<code>/company_create ООО \"РТС ЛТД\"</code>\n\n"
                "Или с названием и кодом приглашения:\n"
                "<code>/company_create ООО \"РТС ЛТД\" RTS2024</code>\n\n"
                "<b>Примеры:</b>\n"
                "• <code>/company_create БАТ 1121</code>\n"
                "• <code>/company_create Софарма</code> (код сгенерируется автоматически)\n"
                "• <code>/company_create \"Мой бухгалтер\" 133</code>\n\n"
                "<b>Способ 2: Интерактивный режим</b>\n"
                "Отправьте команду <code>/company_create</code> без параметров,\n"
                "и бот проведёт вас по шагам.\n\n"
                "Начнём? Отправьте название предприятия:"
            )
            await message.answer(help_text, parse_mode="HTML")
            await state.set_state(CompanyStates.creating_name)
            return
        
        # Quick creation mode
        name = args[1].strip()
        code = args[2].strip() if len(args) > 2 else None
        
        # Validate name
        if not name or len(name) < 2:
            await message.answer(
                "❌ Название предприятия слишком короткое (минимум 2 символа).\n\n"
                "<b>Пример:</b>\n"
                "<code>/company_create ООО \"РТС ЛТД\"</code>",
                parse_mode="HTML"
            )
            return
        
        # Check if code already exists
        if code:
            code = code.strip().upper()
            existing = await db.get_company_by_invite(code)
            if existing:
                await message.answer(
                    f"❌ Код приглашения <code>{code}</code> уже используется предприятием: {existing['name']}\n\n"
                    f"Попробуйте другой код или создайте без кода (он сгенерируется автоматически).",
                    parse_mode="HTML"
                )
                return
        
        # Create company
        try:
            company_id = await db.create_company(name, code)
            comp = await db.get_company(company_id)
            if comp:
                await message.answer(
                    f"✅ <b>Предприятие успешно создано!</b>\n\n"
                    f"🏢 Название: {comp['name']}\n"
                    f"🆔 ID: #{comp['id']}\n"
                    f"🔑 Код приглашения: <code>{comp['invite_code']}</code>\n\n"
                    f"Поделитесь этим кодом с арендаторами, чтобы они могли присоединиться.",
                    parse_mode="HTML"
                )
            else:
                await message.answer(f"✅ Создано предприятие #{company_id}: {name}")
        except Exception as e:
            await message.answer(
                f"❌ Ошибка при создании предприятия: {str(e)}\n\n"
                "Попробуйте ещё раз или используйте интерактивный режим."
            )
    
    # Interactive company creation: name input
    @dp.message(CompanyStates.creating_name)
    async def company_create_name(message: Message, state: FSMContext):
        name = (message.text or "").strip()
        
        if not name or len(name) < 2:
            await message.answer(
                "❌ Название предприятия слишком короткое (минимум 2 символа).\n"
                "Пожалуйста, введите корректное название:"
            )
            return
        
        # Check if company with this name already exists
        companies = await db.list_companies()
        for comp in companies:
            if comp['name'].lower() == name.lower():
                await message.answer(
                    f"⚠️ Предприятие с названием <b>\"{name}\"</b> уже существует!\n\n"
                    f"ID: #{comp['id']}\n"
                    f"Код: {comp['invite_code']}\n\n"
                    "Введите другое название или отправьте /cancel для отмены.",
                    parse_mode="HTML"
                )
                return
        
        await state.update_data(company_name=name)
        await state.set_state(CompanyStates.creating_code)
        await message.answer(
            f"✅ Название принято: <b>{name}</b>\n\n"
            f"Теперь введите код приглашения (или отправьте <code>-</code> для автоматической генерации):\n\n"
            f"<b>Примеры кодов:</b>\n"
            f"• <code>1121</code>\n"
            f"• <code>RTS2024</code>\n"
            f"• <code>ABC123</code>\n\n"
            f"Отправьте <code>-</code> если хотите, чтобы код был сгенерирован автоматически.",
            parse_mode="HTML"
        )
    
    # Interactive company creation: code input
    @dp.message(CompanyStates.creating_code)
    async def company_create_code(message: Message, state: FSMContext):
        code_input = (message.text or "").strip()
        data = await state.get_data()
        name = data.get("company_name")
        
        if not name:
            await message.answer("❌ Ошибка: название не найдено. Начните заново: /company_create")
            await state.clear()
            return
        
        # Auto-generate code if user sent "-"
        code = None
        if code_input and code_input != "-":
            code = code_input.strip().upper()
            
            # Validate code format
            if len(code) < 2:
                await message.answer(
                    "❌ Код приглашения слишком короткий (минимум 2 символа).\n"
                    "Введите код ещё раз или отправьте <code>-</code> для автоматической генерации:",
                    parse_mode="HTML"
                )
                return
            
            # Check if code already exists
            existing = await db.get_company_by_invite(code)
            if existing:
                await message.answer(
                    f"❌ Код приглашения <code>{code}</code> уже используется предприятием: {existing['name']}\n\n"
                    f"Введите другой код или отправьте <code>-</code> для автоматической генерации:",
                    parse_mode="HTML"
                )
                return
        
        # Create company
        try:
            company_id = await db.create_company(name, code)
            comp = await db.get_company(company_id)
            await state.clear()
            
            if comp:
                await message.answer(
                    f"✅ <b>Предприятие успешно создано!</b>\n\n"
                    f"🏢 Название: {comp['name']}\n"
                    f"🆔 ID: #{comp['id']}\n"
                    f"🔑 Код приглашения: <code>{comp['invite_code']}</code>\n\n"
                    f"💡 <b>Поделитесь этим кодом с арендаторами:</b>\n"
                    f"<code>/company_join {comp['invite_code']}</code>",
                    parse_mode="HTML"
                )
        except Exception as e:
            await message.answer(
                f"❌ Ошибка при создании предприятия: {str(e)}\n\n"
                "Попробуйте ещё раз: /company_create"
            )
            await state.clear()

    # Admin: list companies - improved formatting
    @dp.message(Command("company_list"))
    async def company_list(message: Message):
        if message.from_user is None:
            return
        if not (admin_ids and message.from_user.id in admin_ids):
            await message.answer("❌ Доступно только администраторам.")
            return
        
        comps = await db.list_companies()
        if not comps:
            await message.answer(
                "📋 <b>Список предприятий пуст</b>\n\n"
                "Создайте первое предприятие командой:\n"
                "<code>/company_create</code>",
                parse_mode="HTML"
            )
            return
        
        total_members = 0
        lines = []
        
        for c in comps:
            cnt = await db.company_member_count(c["id"])
            total_members += cnt
            code_display = f"<code>{c['invite_code']}</code>" if c['invite_code'] else "—"
            member_emoji = "👥" if cnt > 0 else "👤"
            lines.append(
                f"#{c['id']} <b>{c['name']}</b>\n"
                f"   🔑 Код: {code_display} | {member_emoji} Пользователей: {cnt}"
            )
        
        header = (
            f"📋 <b>Список предприятий</b>\n\n"
            f"Всего предприятий: <b>{len(comps)}</b>\n"
            f"Всего пользователей: <b>{total_members}</b>\n\n"
        )
        
        # Split into chunks if too long (Telegram limit ~4096 chars)
        full_text = header + "\n\n".join(lines)
        
        if len(full_text) > 4000:
            # Send in chunks
            await message.answer(header, parse_mode="HTML")
            current_chunk = []
            current_length = 0
            
            for line in lines:
                line_with_sep = line + "\n\n"
                if current_length + len(line_with_sep) > 4000:
                    await message.answer("\n\n".join(current_chunk), parse_mode="HTML")
                    current_chunk = [line]
                    current_length = len(line)
                else:
                    current_chunk.append(line)
                    current_length += len(line_with_sep)
            
            if current_chunk:
                await message.answer("\n\n".join(current_chunk), parse_mode="HTML")
        else:
            await message.answer(full_text, parse_mode="HTML")

    # Tenant: join company by invite code - improved
    @dp.message(Command("company_join"))
    async def company_join(message: Message):
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "🔑 <b>Привязка к предприятию</b>\n\n"
                "<b>Использование:</b>\n"
                "<code>/company_join &lt;код&gt;</code>\n\n"
                "<b>Примеры:</b>\n"
                "• <code>/company_join 1121</code>\n"
                "• <code>/company_join RTS2024</code>\n"
                "• <code>/company_join ABC123</code>\n\n"
                "💡 Получите код приглашения у администратора вашего предприятия.",
                parse_mode="HTML"
            )
            return
        
        code = args[1].strip().upper()
        
        if message.from_user is None:
            return
        
        comp = await db.get_company_by_invite(code)
        if not comp:
            await message.answer(
                f"❌ <b>Неверный код приглашения</b>\n\n"
                f"Код <code>{code}</code> не найден в базе данных.\n\n"
                f"💡 Проверьте код и попробуйте снова.\n"
                f"Если код не работает, обратитесь к администратору вашего предприятия.",
                parse_mode="HTML"
            )
            return
        
        # Check if already bound to this company
        current_company = await db.get_user_company(message.from_user.id)
        if current_company and current_company["id"] == comp["id"]:
            await message.answer(
                f"ℹ️ Вы уже привязаны к этому предприятию:\n"
                f"<b>{comp['name']}</b> (#{comp['id']})\n\n"
                f"Вы можете создать заявку через меню.",
                parse_mode="HTML",
                reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id))
            )
            return
        
        await db.set_user_company(message.from_user.id, comp["id"])
        
        if current_company:
            await message.answer(
                f"✅ <b>Предприятие успешно изменено!</b>\n\n"
                f"Было: {current_company['name']}\n"
                f"Стало: <b>{comp['name']}</b> (#{comp['id']})\n\n"
                f"Теперь вы можете создавать заявки от имени нового предприятия.",
                parse_mode="HTML",
                reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id))
            )
        else:
            await message.answer(
                f"✅ <b>Предприятие успешно привязано!</b>\n\n"
                f"🏢 <b>{comp['name']}</b>\n"
                f"🆔 ID: #{comp['id']}\n\n"
                f"Теперь вы можете создавать заявки. Нажмите кнопку ниже:",
                parse_mode="HTML",
                reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id))
            )

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
        await message.answer(f"Привязано предприятие: {comp['name']} (#{comp['id']}).", reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id)))

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

    # My issues (for regular users)
    @dp.message(Command("my"))
    @dp.message(F.text == "📋 Мои заявки")
    async def my_issues(message: Message, state: FSMContext):
        await state.clear()  # Reset any active state
        if message.from_user is None:
            return
        rows = await db.user_issues(message.from_user.id, limit=5)
        if not rows:
            await message.answer("У вас пока нет заявок.")
            return
        lines = []
        status_map = {"open": ("🟡", "ожидает"), "assigned": ("🟠", "в работе"), "closed": ("🟢", "выполнена")}
        for r in rows:
            emoji, status_text = status_map.get(r["status"], ("⚪️", r["status"]))
            line = f"{emoji} #{r['id']} — {human_category(r['category'])} — {status_text}"
            if r["status"] == "assigned" and r["assignee_name"]:
                line += f" ({r['assignee_name']})"
            lines.append(line)
        await message.answer("Ваши последние заявки:\n" + "\n".join(lines))

    # All issues (for admin via button)
    @dp.message(F.text == "📋 Все заявки")
    async def all_issues_handler(message: Message, state: FSMContext):
        await state.clear()  # Reset any active state
        if message.from_user is None:
            return
        # Only admins can see all issues
        if message.from_user.id not in admin_ids:
            await message.answer("⛔️ Эта функция доступна только администраторам.")
            return
        await _show_all_pending_issues(message, page=0)

    # /all command for staff chat
    @dp.message(Command("all"))
    async def all_issues_command(message: Message, state: FSMContext):
        await state.clear()
        if message.from_user is None:
            return
        
        # Allow in staff chat or for admins
        staff_chat_id = None
        setting_chat = await db.get_setting("staff_chat_id")
        if setting_chat:
            with suppress(ValueError):
                staff_chat_id = int(setting_chat)
        
        is_admin = message.from_user.id in admin_ids
        is_staff_chat = message.chat.id == staff_chat_id
        
        if not is_admin and not is_staff_chat:
            await message.answer("⛔️ Эта команда доступна только в группе сотрудников или для администраторов.")
            return
        
        await _show_all_pending_issues(message, page=0)

    ISSUES_PER_PAGE = 5

    async def _show_all_pending_issues(message: Message, page: int = 0):
        """Helper to display paginated pending issues with action buttons."""
        total_count = await db.count_pending_issues()
        if total_count == 0:
            await message.answer("✅ Нет невыполненных заявок.")
            return
        
        total_pages = (total_count + ISSUES_PER_PAGE - 1) // ISSUES_PER_PAGE
        offset = page * ISSUES_PER_PAGE
        rows = await db.all_pending_issues(limit=ISSUES_PER_PAGE, offset=offset)
        
        lines = []
        status_map = {"open": ("🟡", "ожидает"), "assigned": ("🟠", "в работе")}
        for r in rows:
            emoji, status_text = status_map.get(r["status"], ("⚪️", r["status"]))
            line = f"{emoji} #{r['id']} — {human_category(r['category'])} — {status_text}"
            if r["user_name"]:
                line += f"\n   👤 От: {r['user_name']}"
            if r["status"] == "assigned" and r["assignee_name"]:
                line += f"\n   🛠 Исп.: {r['assignee_name']}"
            lines.append(line)
        
        header = f"📋 <b>Невыполненные заявки</b> ({total_count}):\n\n"
        legend = "\n\n<i>🛠=Взяться  ✅=Завершить</i>"
        await message.answer(
            header + "\n\n".join(lines) + legend, 
            parse_mode="HTML",
            reply_markup=all_issues_page_kb(list(rows), page, total_pages)
        )

    # Pagination callback
    @dp.callback_query(F.data.startswith("all_page:"))
    async def all_page_callback(cb: CallbackQuery, state: FSMContext):
        if not cb.data or not cb.message:
            await cb.answer()
            return
        
        page = int(cb.data.split(":")[1])
        
        # Get data for the new page
        total_count = await db.count_pending_issues()
        if total_count == 0:
            await cb.message.edit_text("✅ Нет невыполненных заявок.")
            await cb.answer()
            return
        
        total_pages = (total_count + ISSUES_PER_PAGE - 1) // ISSUES_PER_PAGE
        offset = page * ISSUES_PER_PAGE
        rows = await db.all_pending_issues(limit=ISSUES_PER_PAGE, offset=offset)
        
        lines = []
        status_map = {"open": ("🟡", "ожидает"), "assigned": ("🟠", "в работе")}
        for r in rows:
            emoji, status_text = status_map.get(r["status"], ("⚪️", r["status"]))
            line = f"{emoji} #{r['id']} — {human_category(r['category'])} — {status_text}"
            if r["user_name"]:
                line += f"\n   👤 От: {r['user_name']}"
            if r["status"] == "assigned" and r["assignee_name"]:
                line += f"\n   🛠 Исп.: {r['assignee_name']}"
            lines.append(line)
        
        header = f"📋 <b>Невыполненные заявки</b> ({total_count}):\n\n"
        legend = "\n\n<i>🛠=Взяться  ✅=Завершить</i>"
        
        try:
            await cb.message.edit_text(
                header + "\n\n".join(lines) + legend,
                parse_mode="HTML",
                reply_markup=all_issues_page_kb(list(rows), page, total_pages)
            )
        except Exception:
            pass  # Message unchanged
        await cb.answer()

    # Confirmation for claim from list
    @dp.callback_query(F.data.startswith("confirm_claim:"))
    async def confirm_claim_callback(cb: CallbackQuery, state: FSMContext):
        if not cb.data or not cb.message:
            await cb.answer()
            return
        
        issue_id = int(cb.data.split(":")[1])
        issue = await db.get_issue(issue_id)
        
        if not issue:
            await cb.answer("⚠️ Заявка не найдена", show_alert=True)
            return
        
        if issue["status"] != "open":
            await cb.answer("⚠️ Заявка уже взята в работу", show_alert=True)
            return
        
        text = (
            f"⚠️ <b>Подтвердите действие</b>\n\n"
            f"Взяться за заявку #{issue_id}?\n"
            f"📋 {human_category(issue['category'])}\n"
            f"👤 От: {issue['user_name'] or '—'}"
        )
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=confirm_action_kb("claim", issue_id))
        await cb.answer()

    # Confirmation for complete from list
    @dp.callback_query(F.data.startswith("confirm_complete:"))
    async def confirm_complete_callback(cb: CallbackQuery, state: FSMContext):
        if not cb.data or not cb.message:
            await cb.answer()
            return
        
        issue_id = int(cb.data.split(":")[1])
        issue = await db.get_issue(issue_id)
        
        if not issue:
            await cb.answer("⚠️ Заявка не найдена", show_alert=True)
            return
        
        if issue["status"] != "assigned":
            await cb.answer("⚠️ Заявка не в работе", show_alert=True)
            return
        
        text = (
            f"⚠️ <b>Подтвердите действие</b>\n\n"
            f"Завершить заявку #{issue_id}?\n"
            f"📋 {human_category(issue['category'])}\n"
            f"🛠 Исполнитель: {issue['assignee_name'] or '—'}"
        )
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=confirm_action_kb("complete", issue_id))
        await cb.answer()

    # Cancel confirmation
    @dp.callback_query(F.data == "cancel_confirm")
    async def cancel_confirm_callback(cb: CallbackQuery, state: FSMContext):
        if not cb.message:
            await cb.answer()
            return
        
        # Return to the list
        await cb.message.delete()
        if cb.message.chat:
            # Send fresh list
            total_count = await db.count_pending_issues()
            if total_count == 0:
                await cb.message.answer("✅ Нет невыполненных заявок.")
            else:
                total_pages = (total_count + ISSUES_PER_PAGE - 1) // ISSUES_PER_PAGE
                rows = await db.all_pending_issues(limit=ISSUES_PER_PAGE, offset=0)
                
                lines = []
                status_map = {"open": ("🟡", "ожидает"), "assigned": ("🟠", "в работе")}
                for r in rows:
                    emoji, status_text = status_map.get(r["status"], ("⚪️", r["status"]))
                    line = f"{emoji} #{r['id']} — {human_category(r['category'])} — {status_text}"
                    if r["user_name"]:
                        line += f"\n   👤 От: {r['user_name']}"
                    if r["status"] == "assigned" and r["assignee_name"]:
                        line += f"\n   🛠 Исп.: {r['assignee_name']}"
                    lines.append(line)
                
                header = f"📋 <b>Невыполненные заявки</b> ({total_count}):\n\n"
                legend = "\n\n<i>🛠=Взяться  ✅=Завершить</i>"
                await cb.message.answer(
                    header + "\n\n".join(lines) + legend,
                    parse_mode="HTML",
                    reply_markup=all_issues_page_kb(list(rows), 0, total_pages)
                )
        await cb.answer()

    # Noop callback for page number button
    @dp.callback_query(F.data == "noop")
    async def noop_callback(cb: CallbackQuery):
        await cb.answer()

    # Bind company via menu
    @dp.message(F.text == "🔑 Привязать предприятие")
    async def menu_bind_company(message: Message, state: FSMContext):
        await state.clear()  # Reset any active state first
        await state.set_state(CompanyStates.entering_code)
        await message.answer("Введите код предприятия (или /cancel для отмены):")

    # Help via menu
    @dp.message(F.text == "ℹ️ Помощь")
    async def menu_help(message: Message, state: FSMContext):
        await state.clear()  # Reset any active state
        await cmd_help(message)

    # Cancel any flow
    @dp.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext):
        await state.clear()
        is_admin = is_admin_user(message.from_user.id) if message.from_user else False
        await message.answer("Действие отменено.", reply_markup=main_menu(is_admin=is_admin))

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

    # Staff: claim - show deadline selection
    @dp.callback_query(F.data.startswith("claim:"))
    async def cb_claim(cb: CallbackQuery, state: FSMContext):
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
        
        # Show deadline selection instead of claiming immediately
        await state.update_data(claim_issue_id=issue_id, claim_assignee_user_id=cb.from_user.id, claim_assignee_name=display_from(cb))
        if cb.message:
            # Edit the confirmation message to show deadline selection
            try:
                await cb.message.edit_text(
                    f"⏰ Выберите срок выполнения заявки #{issue_id}:",
                    reply_markup=deadline_choice_kb(issue_id)
                )
            except Exception:
                # Fallback: send new message if edit fails
                await cb.message.answer(
                    f"⏰ Выберите срок выполнения заявки #{issue_id}:",
                    reply_markup=deadline_choice_kb(issue_id)
                )
        await cb.answer()

    # Staff: handle deadline selection
    @dp.callback_query(F.data.startswith("deadline:"))
    async def cb_deadline_choice(cb: CallbackQuery, state: FSMContext):
        if not cb.data:
            await cb.answer()
            return
        
        # Parse: deadline:issue_id:choice
        parts = cb.data.split(":")
        if len(parts) < 3:
            await cb.answer("Ошибка данных", show_alert=True)
            return
        
        issue_id = int(parts[1])
        choice = parts[2]
        
        data = await state.get_data()
        stored_issue_id = data.get("claim_issue_id")
        
        if stored_issue_id != issue_id:
            await cb.answer("Ошибка: несоответствие данных", show_alert=True)
            await state.clear()
            return
        
        issue = await db.get_issue(issue_id)
        if not issue:
            await cb.answer("Заявка не найдена", show_alert=True)
            await state.clear()
            return
        
        if issue["status"] != "open":
            await cb.answer("Заявка уже взята кем-то другим", show_alert=True)
            await state.clear()
            return
        
        assignee_user_id = data.get("claim_assignee_user_id")
        assignee_name = data.get("claim_assignee_name")
        
        if not assignee_user_id or not assignee_name:
            await cb.answer("Ошибка: данные исполнителя не найдены", show_alert=True)
            await state.clear()
            return
        
        deadline_text = ""
        deadline_iso = None
        
        from datetime import datetime, timedelta, timezone
        
        if choice == "1hour":
            deadline_text = "в течение часа"
            deadline_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        elif choice == "1day":
            deadline_text = "в течение дня"
            deadline_iso = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        elif choice == "custom":
            await state.set_state(ClaimStates.entering_custom_deadline)
            if cb.message:
                await cb.message.answer(
                    "Введите срок выполнения заявки текстом (например: 'в течение 2 часов', 'до конца недели', 'завтра до 18:00'):"
                )
            await cb.answer()
            return
        
        # Claim with deadline
        if deadline_iso:
            ok = await db.claim_issue(issue_id, assignee_user_id, assignee_name, deadline_iso)
            if not ok:
                await cb.answer("Не удалось взять заявку", show_alert=True)
                await state.clear()
                return
            
            # Notify tenant about deadline
            tenant_chat_id = issue["tenant_chat_id"]
            try:
                await bot.send_message(
                    tenant_chat_id,
                    f"✅ Ваша заявка #{issue_id} взята в работу.\n"
                    f"📅 Срок выполнения: {deadline_text}\n"
                    f"👤 Ответственный: {assignee_name}"
                )
            except Exception:
                pass  # Ignore errors when sending notification
            
            # Hide deadline selection buttons (edit message to remove buttons)
            try:
                if isinstance(cb.message, Message):
                    await cb.message.edit_text(
                        f"✅ Заявка #{issue_id} взята в работу.\n📅 Срок: {deadline_text}",
                        reply_markup=None
                    )
            except Exception:
                pass
            
            # Update staff message: убираем кнопку со старого сообщения и отправляем новое с кнопкой "Завершить"
            comp = await db.get_company(issue["company_id"]) if issue["company_id"] else None
            updated_issue = await db.get_issue(issue_id)
            text = staff_message_text(updated_issue, company_name=(comp["name"] if comp else None))
            
            staff_chat_id = issue["staff_chat_id"]
            staff_message_id = issue["staff_message_id"]
            
            try:
                if staff_chat_id and staff_message_id:
                    # Убираем кнопку со старого сообщения (помечаем как взятое)
                    await bot.edit_message_reply_markup(
                        chat_id=staff_chat_id,
                        message_id=staff_message_id,
                        reply_markup=None
                    )
            except Exception:
                pass
            
            try:
                if staff_chat_id:
                    # Отправляем НОВОЕ сообщение с кнопкой "Завершить" (внизу чата!)
                    category_name = human_category(issue["category"]) if issue["category"] else ""
                    new_staff_msg = await bot.send_message(
                        chat_id=staff_chat_id,
                        text=f"🔧 Заявка #{issue_id} в работе\n"
                             f"📁 {category_name}\n"
                             f"👤 Исполнитель: {assignee_name}\n"
                             f"📅 Срок: {deadline_text}",
                        reply_markup=staff_task_kb(issue_id, assigned_to=assignee_name)
                    )
                    # Обновляем привязку к новому сообщению для кнопки "Завершить"
                    await db.set_staff_message(issue_id, staff_chat_id, new_staff_msg.message_id)
            except Exception as e:
                logger.error(f"Failed to send 'Complete' button for issue #{issue_id}: {e}")
            
            await cb.answer(f"Заявка взята в работу. Срок: {deadline_text}")
            await state.clear()


    # Staff: handle custom deadline text input
    @dp.message(ClaimStates.entering_custom_deadline)
    async def cb_custom_deadline_text(message: Message, state: FSMContext):
        custom_deadline_text = (message.text or "").strip()
        if not custom_deadline_text:
            await message.answer("Пожалуйста, введите срок выполнения:")
            return
        
        data = await state.get_data()
        issue_id = data.get("claim_issue_id")
        assignee_user_id = data.get("claim_assignee_user_id")
        assignee_name = data.get("claim_assignee_name")
        
        if not issue_id or not assignee_user_id or not assignee_name:
            await message.answer("Ошибка: данные не найдены. Попробуйте взять заявку заново.")
            await state.clear()
            return
        
        issue = await db.get_issue(issue_id)
        if not issue:
            await message.answer("Заявка не найдена")
            await state.clear()
            return
        
        if issue["status"] != "open":
            await message.answer("Заявка уже взята кем-то другим")
            await state.clear()
            return
        
        # Claim with custom deadline (store text in deadline field)
        ok = await db.claim_issue(issue_id, assignee_user_id, assignee_name, custom_deadline_text)
        if not ok:
            await message.answer("Не удалось взять заявку. Попробуйте ещё раз.")
            await state.clear()
            return
        
        # Notify tenant about deadline
        tenant_chat_id = issue["tenant_chat_id"]
        try:
            await bot.send_message(
                tenant_chat_id,
                f"✅ Ваша заявка #{issue_id} взята в работу.\n"
                f"📅 Срок выполнения: {custom_deadline_text}\n"
                f"👤 Ответственный: {assignee_name}"
            )
        except Exception:
            pass  # Ignore errors when sending notification
        
        # Update staff message: убираем кнопку со старого и отправляем новое с "Завершить"
        comp = await db.get_company(issue["company_id"]) if issue["company_id"] else None
        
        staff_chat_id = issue["staff_chat_id"]
        staff_message_id = issue["staff_message_id"]
        
        try:
            if staff_chat_id and staff_message_id:
                # Убираем кнопку со старого сообщения
                await bot.edit_message_reply_markup(
                    chat_id=staff_chat_id,
                    message_id=staff_message_id,
                    reply_markup=None
                )
        except Exception:
            pass
        
        try:
            if staff_chat_id:
                # Отправляем НОВОЕ сообщение с кнопкой "Завершить" (внизу чата!)
                category_name = human_category(issue["category"]) if issue["category"] else ""
                new_staff_msg = await bot.send_message(
                    chat_id=staff_chat_id,
                    text=f"🔧 Заявка #{issue_id} в работе\n"
                         f"📁 {category_name}\n"
                         f"👤 Исполнитель: {assignee_name}\n"
                         f"📅 Срок: {custom_deadline_text}",
                    reply_markup=staff_task_kb(issue_id, assigned_to=assignee_name)
                )
                # Обновляем привязку к новому сообщению
                await db.set_staff_message(issue_id, staff_chat_id, new_staff_msg.message_id)
        except Exception:
            pass
        
        await message.answer(f"✅ Заявка #{issue_id} взята в работу. Срок: {custom_deadline_text}")
        await state.clear()

    # Cancel claim
    @dp.callback_query(F.data.startswith("cancel_claim:"))
    async def cb_cancel_claim(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.answer("Выбор срока отменён")
        if cb.message:
            await cb.message.answer("Действие отменено.")

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
            # Edit the confirmation message
            try:
                await cb.message.edit_text(
                    f"📝 Завершение заявки #{issue_id}\n\nПришлите текстовый комментарий (или '-' чтобы пропустить).",
                    reply_markup=None
                )
            except Exception:
                await cb.message.answer(f"📝 Завершение заявки #{issue_id}. Пришлите текстовый комментарий (или '-' чтобы пропустить).")
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
        
        # Скрываем кнопку отправки
        try:
            if cb.message and isinstance(cb.message, Message):
                await cb.message.edit_text("📤 Отправка отчёта...", reply_markup=None)
        except Exception:
            pass
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
        try:
            await bot.send_message(tenant_chat_id, notify_text)
            if photos:
                if len(photos) == 1:
                    await bot.send_photo(tenant_chat_id, photos[0], caption=f"Фотоотчёт по заявке #{issue_id}")
                else:
                    media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
                    await bot.send_media_group(tenant_chat_id, cast(List[MediaUnion], media))
                    await bot.send_message(tenant_chat_id, f"Фотоотчёт по заявке #{issue_id}")
        except Exception:
            pass  # User may have blocked the bot

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
            is_admin = is_admin_user(cb.from_user.id) if cb.from_user else False
            await cb.message.answer(f"Заявка #{issue_id} отмечена как выполненная.", reply_markup=main_menu(is_admin=is_admin))
        await state.clear()

    # Inline cancel for any active flow
    @dp.callback_query(F.data == "cancel_flow")
    async def cancel_flow(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.answer()
        if cb.message:
            is_admin = is_admin_user(cb.from_user.id) if cb.from_user else False
            await cb.message.answer("Действие отменено.", reply_markup=main_menu(is_admin=is_admin))

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
        await state.set_state(ReportStates.creating_report)
        if cb.message:
            await cb.message.answer(
                "Отправьте фото(а) с подписью или просто текст.\n"
                "Когда будете готовы — нажмите «✅ Отправить».",
                reply_markup=skip_or_done_kb(),
            )


    # Help command - короткая информативная версия
    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        if message.from_user is None:
            return
        
        is_admin = admin_ids and message.from_user.id in admin_ids
        
        # Check if in staff chat
        staff_chat_id = None
        setting_chat = await db.get_setting("staff_chat_id")
        if setting_chat:
            with suppress(ValueError):
                staff_chat_id = int(setting_chat)
        is_staff_chat = message.chat.id == staff_chat_id
        
        help_text = (
            "📚 <b>Справка по командам</b>\n\n"
            "<b>Основные команды:</b>\n"
            "/start — главное меню\n"
            "/my — мои последние заявки\n"
            "/company_join &lt;код&gt; — привязать предприятие\n"
            "/chatid — показать chat id и ваш user id\n"
            "/cancel — отменить текущее действие\n"
        )
        
        # Show /all for staff chat members
        if is_staff_chat and not is_admin:
            help_text += (
                "\n<b>Команды сотрудников:</b>\n"
                "/all — все невыполненные заявки\n"
            )
        
        if is_admin:
            help_text += (
                "\n<b>Команды администратора:</b>\n"
                "/all — все невыполненные заявки\n"
                "/company_create &lt;название&gt; [код] — создать предприятие\n"
                "/company_list — список предприятий\n"
                "/setstaffchat — установить группу сотрудников (выполнить в группе)\n"
            )
        
        help_text += (
            "\n💡 <b>Совет:</b> Используйте кнопку '/' для быстрого доступа к командам!"
        )
        
        await message.answer(help_text, parse_mode="HTML")

    # Ignore other photos outside states to avoid confusion
    @dp.message(F.content_type == ContentType.PHOTO)
    async def generic_photo(message: Message):
        await message.answer("Чтобы приложить фото к заявке, начните с '🆕 Новая заявка' и следуйте шагам.")


def display_from(cb: CallbackQuery) -> str:
    u = cb.from_user
    name = (u.full_name or u.username or str(u.id)) if u else "Unknown"
    return name


def human_category(code: str) -> str:
    """Преобразует код категории в человекочитаемое название."""
    for title, cat_code in CATEGORIES:
        if cat_code == code:
            return title
    return code


def staff_message_text(issue_row, *, override_assignee: Optional[str] = None, override_status: Optional[str] = None, company_name: Optional[str] = None) -> str:
    status = override_status or issue_row["status"]
    assignee = override_assignee or issue_row["assignee_name"]
    deadline = issue_row["deadline"]
    
    text = (
        f"Заявка #{issue_row['id']}\n"
        f"🏢 Предприятие: {company_name or '—'}\n"
        f"Категория: {human_category(issue_row['category'])}\n"
        f"От: {issue_row['user_name']}\n\n"
        f"Описание:\n{issue_row['description']}\n\n"
        f"Статус: {status}"
    )
    if assignee:
        text += f"\nОтветственный: {assignee}"
    if deadline:
        # Parse deadline to show readable format
        deadline_display = deadline
        try:
            from datetime import datetime
            # Try to parse ISO format
            dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
            deadline_display = dt.strftime('%d.%m.%Y %H:%M')
        except (ValueError, AttributeError):
            # If it's custom text, use as is
            pass
        text += f"\n📅 Срок выполнения: {deadline_display}"
    return text


async def main():
    bot, dp, staff_chat_id = await on_startup()
    # Устанавливаем команды после создания бота (на случай если не установились при старте)
    settings = load_settings()
    await setup_bot_commands(bot, admin_ids=settings.admin_user_ids, staff_chat_id=staff_chat_id)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
