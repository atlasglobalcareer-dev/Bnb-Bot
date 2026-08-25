"""
Thin client around the GeckoTerminal public API (no key required).
Docs: https://apiguide.geckoterminal.com/

We use three endpoints:
  - /networks/bsc/new_pools        -> freshly created pools
  - /networks/bsc/trending_pools   -> pools with rising activity
  - /networks/bsc/pools/{address}  -> full detail for one pool

GeckoTerminal rate-limits public (no key) usage to ~30 req/min. The polling
loop in scanner.py is deliberately conservative to stay well under that.
"""
import httpx
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential
from ratelimit import RateLimiter

BASE_URL = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json;version=20230302"}

# Observed safe ceiling for unauthenticated GeckoTerminal usage.
_rate_limiter = RateLimiter(max_calls=28, period_seconds=60)


class PoolData:
    """Normalized view over a single GeckoTerminal pool record."""

    def __init__(self, raw: dict):
        attrs = raw.get("attributes", {})
        self.pool_address: str = attrs.get("address", "")
        self.name: str = attrs.get("name", "unknown")
        self.base_token_symbol: str = self.name.split("/")[0].strip() if "/" in self.name else self.name

        self.price_usd: float = _to_float(attrs.get("base_token_price_usd"))
        self.market_cap_usd: float = _to_float(attrs.get("market_cap_usd")) or _to_float(attrs.get("fdv_usd"))
        self.fdv_usd: float = _to_float(attrs.get("fdv_usd"))

        reserve = attrs.get("reserve_in_usd")
        self.liquidity_usd: float = _to_float(reserve)

        vol = attrs.get("volume_usd", {}) or {}
        self.volume_24h_usd: float = _to_float(vol.get("h24"))
        self.volume_6h_usd: float = _to_float(vol.get("h6"))
        self.volume_1h_usd: float = _to_float(vol.get("h1"))

        pc = attrs.get("price_change_percentage", {}) or {}
        self.price_change_1h: float = _to_float(pc.get("h1"))
        self.price_change_6h: float = _to_float(pc.get("h6"))
        self.price_change_24h: float = _to_float(pc.get("h24"))

        txns = attrs.get("transactions", {}) or {}
        h1 = txns.get("h1", {}) or {}
        h6 = txns.get("h6", {}) or {}
        self.buys_1h: int = int(h1.get("buys") or 0)
        self.sells_1h: int = int(h1.get("sells") or 0)
        self.buys_6h: int = int(h6.get("buys") or 0)
        self.sells_6h: int = int(h6.get("sells") or 0)

        created_at = attrs.get("pool_created_at")
        self.created_at = _parse_dt(created_at)
        self.age_minutes: float = _age_minutes(self.created_at)

        self.dex_url: str = f"https://www.geckoterminal.com/bsc/pools/{self.pool_address}"

        # base token contract address, needed for BscScan lookups
        rel = raw.get("relationships", {}) or {}
        base_token = rel.get("base_token", {}) or {}
        base_id = (base_token.get("data") or {}).get("id", "")
        # id format is usually "bsc_0xTOKENADDRESS"
        self.token_address: str = base_id.split("_", 1)[1] if "_" in base_id else ""


def _to_float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(val: str):
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_minutes(created_at) -> float:
    if not created_at:
        return 0.0
    delta = datetime.now(timezone.utc) - created_at
    return delta.total_seconds() / 60.0


class GeckoTerminalClient:
    def __init__(self, network: str = "bsc"):
        self.network = network
        self._client = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=15.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get(self, path: str, params: dict | None = None) -> dict:
        _rate_limiter.acquire()
        resp = self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    def new_pools(self, page: int = 1) -> list[PoolData]:
        data = self._get(f"/networks/{self.network}/new_pools", params={"page": page})
        return [PoolData(item) for item in data.get("data", [])]

    def trending_pools(self, page: int = 1) -> list[PoolData]:
        data = self._get(f"/networks/{self.network}/trending_pools", params={"page": page})
        return [PoolData(item) for item in data.get("data", [])]

    def pool_detail(self, pool_address: str) -> PoolData | None:
        data = self._get(f"/networks/{self.network}/pools/{pool_address}")
        item = data.get("data")
        return PoolData(item) if item else None

    def close(self):
        self._client.close()
