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
from aiogram.filters import CommandStart, Command

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8099587334:AAHPFtG9QEGxtvdD7W7S6n8Ntc2ng7v1Meo"
ADMIN_ID = 5958249983
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
    logging.info("База данных успешно инициализирована.")

# =====================================================================
# 1. ОБРАБОТЧИКИ ОБЫЧНЫХ СООБЩЕНИЙ (КОГДА ТЫ ПИШЕШЬ В ЛИЧКУ БОТУ)
# =====================================================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Реакция на команду /start внутри диалога с самим ботом"""
    logging.info(f"!!! ПОЛУЧЕНА КОМАНДА /start ОТ ID: {message.from_user.id} !!!")
    
    if message.from_user.id != ADMIN_ID:
        logging.warning(f"Чужой юзер попытался запустить бота! Его ID: {message.from_user.id}")
        return
        
    await message.answer(
        "👋 <b>Привет, создатель!</b>\n\n"
        "Я тебя узнал. Диалог открыт. Теперь я могу беспрепятственно присылать "
        "тебе сюда удаленные и измененные сообщения из твоих чатов!\n\n"
        "Доступные команды:\n"
        "📊 /stats — посмотреть, сколько сообщений сохранено в базе"
    )
    logging.info("Приветственное сообщение успешно отправлено админу.")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает статистику сохраненных сообщений"""
    logging.info(f"Запрос статистики от ID: {message.from_user.id}")
    
    if message.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0

    await message.answer(f"🗄 <b>Статистика базы данных:</b>\nСейчас в памяти сохранено <b>{count}</b> сообщений.")

# =====================================================================
# 2. ОБРАБОТЧИКИ БИЗНЕС-СООБЩЕНИЙ (КОГДА ТЕБЕ ПИШУТ ЛЮДИ)
# =====================================================================

@router.business_message()
async def on_new_business_message(message: Message):
    """Ловим новые бизнес-сообщения и сохраняем в БД"""
    logging.info("Поймано новое бизнес-сообщение. Сохраняю в базу...")
    
    text = message.text or message.caption or "[Медиафайл без текста / Стикер / Голосовое]"
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
    logging.info("Поймано ИЗМЕНЕНИЕ бизнес-сообщения. Обрабатываю...")
    
    new_text = message.text or message.caption or "[Медиафайл / Стикер]"
    sender_name = message.from_user.full_name if message.from_user else "Неизвестный"
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT text FROM messages WHERE chat_id = ? AND message_id = ?",
            (message.chat.id, message.message_id)
        ) as cursor:
            row = await cursor.fetchone()
            
        old_text = row[0] if row else "[Текста не было в базе (возможно сообщение отправлено до запуска бота)]"

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
        logging.info("Уведомление об изменении успешно отправлено админу.")
    except Exception as e:
        logging.error(f"КРИТИЧЕСКАЯ ОШИБКА ОТПРАВКИ (ИЗМЕНЕНИЕ): {e}")

@router.deleted_business_messages()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted, bot: Bot):
    """Ловим удаление бизнес-сообщений и скидываем в ЛС бота"""
    logging.info(f"Поймано УДАЛЕНИЕ бизнес-сообщения. ID сообщений: {deleted.message_ids}")
    
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
                    logging.info("Уведомление об удалении успешно отправлено админу.")
                except Exception as e:
                    logging.error(f"КРИТИЧЕСКАЯ ОШИБКА ОТПРАВКИ (УДАЛЕНИЕ): {e}")

                await db.execute("DELETE FROM messages WHERE chat_id = ? AND message_id = ?", (chat_id, msg_id))
        
        await db.commit()

# =====================================================================
# 3. ЗАГЛУШКА ДЛЯ СЕРВЕРА И ЗАПУСК
# =====================================================================

async def handle_ping(request):
    """Ответ для Render, чтобы он не убил процесс"""
    return web.Response(text="Бот работает и ловит сообщения!")

async def main():
    await init_db()
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    
    # Запускаем фиктивный веб-сервер для Render в фоновом режиме
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    # Запускаем веб-сервер не блокируя бота
    asyncio.create_task(site.start())
    logging.info(f"Веб-заглушка успешно запущена на порту {port}")

    logging.info("Удаляем старые вебхуки...")
    await bot.delete_webhook(drop_pending_updates=True) 
    
    logging.info("Запускаем поллинг бота...")
    # ВАЖНО: Жестко указываем Телеграму присылать ВСЕ типы обновлений
    await dp.start_polling(bot, allowed_updates=[
        "message", 
        "business_connection", 
        "business_message", 
        "edited_business_message", 
        "deleted_business_messages"
    ])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем.")