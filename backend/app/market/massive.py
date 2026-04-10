from __future__ import annotations

import logging

import httpx

from .base import MarketDataProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.massive.com"


def extract_price(result: dict) -> float | None:
    """Return the best available price from a Massive snapshot payload."""
    last_trade = result.get("last_trade") or {}
    if last_trade.get("price") is not None:
        return float(last_trade["price"])

    session = result.get("session") or {}
    if session.get("close") is not None:
        return float(session["close"])

    prev_day = result.get("prev_day") or {}
    if prev_day.get("close") is not None:
        return float(prev_day["close"])

    return None


class MassiveProvider(MarketDataProvider):
    """
    REST polling client for the Massive multi-ticker snapshot endpoint.

    The provider is intentionally stateless apart from the shared AsyncClient so the
    polling loop can decide cadence and tracked symbols externally.
    """

    def __init__(
        self,
        api_key: str,
        poll_interval: float = 15.0,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        logger.info("MassiveProvider started with poll interval %.1fs", self._poll_interval)

    async def stop(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    async def fetch_prices(self, tickers: list[str]) -> dict[str, float]:
        if not tickers:
            return {}

        if self._client is None:
            logger.error("MassiveProvider.fetch_prices called before start()")
            return {}

        params = {
            "ticker.any_of": ",".join(sorted(set(tickers))),
            "limit": 250,
            "apiKey": self._api_key,
        }

        try:
            response = await self._client.get(f"{self._base_url}/v3/snapshot", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Massive API returned HTTP %s for %s",
                exc.response.status_code,
                params["ticker.any_of"],
            )
            return {}
        except httpx.RequestError as exc:
            logger.warning("Massive API request failed: %s", exc)
            return {}

        prices: dict[str, float] = {}
        for result in payload.get("results") or []:
            ticker = result.get("ticker")
            price = extract_price(result)
            if ticker and price is not None:
                prices[ticker] = price

        return prices
