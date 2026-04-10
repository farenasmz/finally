# Market Data Interface — Design

This document defines the unified Python interface for market data in FinAlly, and describes how
the backend selects between the Massive API provider and the built-in simulator.

---

## Design Goals

1. **Single interface** — all downstream code (`/api/stream/prices`, portfolio valuation, watchlist
   endpoint) talks to one abstraction and never knows whether prices come from Massive or the sim.
2. **Environment-variable switching** — no code change needed; just set or unset `MASSIVE_API_KEY`.
3. **Async-first** — the backend uses FastAPI (async); providers must be `async`.
4. **In-memory price cache** — one background task writes to a shared dict; SSE readers poll it.
   No locking required: Python's GIL protects dict reads/writes, and stale-by-one-cycle is fine.

---

## File Layout

```
backend/
└── app/
    └── market/
        ├── __init__.py          # exports: get_provider(), PriceData
        ├── base.py              # MarketDataProvider ABC + PriceData dataclass
        ├── massive.py           # MassiveProvider (REST polling)
        ├── simulator.py         # SimulatorProvider (GBM)
        └── cache.py             # PriceCache: shared in-memory store + background task
```

---

## Core Types

```python
# backend/app/market/base.py

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PriceData:
    ticker: str
    price: float
    prev_price: float          # price from previous update cycle
    timestamp: datetime
    direction: str             # "up" | "down" | "flat"

    @classmethod
    def from_update(cls, ticker: str, new_price: float, old_price: float) -> "PriceData":
        if new_price > old_price:
            direction = "up"
        elif new_price < old_price:
            direction = "down"
        else:
            direction = "flat"
        return cls(
            ticker=ticker,
            price=new_price,
            prev_price=old_price,
            timestamp=datetime.utcnow(),
            direction=direction,
        )

    def to_sse_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "prev_price": self.prev_price,
            "timestamp": self.timestamp.isoformat() + "Z",
            "direction": self.direction,
        }


class MarketDataProvider(ABC):
    """
    Abstract base for all market data sources.

    Implementations must be safe to call concurrently from asyncio tasks.
    They must NOT block the event loop — use `httpx.AsyncClient` (not `requests`)
    and `asyncio.sleep` (not `time.sleep`).
    """

    @abstractmethod
    async def start(self) -> None:
        """
        Called once at application startup. Use for connection setup,
        initial price fetch, or starting internal background tasks.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Called at application shutdown. Cancel background tasks, close connections."""
        ...

    @abstractmethod
    async def fetch_prices(self, tickers: list[str]) -> dict[str, float]:
        """
        Fetch current prices for the given tickers.

        Returns a dict of {ticker: price}. Tickers that cannot be resolved
        (unknown symbol, API error) are omitted from the result — callers must
        handle missing tickers gracefully.

        This method may be called at any time by the price cache background task.
        It should be resilient to transient errors (log and return partial results).
        """
        ...
```

---

## Provider Factory

```python
# backend/app/market/__init__.py

import os
from .base import MarketDataProvider, PriceData
from .massive import MassiveProvider
from .simulator import SimulatorProvider


def get_provider() -> MarketDataProvider:
    """
    Returns the appropriate market data provider based on environment config.

    If MASSIVE_API_KEY is set and non-empty, returns a MassiveProvider.
    Otherwise returns a SimulatorProvider.

    Call once at application startup and store the result on app.state.
    """
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveProvider(api_key=api_key)
    return SimulatorProvider()


__all__ = ["get_provider", "MarketDataProvider", "PriceData"]
```

---

## Massive Provider

```python
# backend/app/market/massive.py

import asyncio
import logging
import httpx
from .base import MarketDataProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.massive.com"


class MassiveProvider(MarketDataProvider):
    """
    Fetches live stock prices from the Massive (formerly Polygon.io) REST API.
    Uses the /v3/snapshot endpoint to batch-fetch up to 250 tickers per call.
    """

    def __init__(self, api_key: str, poll_interval: float = 15.0) -> None:
        self._api_key = api_key
        self._poll_interval = poll_interval  # seconds between polls
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info(
            "MassiveProvider started (poll_interval=%.1fs)", self._poll_interval
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_prices(self, tickers: list[str]) -> dict[str, float]:
        if not tickers:
            return {}
        if self._client is None:
            logger.error("MassiveProvider.fetch_prices called before start()")
            return {}

        params = {
            "ticker.any_of": ",".join(tickers),
            "limit": 250,
            "apiKey": self._api_key,
        }
        try:
            resp = await self._client.get(f"{BASE_URL}/v3/snapshot", params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Massive API HTTP %s for tickers %s: %s",
                e.response.status_code, tickers, e,
            )
            return {}
        except httpx.RequestError as e:
            logger.warning("Massive API request error: %s", e)
            return {}

        prices: dict[str, float] = {}
        for result in data.get("results") or []:
            ticker = result.get("ticker")
            price = _extract_price(result)
            if ticker and price is not None:
                prices[ticker] = price

        return prices


def _extract_price(result: dict) -> float | None:
    """Extract the best available price from a /v3/snapshot result object."""
    last_trade = result.get("last_trade") or {}
    if price := last_trade.get("price"):
        return float(price)
    session = result.get("session") or {}
    if price := session.get("close"):
        return float(price)
    prev_day = result.get("prev_day") or {}
    if price := prev_day.get("close"):
        return float(price)
    return None
```

---

## Price Cache

The price cache is the shared in-memory store that decouples the polling/simulation loop from
the SSE stream. One background task writes; multiple SSE connections read.

