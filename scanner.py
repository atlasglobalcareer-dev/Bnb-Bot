"""Production BNB high-confidence micro-cap scanner."""
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
        for fetch in (self.gecko.new_pools, self.gecko.trending_pools):
            for page in (1, 2, 3):
                try: pools.extend(fetch(page=page))
                except Exception: logger.exception("Failed to fetch candidate pools page=%d", page)
        return list({p.pool_address: p for p in pools if p.pool_address}.values())

    def _evaluate(self, pool):
        contract = self.bscscan.check_contract(pool.token_address) if pool.token_address else None
        honeypot = self.honeypot.check(pool.token_address, pool.pool_address) if pool.token_address else None
        score = score_pool(pool, contract, min_age_minutes=settings.min_pool_age_minutes, max_age_minutes=settings.max_pool_age_minutes, honeypot=honeypot)
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
    def _usable_dex_pairs(pairs):
        usable = []
        for pair in pairs or []:
            try:
                mc = float(pair.get("marketCap") or 0)
                liq = float((pair.get("liquidity") or {}).get("usd") or 0)
            except (TypeError, ValueError):
                continue
            if mc > 0 and liq > 0:
                usable.append(pair)
        return sorted(usable, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)

    @staticmethod
    def _apply_dex_pair(pool, pair):
        """Fill only market-data fields that DexScreener actually reports.
        Never substitute FDV for market cap: the scanner requires real MC.
        """
        mc = float(pair.get("marketCap") or 0)
        if mc <= 0:
            return False
        pool.market_cap_usd = mc
        pool.price_usd = float(pair.get("priceUsd") or pool.price_usd or 0)
        pool.liquidity_usd = float((pair.get("liquidity") or {}).get("usd") or pool.liquidity_usd or 0)
        vol = pair.get("volume") or {}
        if float(vol.get("h24") or 0) > 0:
            pool.volume_24h_usd = float(vol.get("h24"))
        if float(vol.get("h6") or 0) > 0:
            pool.volume_6h_usd = float(vol.get("h6"))
        if float(vol.get("h1") or 0) > 0:
            pool.volume_1h_usd = float(vol.get("h1"))
        tx = pair.get("txns") or {}
        h1 = tx.get("h1") or {}
        if h1:
            pool.buys_1h = int(h1.get("buys") or pool.buys_1h or 0)
            pool.sells_1h = int(h1.get("sells") or pool.sells_1h or 0)
        pc = pair.get("priceChange") or {}
        if pc.get("h1") is not None: pool.price_change_1h = float(pc.get("h1") or 0)
        if pc.get("h6") is not None: pool.price_change_6h = float(pc.get("h6") or 0)
        if pc.get("h24") is not None: pool.price_change_24h = float(pc.get("h24") or 0)
        return True

    async def _enrich_market_caps(self, pools):
        initial_missing = sum(1 for p in pools if p.market_cap_usd <= 0)
        gecko_enriched = dex_pair_enriched = dex_token_enriched = dex_search_enriched = still_missing = 0
        for pool in pools:
            if pool.market_cap_usd > 0:
                continue
            # 1) Exact pair lookup: safest match to the candidate pool.
            try:
                mc = await asyncio.to_thread(self.gecko.dexscreener_market_cap, pool.pool_address)
                if mc > 0:
                    pool.market_cap_usd = mc
                    dex_pair_enriched += 1
                    continue
            except Exception:
                logger.warning("DexScreener pair MC lookup failed for %s", pool.base_token_symbol)
            if not pool.token_address:
                still_missing += 1
                continue
            # 2) Token endpoint: more reliable than /search for an exact contract.
            try:
                pairs = await asyncio.to_thread(self.gecko.token_pairs, pool.token_address)
                usable = self._usable_dex_pairs(pairs)
                exact = [p for p in usable if str(p.get("pairAddress", "")).lower() == pool.pool_address.lower()]
                chosen = exact[0] if exact else (usable[0] if usable else None)
                if chosen and self._apply_dex_pair(pool, chosen):
                    dex_token_enriched += 1
                    continue
            except Exception:
                logger.warning("DexScreener token lookup failed for %s", pool.base_token_symbol)
            # 3) Search endpoint fallback.
            try:
                pairs = await asyncio.to_thread(self.gecko.search_token_pairs, pool.token_address)
                usable = self._usable_dex_pairs(pairs)
                exact = [p for p in usable if str(p.get("pairAddress", "")).lower() == pool.pool_address.lower()]
                chosen = exact[0] if exact else (usable[0] if usable else None)
                if chosen and self._apply_dex_pair(pool, chosen):
                    dex_search_enriched += 1
                    continue
            except Exception:
                logger.warning("DexScreener search failed for %s", pool.base_token_symbol)
            # 4) Gecko detail is last resort only; rate limiting prevents a burst.
            try:
                detail = await asyncio.to_thread(self.gecko.pool_detail, pool.pool_address)
                if detail and detail.market_cap_usd > 0:
                    pool.__dict__.update(detail.__dict__)
                    gecko_enriched += 1
                    continue
            except Exception:
                logger.warning("Gecko detail MC lookup failed for %s", pool.base_token_symbol)
            still_missing += 1
        logger.info("MC ENRICHMENT SUMMARY: missing_initial=%d gecko_enriched=%d dexscreener_pair_enriched=%d dexscreener_token_enriched=%d dexscreener_search_enriched=%d still_missing=%d", initial_missing, gecko_enriched, dex_pair_enriched, dex_token_enriched, dex_search_enriched, still_missing)
        return pools

    async def run_once(self):
        pools = await asyncio.to_thread(self._candidate_pools); logger.info("Fetched %d candidate pools", len(pools))
        if any(p.market_cap_usd <= 0 for p in pools): pools = await self._enrich_market_caps(pools)
        stats = {k: 0 for k in ("mc_missing_or_zero","mc_below_min","mc_at_or_above_max","age_too_new","age_too_old","liquidity_below_floor","low_liquidity_mc_ratio","volume_below_floor","transactions_below_floor","sell_pressure","reached_scoring","blocked_by_audit","unknown_honeypot","unverified_contract","bad_tax","bad_owner","bad_holders","score_below_threshold","already_alerted_recently","telegram_unconfigured","telegram_delivery_failed","alerts_sent")}
        failures = 0
        for pool in pools:
            mc = pool.market_cap_usd
            if mc <= 0: stats["mc_missing_or_zero"] += 1; logger.info("FILTER %s: market cap missing/zero", pool.base_token_symbol); continue
            if mc < settings.min_market_cap_usd: stats["mc_below_min"] += 1; continue
            if mc >= settings.max_market_cap_usd: stats["mc_at_or_above_max"] += 1; continue
            if pool.age_minutes < settings.min_pool_age_minutes: stats["age_too_new"] += 1; continue
            if pool.age_minutes > settings.max_pool_age_minutes: stats["age_too_old"] += 1; continue
            if pool.liquidity_usd < settings.min_liquidity_usd: stats["liquidity_below_floor"] += 1; continue
            if pool.market_cap_usd > 0 and pool.liquidity_usd / pool.market_cap_usd < settings.min_liquidity_to_mc_ratio: stats["low_liquidity_mc_ratio"] += 1; continue
            if pool.volume_24h_usd < settings.min_volume_24h_usd: stats["volume_below_floor"] += 1; continue
            tx1 = pool.buys_1h + pool.sells_1h
            if tx1 < settings.min_1h_transactions: stats["transactions_below_floor"] += 1; continue
            ratio = pool.buys_1h / max(pool.sells_1h, 1)
            if ratio < settings.min_buy_sell_ratio: stats["sell_pressure"] += 1; continue
            stats["reached_scoring"] += 1
            score = await asyncio.to_thread(self._evaluate, pool); self.db.mark_seen(pool.pool_address, score.total)
            if score.honeypot_checked and score.is_honeypot is True:
                stats["blocked_by_audit"] += 1; logger.info("BLOCK %s: confirmed honeypot", pool.base_token_symbol); continue
            if not score.honeypot_checked or score.is_honeypot is None:
                stats["unknown_honeypot"] += 1
                logger.info("UNVERIFIED %s: honeypot result unavailable", pool.base_token_symbol)
            if not score.contract_available or score.contract_verified is not True: stats["unverified_contract"] += 1; continue
            if score.honeypot_checked:
                if score.buy_tax is not None and score.buy_tax > settings.max_buy_tax_pct: stats["bad_tax"] += 1; continue
                if score.sell_tax is None or score.sell_tax > settings.max_sell_tax_pct: stats["bad_tax"] += 1; continue
            if score.owner_renounced is False: stats["bad_owner"] += 1; continue
            if score.top10_holder_pct is None or score.top10_holder_pct > 60: stats["bad_holders"] += 1; continue
            if score.total < settings.min_score_to_alert: stats["score_below_threshold"] += 1; continue
            if self.db.already_alerted_recently(pool.pool_address, ALERT_COOLDOWN_SECONDS): stats["already_alerted_recently"] += 1; continue
            if not self.telegram_bot: stats["telegram_unconfigured"] += 1; failures += 1; continue
            delivered = await self.telegram_bot.send_alert(settings.telegram_chat_id, pool, score)
            if delivered is not True: stats["telegram_delivery_failed"] += 1; failures += 1; continue
            self.db.record_alert(pool.pool_address, pool.base_token_symbol, score.total, mc); stats["alerts_sent"] += 1
            logger.info("ALERT SENT: %s score=%.1f mcap=%.0f", pool.base_token_symbol, score.total, mc)
        logger.info("FILTER SUMMARY: candidates=%d missing_mc=%d below_min_mc=%d at_or_above_%.0fk=%d too_new=%d too_old=%d low_liquidity=%d low_liquidity_mc_ratio=%d low_volume=%d low_transactions=%d sell_pressure=%d reached_scoring=%d blocked=%d unknown_honeypot=%d unverified_contract=%d bad_tax=%d bad_owner=%d bad_holders=%d low_score=%d cooldown=%d telegram_unconfigured=%d telegram_failed=%d alerts_sent=%d", len(pools), stats["mc_missing_or_zero"], stats["mc_below_min"], settings.max_market_cap_usd / 1000, stats["mc_at_or_above_max"], stats["age_too_new"], stats["age_too_old"], stats["liquidity_below_floor"], stats["low_liquidity_mc_ratio"], stats["volume_below_floor"], stats["transactions_below_floor"], stats["sell_pressure"], stats["reached_scoring"], stats["blocked_by_audit"], stats["unknown_honeypot"], stats["unverified_contract"], stats["bad_tax"], stats["bad_owner"], stats["bad_holders"], stats["score_below_threshold"], stats["already_alerted_recently"], stats["telegram_unconfigured"], stats["telegram_delivery_failed"], stats["alerts_sent"])
        if failures: raise RuntimeError(f"Telegram delivery failed for {failures} alert(s)")
        return stats["alerts_sent"]

    def close(self): self.gecko.close(); self.bscscan.close(); self.honeypot.close()
