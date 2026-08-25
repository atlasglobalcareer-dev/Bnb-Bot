"""Telegram alert sender for GitHub Actions."""
import logging
from telegram import Bot
from formatting import format_alert

logger = logging.getLogger(__name__)


class SimpleAlertSender:
    def __init__(self, bot_token: str):
        if not bot_token or not bot_token.strip():
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        self.bot = Bot(token=bot_token.strip())

    async def validate_destination(self, chat_id: str):
        """Fail loudly if the bot token or destination chat is inaccessible."""
        if not chat_id or not str(chat_id).strip():
            raise RuntimeError("TELEGRAM_CHAT_ID is not configured")
        me = await self.bot.get_me()
        chat = await self.bot.get_chat(chat_id=str(chat_id).strip())
        logger.info(
            "Telegram destination validated: bot=@%s chat_id=%s chat_type=%s",
            me.username,
            chat.id,
            getattr(chat, "type", "unknown"),
        )
        return chat

    async def send_alert(self, chat_id: str, pool, score) -> bool:
        try:
            if not chat_id or not str(chat_id).strip():
                raise RuntimeError("TELEGRAM_CHAT_ID is not configured")
            message = await self.bot.send_message(
                chat_id=str(chat_id).strip(),
                text=format_alert(pool, score),
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
            logger.info(
                "Telegram delivery confirmed: message_id=%s chat_id=%s",
                message.message_id,
                chat_id,
            )
            return True
        except Exception:
            logger.exception("Telegram delivery failed for chat %s", chat_id)
            return False
