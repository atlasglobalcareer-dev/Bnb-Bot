"""Production BNB micro-cap scanner: real MC + liquidity + activity + momentum."""
import asyncio
import logging
from datasource import GeckoTerminalClient
from bscscan import BscScanClient
from honeypot import HoneypotClient
from scoring import score_pool
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
        # Use several pages from both discovery feeds instead of relying on only
        # the first 20 results from each endpoint.
        for fetch in (self.gecko.new_pools, self.gecko.trending_pools):
            for page in (1, 2, 3):
                try:
                    pools.extend(fetch(page=page))
                except Exception:
                    logger.exception("Failed to fetch candidate pools page=%d", page)
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

    async def _enrich_market_caps(self, pools):
        """Resolve missing MC through Gecko detail, then DexScreener pair/search."""
        initial_missing = sum(1 for p in pools if p.market_cap_usd <= 0)
        gecko_enriched = 0
        dex_pair_enriched = 0
        dex_search_enriched = 0
        still_missing = 0
        for pool in pools:
            if pool.market_cap_usd > 0:
                continue
            try:
                detail = await asyncio.to_thread(self.gecko.pool_detail, pool.pool_address)
                if detail and detail.market_cap_usd > 0:
                    pool.__dict__.update(detail.__dict__)
                    gecko_enriched += 1
                    logger.info("MC ENRICHED %s: source=GeckoTerminal market_cap=%.0f", pool.base_token_symbol, pool.market_cap_usd)
                    continue
            except Exception:
                logger.exception("MC GECKO DETAIL ERROR %s", pool.base_token_symbol)
            try:
                mc = await asyncio.to_thread(self.gecko.dexscreener_market_cap, pool.pool_address)
                if mc > 0:
                    pool.market_cap_usd = mc
                    dex_pair_enriched += 1
                    logger.info("MC ENRICHED %s: source=DexScreenerPair market_cap=%.0f", pool.base_token_symbol, mc)
                    continue
            except Exception:
                logger.exception("MC DEX PAIR ERROR %s", pool.base_token_symbol)
            if pool.token_address:
                try:
                    pairs = await asyncio.to_thread(self.gecko.search_token_pairs, pool.token_address)
                    # Prefer a BSC pair with a reported MC and the strongest liquidity.
                    usable = [p for p in pairs if float(p.get("marketCap") or 0) > 0]
                    usable.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
                    if usable:
                        mc = float(usable[0].get("marketCap"))
                        pool.market_cap_usd = mc
                        dex_search_enriched += 1
                        logger.info("MC ENRICHED %s: source=DexScreenerSearch market_cap=%.0f", pool.base_token_symbol, mc)
                        continue
                except Exception:
                    logger.exception("MC DEX SEARCH ERROR %s", pool.base_token_symbol)
            still_missing += 1
            logger.info("MC ENRICH FAILED %s: no reported market cap from available sources", pool.base_token_symbol)
        logger.info("MC ENRICHMENT SUMMARY: missing_initial=%d gecko_enriched=%d dexscreener_pair_enriched=%d dexscreener_search_enriched=%d still_missing=%d",
                    initial_missing, gecko_enriched, dex_pair_enriched, dex_search_enriched, still_missing)
        return pools

    async def run_once(self):
        pools = await asyncio.to_thread(self._candidate_pools)
        logger.info("Fetched %d candidate pools", len(pools))
        if any(p.market_cap_usd <= 0 for p in pools):
            pools = await self._enrich_market_caps(pools)
        stats = {k: 0 for k in (
            "mc_missing_or_zero", "mc_below_min", "mc_at_or_above_max", "age_too_new", "age_too_old",
            "liquidity_below_floor", "volume_below_floor", "transactions_below_floor", "sell_pressure",
            "reached_scoring", "blocked_by_audit", "score_below_threshold", "already_alerted_recently",
            "telegram_unconfigured", "telegram_delivery_failed", "alerts_sent")}
        failures = 0
        for pool in pools:
            mc = pool.market_cap_usd
            if mc <= 0:
                stats["mc_missing_or_zero"] += 1; logger.info("FILTER %s: market cap missing/zero (mc=%s)", pool.base_token_symbol, mc); continue
            if mc < settings.min_market_cap_usd:
                stats["mc_below_min"] += 1; logger.info("FILTER %s: MC below minimum (mc=%.0f)", pool.base_token_symbol, mc); continue
            if mc >= settings.max_market_cap_usd:
                stats["mc_at_or_above_max"] += 1; logger.info("FILTER %s: MC at/above $50K ceiling (mc=%.0f)", pool.base_token_symbol, mc); continue
            if pool.age_minutes < settings.min_pool_age_minutes:
                stats["age_too_new"] += 1; logger.info("FILTER %s: pool too new (age=%.1f min)", pool.base_token_symbol, pool.age_minutes); continue
            if pool.age_minutes > settings.max_pool_age_minutes:
                stats["age_too_old"] += 1; logger.info("FILTER %s: pool too old (age=%.1f min)", pool.base_token_symbol, pool.age_minutes); continue
            if pool.liquidity_usd < settings.min_liquidity_usd:
                stats["liquidity_below_floor"] += 1; logger.info("FILTER %s: liquidity below floor (liq=%.0f)", pool.base_token_symbol, pool.liquidity_usd); continue
            if pool.volume_24h_usd < settings.min_volume_24h_usd:
                stats["volume_below_floor"] += 1; logger.info("FILTER %s: 24h volume below floor (vol=%.0f)", pool.base_token_symbol, pool.volume_24h_usd); continue
            tx1 = pool.buys_1h + pool.sells_1h
            if tx1 < settings.min_1h_transactions:
                stats["transactions_below_floor"] += 1; logger.info("FILTER %s: insufficient 1h transactions (tx=%d)", pool.base_token_symbol, tx1); continue
            if pool.buys_1h <= pool.sells_1h:
                stats["sell_pressure"] += 1; logger.info("FILTER %s: 1h sell pressure (buys=%d sells=%d)", pool.base_token_symbol, pool.buys_1h, pool.sells_1h); continue
            stats["reached_scoring"] += 1
            logger.info("QUALIFIED FOR SCORING %s: mc=%.0f liq=%.0f vol24=%.0f tx1h=%d buys=%d sells=%d", pool.base_token_symbol, mc, pool.liquidity_usd, pool.volume_24h_usd, tx1, pool.buys_1h, pool.sells_1h)
            score = await asyncio.to_thread(self._evaluate, pool)
            self.db.mark_seen(pool.pool_address, score.total)
            if score.blocked:
                stats["blocked_by_audit"] += 1; logger.info("FILTER %s: blocked by audit/security checks (score=%.1f)", pool.base_token_symbol, score.total); continue
            if score.total < settings.min_score_to_alert:
                stats["score_below_threshold"] += 1; logger.info("FILTER %s: score below threshold (score=%.1f)", pool.base_token_symbol, score.total); continue
            if self.db.already_alerted_recently(pool.pool_address, ALERT_COOLDOWN_SECONDS):
                stats["already_alerted_recently"] += 1; logger.info("FILTER %s: alert cooldown active", pool.base_token_symbol); continue
            if not self.telegram_bot:
                stats["telegram_unconfigured"] += 1; failures += 1; logger.error("ALERT NOT SENT %s: Telegram bot not configured", pool.base_token_symbol); continue
            delivered = await self.telegram_bot.send_alert(settings.telegram_chat_id, pool, score)
            if delivered is not True:
                stats["telegram_delivery_failed"] += 1; failures += 1; logger.error("ALERT NOT SENT: %s score=%.1f mcap=%.0f", pool.base_token_symbol, score.total, mc); continue
            self.db.record_alert(pool.pool_address, pool.base_token_symbol, score.total, mc)
            stats["alerts_sent"] += 1
            logger.info("ALERT SENT: %s score=%.1f mcap=%.0f", pool.base_token_symbol, score.total, mc)
        logger.info("FILTER SUMMARY: candidates=%d missing_mc=%d below_min_mc=%d at_or_above_50k=%d too_new=%d too_old=%d low_liquidity=%d low_volume=%d low_transactions=%d sell_pressure=%d reached_scoring=%d blocked=%d low_score=%d cooldown=%d telegram_unconfigured=%d telegram_failed=%d alerts_sent=%d",
                    len(pools), stats["mc_missing_or_zero"], stats["mc_below_min"], stats["mc_at_or_above_max"], stats["age_too_new"], stats["age_too_old"], stats["liquidity_below_floor"], stats["volume_below_floor"], stats["transactions_below_floor"], stats["sell_pressure"], stats["reached_scoring"], stats["blocked_by_audit"], stats["score_below_threshold"], stats["already_alerted_recently"], stats["telegram_unconfigured"], stats["telegram_delivery_failed"], stats["alerts_sent"])
        if failures:
            raise RuntimeError(f"Telegram delivery failed for {failures} alert(s)")
        return stats["alerts_sent"]

    def close(self):
        self.gecko.close(); self.bscscan.close(); self.honeypot.close()
