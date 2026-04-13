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

BOT_TOKEN = "8099587334:AAHPFtG9QEGxtvdD7W7S6n8Ntc2ng7v1Meo"
ADMIN_ID = 5153531676
DB_NAME = "business_messages.db"
BOT_USERNAME = "@nodelchat_bot"

# Каналы для обязательной подписки (БОТ ДОЛЖЕН БЫТЬ АДМИНОМ В ЭТИХ КАНАЛАХ!)
CHANNELS = ["@xSp1der42", "@neon9_news"]

# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
router = Router()

async def init_db():
    """Инициализация базы данных SQLite с полной изоляцией пользователей"""
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
    logging.info("База данных успешно инициализирована.")


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================

async def check_subscription(bot: Bot, user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на обязательные каналы."""
    if user_id == ADMIN_ID:
        return True # Админу можно всё

    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked', 'banned']:
                return False
        except Exception as e:
            logging.error(f"Ошибка проверки подписки на {channel} для {user_id}. Бот админ в канале?: {e}")
            return False 
            
    return True

async def get_owner_id(connection_id: str) -> int:
    """Получает ID владельца бизнес-аккаунта по ID подключения."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM business_connections WHERE connection_id = ?", (connection_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

def extract_media(message: Message):
    """Извлекает file_id, тип контента и текст/данные из ЛЮБОГО сообщения для сохранения в БД."""
    file_id = None
    content_type = message.content_type
    text = message.text or message.caption or ""

    if message.photo: file_id = message.photo[-1].file_id
    elif message.video: file_id = message.video.file_id
    elif message.voice: file_id = message.voice.file_id
    elif message.video_note: file_id = message.video_note.file_id
    elif message.document: file_id = message.document.file_id
    elif message.sticker: file_id = message.sticker.file_id
    elif message.animation: file_id = message.animation.file_id
    elif message.audio: file_id = message.audio.file_id
    
    # Сохраняем специфичные форматы (Контакты, Локации, Опросы) в виде текста
    elif message.contact: 
        text = f"📱 Контакт: {message.contact.first_name} ({message.contact.phone_number})"
    elif message.location: 
        text = f"📍 Локация: {message.location.latitude}, {message.location.longitude}"
    elif message.poll: 
        text = f"📊 Опрос: {message.poll.question}"
    elif message.dice: 
        text = f"🎲 Эмодзи: {message.dice.emoji} (Выпало: {message.dice.value})"
    elif message.story:
        text = f"📖 [Пользователь отправил Историю (Story)]"

    return file_id, content_type, text

async def send_media_alert(bot: Bot, target_id: int, caption: str, file_id: str, content_type: str):
    """
    Умная отправка медиа. 
    Голосовые, фото, видео - отправляются ОДНИМ сообщением.
    Стикеры и кружочки - отправляются через Reply (так как TG не поддерживает текст внутри них).
    """
    try:
        # Если медиафайла нет, просто шлем текст
        if not file_id:
            await bot.send_message(target_id, caption)
            return

        # Если текст больше 1024 символов (лимит телеграма для медиа), мы вынуждены разделить
        if len(caption) > 1024 and content_type not in ['video_note', 'sticker']:
            sent_msg = await bot.send_message(target_id, caption)
            caption = "" # Очищаем текст, так как уже отправили его
            reply_id = sent_msg.message_id
        else:
            reply_id = None

        if content_type == 'photo': 
            await bot.send_photo(target_id, file_id, caption=caption, reply_to_message_id=reply_id)
        elif content_type == 'video': 
            await bot.send_video(target_id, file_id, caption=caption, reply_to_message_id=reply_id)
        elif content_type == 'voice': 
            await bot.send_voice(target_id, file_id, caption=caption, reply_to_message_id=reply_id)
        elif content_type == 'document': 
            await bot.send_document(target_id, file_id, caption=caption, reply_to_message_id=reply_id)
        elif content_type == 'animation': 
            await bot.send_animation(target_id, file_id, caption=caption, reply_to_message_id=reply_id)
        elif content_type == 'audio': 
            await bot.send_audio(target_id, file_id, caption=caption, reply_to_message_id=reply_id)
        elif content_type in ['video_note', 'sticker']:
            # Телеграм физически не поддерживает текст для стикеров и кружочков. Делаем связку через Reply
            sent_msg = await bot.send_message(target_id, caption)
            if content_type == 'video_note':
                await bot.send_video_note(target_id, file_id, reply_to_message_id=sent_msg.message_id)
            else:
                await bot.send_sticker(target_id, file_id, reply_to_message_id=sent_msg.message_id)
        else:
            await bot.send_message(target_id, f"{caption}\n\n[Медиафайл формата {content_type}]")
            
    except Exception as e:
        logging.error(f"Ошибка отправки файла: {e}")
        await bot.send_message(target_id, f"{caption}\n\n⚠️ <i>[Не удалось загрузить сам файл, возможно он полностью удален с серверов Telegram]</i>")


# ================= 1. ОБРАБОТЧИКИ ОБЫЧНЫХ СООБЩЕНИЙ =================

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    """Реакция на команду /start внутри диалога с самим ботом"""
    is_subbed = await check_subscription(bot, message.from_user.id)
    
    if not is_subbed:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Канал 1", url="https://t.me/xSp1der42")],
            [InlineKeyboardButton(text="📢 Канал 2", url="https://t.me/neon9_news")]
        ])
        await message.answer(
            "❌ <b>ОШИБКА ДОСТУПА</b>\n\n"
            "Чтобы использовать этого бота и сохранять сообщения, вы <b>ОБЯЗАНЫ</b> быть подписанными на наши каналы.\n\n"
            "Подпишитесь, а затем снова нажмите /start",
            reply_markup=keyboard
        )
        return

    welcome_text = (
        "👋 <b>Привет! Я работаю и готов сохранять сообщения.</b>\n\n"
        "Теперь я буду присылать тебе сюда удаленные и измененные сообщения ТОЛЬКО из твоих чатов (включая фото, ГС, видео и стикеры)!\n\n"
        "<i>Если ты отпишешься от обязательных каналов — я автоматически перестану работать.</i>"
    )
    
    if message.from_user.id == ADMIN_ID:
        welcome_text += "\n\n🛠 <b>Команды Админа:</b>\n📊 /stats — расширенная статистика"
        
    await message.answer(welcome_text)

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает подробную статистику (Только для Админа)"""
    if message.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM messages_v2") as cursor:
            total_msgs = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM messages_v2 WHERE file_id IS NOT NULL") as cursor:
            media_msgs = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM business_connections") as cursor:
            total_users = (await cursor.fetchone())[0]

    text_msgs = total_msgs - media_msgs

    await message.answer(
        f"🗄 <b>РАСШИРЕННАЯ СТАТИСТИКА БОТА:</b>\n\n"
        f"👤 Всего пользователей подключило бота: <b>{total_users}</b>\n\n"
        f"💬 <b>Всего сохранено сообщений в БД: {total_msgs}</b>\n"
        f"├ 📝 Текстовых: <b>{text_msgs}</b>\n"
        f"└ 📸 С медиафайлами/ГС: <b>{media_msgs}</b>"
    )


# ================= 2. ОБРАБОТЧИКИ БИЗНЕС-СООБЩЕНИЙ =================

@router.business_connection()
async def on_business_connection(connection: BusinessConnection):
    """Ловим подключение бота к бизнес-аккаунту пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO business_connections (connection_id, user_id) VALUES (?, ?)",
            (connection.id, connection.user.id)
        )
        await db.commit()
    logging.info(f"Новое бизнес-подключение: {connection.id} от пользователя {connection.user.id}")

