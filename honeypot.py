"""Honeypot check via honeypot.is public API (free, no key required)."""
import httpx
from dataclasses import dataclass
from tenacity import retry, stop_after_attempt, wait_exponential
from ratelimit import RateLimiter

BASE_URL = "https://api.honeypot.is/v2"
_rate_limiter = RateLimiter(max_calls=1, period_seconds=1.2)

@dataclass
class HoneypotResult:
    checked: bool = False
    is_honeypot: bool | None = None
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

    def _check_once(self, token_address: str, pair_address: str | None) -> HoneypotResult:
        params = {"address": token_address, "chainID": 56}
        if pair_address:
            params["pair"] = pair_address
        data = self._get("/IsHoneypot", params)
        hp = data.get("honeypotResult", {}) or {}
        sim = data.get("simulationResult", {}) or {}
        result = HoneypotResult(
            checked=True,
            is_honeypot=hp.get("isHoneypot"),
            buy_tax=sim.get("buyTax"),
            sell_tax=sim.get("sellTax"),
            simulation_error=hp.get("honeypotReason"),
        )
        if result.is_honeypot is None:
            result.checked = False
            result.simulation_error = result.simulation_error or "API returned no definitive honeypot result"
        return result

    def check(self, token_address: str, pair_address: str | None = None) -> HoneypotResult:
        if not token_address:
            return HoneypotResult(simulation_error="missing token address")
        try:
            result = self._check_once(token_address, pair_address)
            if result.checked:
                return result
            # Some newly-created BSC pairs are not recognized correctly when
            # the pair hint is supplied. Retry by token only before declaring
            # the safety check unavailable.
            if pair_address:
                try:
                    fallback = self._check_once(token_address, None)
                    if fallback.checked:
                        return fallback
                    result.simulation_error = fallback.simulation_error or result.simulation_error
                except Exception as e:
                    result.simulation_error = f"pair and token-only checks failed: {e}"
            return result
        except Exception as first_error:
            if pair_address:
                try:
                    return self._check_once(token_address, None)
                except Exception as second_error:
                    return HoneypotResult(
                        simulation_error=f"pair check failed: {first_error}; token-only check failed: {second_error}"
                    )
            return HoneypotResult(simulation_error=str(first_error))

    def close(self):
        self._client.close()
