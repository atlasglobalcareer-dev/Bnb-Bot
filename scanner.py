"""
Scanner orchestration.

Product rule: only BNB tokens below the configured hard $50K market-cap
ceiling are candidates. Liquidity and other signals are audited/scored rather
than silently filtering the token out before the audit is visible.
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
ALERT_COOLDOWN_SECONDS = 6 * 3600


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

        seen = {}
        for p in pools:
            if p.pool_address:
                seen[p.pool_address] = p
        return list(seen.values())

    def _passes_hard_filters(self, pool: PoolData, min_mcap: float, max_mcap: float) -> bool:
        # Market cap is the only economic hard filter at discovery time.
        # Liquidity is deliberately audited and scored so the Telegram alert
        # can show exactly how strong/weak the pool is.
        if pool.market_cap_usd <= 0:
            return False
        return min_mcap <= pool.market_cap_usd < max_mcap

    def _evaluate_pool_blocking(self, pool: PoolData):
        contract = self.bscscan.check_contract(pool.token_address) if pool.token_address else None
        honeypot = self.honeypot.check(pool.token_address, pool.pool_address) if pool.token_address else None
        return score_pool(
            pool,
            contract,
            min_age_minutes=settings.min_pool_age_minutes,
            max_age_minutes=settings.max_pool_age_minutes,
            honeypot=honeypot,
        )

    async def run_once(self) -> int:
        pools = await asyncio.to_thread(self._candidate_pools)
        logger.info("Fetched %d candidate pools", len(pools))
        chats = self.db.all_registered_chats()
        alerts_sent = 0

        for pool in pools:
            if not chats:
                widest_min, widest_max = settings.min_market_cap_usd, settings.max_market_cap_usd
            else:
                widest_min = min(c["min_market_cap_usd"] for c in chats)
                widest_max = min(max(c["max_market_cap_usd"] for c in chats), settings.max_market_cap_usd)

            if not self._passes_hard_filters(pool, widest_min, widest_max):
                continue

            score = await asyncio.to_thread(self._evaluate_pool_blocking, pool)
            self.db.mark_seen(pool.pool_address, score.total)

            # Confirmed honeypots remain the one hard security block.
            if score.blocked:
                logger.info("BLOCKED (honeypot): %s reason=%s", pool.base_token_symbol, score.blocked_reason)
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
                # Never allow a chat-specific setting to bypass the $50K product ceiling.
                chat_max = min(float(chat["max_market_cap_usd"]), settings.max_market_cap_usd)
                if not (float(chat["min_market_cap_usd"]) <= pool.market_cap_usd < chat_max):
                    continue
                if score.total < float(chat["min_score_to_alert"]):
                    continue
                if self.db.already_alerted_recently(pool.pool_address, ALERT_COOLDOWN_SECONDS):
                    continue

                self.db.record_alert(pool.pool_address, pool.base_token_symbol, score.total, pool.market_cap_usd)
                alerts_sent += 1
                if self.telegram_bot:
                    await self.telegram_bot.send_alert(chat["chat_id"], pool, score)
                logger.info("ALERT: %s score=%s mcap=%.0f chat=%s", pool.base_token_symbol, score.total, pool.market_cap_usd, chat["chat_id"])

        return alerts_sent

    def close(self):
        self.gecko.close()
        self.bscscan.close()
        self.honeypot.close()
