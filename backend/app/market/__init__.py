from __future__ import annotations

import os

from .base import MarketDataProvider, PriceData
from .massive import MassiveProvider
from .simulator import SimulatorProvider


def get_provider() -> MarketDataProvider:
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveProvider(api_key=api_key)
    return SimulatorProvider()


__all__ = ["MarketDataProvider", "PriceData", "get_provider"]
