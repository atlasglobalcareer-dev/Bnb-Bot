"""
One-shot entrypoint for GitHub Actions mode.

Runs one scan pass after validating the configured Telegram bot and destination
chat. Telegram failures are surfaced clearly instead of allowing a green job
to imply that alerts were delivered.
"""
import asyncio
import logging
import sys

from config import settings
from database import Database
from alert_sender import SimpleAlertSender
from scanner import Scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scan_once")


async def main():
    problems = settings.validate()
    if problems:
        for p in problems:
            logger.error("Config problem: %s", p)
        sys.exit(1)

    db = Database(settings.database_path)
    db.register_chat(
        settings.telegram_chat_id,
        settings.min_market_cap_usd,
        settings.max_market_cap_usd,
        settings.min_score_to_alert,
    )

    alert_sender = SimpleAlertSender(settings.telegram_bot_token)
    scanner = Scanner(db, telegram_bot=alert_sender)

    try:
        # Validate the exact bot token + destination before spending time on a scan.
        await alert_sender.validate_destination(settings.telegram_chat_id)
        logger.info("Telegram configuration OK. Starting token scan.")

        count = await scanner.run_once()
        logger.info("Scan complete. %d alert(s) confirmed delivered.", count)
    finally:
        scanner.close()


if __name__ == "__main__":
    asyncio.run(main())
