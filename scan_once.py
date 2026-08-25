"""
One-shot entrypoint for GitHub Actions mode.

Does exactly one thing: run a single scan pass, send any qualifying alerts
directly via the Telegram Bot API, then exit. No polling, no scheduler, no
interactive commands — those need a persistent process (see main.py for the
VPS-hosted alternative with full /setmcap, /status, /scan support).

Because there's no /start command to register a chat here, this auto-
registers TELEGRAM_CHAT_ID from the environment using the configured
filters on every run (idempotent — see Database.register_chat).

Exit codes: 0 on a completed pass (even with 0 alerts), 1 on a config
problem, so a bad .env/secrets setup fails loudly in the Actions log
instead of silently doing nothing every run.
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
        count = await scanner.run_once()
        logger.info("Scan complete. %d alert(s) sent.", count)
    finally:
        scanner.close()


if __name__ == "__main__":
    asyncio.run(main())
