"""Tests for SSE streaming — drives _generate_events() directly to avoid httpx body-drain hang."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_request(disconnected: bool = False) -> AsyncMock:
    req = AsyncMock()
    req.client = None
    req.is_disconnected = AsyncMock(return_value=disconnected)
    return req


def _parse_chunk(chunk: str) -> dict | None:
    """Parse one SSE chunk into {event, data}; returns None for non-event chunks."""
    result: dict = {}
    for line in chunk.split("\n"):
        line = line.strip()
        if line.startswith("event:"):
            result["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            result["data"] = json.loads(line[len("data:"):].strip())
    return result if "event" in result else None


async def _next_event(gen, timeout: float = 1.0) -> dict:
    """Advance generator until the next named SSE event; raises TimeoutError if none arrive."""
    while True:
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=timeout)
        parsed = _parse_chunk(chunk)
        if parsed is not None:
            return parsed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSSEStream:

    async def test_response_is_event_stream(self):
        """HTTP response headers: 200, text/event-stream, no-cache."""
        cache = PriceCache()
        app = FastAPI()
        app.include_router(create_stream_router(cache))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            try:
                async with asyncio.timeout(3.0):
                    async with client.stream("GET", "/api/stream/prices") as response:
                        assert response.status_code == 200
                        assert "text/event-stream" in response.headers["content-type"]
                        assert response.headers.get("cache-control") == "no-cache"
            except TimeoutError:
                pass  # expected — the stream never ends, we just needed the headers

    async def test_snapshot_event_on_connect(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        cache.update("GOOGL", 175.25)

        gen = _generate_events(cache, _mock_request(), poll_interval=0.05)
        try:
            evt = await _next_event(gen)
            assert evt["event"] == "snapshot"
            tickers = {t["ticker"]: t for t in evt["data"]["tickers"]}
            assert set(tickers.keys()) == {"AAPL", "GOOGL"}
            assert tickers["AAPL"]["price"] == 190.50
            assert tickers["AAPL"]["previous_close"] == 190.50
            assert tickers["AAPL"]["change_pct"] == 0.0
            assert "direction" in tickers["AAPL"]
        finally:
            await gen.aclose()

    async def test_snapshot_empty_cache(self):
        cache = PriceCache()
        gen = _generate_events(cache, _mock_request(), poll_interval=0.05)
        try:
            evt = await _next_event(gen)
            assert evt["event"] == "snapshot"
            assert evt["data"]["tickers"] == []
        finally:
            await gen.aclose()

    async def test_price_event_on_update(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)

        gen = _generate_events(cache, _mock_request(), poll_interval=0.02)
        try:
            snapshot = await _next_event(gen)
            assert snapshot["event"] == "snapshot"

            cache.update("AAPL", 191.50)
            price_evt = await _next_event(gen)

            assert price_evt["event"] == "price"
            d = price_evt["data"]
            assert d["ticker"] == "AAPL"
            assert d["price"] == 191.50
            assert d["previous_close"] == 190.00
            # (191.50 - 190.00) / 190.00 * 100 ≈ 0.7895
            assert abs(d["change_pct"] - 0.7895) < 0.001
            assert d["direction"] == "up"
        finally:
            await gen.aclose()

    async def test_no_price_event_when_price_unchanged(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)

        gen = _generate_events(cache, _mock_request(), poll_interval=0.02)
        try:
            snapshot = await _next_event(gen)
            assert snapshot["event"] == "snapshot"

            # Do NOT update the cache; wait a couple of poll cycles
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                await _next_event(gen, timeout=0.15)
        finally:
            await gen.aclose()

    async def test_watchlist_added_event(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)

        gen = _generate_events(cache, _mock_request(), poll_interval=0.02)
        try:
            snapshot = await _next_event(gen)
            assert snapshot["event"] == "snapshot"

            cache.update("GOOGL", 175.00)
            wl_evt = await _next_event(gen)

            assert wl_evt["event"] == "watchlist"
            d = wl_evt["data"]
            assert d["action"] == "added"
            assert d["ticker"] == "GOOGL"
            assert "AAPL" in d["watchlist"]
            assert "GOOGL" in d["watchlist"]
        finally:
            await gen.aclose()

    async def test_watchlist_removed_event(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)

        gen = _generate_events(cache, _mock_request(), poll_interval=0.02)
        try:
            snapshot = await _next_event(gen)
            assert snapshot["event"] == "snapshot"

            cache.remove("GOOGL")
            wl_evt = await _next_event(gen)

            assert wl_evt["event"] == "watchlist"
            d = wl_evt["data"]
            assert d["action"] == "removed"
            assert d["ticker"] == "GOOGL"
            assert "GOOGL" not in d["watchlist"]
            assert "AAPL" in d["watchlist"]
        finally:
            await gen.aclose()

    async def test_previous_close_is_session_baseline(self):
        """previous_close is the price at connect time, not updated on subsequent ticks."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)

        gen = _generate_events(cache, _mock_request(), poll_interval=0.02)
        try:
            await _next_event(gen)  # snapshot

            cache.update("AAPL", 191.00)
            tick1 = await _next_event(gen)
            assert tick1["data"]["previous_close"] == 190.00

            cache.update("AAPL", 192.00)
            tick2 = await _next_event(gen)
            # previous_close must still be the session-open price, not 191.00
            assert tick2["data"]["previous_close"] == 190.00
        finally:
            await gen.aclose()

    async def test_create_stream_router_is_not_singleton(self):
        """Each call to create_stream_router() returns a new router."""
        cache = PriceCache()
        r1 = create_stream_router(cache)
        r2 = create_stream_router(cache)
        assert r1 is not r2

    async def test_heartbeat_emitted_when_idle(self):
        """Heartbeat fires after heartbeat_interval with no price changes."""
        cache = PriceCache()
        gen = _generate_events(
            cache, _mock_request(), poll_interval=0.02, heartbeat_interval=0.06
        )
        try:
            snapshot = await _next_event(gen)
            assert snapshot["event"] == "snapshot"

            hb = await _next_event(gen, timeout=1.0)
            assert hb["event"] == "heartbeat"
        finally:
            await gen.aclose()
