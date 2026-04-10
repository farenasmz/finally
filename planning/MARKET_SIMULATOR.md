# Market Simulator — Design

This document describes the approach and code structure for the built-in market simulator,
which generates realistic-feeling stock prices without any external API calls.

---

## Goals

- Generate prices that **look plausible** — smooth continuous motion, not random jumps
- Support all tickers in the default watchlist with realistic seed prices
- Produce **correlated moves** — tech stocks move together, as in real markets
- Occasionally fire **random events** — sudden 2–5% spikes for drama
- Run entirely **in-process** with no I/O, so it works offline and in CI
- Implement the same `MarketDataProvider` interface as `MassiveProvider`, making it a drop-in

---

## Price Model: Geometric Brownian Motion (GBM)

GBM is the standard continuous-time model for stock prices. Each tick:

```
S(t+dt) = S(t) * exp((μ - σ²/2) * dt + σ * √dt * Z)
```

Where:
- `S(t)` — current price
- `μ` — drift (annualized expected return, ~0.10 for equities)
- `σ` — volatility (annualized, ~0.30 for individual stocks)
- `dt` — time step (fraction of a year; 500ms ≈ 1.585e-8 years)
- `Z` — standard normal random variable

Because `dt` is tiny, the practical effect is:

```
new_price ≈ current_price * (1 + small_random_perturbation)
```

This produces continuous, never-negative prices with realistic variance.

---

## Correlated Moves

Real stocks in the same sector are correlated — when AAPL rises, MSFT usually rises too.
We achieve this with a **common market factor**:

```
Z_i = ρ * Z_market + sqrt(1 - ρ²) * Z_idiosyncratic_i
```

Where:
- `Z_market` — a single market-wide random draw shared across all tickers each tick
- `Z_idiosyncratic_i` — per-ticker independent noise
- `ρ` — correlation coefficient (0.6 = 60% correlated with market)

Tickers in the same sector get the same `ρ`. This means tech stocks all nudge in the same
direction about 60% of the time.

---

## Random Events

Every tick, each ticker has a small probability of an "event" — a sudden price move of 2–5%.
This adds drama and makes the demo feel alive.

```python
EVENT_PROBABILITY = 0.002   # 0.2% chance per tick (~once every 500 ticks ≈ 4 min)
EVENT_MAGNITUDE   = (0.02, 0.05)   # uniform draw from 2%–5%, direction random
```

Events are one-tick impulses — the price jumps, then continues normal GBM from the new level.

---

## Sector Groupings and Seed Prices

Seed prices reflect realistic 2026 levels. Each sector shares a correlation group.

```python
SEED_PRICES = {
    # Tech / Large Cap Growth (ρ = 0.65)
    "AAPL":  191.0,
    "MSFT":  415.0,
    "GOOGL": 175.0,
    "META":  505.0,
    "NVDA":  870.0,
    "AMZN":  185.0,
    "NFLX":  620.0,
    # Finance (ρ = 0.55)
    "JPM":   200.0,
    "V":     275.0,
    # EV / Auto (ρ = 0.50)
    "TSLA":  250.0,
}

SECTORS = {
    "tech":    ["AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMZN", "NFLX"],
    "finance": ["JPM", "V"],
    "ev":      ["TSLA"],
}

SECTOR_CORRELATION = {
    "tech":    0.65,
    "finance": 0.55,
    "ev":      0.50,
}

# Per-ticker annualized volatility
VOLATILITY = {
    "AAPL":  0.28,
    "MSFT":  0.26,
    "GOOGL": 0.30,
    "META":  0.38,
    "NVDA":  0.55,
    "AMZN":  0.32,
    "NFLX":  0.42,
    "JPM":   0.24,
    "V":     0.22,
    "TSLA":  0.65,
}
DEFAULT_VOLATILITY = 0.35   # for tickers added to the watchlist at runtime
```

---

## Full Implementation

