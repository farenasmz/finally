from __future__ import annotations

import logging
import math
import random

from .base import MarketDataProvider

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5
DRIFT = 0.10
SECONDS_PER_YEAR = 365.25 * 24 * 3600
DT = POLL_INTERVAL / SECONDS_PER_YEAR

EVENT_PROBABILITY = 0.002
EVENT_MAGNITUDE_MIN = 0.02
EVENT_MAGNITUDE_MAX = 0.05

SEED_PRICES = {
    "AAPL": 191.0,
    "MSFT": 415.0,
    "GOOGL": 175.0,
    "META": 505.0,
    "NVDA": 870.0,
    "AMZN": 185.0,
    "NFLX": 620.0,
    "JPM": 200.0,
    "V": 275.0,
    "TSLA": 250.0,
}

VOLATILITY = {
    "AAPL": 0.28,
    "MSFT": 0.26,
    "GOOGL": 0.30,
    "META": 0.38,
    "NVDA": 0.55,
    "AMZN": 0.32,
    "NFLX": 0.42,
    "JPM": 0.24,
    "V": 0.22,
    "TSLA": 0.65,
}
DEFAULT_VOLATILITY = 0.35

SECTORS = {
    "tech": {"AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMZN", "NFLX"},
    "finance": {"JPM", "V"},
    "ev": {"TSLA"},
}
SECTOR_CORRELATION = {"tech": 0.65, "finance": 0.55, "ev": 0.50}
DEFAULT_CORRELATION = 0.40


def get_correlation(ticker: str) -> float:
    for sector, tickers in SECTORS.items():
        if ticker in tickers:
            return SECTOR_CORRELATION[sector]
    return DEFAULT_CORRELATION


class SimulatorProvider(MarketDataProvider):
    _poll_interval: float = POLL_INTERVAL

    def __init__(self, rng: random.Random | None = None) -> None:
        self._prices: dict[str, float] = dict(SEED_PRICES)
        self._rng = rng or random.Random()

    async def start(self) -> None:
        logger.info("SimulatorProvider started")

    async def stop(self) -> None:
        return None

    async def fetch_prices(self, tickers: list[str]) -> dict[str, float]:
        for ticker in tickers:
            if ticker not in self._prices:
                self._prices[ticker] = 100.0

        z_market = self._rng.gauss(0.0, 1.0)
        prices: dict[str, float] = {}

        for ticker in tickers:
            current = self._prices[ticker]
            sigma = VOLATILITY.get(ticker, DEFAULT_VOLATILITY)
            rho = get_correlation(ticker)

            z_idio = self._rng.gauss(0.0, 1.0)
            z = rho * z_market + math.sqrt(1.0 - rho**2) * z_idio

            drift_term = (DRIFT - 0.5 * sigma**2) * DT
            diffusion_term = sigma * math.sqrt(DT) * z
            new_price = current * math.exp(drift_term + diffusion_term)

            if self._rng.random() < EVENT_PROBABILITY:
                magnitude = self._rng.uniform(EVENT_MAGNITUDE_MIN, EVENT_MAGNITUDE_MAX)
                direction = self._rng.choice((-1, 1))
                new_price *= 1.0 + direction * magnitude
                logger.debug(
                    "Simulator event on %s: %.1f%% %s",
                    ticker,
                    magnitude * 100.0,
                    "up" if direction > 0 else "down",
                )

            new_price = max(0.01, round(new_price, 4))
            self._prices[ticker] = new_price
            prices[ticker] = new_price

        return prices
