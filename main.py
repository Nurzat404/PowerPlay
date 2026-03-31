import os
import asyncio
import logging

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from database import init_db
from handlers import start, menu, teams, tournaments, ratings, admin, brackets, stats, match_manual
from middlewares import BanCheckMiddleware, RequiredSubscriptionMiddleware
from utils.notifications import dispatch_due_match_reminders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
REQUIRED_CHANNEL_USERNAME = os.getenv("REQUIRED_CHANNEL_USERNAME", "razryadarena")


def _validate_required_env() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Environment variable BOT_TOKEN is not set")
    if not STEAM_API_KEY:
        raise RuntimeError("Environment variable STEAM_API_KEY is not set")


async def reminder_worker(bot: Bot, interval_seconds: int = 60):
    while True:
        try:
            await dispatch_due_match_reminders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder worker failed")
        await asyncio.sleep(interval_seconds)


async def main():
    _validate_required_env()
    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start.router)
    dp.include_router(menu.router)
    dp.include_router(teams.router)
    dp.include_router(tournaments.router)
    dp.include_router(ratings.router)
    dp.include_router(admin.router)
    dp.include_router(brackets.router)
    dp.include_router(stats.router)
    dp.include_router(match_manual.router)

    dp.message.middleware(BanCheckMiddleware())
    dp.callback_query.middleware(BanCheckMiddleware())
    dp.message.middleware(RequiredSubscriptionMiddleware(REQUIRED_CHANNEL_USERNAME))
    dp.callback_query.middleware(RequiredSubscriptionMiddleware(REQUIRED_CHANNEL_USERNAME))

    await bot.delete_webhook(drop_pending_updates=True)
    reminder_task = asyncio.create_task(reminder_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
