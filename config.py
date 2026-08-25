"""
Central config loader. The scanner is intentionally focused on BNB tokens
below the hard $50,000 market-cap ceiling. Other values control audit/scoring
behaviour without changing that ceiling.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

MAX_TARGET_MARKET_CAP_USD = 50_000.0


def _float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


@dataclass
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    bscscan_api_key: str = os.getenv("BSCSCAN_API_KEY", "")

    min_market_cap_usd: float = field(default_factory=lambda: _float("MIN_MARKET_CAP_USD", 0))
    max_market_cap_usd: float = field(default_factory=lambda: min(_float("MAX_MARKET_CAP_USD", MAX_TARGET_MARKET_CAP_USD), MAX_TARGET_MARKET_CAP_USD))
    min_liquidity_usd: float = field(default_factory=lambda: _float("MIN_LIQUIDITY_USD", 0))
    min_score_to_alert: float = field(default_factory=lambda: _float("MIN_SCORE_TO_ALERT", 0))

    min_pool_age_minutes: int = field(default_factory=lambda: _int("MIN_POOL_AGE_MINUTES", 0))
    max_pool_age_minutes: int = field(default_factory=lambda: _int("MAX_POOL_AGE_MINUTES", 4320))

    poll_interval_seconds: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 120))
    database_path: str = os.getenv("DATABASE_PATH", "data/scanner.db")
    network: str = "bsc"

    def validate(self) -> list[str]:
        problems = []
        if not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN is not set")
        if self.min_market_cap_usd < 0:
            problems.append("MIN_MARKET_CAP_USD cannot be negative")
        if self.min_market_cap_usd >= self.max_market_cap_usd:
            problems.append("MIN_MARKET_CAP_USD must be less than MAX_MARKET_CAP_USD")
        if self.max_market_cap_usd > MAX_TARGET_MARKET_CAP_USD:
            problems.append("MAX_MARKET_CAP_USD cannot exceed $50,000")
        return problems


settings = Settings()