```python
# backend/app/market/simulator.py

from __future__ import annotations
import asyncio
import logging
import math
import random
from .base import MarketDataProvider

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5          # seconds between ticks
DRIFT         = 0.10         # annualized drift (~10% annual return)
SECONDS_PER_YEAR = 365.25 * 24 * 3600
DT = POLL_INTERVAL / SECONDS_PER_YEAR   # time step as fraction of a year

EVENT_PROBABILITY = 0.002
EVENT_MAGNITUDE_MIN = 0.02
EVENT_MAGNITUDE_MAX = 0.05

SEED_PRICES = {
    "AAPL":  191.0,
    "MSFT":  415.0,
    "GOOGL": 175.0,
    "META":  505.0,
    "NVDA":  870.0,
    "AMZN":  185.0,
    "NFLX":  620.0,
    "JPM":   200.0,
    "V":     275.0,
    "TSLA":  250.0,
}

VOLATILITY = {
    "AAPL":  0.28, "MSFT":  0.26, "GOOGL": 0.30, "META":  0.38,
    "NVDA":  0.55, "AMZN":  0.32, "NFLX":  0.42, "JPM":   0.24,
    "V":     0.22, "TSLA":  0.65,
}
DEFAULT_VOLATILITY = 0.35

SECTORS = {
    "tech":    {"AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMZN", "NFLX"},
    "finance": {"JPM", "V"},
    "ev":      {"TSLA"},
}
SECTOR_CORRELATION = {"tech": 0.65, "finance": 0.55, "ev": 0.50}
DEFAULT_CORRELATION = 0.40   # for tickers not in any sector


def _get_correlation(ticker: str) -> float:
    for sector, tickers in SECTORS.items():
        if ticker in tickers:
            return SECTOR_CORRELATION[sector]
    return DEFAULT_CORRELATION


class SimulatorProvider(MarketDataProvider):
    """
    Generates synthetic stock prices using Geometric Brownian Motion.
    Implements the same MarketDataProvider interface as MassiveProvider.
    """

    _poll_interval: float = POLL_INTERVAL   # read by cache.py to set loop frequency

    def __init__(self) -> None:
        # Current prices for all known tickers (grows as new tickers are added)
        self._prices: dict[str, float] = dict(SEED_PRICES)

    async def start(self) -> None:
        logger.info("SimulatorProvider started")

    async def stop(self) -> None:
        pass   # nothing to clean up

    async def fetch_prices(self, tickers: list[str]) -> dict[str, float]:
        """
        Advance GBM by one tick and return the new prices for all requested tickers.
        Tickers not yet in the simulator are seeded at $100 on first encounter.
        """
        # Seed any new tickers
        for ticker in tickers:
            if ticker not in self._prices:
                self._prices[ticker] = 100.0

        # Draw one market-wide factor (shared by all tickers)
        z_market = random.gauss(0.0, 1.0)

        result: dict[str, float] = {}
        for ticker in tickers:
            current = self._prices[ticker]
            sigma = VOLATILITY.get(ticker, DEFAULT_VOLATILITY)
            rho = _get_correlation(ticker)

            # Correlated normal draw
            z_idio = random.gauss(0.0, 1.0)
            z = rho * z_market + math.sqrt(1.0 - rho ** 2) * z_idio

            # GBM step
            drift_term = (DRIFT - 0.5 * sigma ** 2) * DT
            diffusion_term = sigma * math.sqrt(DT) * z
            new_price = current * math.exp(drift_term + diffusion_term)

            # Random event (rare impulse)
            if random.random() < EVENT_PROBABILITY:
                magnitude = random.uniform(EVENT_MAGNITUDE_MIN, EVENT_MAGNITUDE_MAX)
                direction = random.choice([-1, 1])
                new_price *= 1.0 + direction * magnitude
                logger.debug("Event on %s: %.1f%% %s", ticker,
                             magnitude * 100, "up" if direction > 0 else "down")

            # Floor at $0.01 (GBM is theoretically always positive but guard anyway)
            new_price = max(0.01, round(new_price, 4))
            self._prices[ticker] = new_price
            result[ticker] = new_price

        return result
```

---

## How the Simulator Fits Into the Architecture

The `SimulatorProvider` is selected when `MASSIVE_API_KEY` is absent:

```
Application startup
       │
       ▼
get_provider()  ──→  SimulatorProvider()   (no API key)
       │
       ▼
PriceCache.start_loop(provider, get_tickers, poll_interval=0.5)
       │
       ▼
Every 500ms:
  tickers = get_tickers()                 (watchlist ∪ open positions)
  new_prices = await provider.fetch_prices(tickers)
  for each ticker: update PriceCache._data
  push_callback(updated_entries)          (wakes up SSE queues)
       │
       ▼
SSE clients receive price updates ~2x/second
```

The simulator's `fetch_prices` is pure CPU — no I/O — so 500ms feels instantaneous.

---

## Testing the Simulator

Unit tests should verify:

1. **GBM math** — prices never go negative, don't jump more than ~20% in a single tick under
   normal conditions (no event), stay in a reasonable range after 1000 ticks
2. **Interface conformance** — `SimulatorProvider` passes the same contract tests as
   `MassiveProvider` (both implement `MarketDataProvider`, both return `{ticker: float}`)
3. **Seed prices** — after `start()`, calling `fetch_prices(["AAPL"])` once returns a value
   within a few percent of `SEED_PRICES["AAPL"]`
4. **New tickers** — tickers not in `SEED_PRICES` are seeded at $100 and follow GBM normally

```python
# backend/tests/market/test_simulator.py

import asyncio
import pytest
from app.market.simulator import SimulatorProvider, SEED_PRICES


@pytest.fixture
def sim():
    return SimulatorProvider()


def test_prices_not_negative(sim):
    for _ in range(1000):
        prices = asyncio.run(sim.fetch_prices(list(SEED_PRICES.keys())))
    assert all(p > 0 for p in prices.values())


def test_seed_price_close_to_expected(sim):
    prices = asyncio.run(sim.fetch_prices(["AAPL"]))
    assert abs(prices["AAPL"] - SEED_PRICES["AAPL"]) / SEED_PRICES["AAPL"] < 0.05


def test_unknown_ticker_seeded_at_100(sim):
    prices = asyncio.run(sim.fetch_prices(["FAKE"]))
    assert abs(prices["FAKE"] - 100.0) / 100.0 < 0.05


def test_interface_returns_all_tickers(sim):
    tickers = ["AAPL", "TSLA", "NVDA"]
    prices = asyncio.run(sim.fetch_prices(tickers))
    assert set(prices.keys()) == set(tickers)
```

---

## Tuning Parameters

All constants live at the top of `simulator.py` and can be tweaked for demo purposes:

| Constant             | Default    | Effect of increasing                          |
|----------------------|------------|-----------------------------------------------|
| `POLL_INTERVAL`      | `0.5`      | Faster UI updates (higher CPU)                |
| `DRIFT`              | `0.10`     | Prices trend upward more aggressively         |
| `VOLATILITY[ticker]` | varies     | Larger tick-to-tick swings                    |
| `EVENT_PROBABILITY`  | `0.002`    | More frequent sudden spikes                   |
| `EVENT_MAGNITUDE_MAX`| `0.05`     | Larger sudden moves (5% max → more dramatic)  |
| `DEFAULT_CORRELATION`| `0.40`     | More sector-wide co-movement                  |

For a classroom demo, boosting `EVENT_PROBABILITY` to `0.01` and `EVENT_MAGNITUDE_MAX` to `0.08`
makes the heatmap and P&L chart more visually exciting.
