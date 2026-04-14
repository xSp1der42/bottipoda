import asyncio
import logging
import os
import html
from datetime import datetime
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, BusinessMessagesDeleted, BusinessConnection, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command

# ================= НАСТРОЙКИ =================

BOT_TOKEN = "8099587334:AAFSKjWSi0CU8gwe1YYDWHa7zIWLX7jTxh0"
ADMIN_ID = 5153531676
DB_NAME = "business_messages.db"
BOT_USERNAME = "@nodelchat_bot"

CHANNELS = ["@xSp1der42", "@neon9_news"]

# Время запуска бота (для вычисления аптайма)
BOT_START_TIME = datetime.now()

# Текст рассылки при КАЖДОМ запуске/рестарте бота.
RESTART_NOTIFY_TEXT = (
    "🔄 <b>В БОТЕ ВЫШЛО ОБНОВЛЕНИЕ!</b>\n\n"
    "⚙️ <b>Чтобы бот продолжил работать и перехватывать сообщения, сделай следующее:</b>\n\n"
    "1️⃣ Зайди в <b>Настройки Telegram</b>\n"
    "2️⃣ Нажми <b>Telegram для бизнеса → Чат-боты</b>\n"
    f"3️⃣ Найди в списке <code>{BOT_USERNAME}</code> и <b>УДАЛИ ЕГО</b> оттуда\n"
    "4️⃣ Подожди 5-10 секунд\n"
    f"5️⃣ В этой же менюшке снова введи <code>{BOT_USERNAME}</code> и нажми <b>Добавить</b>\n"
    "6️⃣ Вернись сюда и напиши мне /start\n\n"
    "⚠️ <i>Если этого не сделать, я не смогу ловить удаленные сообщения!</i>"
)

# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
router = Router()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица для временного хранения сообщений (чтобы было с чем сравнивать)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages_v2 (
                connection_id TEXT,
                chat_id INTEGER,
                message_id INTEGER,
                sender_name TEXT,
                sender_username TEXT,
                text TEXT,
                date INTEGER,
                file_id TEXT,
                content_type TEXT,
                PRIMARY KEY (connection_id, chat_id, message_id)
            )
        """)
        # Таблица активных бизнес-подключений (Premium пользователи)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS business_connections (
                connection_id TEXT PRIMARY KEY,
                user_id INTEGER
            )
        """)
        # Таблица всех пользователей бота
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                first_seen INTEGER
            )
        """)
        # Таблица для глобальной статистики (счетчики перехватов)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_stats (
                stat_name TEXT PRIMARY KEY,
                stat_value INTEGER DEFAULT 0
            )
        """)
        # Инициализируем счетчики, если их нет
        await db.execute("INSERT OR IGNORE INTO bot_stats (stat_name, stat_value) VALUES ('deleted_caught', 0)")
        await db.execute("INSERT OR IGNORE INTO bot_stats (stat_name, stat_value) VALUES ('edited_caught', 0)")
        
        await db.commit()
    logging.info("База данных инициализирована.")


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================

