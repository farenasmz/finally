from __future__ import annotations

import asyncio
import random

from app.market.simulator import SEED_PRICES, SimulatorProvider


def test_simulator_prices_stay_positive_after_many_ticks() -> None:
    provider = SimulatorProvider(rng=random.Random(7))

    latest: dict[str, float] = {}
    for _ in range(250):
        latest = asyncio.run(provider.fetch_prices(list(SEED_PRICES)))

    assert latest
    assert all(price > 0 for price in latest.values())


def test_unknown_ticker_is_seeded_near_100() -> None:
    provider = SimulatorProvider(rng=random.Random(11))

    prices = asyncio.run(provider.fetch_prices(["FAKE"]))

    assert "FAKE" in prices
    assert abs(prices["FAKE"] - 100.0) / 100.0 < 0.05
