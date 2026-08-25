"""
Alert message formatting — shared by bot.py (interactive/VPS mode) and
scan_once.py (GitHub Actions one-shot mode) so the two modes never drift
into showing different information for the same alert.
"""
from scoring import ScoreBreakdown
from datasource import PoolData


def format_alert(pool: PoolData, score: ScoreBreakdown) -> str:
    lines = [
        f"🚨 *{pool.base_token_symbol}* scored *{score.total}/100*",
        "",
        f"💰 Market Cap: ${pool.market_cap_usd:,.0f}",
        f"💧 Liquidity: ${pool.liquidity_usd:,.0f}",
        f"📊 24h Volume: ${pool.volume_24h_usd:,.0f}",
        f"📈 1h / 6h / 24h: {pool.price_change_1h:+.1f}% / {pool.price_change_6h:+.1f}% / {pool.price_change_24h:+.1f}%",
        f"⏱ Pool age: {pool.age_minutes / 60:.1f}h",
        "",
        "*Score breakdown:*",
        *score.summary_lines(),
    ]
    if score.flags:
        lines.append("")
        lines.append("*Flags:*")
        lines.extend(score.flags)

    lines.append("")
    lines.append(f"[View chart]({pool.dex_url})")
    lines.append("")
    lines.append("_Not financial advice. DYOR before trading — meme coins are high risk._")
    return "\n".join(lines)
