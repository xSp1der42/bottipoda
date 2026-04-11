import asyncio
import logging
import os
from datetime import datetime
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, BusinessMessagesDeleted
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ================= НАСТРОЙКИ =================
# Вставьте сюда НОВЫЙ токен от @BotFather (старый скомпрометирован!)
BOT_TOKEN = "8099587334:AAEiprR86Iavdkx-6CGuPLKh0yP1WMr_Jp0"
ADMIN_ID = 5958249983 # Ваш ID
DB_NAME = "business_messages.db"
# =============================================

logging.basicConfig(level=logging.INFO)
router = Router()

async def init_db():
    """Инициализация базы данных SQLite"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER,
                message_id INTEGER,
                sender_name TEXT,
                text TEXT,
                date INTEGER,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        await db.commit()

@router.business_message()
async def on_new_business_message(message: Message):
    """Ловим новые входящие/исходящие бизнес-сообщения и сохраняем в БД"""
    text = message.text or message.caption or "[Медиафайл без текста]"
    sender_name = message.from_user.full_name if message.from_user else "Неизвестный"
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (chat_id, message_id, sender_name, text, date) VALUES (?, ?, ?, ?, ?)",
            (message.chat.id, message.message_id, sender_name, text, int(message.date.timestamp()))
        )
        await db.commit()

@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot):
    """Ловим изменение бизнес-сообщения"""
    new_text = message.text or message.caption or "[Медиафайл без текста]"
    sender_name = message.from_user.full_name if message.from_user else "Неизвестный"
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT text FROM messages WHERE chat_id = ? AND message_id = ?",
            (message.chat.id, message.message_id)
        ) as cursor:
            row = await cursor.fetchone()
            
        old_text = row[0] if row else "[Текст не найден в базе данных]"

        await db.execute(
            "UPDATE messages SET text = ? WHERE chat_id = ? AND message_id = ?",
            (new_text, message.chat.id, message.message_id)
        )
        await db.commit()

    alert = (
        f"✏️ <b>Изменено сообщение от:</b> {sender_name}\n\n"
        f"❌ <b>Было:</b>\n{old_text}\n\n"
        f"✅ <b>Стало:</b>\n{new_text}"
    )
    # Отправляем уведомление в избранное (ADMIN_ID)
    try:
        await bot.send_message(ADMIN_ID, alert)
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление об изменении: {e}")

# ИСПРАВЛЕНО: Правильное название декоратора в aiogram 3 - business_messages_deleted
@router.business_messages_deleted()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted, bot: Bot):
    """Ловим удаление бизнес-сообщений"""
    chat_id = deleted.chat.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        for msg_id in deleted.message_ids:
            async with db.execute(
                "SELECT sender_name, text, date FROM messages WHERE chat_id = ? AND message_id = ?",
                (chat_id, msg_id)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                sender_name, text, timestamp = row
                dt_str = datetime.fromtimestamp(timestamp).strftime('%d.%m.%Y %H:%M:%S')

                alert = (
                    f"🗑 <b>Удалил:</b> {sender_name}\n"
                    f"📝 <b>Текст:</b> {text}\n"
                    f"🕐 <b>Время:</b> {dt_str}"
                )
                try:
                    await bot.send_message(ADMIN_ID, alert)
                except Exception as e:
                    logging.error(f"Не удалось отправить уведомление об удалении: {e}")

                await db.execute("DELETE FROM messages WHERE chat_id = ? AND message_id = ?", (chat_id, msg_id))
        
        await db.commit()

async def handle_ping(request):
    """Заглушка для Render / серверов, требующих веб-порт"""
    return web.Response(text="Бот работает!")

async def main():
    await init_db()
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    
    # ---------------------------------------------------------
    # Запускаем фиктивный веб-сервер для Render
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    # ---------------------------------------------------------

    logging.info("Бот запущен и готов ловить бизнес-сообщения!")
    # Удаляем вебхуки, чтобы поллинг работал корректно
    await bot.delete_webhook(drop_pending_updates=True) 
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен.")