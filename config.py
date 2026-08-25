"""
Central config loader. Reads from .env (via python-dotenv) with sane defaults.
Keeping this in one place means every other module just does
`from config import settings` instead of touching os.environ directly.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


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

    min_market_cap_usd: float = field(default_factory=lambda: _float("MIN_MARKET_CAP_USD", 50_000))
    max_market_cap_usd: float = field(default_factory=lambda: _float("MAX_MARKET_CAP_USD", 3_000_000))
    min_liquidity_usd: float = field(default_factory=lambda: _float("MIN_LIQUIDITY_USD", 10_000))
    min_score_to_alert: float = field(default_factory=lambda: _float("MIN_SCORE_TO_ALERT", 65))

    min_pool_age_minutes: int = field(default_factory=lambda: _int("MIN_POOL_AGE_MINUTES", 30))
    max_pool_age_minutes: int = field(default_factory=lambda: _int("MAX_POOL_AGE_MINUTES", 4320))

    poll_interval_seconds: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 120))
    database_path: str = os.getenv("DATABASE_PATH", "data/scanner.db")

    network: str = "bsc"  # GeckoTerminal network id for BNB Smart Chain

    def validate(self) -> list[str]:
        problems = []
        if not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN is not set")
        if self.min_market_cap_usd >= self.max_market_cap_usd:
            problems.append("MIN_MARKET_CAP_USD must be less than MAX_MARKET_CAP_USD")
        return problems


settings = Settings()
