"""GeckoTerminal + DexScreener BNB Chain market data sources."""
import time
import httpx
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential
from ratelimit import RateLimiter

BASE_URL = "https://api.geckoterminal.com/api/v2"
DEXSCREENER_URL = "https://api.dexscreener.com"
HEADERS = {"Accept": "application/json;version=20230302"}
DEX_HEADERS = {"Accept": "application/json"}
_rate_limiter = RateLimiter(max_calls=10, period_seconds=60)
_dex_rate_limiter = RateLimiter(max_calls=180, period_seconds=60)

class PoolData:
    def __init__(self, raw: dict):
        attrs = raw.get("attributes", {})
        self.pool_address = attrs.get("address", "")
        self.name = attrs.get("name", "unknown")
        self.base_token_symbol = self.name.split("/")[0].strip() if "/" in self.name else self.name
        self.price_usd = _to_float(attrs.get("base_token_price_usd"))
        self.market_cap_usd = _to_float(attrs.get("market_cap_usd"))
        self.fdv_usd = _to_float(attrs.get("fdv_usd"))
        self.liquidity_usd = _to_float(attrs.get("reserve_in_usd"))
        vol = attrs.get("volume_usd", {}) or {}
        self.volume_24h_usd = _to_float(vol.get("h24"))
        self.volume_6h_usd = _to_float(vol.get("h6"))
        self.volume_1h_usd = _to_float(vol.get("h1"))
        pc = attrs.get("price_change_percentage", {}) or {}
        self.price_change_1h = _to_float(pc.get("h1"))
        self.price_change_6h = _to_float(pc.get("h6"))
        self.price_change_24h = _to_float(pc.get("h24"))
        txns = attrs.get("transactions", {}) or {}
        h1 = txns.get("h1", {}) or {}
        h6 = txns.get("h6", {}) or {}
        self.buys_1h = int(h1.get("buys") or 0)
        self.sells_1h = int(h1.get("sells") or 0)
        self.buys_6h = int(h6.get("buys") or 0)
        self.sells_6h = int(h6.get("sells") or 0)
        created_at = attrs.get("pool_created_at")
        self.created_at = _parse_dt(created_at)
        self.age_minutes = _age_minutes(self.created_at)
        self.dex_url = f"https://www.geckoterminal.com/bsc/pools/{self.pool_address}"
        rel = raw.get("relationships", {}) or {}
        base_token = rel.get("base_token", {}) or {}
        base_id = (base_token.get("data") or {}).get("id", "")
        self.token_address = base_id.split("_", 1)[1] if "_" in base_id else ""

def _to_float(val):
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0

def _parse_dt(val):
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None

def _age_minutes(created_at):
    if not created_at:
        return 0.0
    return (datetime.now(timezone.utc) - created_at).total_seconds() / 60.0

class GeckoTerminalClient:
    def __init__(self, network="bsc"):
        self.network = network
        self._client = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=15.0)
        self._dex_client = httpx.Client(base_url=DEXSCREENER_URL, headers=DEX_HEADERS, timeout=15.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _get(self, path, params=None):
        _rate_limiter.acquire()
        resp = self._client.get(path, params=params or {})
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try: time.sleep(min(float(retry_after), 30.0))
                except ValueError: pass
        resp.raise_for_status()
        return resp.json()

    def new_pools(self, page=1):
        data = self._get(f"/networks/{self.network}/new_pools", params={"page": page})
        return [PoolData(item) for item in data.get("data", [])]

    def trending_pools(self, page=1):
        data = self._get(f"/networks/{self.network}/trending_pools", params={"page": page})
        return [PoolData(item) for item in data.get("data", [])]

    def pool_detail(self, pool_address):
        data = self._get(f"/networks/{self.network}/pools/{pool_address}")
        item = data.get("data")
        return PoolData(item) if item else None

    def _dex_get(self, path, params=None):
        _dex_rate_limiter.acquire()
        resp = self._dex_client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    def dexscreener_market_cap(self, pool_address):
        data = self._dex_get(f"/latest/dex/pairs/{self.network}/{pool_address}")
        for pair in data.get("pairs") or []:
            if str(pair.get("pairAddress", "")).lower() == str(pool_address).lower():
                return _to_float(pair.get("marketCap"))
        return 0.0

    def token_pairs(self, token_address):
        """Return all DexScreener BSC pairs for a token address."""
        data = self._dex_get(f"/latest/dex/tokens/{token_address}")
        return [p for p in (data.get("pairs") or []) if str(p.get("chainId", "")).lower() == self.network]

    def search_token_pairs(self, token_address):
        """Fallback token discovery for tokens not returned by the token endpoint."""
        data = self._dex_get("/latest/dex/search", params={"q": token_address})
        return [p for p in (data.get("pairs") or []) if str(p.get("chainId", "")).lower() == self.network]

    def close(self):
        self._client.close()
        self._dex_client.close()
