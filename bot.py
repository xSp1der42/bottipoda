import asyncio
import logging
import os
from datetime import datetime
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, BusinessMessagesDeleted
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command

# ================= НАСТРОЙКИ =================
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

# =====================================================================
# 1. ОБРАБОТЧИКИ ОБЫЧНЫХ СООБЩЕНИЙ (КОГДА ВЫ ПИШЕТЕ САМОМУ БОТУ В ЛС)
# =====================================================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Реакция на кнопку /start внутри самого бота"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("Извините, я работаю только со своим создателем.")
        return
        
    await message.answer(
        "👋 <b>Привет, босс!</b>\n\n"
        "Я успешно запущен и слушаю твои личные переписки через Business API.\n"
        "Теперь я буду скидывать сюда все удаленные и измененные сообщения.\n\n"
        "Команды:\n"
        "📊 /stats — посмотреть, сколько сообщений сохранено в базе"
    )

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает статистику сохраненных сообщений"""
    if message.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0

    await message.answer(f"🗄 <b>Статистика базы данных:</b>\nСейчас в памяти сохранено <b>{count}</b> сообщений.")

# =====================================================================
# 2. ОБРАБОТЧИКИ БИЗНЕС-СООБЩЕНИЙ (КОГДА ВАМ ПИШУТ ВАШИ КОНТАКТЫ)
# =====================================================================

@router.business_message()
async def on_new_business_message(message: Message):
    """Ловим новые бизнес-сообщения и ТИХО сохраняем в БД"""
    text = message.text or message.caption or "[Медиафайл без текста / Стикер]"
    sender_name = message.from_user.full_name if message.from_user else "Неизвестный"
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (chat_id, message_id, sender_name, text, date) VALUES (?, ?, ?, ?, ?)",
            (message.chat.id, message.message_id, sender_name, text, int(message.date.timestamp()))
        )
        await db.commit()

@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot):
    """Ловим изменение бизнес-сообщения и скидываем в ЛС бота"""
    new_text = message.text or message.caption or "[Медиафайл / Стикер]"
    sender_name = message.from_user.full_name if message.from_user else "Неизвестный"
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT text FROM messages WHERE chat_id = ? AND message_id = ?",
            (message.chat.id, message.message_id)
        ) as cursor:
            row = await cursor.fetchone()
            
        old_text = row[0] if row else "[Текста не было в базе]"

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
    
    try:
        await bot.send_message(ADMIN_ID, alert)
    except Exception as e:
        logging.error(f"Не удалось отправить! Скорее всего вы не нажали /start в боте. Ошибка: {e}")

@router.business_messages_deleted()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted, bot: Bot):
    """Ловим удаление бизнес-сообщений и скидываем в ЛС бота"""
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
                    f"🗑 <b>УДАЛЕНО СООБЩЕНИЕ!</b>\n"
                    f"👤 <b>От:</b> {sender_name}\n"
                    f"📝 <b>Текст:</b> {text}\n"
                    f"🕐 <b>Было написано:</b> {dt_str}"
                )
                try:
                    await bot.send_message(ADMIN_ID, alert)
                except Exception as e:
                    logging.error(f"Не удалось отправить! Нажмите /start в боте. Ошибка: {e}")

                await db.execute("DELETE FROM messages WHERE chat_id = ? AND message_id = ?", (chat_id, msg_id))
        
        await db.commit()

# =====================================================================
# 3. ЗАГЛУШКА ДЛЯ СЕРВЕРА И ЗАПУСК
# =====================================================================

async def handle_ping(request):
    return web.Response(text="Бот работает!")

async def main():
    await init_db()
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    
    # Запускаем фиктивный веб-сервер
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    logging.info("Бот запущен!")
    await bot.delete_webhook(drop_pending_updates=True) 
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен.")