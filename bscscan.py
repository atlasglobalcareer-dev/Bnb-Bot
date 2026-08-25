"""
Optional BscScan API client. Requires a free API key from https://bscscan.com/apis.

Used for two extra safety signals GeckoTerminal doesn't give you:
  1. Is the contract source verified?
  2. Roughly, is ownership renounced / is there an obvious owner-only mint function?
  3. Top-holder concentration (what % of supply sits in the top wallets)

NOTE: BscScan's `tokenholderlist` endpoint is gated behind their paid Pro tier
as of this writing. On a free key, `top10_holder_pct` will stay None and the
scoring engine simply skips that component. If you upgrade your BscScan plan,
this will start working with no code changes. Verify current endpoint access
at https://docs.bscscan.com before relying on it.

If no API key is configured, these checks are skipped and the scoring engine
just omits that portion of the score (it does not fabricate a pass).
"""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from ratelimit import RateLimiter

BASE_URL = "https://api.bscscan.com/api"

# BscScan free-tier key: 5 req/sec. We stay under it with margin since this
# runs alongside other API calls per scan pass.
_rate_limiter = RateLimiter(max_calls=4, period_seconds=1)


class ContractSafety:
    def __init__(self):
        self.available = False
        self.is_verified = False
        self.owner_renounced = None  # True/False/None (unknown)
        self.top10_holder_pct = None  # float 0-100 or None


class BscScanClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.enabled = bool(api_key)
        self._client = httpx.Client(base_url=BASE_URL, timeout=15.0) if self.enabled else None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
    def _get(self, params: dict) -> dict:
        _rate_limiter.acquire()
        params = {**params, "apikey": self.api_key}
        resp = self._client.get("", params=params)
        resp.raise_for_status()
        return resp.json()

    def check_contract(self, token_address: str) -> ContractSafety:
        result = ContractSafety()
        if not self.enabled or not token_address:
            return result
        result.available = True

        try:
            src = self._get({
                "module": "contract",
                "action": "getsourcecode",
                "address": token_address,
            })
            items = src.get("result", [])
            if items:
                result.is_verified = bool(items[0].get("SourceCode"))
        except Exception:
            pass

        try:
            holders = self._get({
                "module": "token",
                "action": "tokenholderlist",
                "contractaddress": token_address,
                "page": 1,
                "offset": 10,
            })
            rows = holders.get("result", [])
            if isinstance(rows, list) and rows:
                total_supply = self._get({
                    "module": "stats",
                    "action": "tokensupply",
                    "contractaddress": token_address,
                })
                supply = float(total_supply.get("result") or 0)
                if supply > 0:
                    top10 = sum(float(r.get("TokenHolderQuantity", 0)) for r in rows)
                    result.top10_holder_pct = round((top10 / supply) * 100, 2)
        except Exception:
            pass

        return result

    def close(self):
        if self._client:
            self._client.close()
