from __future__ import annotations

import asyncio

import httpx

from app.market.massive import MassiveProvider, extract_price


def test_extract_price_prefers_last_trade_then_fallbacks() -> None:
    assert extract_price({"last_trade": {"price": 123.45}}) == 123.45
    assert extract_price({"session": {"close": 120.0}}) == 120.0
    assert extract_price({"prev_day": {"close": 119.0}}) == 119.0
    assert extract_price({}) is None


def test_massive_provider_parses_snapshot_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/snapshot"
        assert request.url.params["ticker.any_of"] == "AAPL,MSFT"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"ticker": "AAPL", "last_trade": {"price": 191.5}},
                    {"ticker": "MSFT", "session": {"close": 414.0}},
                    {"ticker": "BAD"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.massive.com")
    provider = MassiveProvider(api_key="test-key", client=client)

    asyncio.run(provider.start())
    prices = asyncio.run(provider.fetch_prices(["MSFT", "AAPL"]))
    asyncio.run(provider.stop())
    asyncio.run(client.aclose())

    assert prices == {"AAPL": 191.5, "MSFT": 414.0}
