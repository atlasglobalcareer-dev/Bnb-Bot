"""Production scanner configuration for BNB micro-cap alerts."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()
MAX_TARGET_MARKET_CAP_USD = 50_000.0

def _float(name, default):
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default

def _int(name, default):
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default

@dataclass
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    bscscan_api_key: str = os.getenv("BSCSCAN_API_KEY", "")
    min_market_cap_usd: float = field(default_factory=lambda: _float("MIN_MARKET_CAP_USD", 3_000))
    max_market_cap_usd: float = field(default_factory=lambda: min(_float("MAX_MARKET_CAP_USD", MAX_TARGET_MARKET_CAP_USD), MAX_TARGET_MARKET_CAP_USD))
    min_liquidity_usd: float = field(default_factory=lambda: _float("MIN_LIQUIDITY_USD", 5_000))
    min_volume_24h_usd: float = field(default_factory=lambda: _float("MIN_VOLUME_24H_USD", 10_000))
    min_score_to_alert: float = field(default_factory=lambda: _float("MIN_SCORE_TO_ALERT", 60))
    min_1h_transactions: int = field(default_factory=lambda: _int("MIN_1H_TRANSACTIONS", 10))
    min_pool_age_minutes: int = field(default_factory=lambda: _int("MIN_POOL_AGE_MINUTES", 15))
    max_pool_age_minutes: int = field(default_factory=lambda: _int("MAX_POOL_AGE_MINUTES", 4320))
    min_buy_sell_ratio: float = field(default_factory=lambda: _float("MIN_BUY_SELL_RATIO", 1.0))
    min_liquidity_to_mc_ratio: float = field(default_factory=lambda: _float("MIN_LIQUIDITY_MC_RATIO", 0.15))
    max_buy_tax_pct: float = field(default_factory=lambda: _float("MAX_BUY_TAX_PCT", 10))
    max_sell_tax_pct: float = field(default_factory=lambda: _float("MAX_SELL_TAX_PCT", 10))
    poll_interval_seconds: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 120))
    database_path: str = os.getenv("DATABASE_PATH", "data/scanner.db")
    network: str = "bsc"
    def validate(self):
        problems = []
        if not self.telegram_bot_token: problems.append("TELEGRAM_BOT_TOKEN is not set")
        if not self.telegram_chat_id: problems.append("TELEGRAM_CHAT_ID is not set")
        if not self.bscscan_api_key: problems.append("BSCSCAN_API_KEY is not set")
        if self.min_market_cap_usd < 0 or self.min_liquidity_usd < 0 or self.min_volume_24h_usd < 0: problems.append("minimum market cap, liquidity and volume cannot be negative")
        if self.min_market_cap_usd >= self.max_market_cap_usd: problems.append("MIN_MARKET_CAP_USD must be less than MAX_MARKET_CAP_USD")
        if self.max_market_cap_usd > MAX_TARGET_MARKET_CAP_USD: problems.append("MAX_MARKET_CAP_USD cannot exceed $50,000")
        return problems
settings = Settings()
