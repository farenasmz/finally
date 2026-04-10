from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .market import get_provider
from .market.base import PriceData
from .market.cache import PriceCache
from .market.simulator import SEED_PRICES

logger = logging.getLogger(__name__)

DEFAULT_TICKERS = tuple(SEED_PRICES.keys())
_client_queues: list[asyncio.Queue[PriceData]] = []


def broadcast_price_updates(updates: list[PriceData]) -> None:
    stale_queues: list[asyncio.Queue[PriceData]] = []
    for queue in list(_client_queues):
        try:
            for update in updates:
                queue.put_nowait(update)
        except RuntimeError:
            stale_queues.append(queue)

    for queue in stale_queues:
        if queue in _client_queues:
            _client_queues.remove(queue)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    provider = get_provider()
    await provider.start()

    price_cache = PriceCache()
    tracked_tickers: set[str] = set(DEFAULT_TICKERS)
    poll_interval = getattr(provider, "_poll_interval", 0.5)

    price_cache.start_loop(
        provider=provider,
        get_tickers=lambda: sorted(tracked_tickers),
        poll_interval=poll_interval,
        push_callback=broadcast_price_updates,
    )

    app.state.provider = provider
    app.state.price_cache = price_cache
    app.state.tracked_tickers = tracked_tickers

    try:
        yield
    finally:
        await price_cache.stop_loop()
        await provider.stop()


app = FastAPI(title="FinAlly Market Data Backend", lifespan=lifespan)


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    queue: asyncio.Queue[PriceData] = asyncio.Queue()
    _client_queues.append(queue)

    async def event_generator() -> AsyncIterator[str]:
        try:
            cache: PriceCache = request.app.state.price_cache
            for entry in cache.get_all().values():
                yield f"data: {json.dumps(entry.to_sse_dict())}\n\n"

            while True:
                if await request.is_disconnected():
                    break

                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(entry.to_sse_dict())}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            if queue in _client_queues:
                _client_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
