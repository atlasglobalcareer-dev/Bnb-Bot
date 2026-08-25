"""Telegram alert formatting with market data, contract address and audit status."""
from scoring import ScoreBreakdown
from datasource import PoolData


def format_alert(pool: PoolData, score: ScoreBreakdown) -> str:
    token = getattr(pool, "token_address", "") or "UNKNOWN"
    lines = [
        f"🚨 *{pool.base_token_symbol}* — *{score.total}/100*", "",
        "*TOKEN*", f"📋 Contract: `{token}`", "_Copy the contract above into your wallet/watchlist._", "",
        "*MARKET DATA*", f"💰 Market Cap: ${pool.market_cap_usd:,.0f}",
        f"💧 Liquidity: ${pool.liquidity_usd:,.0f}", f"📊 24h Volume: ${pool.volume_24h_usd:,.0f}",
        f"📈 1h / 6h / 24h: {pool.price_change_1h:+.1f}% / {pool.price_change_6h:+.1f}% / {pool.price_change_24h:+.1f}%",
        f"🟢 Buys 1h / Sells 1h: {pool.buys_1h} / {pool.sells_1h}",
        f"🟢 Buys 6h / Sells 6h: {pool.buys_6h} / {pool.sells_6h}", f"⏱ Pool age: {pool.age_minutes / 60:.1f}h", "",
        "*SCORE BREAKDOWN*", *score.summary_lines(), "", "*AUDIT / RISK*"
    ]
    hp_checked = getattr(score, "honeypot_checked", False); hp_value = getattr(score, "is_honeypot", None)
    buy_tax = getattr(score, "buy_tax", None); sell_tax = getattr(score, "sell_tax", None)
    if hp_checked:
        lines.append(f"🛡 Honeypot: {'YES — BLOCKED' if hp_value is True else 'NO — passed'}")
        if buy_tax is not None: lines.append(f"💸 Buy tax: {buy_tax:.2f}%")
        if sell_tax is not None: lines.append(f"💸 Sell tax: {sell_tax:.2f}%")
    else: lines.append("🛡 Honeypot: UNKNOWN / check unavailable")
    verified = getattr(score, "contract_verified", None); holder_pct = getattr(score, "top10_holder_pct", None); owner_renounced = getattr(score, "owner_renounced", None)
    lines.append(f"📜 Contract verified: {'YES' if verified is True else 'NO' if verified is False else 'UNKNOWN'}")
    lines.append(f"👥 Top 10 holders: {holder_pct:.2f}%" if holder_pct is not None else "👥 Top 10 holders: UNKNOWN")
    lines.append(f"🔐 Owner renounced: {'YES' if owner_renounced is True else 'NO' if owner_renounced is False else 'UNKNOWN'}")
    if score.flags: lines.extend(["", "*FLAGS*"] + score.flags)
    lines += ["", f"[View chart]({pool.dex_url})", "", "_Research/screening only — not financial advice. Meme tokens are extremely high risk._"]
    return "\n".join(lines)
