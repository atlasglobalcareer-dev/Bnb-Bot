"""
The scanning loop: pull new + trending BSC pools, filter by market cap,
score them, and alert registered chats whose thresholds are met.

All blocking network/rate-limit calls (GeckoTerminal, BscScan, honeypot.is)
are offloaded to a worker thread via asyncio.to_thread. Without this, a
throttled scan pass would freeze the event loop and make the Telegram bot
stop responding to commands like /status mid-scan.
"""
import asyncio
import logging
from datasource import GeckoTerminalClient, PoolData
from bscscan import BscScanClient
from honeypot import HoneypotClient
from scoring import score_pool
from database import Database
from config import settings

logger = logging.getLogger(__name__)

ALERT_COOLDOWN_SECONDS = 6 * 3600  # don't re-alert the same pool within 6h


class Scanner:
    def __init__(self, db: Database, telegram_bot=None):
        self.db = db
        self.telegram_bot = telegram_bot
        self.gecko = GeckoTerminalClient(network=settings.network)
        self.bscscan = BscScanClient(settings.bscscan_api_key)
        self.honeypot = HoneypotClient()

    def _candidate_pools(self) -> list[PoolData]:
        pools = []
        try:
            pools.extend(self.gecko.new_pools())
        except Exception:
            logger.exception("Failed to fetch new_pools")
        try:
            pools.extend(self.gecko.trending_pools())
        except Exception:
            logger.exception("Failed to fetch trending_pools")

        # de-dup by pool address
        seen = {}
        for p in pools:
            if p.pool_address:
                seen[p.pool_address] = p
        return list(seen.values())

    def _passes_hard_filters(self, pool: PoolData, min_mcap: float, max_mcap: float) -> bool:
        if pool.market_cap_usd <= 0:
            return False
        if not (min_mcap <= pool.market_cap_usd <= max_mcap):
            return False
        if pool.liquidity_usd < settings.min_liquidity_usd:
            return False
        return True

    def _evaluate_pool_blocking(self, pool: PoolData):
        """Runs the network-bound part of evaluating one pool. Called via
        asyncio.to_thread so rate-limit sleeps never block the event loop."""
        contract = self.bscscan.check_contract(pool.token_address) if pool.token_address else None
        honeypot = self.honeypot.check(pool.token_address, pool.pool_address) if pool.token_address else None
        score = score_pool(
            pool,
            contract,
            min_age_minutes=settings.min_pool_age_minutes,
            max_age_minutes=settings.max_pool_age_minutes,
            honeypot=honeypot,
        )
        return score

    async def run_once(self) -> int:
        """Runs one full scan pass. Returns count of alerts sent."""
        pools = await asyncio.to_thread(self._candidate_pools)
        logger.info("Fetched %d candidate pools", len(pools))

        chats = self.db.all_registered_chats()
        alerts_sent = 0

        for pool in pools:
            # Use the widest configured range across chats as a first-pass filter
            # so we don't waste BscScan/honeypot calls on pools nobody cares about.
            if not chats:
                widest_min, widest_max = settings.min_market_cap_usd, settings.max_market_cap_usd
            else:
                widest_min = min(c["min_market_cap_usd"] for c in chats)
                widest_max = max(c["max_market_cap_usd"] for c in chats)

            if not self._passes_hard_filters(pool, widest_min, widest_max):
                continue

            score = await asyncio.to_thread(self._evaluate_pool_blocking, pool)
            self.db.mark_seen(pool.pool_address, score.total)

            if score.blocked:
                logger.info(
                    "BLOCKED (honeypot): %s reason=%s", pool.base_token_symbol, score.blocked_reason
                )
                continue

            targets = chats or [{
                "chat_id": settings.telegram_chat_id,
                "min_market_cap_usd": settings.min_market_cap_usd,
                "max_market_cap_usd": settings.max_market_cap_usd,
                "min_score_to_alert": settings.min_score_to_alert,
            }]

            for chat in targets:
                if not chat["chat_id"]:
                    continue
                if not (chat["min_market_cap_usd"] <= pool.market_cap_usd <= chat["max_market_cap_usd"]):
                    continue
                if score.total < chat["min_score_to_alert"]:
                    continue
                if self.db.already_alerted_recently(pool.pool_address, ALERT_COOLDOWN_SECONDS):
                    continue

                self.db.record_alert(pool.pool_address, pool.base_token_symbol, score.total, pool.market_cap_usd)
                alerts_sent += 1
                if self.telegram_bot:
                    await self.telegram_bot.send_alert(chat["chat_id"], pool, score)
                logger.info(
                    "ALERT: %s score=%s mcap=%.0f chat=%s",
                    pool.base_token_symbol, score.total, pool.market_cap_usd, chat["chat_id"],
                )

        return alerts_sent

    def close(self):
        self.gecko.close()
        self.bscscan.close()
        self.honeypot.close()
