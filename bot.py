import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import TOKEN
from handlers_user import router as user_router
from handlers_admin import router as admin_router
from db import init_db, migrate_db

init_db()

async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    
    dp = Dispatcher()

    dp.include_router(user_router)
    dp.include_router(admin_router)

    migrate_db()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())