async def inc_stat(stat_name: str):
    """Увеличивает счетчик статистики на 1"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE bot_stats SET stat_value = stat_value + 1 WHERE stat_name = ?", (stat_name,))
        await db.commit()

async def save_user(user_id: int, username: str = "", full_name: str = ""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, first_seen) VALUES (?, ?, ?, ?)",
            (user_id, username or "", full_name or "", int(asyncio.get_event_loop().time()))
        )
        await db.commit()

async def broadcast_restart(bot: Bot):
    if not RESTART_NOTIFY_TEXT:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()

    if not users:
        logging.info("Нет пользователей для рассылки при рестарте.")
        return

    success, failed = 0, 0
    for (user_id,) in users:
        try:
            await bot.send_message(user_id, RESTART_NOTIFY_TEXT)
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            logging.warning(f"Не удалось отправить уведомление о рестарте {user_id}: {e}")

    logging.info(f"Рассылка при рестарте завершена: успешно {success}, ошибка {failed}")

    try:
        await bot.send_message(
            ADMIN_ID,
            f"📬 <b>Рассылка об обновлении завершена</b>\n\n"
            f"✅ Доставлено: <b>{success}</b>\n"
            f"❌ Не доставлено: <b>{failed}</b>\n"
            f"👥 Всего пользователей в БД: <b>{len(users)}</b>"
        )
    except Exception:
        pass

async def check_subscription(bot: Bot, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked', 'banned']:
                return False
        except Exception as e:
            logging.error(f"Ошибка проверки подписки на {channel}: {e}")
            return False
    return True

async def get_owner_id(connection_id: str) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM business_connections WHERE connection_id = ?", (connection_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

def extract_media(message: Message):
    file_id = None
    content_type = message.content_type
    text = message.text or message.caption or ""

    if message.photo:       file_id = message.photo[-1].file_id
    elif message.video:     file_id = message.video.file_id
    elif message.voice:     file_id = message.voice.file_id
    elif message.video_note: file_id = message.video_note.file_id
    elif message.document:  file_id = message.document.file_id
    elif message.sticker:   file_id = message.sticker.file_id
    elif message.animation: file_id = message.animation.file_id
    elif message.audio:     file_id = message.audio.file_id

    return file_id, content_type, text

def content_type_emoji(content_type: str) -> str:
    mapping = {
        "photo": "🖼",
        "video": "🎥",
        "voice": "🎤",
        "video_note": "⭕️",
        "document": "📎",
        "sticker": "🎭",
        "animation": "🎞",
        "audio": "🎵",
        "text": "💬",
    }
    return mapping.get(content_type, "📁")

async def send_media_alert(bot: Bot, target_id: int, file_id: str, content_type: str, caption: str):
    """
    Отправляет 1 сообщение (медиа + текст). 
    Если медиа не поддерживает подпись (кружочки, стикеры), отправляет текст, затем само медиа.
    """
    try:
        # Лимит подписи в Telegram - 1024 символа
        safe_caption = caption if len(caption) <= 1024 else caption[:1020] + "..."

        if file_id:
            if content_type == 'photo':      await bot.send_photo(target_id, file_id, caption=safe_caption)
            elif content_type == 'video':    await bot.send_video(target_id, file_id, caption=safe_caption)
            elif content_type == 'voice':    await bot.send_voice(target_id, file_id, caption=safe_caption)
            elif content_type == 'document': await bot.send_document(target_id, file_id, caption=safe_caption)
            elif content_type == 'animation': await bot.send_animation(target_id, file_id, caption=safe_caption)
            elif content_type == 'audio':    await bot.send_audio(target_id, file_id, caption=safe_caption)
            elif content_type == 'video_note':
                await bot.send_message(target_id, caption)
                await bot.send_video_note(target_id, file_id)
            elif content_type == 'sticker':
                await bot.send_message(target_id, caption)
                await bot.send_sticker(target_id, file_id)
            else:
                await bot.send_message(target_id, f"{caption}\n\n[Неизвестный медиафайл: {content_type}]")
        else:
            await bot.send_message(target_id, caption)
    except Exception as e:
        logging.error(f"Ошибка отправки медиа: {e}")
        await bot.send_message(target_id, f"{caption}\n\n⚠️ <i>[Не удалось загрузить файл или он удален с серверов Telegram]</i>")


# ================= ОБРАБОТЧИКИ ОБЫЧНЫХ СООБЩЕНИЙ =================

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    await save_user(message.from_user.id, message.from_user.username or "", message.from_user.full_name or "")
    is_subbed = await check_subscription(bot, message.from_user.id)

    if not is_subbed:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Канал 1", url="https://t.me/xSp1der42")],
            [InlineKeyboardButton(text="📢 Канал 2", url="https://t.me/neon9_news")]
        ])
        await message.answer(
            "❌ <b>ОШИБКА ДОСТУПА</b>\n\n"
            "Чтобы использовать бота — подпишитесь на наши каналы, затем снова нажмите /start",
            reply_markup=keyboard
        )
        return

    welcome_text = (
        "👋 <b>Привет! Я бот, который спалит всё, что тебе пишут и удаляют.</b>\n\n"
        "Я умею сохранять <b>текст, голосовые, фото, видео, кружочки, стикеры и гифки</b>.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>ИНСТРУКЦИЯ ПО ПОДКЛЮЧЕНИЮ:</b>\n\n"
        "<i>(Нужна подписка Telegram Premium)</i>\n"
        "1️⃣ Зайди в настройки Telegram\n"
        "2️⃣ Выбери <b>«Telegram для бизнеса»</b>\n"
        "3️⃣ Пролистай вниз до <b>«Чат-боты»</b>\n"
        f"4️⃣ В поле ввода напиши <code>{BOT_USERNAME}</code> и нажми <b>«Добавить»</b>\n"
        "5️⃣ Обязательно выбери <b>«Все личные чаты»</b> (кроме избранного) и нажми сохранить!\n\n"
        "✅ <b>Всё! Как только ты это сделаешь, я напишу тебе, что всё заработало.</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔄 <b>ЕСЛИ БОТ ПЕРЕСТАЛ РАБОТАТЬ ИЛИ ВЫШЛО ОБНОВЛЕНИЕ:</b>\n"
        f"Просто удали <code>{BOT_USERNAME}</code> из списка бизнес-ботов, подожди 5 секунд и добавь заново! Это решит 99% проблем.\n\n"
        "⚠️ <i>Помни: я вижу сообщения только ПОСЛЕ подключения. Старые сообщения достать невозможно.</i>"
    )

    if message.from_user.id == ADMIN_ID:
        welcome_text += (
            "\n\n🛠 <b>Команды Админа:</b>\n"
            "📊 /stats — расширенная статистика (для рекламодателей)\n"
            "📢 /sendall [текст] — рассылка ВСЕМ юзерам в базе\n"
            "🔥 /sendactive [текст] — рассылка ТОЛЬКО юзерам с подключенным ботом"
        )

    await message.answer(welcome_text)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Аудитория
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_users = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM business_connections") as cursor:
            active_connections = (await cursor.fetchone())[0]
            
        # Глобальные счетчики перехватов
        async with db.execute("SELECT stat_value FROM bot_stats WHERE stat_name = 'deleted_caught'") as cursor:
            row = await cursor.fetchone()
            deleted_caught = row[0] if row else 0
        async with db.execute("SELECT stat_value FROM bot_stats WHERE stat_name = 'edited_caught'") as cursor:
            row = await cursor.fetchone()
            edited_caught = row[0] if row else 0

    # Вычисление аптайма
    delta = datetime.now() - BOT_START_TIME
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    uptime_str = f"{days}д {hours}ч {minutes}м"

    stats_text = (
        "📈 <b>СТАТИСТИКА БОТА (ПРЕЗЕНТАЦИЯ)</b> 📈\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👥 <b>АУДИТОРИЯ:</b>\n"
        f"├ Всего запусков бота: <b>{total_users}</b>\n"
        f"└ Активных Premium-пользователей: <b>{active_connections}</b>\n"
        "<i>(Люди, которые прямо сейчас используют перехватчик)</i>\n\n"
        "🔥 <b>ЭФФЕКТИВНОСТЬ (ПОЧЕМУ МЫ ИМБА):</b>\n"
        f"├ 🗑 Перехвачено удалённых: <b>{deleted_caught}</b>\n"
        f"├ ✏️ Перехвачено изменённых: <b>{edited_caught}</b>\n"
        "└ 📸 <i>Считываем абсолютно всё: тексты, фото, видео, кружочки, голосовые, стикеры и гифки!</i>\n\n"
        f"⏳ <b>Аптайм бота:</b> {uptime_str}\n\n"
        "💡 <b>Для рекламодателей:</b>\n"
        "Наша аудитория — это исключительно <b>Telegram Premium</b> юзеры. "
        "Это самая активная, вовлеченная и платежеспособная прослойка пользователей Telegram, "
        "которая ценит функционал и контроль над своими переписками."
    )

    await message.answer(stats_text)


@router.message(Command("sendall"))
async def cmd_sendall(message: Message, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return
    
    text = message.html_text.replace("/sendall", "").strip()
    if not text:
        await message.answer("⚠️ Использование: `/sendall Ваш текст`", parse_mode="Markdown")
        return

    await message.answer("⏳ Начинаю рассылку по ВСЕМ пользователям...")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()

    success, failed = 0, 0
    for (user_id,) in users:
        try:
            await bot.send_message(user_id, text)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(f"✅ <b>Рассылка завершена!</b>\n\nУспешно: {success}\nОшибка: {failed}")


@router.message(Command("sendactive"))
async def cmd_sendactive(message: Message, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return
    
    text = message.html_text.replace("/sendactive", "").strip()
    if not text:
        await message.answer("⚠️ Использование: `/sendactive Ваш текст`", parse_mode="Markdown")
        return

    await message.answer("⏳ Начинаю рассылку ТОЛЬКО по Premium-пользователям...")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT DISTINCT user_id FROM business_connections") as cursor:
            users = await cursor.fetchall()

    success, failed = 0, 0
    for (user_id,) in users:
        try:
            await bot.send_message(user_id, text)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(f"✅ <b>Рассылка по активным завершена!</b>\n\nУспешно: {success}\nОшибка: {failed}")


# ================= ОБРАБОТЧИКИ БИЗНЕС-СООБЩЕНИЙ =================

@router.business_connection()
async def on_business_connection(connection: BusinessConnection, bot: Bot):
    await save_user(connection.user.id, connection.user.username or "", connection.user.full_name or "")

    if connection.is_enabled:
        # Пользователь ПОДКЛЮЧИЛ бота
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT OR REPLACE INTO business_connections (connection_id, user_id) VALUES (?, ?)",
                (connection.id, connection.user.id)
            )
            await db.commit()

        try:
            await bot.send_message(
                connection.user.id,
                "✅ <b>УСПЕШНО! Бот подключён к твоему аккаунту.</b>\n\n"
                "Теперь я работаю как шпион 🥷\n"
                "Если кто-то удалит или изменит сообщение, фото, голосовое, кружок или стикер — я сразу пришлю его тебе сюда.\n\n"
                "<i>Чтобы посмотреть настройки, напиши /start</i>"
            )
        except Exception as e:
            logging.error(f"Не удалось отправить приветствие: {e}")

    else:
        # Пользователь ОТКЛЮЧИЛ бота
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM business_connections WHERE connection_id = ?", (connection.id,))
            await db.commit()

        try:
            await bot.send_message(
                connection.user.id,
                "❌ <b>Ты отключил меня от бизнес-аккаунта.</b>\n\n"
                "Окей, окей... Я удалил доступ и больше не слежу за твоими чатами. Удаленные сообщения больше приходить не будут.\n\n"
                "🔁 Если передумаешь и захочешь вернуть ИМБА-функции — просто добавь меня заново в <b>Настройки → Telegram для бизнеса → Чат-боты</b>!"
            )
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение об отключении: {e}")


@router.business_message()
async def on_new_business_message(message: Message, bot: Bot):
    connection_id = message.business_connection_id
    owner_id = await get_owner_id(connection_id)
    if not owner_id or not await check_subscription(bot, owner_id):
        return

    file_id, content_type, text = extract_media(message)
    sender_name = message.from_user.full_name if message.from_user else "Неизвестный"
    sender_username = message.from_user.username if message.from_user and message.from_user.username else ""

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages_v2 (connection_id, chat_id, message_id, sender_name, sender_username, text, date, file_id, content_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (connection_id, message.chat.id, message.message_id, sender_name, sender_username, text, int(message.date.timestamp()), file_id, content_type)
        )
        await db.commit()


@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot):
    connection_id = message.business_connection_id
    owner_id = await get_owner_id(connection_id)
    if not owner_id or not await check_subscription(bot, owner_id):
        return

    new_file_id, new_content_type, new_text = extract_media(message)
    sender_name = message.from_user.full_name if message.from_user else "Неизвестный"
    sender_username = message.from_user.username if message.from_user and message.from_user.username else ""
    author_str = f"{sender_name} (@{sender_username})" if sender_username else sender_name

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT text, file_id, content_type FROM messages_v2 WHERE connection_id = ? AND chat_id = ? AND message_id = ?",
            (connection_id, message.chat.id, message.message_id)
        ) as cursor:
            row = await cursor.fetchone()

        old_text = row[0] if row else "[Текст не был сохранен]"
        old_file_id = row[1] if row else None
        old_content_type = row[2] if row else "text"

        await db.execute(
            "UPDATE messages_v2 SET text = ?, file_id = ?, content_type = ? WHERE connection_id = ? AND chat_id = ? AND message_id = ?",
            (new_text, new_file_id, new_content_type, connection_id, message.chat.id, message.message_id)
        )
        await db.commit()

    # Увеличиваем счетчик измененных сообщений
    await inc_stat("edited_caught")

    safe_old = html.escape(old_text) if old_text else ""
    safe_new = html.escape(new_text) if new_text else ""

    # Если текста слишком много, обрезаем, чтобы не словить ошибку Telegram (Caption too long)
    if len(safe_old) > 400: safe_old = safe_old[:400] + "..."
    if len(safe_new) > 400: safe_new = safe_new[:400] + "..."

    old_emoji = content_type_emoji(old_content_type)
    new_emoji = content_type_emoji(new_content_type)

    # Формируем ОДНО сообщение
    caption = f"✏️ <b>{author_str} ИЗМЕНИЛ(А) СООБЩЕНИЕ:</b>\n\n"

    caption += f"<b>Было {old_emoji}:</b>\n"
    if safe_old:
        caption += f"<blockquote>{safe_old}</blockquote>\n"
    elif old_file_id:
        caption += f"<i>[Медиа: {old_content_type}]</i>\n"
    else:
        caption += "<i>[Пустое сообщение]</i>\n"

    caption += f"\n<b>Стало {new_emoji}:</b>\n"
    if safe_new:
        caption += f"<blockquote>{safe_new}</blockquote>\n"
    elif new_file_id:
        caption += f"<i>[Медиа: {new_content_type}]</i>\n"
    else:
        caption += "<i>[Пустое сообщение]</i>\n"

    caption += f"\n{BOT_USERNAME}"

    # Отправляем 1 итоговое сообщение
    if new_file_id:
        await send_media_alert(bot, owner_id, new_file_id, new_content_type, caption)
    elif old_file_id:
        await send_media_alert(bot, owner_id, old_file_id, old_content_type, caption)
    else:
        await send_media_alert(bot, owner_id, None, "text", caption)


@router.deleted_business_messages()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted, bot: Bot):
    connection_id = deleted.business_connection_id
    owner_id = await get_owner_id(connection_id)
    if not owner_id or not await check_subscription(bot, owner_id):
        return

    chat_id = deleted.chat.id

    async with aiosqlite.connect(DB_NAME) as db:
        for msg_id in deleted.message_ids:
            async with db.execute(
                "SELECT sender_name, sender_username, text, file_id, content_type FROM messages_v2 WHERE connection_id = ? AND chat_id = ? AND message_id = ?",
                (connection_id, chat_id, msg_id)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                sender_name, sender_username, text, file_id, content_type = row
                author_str = f"{sender_name} (@{sender_username})" if sender_username else sender_name
                safe_text = html.escape(text) if text else ""
                emoji = content_type_emoji(content_type)

                caption = f"🗑 <b>{author_str} УДАЛИЛ(А) СООБЩЕНИЕ:</b>\n\n"
                if safe_text:
                    caption += f"{emoji} <blockquote>{safe_text}</blockquote>\n\n"
                elif file_id:
                    caption += f"{emoji} <i>[Удален файл: {content_type}]</i>\n\n"
                caption += f"{BOT_USERNAME}"

                await send_media_alert(bot, owner_id, file_id, content_type, caption)
                
                # Увеличиваем счетчик удаленных
                await inc_stat("deleted_caught")

                await db.execute(
                    "DELETE FROM messages_v2 WHERE connection_id = ? AND chat_id = ? AND message_id = ?",
                    (connection_id, chat_id, msg_id)
                )

        await db.commit()


# ================= СЕРВЕР И ЗАПУСК =================

async def handle_ping(request):
    return web.Response(text="Бот работает!")

async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {port}")

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Запускаем поллинг...")

    # Рассылка всем пользователям об обновлении (рестарте)
    await broadcast_restart(bot)

    try:
        await dp.start_polling(bot, allowed_updates=[
            "message",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages"
        ])
    finally:
        await bot.session.close()
        await runner.cleanup()
        logging.info("Бот остановлен.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Принудительная остановка.")