@router.business_message()
async def on_new_business_message(message: Message, bot: Bot):
    """Ловим новые бизнес-сообщения и сохраняем в БД (Текст, Медиа, Файлы, ГС)"""
    connection_id = message.business_connection_id
    owner_id = await get_owner_id(connection_id)
    
    if not owner_id:
        return
        
    if not await check_subscription(bot, owner_id):
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
    """Ловим изменение и скидываем в ЛС ВЛАДЕЛЬЦУ ИМЕННО ЭТОГО АККАУНТА ОДНИМ СООБЩЕНИЕМ"""
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
        
        old_text = row[0] if row else ""
        old_file_id = row[1] if row else None
        old_content_type = row[2] if row else "text"

        await db.execute(
            "UPDATE messages_v2 SET text = ?, file_id = ?, content_type = ? WHERE connection_id = ? AND chat_id = ? AND message_id = ?",
            (new_text, new_file_id, new_content_type, connection_id, message.chat.id, message.message_id)
        )
        await db.commit()

    safe_old_text = html.escape(old_text) if old_text else "<i>[Без текста/Только медиа]</i>"
    safe_new_text = html.escape(new_text) if new_text else "<i>[Без текста/Только медиа]</i>"

    # Игнорируем пустые изменения (иногда ТГ присылает ложные апдейты)
    if old_text == new_text and old_file_id == new_file_id:
        return

    # Собираем все в один красивый текст (одно сообщение)
    caption = (
        f"✏️ <b>{author_str} ИЗМЕНИЛ(А) СООБЩЕНИЕ:</b>\n\n"
        f"<b>❌ Было:</b>\n<blockquote>{safe_old_text}</blockquote>\n"
        f"<b>✅ Стало:</b>\n<blockquote>{safe_new_text}</blockquote>\n\n"
        f"{BOT_USERNAME}"
    )

    # Отправляем одним сообщением (Текст "было/стало" + Прикрепленный старый медиафайл)
    await send_media_alert(bot, owner_id, caption, old_file_id, old_content_type)


