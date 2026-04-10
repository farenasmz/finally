# Massive API (formerly Polygon.io) — Reference for FinAlly

Polygon.io rebranded to **Massive** on October 30, 2025. All existing API keys continue to work.
Both base URLs are supported during the transition period:

- New: `https://api.massive.com`
- Legacy: `https://api.polygon.io`

This document focuses on the REST endpoints relevant to FinAlly's market-data polling approach.
We use **REST polling** (not WebSockets) for simplicity — no persistent connections, works on all
API tiers including free.

---

## Authentication

Pass your API key as a query parameter on every request:

```
?apiKey=YOUR_MASSIVE_API_KEY
```

Or via the `Authorization` header:

```
Authorization: Bearer YOUR_MASSIVE_API_KEY
```

Query parameter is simpler for `httpx`/`requests` usage. The header approach avoids logging the
key in URLs, but either works.

---

## Rate Limits

| Tier     | Limit                     | Suggested poll interval |
|----------|---------------------------|-------------------------|
| Free     | 5 requests / minute       | ≥ 15 seconds            |
| Starter  | Unlimited (no WebSocket)  | 5–10 seconds            |
| Developer| Unlimited + WebSocket      | 2–5 seconds             |
| Advanced | Unlimited + WebSocket      | 1–2 seconds             |

For FinAlly's default configuration, assume the free tier: one snapshot request every 15 seconds.

---

## Key Endpoints

### 1. Multi-Ticker Snapshot (primary endpoint for live prices)

Fetches the latest trade price, session OHLCV, and today's change for up to 250 tickers in a
single call. This is the main endpoint used by the Massive poller.

```
GET /v3/snapshot
```

**Query parameters:**

| Parameter      | Type   | Description                                          |
|----------------|--------|------------------------------------------------------|
| `ticker.any_of`| string | Comma-separated ticker list, e.g. `AAPL,MSFT,GOOGL` |
| `limit`        | int    | Max results (default 10, max 250)                    |
| `apiKey`       | string | Your API key                                         |

**Example request:**

```python
import httpx

BASE_URL = "https://api.massive.com"

def fetch_snapshots(tickers: list[str], api_key: str) -> dict:
    params = {
        "ticker.any_of": ",".join(tickers),
        "limit": 250,
        "apiKey": api_key,
    }
    resp = httpx.get(f"{BASE_URL}/v3/snapshot", params=params, timeout=10.0)
    resp.raise_for_status()
    return resp.json()

data = fetch_snapshots(["AAPL", "MSFT", "GOOGL"], "YOUR_KEY")
```

**Example response:**

```json
{
  "request_id": "abc123def456",
  "status": "OK",
  "results": [
    {
      "ticker": "AAPL",
      "type": "stocks",
      "name": "Apple Inc.",
      "market_status": "open",
      "last_trade": {
        "price": 191.45,
        "size": 100,
        "timeframe": "REAL-TIME"
      },
      "session": {
        "open": 189.80,
        "close": 190.20,
        "high": 192.10,
        "low": 189.40,
        "volume": 42500000,
        "change": 1.25,
        "change_percent": 0.66
      },
      "prev_day": {
        "close": 190.20,
        "volume": 51200000
      }
    }
  ],
  "next_url": null
}
```

**Extracting the price from a result object:**

```python
def extract_price(result: dict) -> float | None:
    """Return the best available price from a /v3/snapshot result."""
    # Prefer last_trade.price (real-time when market is open)
    last_trade = result.get("last_trade") or {}
    if price := last_trade.get("price"):
        return float(price)
    # Fall back to session close (end-of-day or pre-open)
    session = result.get("session") or {}
    if price := session.get("close"):
        return float(price)
    # Last resort: previous day close
    prev_day = result.get("prev_day") or {}
    if price := prev_day.get("close"):
        return float(price)
    return None
```

**Pagination:** If the watchlist exceeds 250 tickers, use the `next_url` field to fetch the next
page. FinAlly's default watchlist is 10 tickers so this is not an issue in practice.

---

### 2. Single Ticker Snapshot (alternative / debugging)

Returns the same data as above for one ticker, via the v2 endpoint:

```
GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
```

**Example:**

```python
def fetch_single_snapshot(ticker: str, api_key: str) -> dict:
    url = f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    resp = httpx.get(url, params={"apiKey": api_key}, timeout=10.0)
    resp.raise_for_status()
    return resp.json()
```

**Example response:**

```json
{
  "status": "OK",
  "ticker": {
    "ticker": "AAPL",
    "todaysChange": 1.25,
    "todaysChangePerc": 0.66,
    "updated": 1712700000000000000,
    "day": {
      "o": 189.80,
      "h": 192.10,
      "l": 189.40,
      "c": 191.45,
      "v": 42500000,
      "vw": 191.02
    },
    "lastTrade": {
      "p": 191.45,
      "s": 100,
      "t": 1712699998123456789
    },
    "lastQuote": {
      "P": 191.50,
      "S": 2,
      "p": 191.44,
      "s": 3
    },
    "prevDay": {
      "o": 188.50,
      "h": 190.80,
      "l": 188.10,
      "c": 190.20,
      "v": 51200000,
      "vw": 189.74
    }
  }
}
```

Note the v2 endpoint uses single-letter field names (`o`, `h`, `l`, `c`, `v`, `vw`) for OHLCV
data. The v3 endpoint uses full names (`open`, `high`, etc.).

