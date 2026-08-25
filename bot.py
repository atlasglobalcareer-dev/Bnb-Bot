"""
Telegram bot: command handlers + alert sending, for the interactive/VPS
deployment mode. Built on python-telegram-bot v21 (async). For the
GitHub Actions one-shot mode (no interactive commands), see scan_once.py
and alert_sender.py instead.
"""
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import settings
from database import Database
from formatting import format_alert

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, db: Database, on_manual_scan=None):
        self.db = db
        self.on_manual_scan = on_manual_scan
        self.app = Application.builder().token(settings.telegram_bot_token).build()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_start))
        self.app.add_handler(CommandHandler("setmcap", self.cmd_setmcap))
        self.app.add_handler(CommandHandler("setscore", self.cmd_setscore))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("scan", self.cmd_scan))
        self.app.add_handler(CommandHandler("watchlist", self.cmd_watchlist))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        self.db.register_chat(
            chat_id,
            settings.min_market_cap_usd,
            settings.max_market_cap_usd,
            settings.min_score_to_alert,
        )
        await update.message.reply_text(
            "👋 BNB Meme Scanner registered for this chat.\n\n"
            f"Chat ID: `{chat_id}` (put this in TELEGRAM_CHAT_ID if you want the "
            "background scanner to push here automatically)\n\n"
            "Commands:\n"
            "/setmcap <min> <max> — set market cap range in USD\n"
            "/setscore <0-100> — minimum score required to alert\n"
            "/status — show current filters and scan stats\n"
            "/scan — trigger an immediate manual scan\n"
            "/watchlist — recent tokens that crossed your score threshold\n\n"
            "⚠️ This is a research tool, not financial advice. Meme coins are "
            "high risk — always do your own due diligence.",
            parse_mode="Markdown",
        )

    async def cmd_setmcap(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        if len(context.args) != 2:
            await update.message.reply_text("Usage: /setmcap <min_usd> <max_usd>")
            return
        try:
            min_mcap, max_mcap = float(context.args[0]), float(context.args[1])
        except ValueError:
            await update.message.reply_text("Both values must be numbers, e.g. /setmcap 50000 2000000")
            return
        if min_mcap >= max_mcap:
            await update.message.reply_text("Min must be less than max.")
            return
        self.db.update_mcap(chat_id, min_mcap, max_mcap)
        await update.message.reply_text(f"✅ Market cap range set: ${min_mcap:,.0f} – ${max_mcap:,.0f}")

    async def cmd_setscore(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        if len(context.args) != 1:
            await update.message.reply_text("Usage: /setscore <0-100>")
            return
        try:
            score = float(context.args[0])
        except ValueError:
            await update.message.reply_text("Score must be a number 0-100.")
            return
        score = max(0, min(100, score))
        self.db.update_min_score(chat_id, score)
        await update.message.reply_text(f"✅ Minimum alert score set to {score}")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        row = self.db.get_chat_settings(chat_id)
        total = self.db.total_pools_scanned()
        if not row:
            await update.message.reply_text("Not registered yet — send /start first.")
            return
        await update.message.reply_text(
            f"📡 Status\n\n"
            f"Market cap range: ${row['min_market_cap_usd']:,.0f} – ${row['max_market_cap_usd']:,.0f}\n"
            f"Min score to alert: {row['min_score_to_alert']}\n"
            f"Total pools evaluated so far: {total}\n"
            f"Poll interval: {settings.poll_interval_seconds}s"
        )

    async def cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🔍 Running a manual scan now...")
        if self.on_manual_scan:
            count = await self.on_manual_scan()
            await update.message.reply_text(f"Done. {count} qualifying token(s) found this pass.")
        else:
            await update.message.reply_text("Manual scan hook not wired up.")

    async def cmd_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        rows = self.db.recent_alerts(limit=10)
        if not rows:
            await update.message.reply_text("No alerts yet.")
            return
        lines = ["📋 Recent alerts:\n"]
        for r in rows:
            lines.append(f"• {r['symbol']} — score {r['score']} — ${r['market_cap_usd']:,.0f} mcap")
        await update.message.reply_text("\n".join(lines))

    async def send_alert(self, chat_id: str, pool, score):
        try:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=format_alert(pool, score),
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
        except Exception:
            logger.exception("Failed to send alert to chat %s", chat_id)