```python
# backend/app/market/cache.py

import asyncio
import logging
from datetime import datetime
from .base import MarketDataProvider, PriceData

logger = logging.getLogger(__name__)


class PriceCache:
    """
    In-memory store of the latest PriceData for every tracked ticker.

    Written by a single background task (the market data loop).
    Read by SSE stream handlers and API endpoints.

    Thread/async safety: Python dict reads and writes are GIL-protected.
    The single-writer pattern means no additional locking is needed.
    """

    def __init__(self) -> None:
        self._data: dict[str, PriceData] = {}
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public read interface (used by SSE, portfolio valuation, watchlist)
    # ------------------------------------------------------------------

    def get(self, ticker: str) -> PriceData | None:
        return self._data.get(ticker)

    def get_all(self) -> dict[str, PriceData]:
        """Returns a shallow copy — safe to iterate while the loop updates."""
        return dict(self._data)

    def get_price(self, ticker: str) -> float | None:
        entry = self._data.get(ticker)
        return entry.price if entry else None

    # ------------------------------------------------------------------
    # Lifecycle (called by FastAPI lifespan)
    # ------------------------------------------------------------------

    def start_loop(
        self,
        provider: MarketDataProvider,
        get_tickers: "Callable[[], list[str]]",
        poll_interval: float,
        push_callback: "Callable[[list[PriceData]], None] | None" = None,
    ) -> None:
        """
        Starts the background polling/simulation loop.

        provider        — the MarketDataProvider to call for prices
        get_tickers     — zero-arg callable returning the current ticker list
                          (union of watchlist + open positions); called each cycle
        poll_interval   — seconds between fetch cycles
        push_callback   — optional callback invoked with new PriceData objects
                          after each cycle; used by SSE to notify waiting clients
        """
        self._task = asyncio.create_task(
            self._loop(provider, get_tickers, poll_interval, push_callback)
        )

    async def stop_loop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(
        self,
        provider: MarketDataProvider,
        get_tickers,
        poll_interval: float,
        push_callback,
    ) -> None:
        logger.info("Price cache loop started (interval=%.1fs)", poll_interval)
        while True:
            tickers = get_tickers()
            if tickers:
                try:
                    new_prices = await provider.fetch_prices(tickers)
                    updated: list[PriceData] = []
                    for ticker, price in new_prices.items():
                        prev = self._data.get(ticker)
                        old_price = prev.price if prev else price
                        entry = PriceData.from_update(ticker, price, old_price)
                        self._data[ticker] = entry
                        updated.append(entry)
                    if push_callback and updated:
                        push_callback(updated)
                except Exception:
                    logger.exception("Unexpected error in price cache loop")
            await asyncio.sleep(poll_interval)
```

---

## FastAPI Integration

Wire everything together in the application lifespan:

```python
# backend/app/main.py (relevant excerpt)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from .market import get_provider
from .market.cache import PriceCache
from .db import get_active_tickers   # returns union of watchlist + open positions

price_cache = PriceCache()

@asynccontextmanager
async def lifespan(app: FastAPI):
    provider = get_provider()
    await provider.start()

    # Simulator uses 500ms; Massive uses 15s on free tier
    poll_interval = getattr(provider, "_poll_interval", 0.5)

    price_cache.start_loop(
        provider=provider,
        get_tickers=lambda: get_active_tickers(),
        poll_interval=poll_interval,
        push_callback=broadcast_price_updates,   # notifies SSE clients
    )

    app.state.price_cache = price_cache
    app.state.provider = provider

    yield

    await price_cache.stop_loop()
    await provider.stop()

app = FastAPI(lifespan=lifespan)
```

---

## SSE Stream Endpoint

```python
# backend/app/routes/stream.py

import asyncio
import json
from fastapi import Request
from fastapi.responses import StreamingResponse
from ..market.base import PriceData

# Each connected SSE client registers a queue here.
# The push_callback (wired in main.py) puts PriceData into every queue.
_client_queues: list[asyncio.Queue] = []


def broadcast_price_updates(updates: list[PriceData]) -> None:
    """Called by the price cache loop after each fetch cycle."""
    for queue in list(_client_queues):
        for update in updates:
            queue.put_nowait(update)


async def price_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _client_queues.append(queue)

    async def event_generator():
        try:
            # Send current snapshot immediately on connect
            cache = request.app.state.price_cache
            for entry in cache.get_all().values():
                yield f"data: {json.dumps(entry.to_sse_dict())}\n\n"

            # Then stream live updates
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry: PriceData = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(entry.to_sse_dict())}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"   # keep connection alive
        finally:
            _client_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

---

## Watchlist + Portfolio Price Lookup

```python
# In portfolio/watchlist route handlers:

async def get_watchlist(request: Request):
    cache = request.app.state.price_cache
    tickers = db.get_watchlist_tickers()
    result = []
    for ticker in tickers:
        entry = cache.get(ticker)
        result.append({
            "ticker": ticker,
            "price": entry.price if entry else None,
            "direction": entry.direction if entry else "flat",
        })
    return result


def get_current_price(cache, ticker: str) -> float | None:
    """Use for portfolio valuation (P&L calculation)."""
    return cache.get_price(ticker)
```

---

## Behavior Summary

| Condition                         | Provider used      | Poll interval | Data source          |
|-----------------------------------|--------------------|---------------|----------------------|
| `MASSIVE_API_KEY` unset or empty  | `SimulatorProvider`| 500 ms        | GBM math (in-process)|
| `MASSIVE_API_KEY` = valid key     | `MassiveProvider`  | 15 s (free)   | Massive REST API     |

The SSE stream pushes updates immediately after each fetch/simulation cycle. In simulator mode,
clients see ~500ms refresh. In Massive free-tier mode, clients see updates every 15 seconds.
