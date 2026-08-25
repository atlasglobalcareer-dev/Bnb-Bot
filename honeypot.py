"""
Honeypot check via honeypot.is public API (free, no key required).
Docs: https://api.honeypot.is/

This is the single highest-value safety check in this whole project: it
actually simulates a buy + sell against the live contract/router and tells
you whether selling reverts, or what the effective buy/sell tax is. Liquidity
and holder-concentration heuristics can only ever *suggest* rug risk — this
check directly answers "can I get out."

If the API is unreachable or the token isn't recognized, we return an
"unknown" result rather than pretending it's safe. The scanner treats
"unknown" as neutral, not as a pass.
"""
import httpx
from dataclasses import dataclass
from tenacity import retry, stop_after_attempt, wait_exponential
from ratelimit import RateLimiter

BASE_URL = "https://api.honeypot.is/v2"

# honeypot.is doesn't publish a rate limit. Kept deliberately conservative
# since a 429 here means a token silently skips the sellability check
# entirely (see the "unknown -> neutral" fallback in scoring.py) — better
# to go slow than to lose the most important safety check in the pipeline.
_rate_limiter = RateLimiter(max_calls=1, period_seconds=1.2)


@dataclass
class HoneypotResult:
    checked: bool = False           # did we get a real answer back
    is_honeypot: bool | None = None  # True/False/None (unknown)
    buy_tax: float | None = None
    sell_tax: float | None = None
    simulation_error: str | None = None


class HoneypotClient:
    def __init__(self):
        self._client = httpx.Client(base_url=BASE_URL, timeout=15.0)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=6))
    def _get(self, path: str, params: dict) -> dict:
        _rate_limiter.acquire()
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def check(self, token_address: str, pair_address: str | None = None) -> HoneypotResult:
        result = HoneypotResult()
        if not token_address:
            return result
        try:
            params = {"address": token_address, "chainID": 56}  # 56 = BSC
            if pair_address:
                params["pair"] = pair_address
            data = self._get("/IsHoneypot", params)

            honeypot_result = data.get("honeypotResult", {}) or {}
            simulation = data.get("simulationResult", {}) or {}

            result.checked = True
            result.is_honeypot = honeypot_result.get("isHoneypot")
            result.simulation_error = honeypot_result.get("honeypotReason")
            result.buy_tax = simulation.get("buyTax")
            result.sell_tax = simulation.get("sellTax")
        except Exception as e:
            # Network error, rate limit, or token not recognized yet (very new
            # pools sometimes aren't indexed for a few minutes). Stay honest
            # about not knowing rather than defaulting to "safe".
            result.checked = False
            result.simulation_error = str(e)
        return result

    def close(self):
        self._client.close()