---

### 3. Previous Day Close (end-of-day price)

Returns the previous trading day's open, high, low, close, and volume:

```
GET /v2/aggs/ticker/{ticker}/prev
```

**Example:**

```python
def fetch_previous_close(ticker: str, api_key: str) -> float | None:
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/prev"
    resp = httpx.get(url, params={"adjusted": "true", "apiKey": api_key}, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    if results:
        return float(results[0]["c"])  # closing price
    return None
```

**Example response:**

```json
{
  "ticker": "AAPL",
  "queryCount": 1,
  "resultsCount": 1,
  "adjusted": true,
  "results": [
    {
      "T": "AAPL",
      "v": 51200000,
      "vw": 189.74,
      "o": 188.50,
      "c": 190.20,
      "h": 190.80,
      "l": 188.10,
      "t": 1712620800000,
      "n": 412537
    }
  ],
  "status": "OK",
  "request_id": "def789abc123"
}
```

---

### 4. Historical Daily Aggregates (for charts)

Returns OHLCV bars for a date range. Useful if you want to pre-populate the chart with historical
data rather than waiting for SSE to accumulate:

```
GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
```

**Example — fetch last 30 trading days of daily bars for AAPL:**

```python
def fetch_daily_bars(ticker: str, from_date: str, to_date: str, api_key: str) -> list[dict]:
    """
    from_date, to_date: ISO date strings like "2026-03-01"
    Returns list of {timestamp_ms, open, high, low, close, volume} dicts.
    """
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 120, "apiKey": api_key}
    resp = httpx.get(url, params=params, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    bars = []
    for r in data.get("results", []):
        bars.append({
            "timestamp_ms": r["t"],
            "open": r["o"],
            "high": r["h"],
            "low": r["l"],
            "close": r["c"],
            "volume": r["v"],
        })
    return bars
```

---

### 5. Open-Close (specific date EOD summary)

Returns the official open, high, low, close for a specific date. Useful for verifying or
backfilling a specific day:

```
GET /v1/open-close/{ticker}/{date}
```

**Example:**

```python
def fetch_open_close(ticker: str, date: str, api_key: str) -> dict | None:
    """date: ISO date string like '2026-04-09'"""
    url = f"{BASE_URL}/v1/open-close/{ticker}/{date}"
    resp = httpx.get(url, params={"adjusted": "true", "apiKey": api_key}, timeout=10.0)
    if resp.status_code == 404:
        return None  # market was closed that day
    resp.raise_for_status()
    return resp.json()
```

**Example response:**

```json
{
  "status": "OK",
  "from": "2026-04-09",
  "symbol": "AAPL",
  "open": 189.80,
  "high": 192.10,
  "low": 189.40,
  "close": 191.45,
  "volume": 42500000,
  "afterHours": 191.80,
  "preMarket": 189.50,
  "otc": false
}
```

---

## Error Handling

The API returns HTTP 4xx/5xx for errors. Common cases to handle:

| Status | Cause                                    | Action                          |
|--------|------------------------------------------|---------------------------------|
| 403    | Invalid or missing API key               | Log error, fall back to sim     |
| 404    | Ticker not found or market closed        | Return `None` for that ticker   |
| 429    | Rate limit exceeded                      | Back off, increase poll interval|
| 5xx    | Server error                             | Log, retry on next poll cycle   |

**Recommended pattern:**

```python
import httpx
import logging

logger = logging.getLogger(__name__)

async def safe_fetch_snapshots(
    tickers: list[str], api_key: str
) -> dict[str, float]:
    """Returns {ticker: price} for tickers that resolved successfully."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            params = {
                "ticker.any_of": ",".join(tickers),
                "limit": 250,
                "apiKey": api_key,
            }
            resp = await client.get(f"{BASE_URL}/v3/snapshot", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Massive API HTTP error %s: %s", e.response.status_code, e)
        return {}
    except httpx.RequestError as e:
        logger.warning("Massive API request error: %s", e)
        return {}

    prices: dict[str, float] = {}
    for result in data.get("results") or []:
        ticker = result.get("ticker")
        price = extract_price(result)
        if ticker and price is not None:
            prices[ticker] = price
    return prices
```

---

## Python SDK (optional)

Massive provides an official Python client:

```bash
pip install polygon-api-client
```

```python
from polygon import RESTClient

client = RESTClient(api_key="YOUR_KEY")

# Snapshot for multiple tickers
for snapshot in client.list_snapshot_all_tickers(
    locale="us", market_type="stocks", tickers=["AAPL", "MSFT"]
):
    print(snapshot.ticker, snapshot.day.c)
```

For FinAlly, direct `httpx` calls are preferred to avoid an extra dependency and keep the
integration transparent.

---

## Market Hours and Data Freshness

- **Market open:** 9:30 AM – 4:00 PM ET, Monday–Friday (excluding holidays)
- **Pre-market:** 4:00 AM – 9:30 AM ET (data available on paid tiers)
- **After-hours:** 4:00 PM – 8:00 PM ET (data available on paid tiers)
- **Weekends/holidays:** `last_trade.price` is from the last session; the snapshot still returns
  stale but valid data — `market_status` will be `"closed"`

During market hours, `last_trade.price` reflects the most recent transaction.
Outside market hours, fall back to `session.close` or `prev_day.close`.
