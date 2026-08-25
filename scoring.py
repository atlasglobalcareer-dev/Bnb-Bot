"""
Scoring engine.

Turns raw pool + optional contract-safety data into a single 0-100 score,
plus a breakdown so every alert shows exactly why a token scored the way it
did. This is a heuristic momentum/risk filter — NOT a price prediction.

Weights are intentionally exposed as module-level constants so you can tune
them without hunting through logic. They sum to 100.
"""
from dataclasses import dataclass, field
from datasource import PoolData
from bscscan import ContractSafety
from honeypot import HoneypotResult

WEIGHTS = {
    "liquidity_ratio": 18,   # liquidity / market cap - deeper is safer
    "volume_ratio": 18,      # 24h volume / market cap - real activity
    "buy_pressure": 18,      # buy/sell tx ratio over 1h and 6h
    "momentum": 14,          # recent price change, capped so pumps don't dominate
    "age_fit": 9,            # inside the configured age sweet-spot
    "verified_contract": 7,  # source verified on BscScan
    "holder_spread": 6,      # top-10 holders don't dominate supply
    "sellability": 10,       # can you actually sell it, and at what tax
}
assert sum(WEIGHTS.values()) == 100


@dataclass
class ScoreBreakdown:
    total: float = 0.0
    components: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)  # human-readable red/green flags
    blocked: bool = False        # hard block - never alert regardless of score
    blocked_reason: str = ""

    def summary_lines(self) -> list[str]:
        lines = []
        for key, val in self.components.items():
            lines.append(f"  • {key.replace('_', ' ').title()}: {val:.1f}/{WEIGHTS[key]}")
        return lines


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def score_pool(
    pool: PoolData,
    contract: ContractSafety | None,
    min_age_minutes: int,
    max_age_minutes: int,
    honeypot: HoneypotResult | None = None,
) -> ScoreBreakdown:
    result = ScoreBreakdown()

    # --- Sellability / honeypot check ---
    # Confirmed honeypot is a hard block: no score is high enough to justify
    # alerting on a token you can't sell. Everything else below is nuance;
    # this one is binary.
    if honeypot and honeypot.checked and honeypot.is_honeypot is True:
        result.blocked = True
        result.blocked_reason = honeypot.simulation_error or "Simulated sell failed (honeypot)"
        result.flags.append("🚫 HONEYPOT DETECTED — sell simulation fails")
        result.components["sellability"] = 0
        # still compute the rest for logging/debug purposes, but the caller
        # (scanner.py) must respect `blocked` and never send this as an alert
    elif honeypot and honeypot.checked and honeypot.is_honeypot is False:
        sell_tax = honeypot.sell_tax or 0
        buy_tax = honeypot.buy_tax or 0
        total_tax = sell_tax + buy_tax
        if total_tax <= 10:
            sell_score = WEIGHTS["sellability"]
        else:
            sell_score = WEIGHTS["sellability"] * _clamp(1 - ((total_tax - 10) / 40), 0, 1)
        result.components["sellability"] = sell_score
        if total_tax > 15:
            result.flags.append(f"⚠️ High combined buy/sell tax: {total_tax:.1f}%")
        else:
            result.flags.append("✅ Sell simulation passed")
    else:
        # not checked / unknown — neutral half-credit, and a visible flag so
        # you know the safety net didn't actually fire for this one
        result.components["sellability"] = WEIGHTS["sellability"] * 0.5
        result.flags.append("❔ Honeypot check unavailable — verify sellability manually")

    # --- Liquidity / market cap ---
    if pool.market_cap_usd > 0:
        liq_ratio = pool.liquidity_usd / pool.market_cap_usd
    else:
        liq_ratio = 0
    # sweet spot: 10%-60% of mcap in liquidity. Below 5% is a red flag, above
    # ~80% usually just means a very new/small pool.
    liq_score = _clamp((liq_ratio / 0.3) * WEIGHTS["liquidity_ratio"], 0, WEIGHTS["liquidity_ratio"])
    result.components["liquidity_ratio"] = liq_score
    if liq_ratio < 0.05:
        result.flags.append("⚠️ Thin liquidity relative to market cap")

    # --- Volume / market cap ---
    if pool.market_cap_usd > 0:
        vol_ratio = pool.volume_24h_usd / pool.market_cap_usd
    else:
        vol_ratio = 0
    vol_score = _clamp((vol_ratio / 0.5) * WEIGHTS["volume_ratio"], 0, WEIGHTS["volume_ratio"])
    result.components["volume_ratio"] = vol_score
    if vol_ratio < 0.05:
        result.flags.append("⚠️ Low trading volume relative to market cap")

    # --- Buy pressure (weighted toward the more recent 1h window) ---
    def buy_ratio(buys, sells):
        total = buys + sells
        return buys / total if total > 0 else 0.5

    ratio_1h = buy_ratio(pool.buys_1h, pool.sells_1h)
    ratio_6h = buy_ratio(pool.buys_6h, pool.sells_6h)
    blended = ratio_1h * 0.65 + ratio_6h * 0.35
    # 0.5 = neutral, scale so 0.5->0 points, 0.8+->full points
    buy_score = _clamp(((blended - 0.5) / 0.3) * WEIGHTS["buy_pressure"], 0, WEIGHTS["buy_pressure"])
    result.components["buy_pressure"] = buy_score
    if blended < 0.4:
        result.flags.append("⚠️ Sell-heavy: more sells than buys recently")
    elif blended > 0.65:
        result.flags.append("✅ Strong recent buy pressure")

    # --- Momentum (recent price change, capped to avoid rewarding blowoff tops) ---
    momentum_raw = (pool.price_change_1h * 0.5) + (pool.price_change_6h * 0.5)
    momentum_score = _clamp((momentum_raw / 40) * WEIGHTS["momentum"], 0, WEIGHTS["momentum"])
    result.components["momentum"] = momentum_score
    if momentum_raw > 100:
        result.flags.append("⚠️ Parabolic move already — high chance of pullback")

    # --- Age fit ---
    if min_age_minutes <= pool.age_minutes <= max_age_minutes:
        age_score = WEIGHTS["age_fit"]
    elif pool.age_minutes < min_age_minutes:
        # too new: scale down toward 0 as it approaches 0 minutes old
        age_score = WEIGHTS["age_fit"] * _clamp(pool.age_minutes / min_age_minutes, 0, 1) * 0.5
        result.flags.append("⚠️ Very new pool — elevated rug risk")
    else:
        age_score = WEIGHTS["age_fit"] * 0.3
    result.components["age_fit"] = age_score

    # --- Contract safety (optional, degrades gracefully without a BscScan key) ---
    if contract and contract.available:
        verified_score = WEIGHTS["verified_contract"] if contract.is_verified else 0
        if not contract.is_verified:
            result.flags.append("🚫 Contract source not verified on BscScan")
        result.components["verified_contract"] = verified_score

        if contract.top10_holder_pct is not None:
            if contract.top10_holder_pct <= 30:
                holder_score = WEIGHTS["holder_spread"]
            else:
                holder_score = WEIGHTS["holder_spread"] * _clamp(
                    1 - ((contract.top10_holder_pct - 30) / 50), 0, 1
                )
            if contract.top10_holder_pct > 50:
                result.flags.append(f"⚠️ Top 10 wallets hold {contract.top10_holder_pct:.0f}% of supply")
            result.components["holder_spread"] = holder_score
        else:
            # unknown — award half credit rather than punishing for a data gap
            result.components["holder_spread"] = WEIGHTS["holder_spread"] * 0.5
    else:
        # No BscScan key configured — split the difference so the score isn't
        # artificially deflated just because that data source is off.
        result.components["verified_contract"] = WEIGHTS["verified_contract"] * 0.5
        result.components["holder_spread"] = WEIGHTS["holder_spread"] * 0.5

    result.total = round(sum(result.components.values()), 1)
    return result