@router.deleted_business_messages()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted, bot: Bot):
    """Ловим удаление и скидываем в ЛС ВЛАДЕЛЬЦУ ИМЕННО ЭТОГО АККАУНТА (текст + сам файл)"""
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
                
                safe_text = html.escape(text) if text else "<i>[Голосовое, стикер или медиа без текста]</i>"
                
                caption = f"🗑 <b>{author_str} УДАЛИЛ(А) СООБЩЕНИЕ:</b>\n\n"
                caption += f"<blockquote>{safe_text}</blockquote>\n\n"
                caption += f"{BOT_USERNAME}"
                
                # Отправляем одним сообщением (Удаленный медиафайл + Текст под ним)
                await send_media_alert(bot, owner_id, caption, file_id, content_type)

                # Удаляем из БД чтобы не засорять память
                await db.execute("DELETE FROM messages_v2 WHERE connection_id = ? AND chat_id = ? AND message_id = ?", (connection_id, chat_id, msg_id))
        
        await db.commit()


# ================= 3. ЗАГЛУШКА ДЛЯ СЕРВЕРА И ЗАПУСК =================

async def handle_ping(request):
    """Ответ для Render, чтобы он не убил процесс"""
    return web.Response(text="Бот работает, сообщения изолированы, медиа сохраняются в БД!")

async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    # Настройка веб-сервера для Render
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)

    # Запускаем веб-сервер
    await site.start()
    logging.info(f"Веб-заглушка успешно запущена на порту {port}")

    # Удаляем вебхуки, чтобы поллинг работал
    logging.info("Удаляем старые вебхуки...")
    await bot.delete_webhook(drop_pending_updates=True) 

    logging.info("Запускаем поллинг бота...")
    try:
        # ВАЖНО: Жестко указываем Телеграму присылать ВСЕ типы обновлений
        await dp.start_polling(bot, allowed_updates=[
            "message", 
            "business_connection", 
            "business_message", 
            "edited_business_message", 
            "deleted_business_messages"
        ])
    finally:
        # ПРАВИЛЬНОЕ ЗАВЕРШЕНИЕ РАБОТЫ
        logging.info("Остановка бота... Очистка соединений...")
        await bot.session.close()
        await runner.cleanup()
        logging.info("Все соединения закрыты.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот принудительно остановлен.")