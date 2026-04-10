from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .base import MarketDataProvider, PriceData

logger = logging.getLogger(__name__)


class PriceCache:
    """Shared in-memory latest-price store populated by one background task."""

    def __init__(self) -> None:
        self._data: dict[str, PriceData] = {}
        self._task: asyncio.Task[None] | None = None

    def get(self, ticker: str) -> PriceData | None:
        return self._data.get(ticker)

    def get_all(self) -> dict[str, PriceData]:
        return dict(self._data)

    def get_price(self, ticker: str) -> float | None:
        entry = self._data.get(ticker)
        return entry.price if entry else None

    def start_loop(
        self,
        provider: MarketDataProvider,
        get_tickers: Callable[[], list[str]],
        poll_interval: float,
        push_callback: Callable[[list[PriceData]], None] | None = None,
    ) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Price cache loop already running")

        self._task = asyncio.create_task(
            self._run_loop(
                provider=provider,
                get_tickers=get_tickers,
                poll_interval=poll_interval,
                push_callback=push_callback,
            )
        )

    async def stop_loop(self) -> None:
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(
        self,
        provider: MarketDataProvider,
        get_tickers: Callable[[], list[str]],
        poll_interval: float,
        push_callback: Callable[[list[PriceData]], None] | None,
    ) -> None:
        logger.info("Price cache loop started with interval %.2fs", poll_interval)
        while True:
            tickers = sorted(set(get_tickers()))
            if tickers:
                try:
                    latest_prices = await provider.fetch_prices(tickers)
                    updated_entries: list[PriceData] = []
                    for ticker, new_price in latest_prices.items():
                        previous = self._data.get(ticker)
                        previous_price = previous.price if previous else new_price
                        entry = PriceData.from_update(ticker, new_price, previous_price)
                        self._data[ticker] = entry
                        updated_entries.append(entry)

                    if push_callback and updated_entries:
                        push_callback(updated_entries)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Unexpected error while updating the price cache")

            await asyncio.sleep(poll_interval)
