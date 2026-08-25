"""
Minimal alert sender for the GitHub Actions one-shot mode. Unlike bot.py's
TelegramBot, this does NOT start an Application or long-polling — it just
wraps a plain `telegram.Bot` instance to send one message and exit. That
matters here specifically: Actions jobs are finite, so nothing in this path
should try to stay alive listening for updates.

Exposes the same `send_alert(chat_id, pool, score)` interface that
scanner.Scanner expects, so scanner.py doesn't need to know which mode it's
running in.
"""
import logging
from telegram import Bot
from formatting import format_alert

logger = logging.getLogger(__name__)


class SimpleAlertSender:
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token)

    async def send_alert(self, chat_id: str, pool, score):
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=format_alert(pool, score),
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
        except Exception:
            logger.exception("Failed to send alert to chat %s", chat_id)
