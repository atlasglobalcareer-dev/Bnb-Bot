"""
Entry point. Wires together:
  - the Telegram bot (commands + alert sending)
  - the scanner (polls GeckoTerminal + BscScan, scores, alerts)
  - a background scheduler that runs the scanner on an interval

Run with: python main.py
"""
import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from database import Database
from bot import TelegramBot
from scanner import Scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


async def main():
    problems = settings.validate()
    if problems:
        for p in problems:
            logger.error("Config problem: %s", p)
        logger.error("Fix your .env file (see .env.example) and re-run.")
        sys.exit(1)

    db = Database(settings.database_path)
    telegram_bot = TelegramBot(db)
    scanner = Scanner(db, telegram_bot=telegram_bot)

    async def manual_scan():
        return await scanner.run_once()

    telegram_bot.on_manual_scan = manual_scan

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scanner.run_once,
        "interval",
        seconds=settings.poll_interval_seconds,
        next_run_time=None,  # first run fires after the initial delay below
    )
    scheduler.start()

    async with telegram_bot.app:
        await telegram_bot.app.start()
        await telegram_bot.app.updater.start_polling()
        logger.info(
            "Bot running. Polling BSC every %ss for tokens between $%.0f and $%.0f mcap.",
            settings.poll_interval_seconds,
            settings.min_market_cap_usd,
            settings.max_market_cap_usd,
        )
        try:
            # keep the process alive
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await telegram_bot.app.updater.stop()
            await telegram_bot.app.stop()
            scanner.close()


if __name__ == "__main__":
    asyncio.run(main())
