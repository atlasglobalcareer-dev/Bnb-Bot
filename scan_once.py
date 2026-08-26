"""One-shot GitHub Actions scanner."""
import asyncio
import logging
import sys
from config import settings
from database import Database
from alert_sender import SimpleAlertSender
from scanner import Scanner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("scan_once")

async def main():
    problems = settings.validate()
    if problems:
        for problem in problems:
            logger.error("Config problem: %s", problem)
        sys.exit(1)

    db = Database(settings.database_path)
    alert_sender = SimpleAlertSender(settings.telegram_bot_token)
    scanner = Scanner(db, telegram_bot=alert_sender)
    try:
        destination = await alert_sender.validate_destination(settings.telegram_chat_id)
        logger.info("Telegram configuration OK: chat_id=%s type=%s", destination.id, getattr(destination, "type", "unknown"))
        count = await scanner.run_once()
        logger.info("Scan complete. %d market alert(s) confirmed delivered.", count)
    finally:
        scanner.close()

if __name__ == "__main__":
    asyncio.run(main())
