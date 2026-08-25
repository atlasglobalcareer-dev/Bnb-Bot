"""Production BNB micro-cap scanner: real MC + liquidity + activity + momentum."""
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
    def __init__(self, db, telegram_bot=None):
        self.db = db
        self.telegram_bot = telegram_bot
        self.gecko = GeckoTerminalClient(network=settings.network)
        self.bscscan = BscScanClient(settings.bscscan_api_key)
        self.honeypot = HoneypotClient()

    def _candidate_pools(self):
        pools = []
        for fetch in (self.gecko.new_pools, self.gecko.trending_pools):
            try:
                pools.extend(fetch())
            except Exception:
                logger.exception("Failed to fetch candidate pools")
        return list({p.pool_address: p for p in pools if p.pool_address}.values())

    def _evaluate(self, pool):
        contract = self.bscscan.check_contract(pool.token_address) if pool.token_address else None
        honeypot = self.honeypot.check(pool.token_address, pool.pool_address) if pool.token_address else None
        score = score_pool(pool, contract, min_age_minutes=settings.min_pool_age_minutes,
                           max_age_minutes=settings.max_pool_age_minutes, honeypot=honeypot)
        score.honeypot_checked = bool(honeypot and honeypot.checked)
        score.is_honeypot = honeypot.is_honeypot if honeypot else None
        score.buy_tax = honeypot.buy_tax if honeypot else None
        score.sell_tax = honeypot.sell_tax if honeypot else None
        score.honeypot_reason = honeypot.simulation_error if honeypot else None
        score.contract_available = bool(contract and contract.available)
        score.contract_verified = contract.is_verified if contract and contract.available else None
        score.owner_renounced = contract.owner_renounced if contract and contract.available else None
        score.top10_holder_pct = contract.top10_holder_pct if contract and contract.available else None
        return score

    @staticmethod
    def _activity_ok(pool):
        tx1 = pool.buys_1h + pool.sells_1h
        if pool.liquidity_usd < settings.min_liquidity_usd:
            return False, "liquidity below floor"
        if pool.volume_24h_usd < settings.min_volume_24h_usd:
            return False, "24h volume below floor"
        if tx1 < settings.min_1h_transactions:
            return False, "insufficient 1h transactions"
        if pool.buys_1h <= pool.sells_1h:
            return False, "1h sell pressure"
        return True, "ok"

    async def run_once(self):
        pools = await asyncio.to_thread(self._candidate_pools)
        logger.info("Fetched %d candidate pools", len(pools))
        alerts_sent = 0
        failures = 0

        for pool in pools:
            # Hard market-cap rule: reported circulating MC only; FDV is never a substitute.
            if not (settings.min_market_cap_usd <= pool.market_cap_usd < settings.max_market_cap_usd):
                continue
            if not (settings.min_pool_age_minutes <= pool.age_minutes <= settings.max_pool_age_minutes):
                continue
            activity_ok, reason = self._activity_ok(pool)
            if not activity_ok:
                logger.info("SKIP %s: %s", pool.base_token_symbol, reason)
                continue

            score = await asyncio.to_thread(self._evaluate, pool)
            self.db.mark_seen(pool.pool_address, score.total)
            if score.blocked or score.total < settings.min_score_to_alert:
                continue
            if self.db.already_alerted_recently(pool.pool_address, ALERT_COOLDOWN_SECONDS):
                continue
            if not self.telegram_bot:
                failures += 1
                continue

            delivered = await self.telegram_bot.send_alert(settings.telegram_chat_id, pool, score)
            if delivered is not True:
                failures += 1
                logger.error("ALERT NOT SENT: %s score=%.1f mcap=%.0f", pool.base_token_symbol, score.total, pool.market_cap_usd)
                continue
            self.db.record_alert(pool.pool_address, pool.base_token_symbol, score.total, pool.market_cap_usd)
            alerts_sent += 1
            logger.info("ALERT SENT: %s score=%.1f mcap=%.0f liq=%.0f vol24=%.0f", pool.base_token_symbol, score.total, pool.market_cap_usd, pool.liquidity_usd, pool.volume_24h_usd)

        if failures:
            raise RuntimeError(f"Telegram delivery failed for {failures} alert(s)")
        return alerts_sent

    def close(self):
        self.gecko.close()
        self.bscscan.close()
        self.honeypot.close()
