from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class PriceData:
    ticker: str
    price: float
    prev_price: float
    timestamp: datetime
    direction: str

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
            price=float(new_price),
            prev_price=float(old_price),
            timestamp=datetime.now(timezone.utc),
            direction=direction,
        )

    def to_sse_dict(self) -> dict[str, str | float]:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "prev_price": self.prev_price,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "direction": self.direction,
        }


class MarketDataProvider(ABC):
    @abstractmethod
    async def start(self) -> None:
        """Prepare the provider for use."""

    @abstractmethod
    async def stop(self) -> None:
        """Release any resources owned by the provider."""

    @abstractmethod
    async def fetch_prices(self, tickers: list[str]) -> dict[str, float]:
        """Return the latest prices for the requested tickers."""
