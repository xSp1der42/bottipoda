import asyncio
import logging
import os
import html
from datetime import datetime
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, BusinessMessagesDeleted, BusinessConnection, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command

# ================= НАСТРОЙКИ =================

BOT_TOKEN = "8099587334:AAH_QvFyc_8d1Y5_5_D3r9lXoyL3L7hNLFE"
ADMIN_ID = 5153531676
DB_NAME = "business_messages.db"
BOT_USERNAME = "@nodelchat_bot"

CHANNELS = ["@xSp1der42", "@neon9_news"]

# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
router = Router()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS business_connections (
                connection_id TEXT PRIMARY KEY,
                user_id INTEGER
            )
        """)
        await db.commit()
    logging.info("База данных инициализирована.")


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================

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

async def send_media_alert(bot: Bot, target_id: int, text: str, file_id: str, content_type: str, caption: str):
    try:
        if file_id:
            if content_type == 'photo':      await bot.send_photo(target_id, file_id, caption=caption)
            elif content_type == 'video':    await bot.send_video(target_id, file_id, caption=caption)
            elif content_type == 'voice':    await bot.send_voice(target_id, file_id, caption=caption)
            elif content_type == 'video_note':
                await bot.send_message(target_id, caption)
                await bot.send_video_note(target_id, file_id)
            elif content_type == 'document': await bot.send_document(target_id, file_id, caption=caption)
            elif content_type == 'sticker':
                await bot.send_message(target_id, caption)
                await bot.send_sticker(target_id, file_id)
            elif content_type == 'animation': await bot.send_animation(target_id, file_id, caption=caption)
            elif content_type == 'audio':    await bot.send_audio(target_id, file_id, caption=caption)
            else: await bot.send_message(target_id, f"{caption}\n\n[Медиафайл: {content_type}]")
        else:
            await bot.send_message(target_id, caption)
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        await bot.send_message(target_id, f"{caption}\n\n⚠️ <i>[Файл удалён с серверов Telegram]</i>")


# ================= ОБРАБОТЧИКИ ОБЫЧНЫХ СООБЩЕНИЙ =================

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
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
        "👋 <b>Привет! Я слежу за твоими диалогами.</b>\n\n"
        "Я буду присылать тебе сюда <b>удалённые и изменённые</b> сообщения из твоих чатов — включая фото, голосовые, кружочки, стикеры, гифки и видео.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>КАК ПОДКЛЮЧИТЬ БОТА:</b>\n\n"
        "1️⃣ Открой <b>Telegram → Настройки</b>\n"
        "2️⃣ Зайди в <b>«Telegram для бизнеса»</b>\n"
        "3️⃣ Нажми <b>«Чат-боты»</b>\n"
        f"4️⃣ В поле ввода напиши <code>{BOT_USERNAME}</code> и нажми <b>«Добавить»</b>\n"
        "5️⃣ Поставь галочку <b>«Все личные чаты»</b> и сохрани\n\n"
        "✅ Готово! Бот начнёт работать сразу.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔄 <b>ЕСЛИ БОТ НЕ РАБОТАЕТ / ОБНОВИЛСЯ:</b>\n\n"
        "1️⃣ Зайди в <b>Настройки → Telegram для бизнеса → Чат-боты</b>\n"
        f"2️⃣ Удали <code>{BOT_USERNAME}</code> из списка\n"
        "3️⃣ Подожди 5 секунд\n"
        f"4️⃣ Снова добавь <code>{BOT_USERNAME}</code>\n"
        "5️⃣ Напиши мне /start ещё раз\n\n"

        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>ВАЖНО:</b> Бот видит только те сообщения, которые пришли <b>после</b> подключения. "
        "Старые сообщения не сохраняются.\n\n"
        "❓ Если что-то не работает — просто переподключи бота по инструкции выше."
    )

    if message.from_user.id == ADMIN_ID:
        welcome_text += "\n\n🛠 <b>Команды Админа:</b>\n📊 /stats — статистика"

    await message.answer(welcome_text)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM messages_v2") as cursor:
            total_msgs = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM messages_v2 WHERE file_id IS NOT NULL") as cursor:
            media_msgs = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM business_connections") as cursor:
            total_users = (await cursor.fetchone())[0]

    await message.answer(
        f"🗄 <b>СТАТИСТИКА:</b>\n\n"
        f"👤 Пользователей: <b>{total_users}</b>\n"
        f"💬 Всего сообщений в БД: <b>{total_msgs}</b>\n"
        f"├ 📝 Текстовых: <b>{total_msgs - media_msgs}</b>\n"
        f"└ 📸 С медиа: <b>{media_msgs}</b>"
    )


# ================= ОБРАБОТЧИКИ БИЗНЕС-СООБЩЕНИЙ =================

@router.business_connection()
async def on_business_connection(connection: BusinessConnection, bot: Bot):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO business_connections (connection_id, user_id) VALUES (?, ?)",
            (connection.id, connection.user.id)
        )
        await db.commit()

    # Уведомляем пользователя что бот успешно подключён
    try:
        await bot.send_message(
            connection.user.id,
            "✅ <b>Бот успешно подключён к твоему аккаунту!</b>\n\n"
            "Теперь я буду перехватывать удалённые и изменённые сообщения из твоих чатов.\n\n"
            "Напиши /start чтобы увидеть полную инструкцию."
        )
    except Exception as e:
        logging.error(f"Не удалось отправить приветствие после подключения: {e}")

    logging.info(f"Бизнес-подключение: {connection.id} от {connection.user.id}")


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

        old_text = row[0] if row else "[не было в базе]"
        old_file_id = row[1] if row else None
        old_content_type = row[2] if row else "text"

        await db.execute(
            "UPDATE messages_v2 SET text = ?, file_id = ?, content_type = ? WHERE connection_id = ? AND chat_id = ? AND message_id = ?",
            (new_text, new_file_id, new_content_type, connection_id, message.chat.id, message.message_id)
        )
        await db.commit()

    safe_old = html.escape(old_text) if old_text else ""
    safe_new = html.escape(new_text) if new_text else ""
    old_emoji = content_type_emoji(old_content_type)
    new_emoji = content_type_emoji(new_content_type)

    # Одно сообщение с обеими версиями
    caption = f"✏️ <b>{author_str} ИЗМЕНИЛ(А) СООБЩЕНИЕ:</b>\n\n"

    caption += f"<b>Было {old_emoji}:</b>\n"
    if safe_old:
        caption += f"<blockquote>{safe_old}</blockquote>\n"
    elif old_file_id:
        caption += f"<i>[{old_content_type}]</i>\n"
    else:
        caption += "<i>[пусто]</i>\n"

    caption += f"\n<b>Стало {new_emoji}:</b>\n"
    if safe_new:
        caption += f"<blockquote>{safe_new}</blockquote>\n"
    elif new_file_id:
        caption += f"<i>[{new_content_type}]</i>\n"
    else:
        caption += "<i>[пусто]</i>\n"

    caption += f"\n{BOT_USERNAME}"

    # Если новая версия содержит медиа — отправляем с ним
    # Если нет — пробуем со старым медиа
    if new_file_id:
        await send_media_alert(bot, owner_id, safe_new, new_file_id, new_content_type, caption)
    elif old_file_id:
        await send_media_alert(bot, owner_id, safe_old, old_file_id, old_content_type, caption)
    else:
        await bot.send_message(owner_id, caption)


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
                    caption += f"{emoji} <i>[{content_type}]</i>\n\n"
                caption += f"{BOT_USERNAME}"

                await send_media_alert(bot, owner_id, safe_text, file_id, content_type, caption)

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