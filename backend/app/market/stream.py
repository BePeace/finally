"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache
from .models import PriceUpdate

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.1       # seconds between cache polls (per MARKET_INTERFACE.md)
_HEARTBEAT_INTERVAL = 15.0  # seconds before emitting a heartbeat when idle


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create the SSE streaming router with a reference to the price cache.

    Creates a fresh router on each call — safe to call in tests.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        Named events emitted on this stream:
          snapshot  — full current watchlist payload, sent immediately on connect
          price     — single ticker update whenever its price changes
          watchlist — ticker added or removed from the active set
          heartbeat — keepalive when no other events fire for ~15 seconds
        """
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable nginx buffering when proxied
            },
        )

    return router


def _price_payload(update: PriceUpdate, previous_close: float) -> dict:
    """Build the data dict for a price or snapshot ticker entry."""
    change_pct = (
        round((update.price - previous_close) / previous_close * 100, 4)
        if previous_close
        else 0.0
    )
    return {
        **update.to_dict(),
        "previous_close": previous_close,
        "change_pct": change_pct,
    }


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    poll_interval: float = _POLL_INTERVAL,
    heartbeat_interval: float = _HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Async generator yielding named SSE events.

    Protocol:
      - Sends ``retry: 1000`` so the browser reconnects within 1 s on drop.
      - Sends a ``snapshot`` event immediately on connect.
      - Sends ``price`` events as individual tickers change.
      - Sends ``watchlist`` events when tickers appear or disappear in the cache.
      - Sends ``heartbeat`` every ~heartbeat_interval seconds when otherwise idle.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    yield "retry: 1000\n\n"

    # Capture current state; these prices become the session's previous-close baseline.
    current = price_cache.get_all()
    previous_close: dict[str, float] = {t: u.price for t, u in current.items()}
    # Capture version before yielding so any updates during the yield are detected next poll.
    last_version = price_cache.version

    snapshot_tickers = [_price_payload(u, previous_close[t]) for t, u in current.items()]
    yield f"event: snapshot\ndata: {json.dumps({'tickers': snapshot_tickers})}\n\n"

    # Tracking state for incremental change detection
    known_tickers: set[str] = set(current.keys())
    last_prices: dict[str, float] = {t: u.price for t, u in current.items()}
    last_event_time = time.monotonic()

    try:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            current_version = price_cache.version
            now = time.monotonic()

            if current_version != last_version:
                last_version = current_version
                all_prices = price_cache.get_all()
                current_tickers = set(all_prices.keys())

                # --- watchlist changes ---
                added = current_tickers - known_tickers
                removed = known_tickers - current_tickers

                for ticker in sorted(added):
                    update = all_prices[ticker]
                    previous_close[ticker] = update.price  # seed price = daily baseline
                    known_tickers.add(ticker)
                    last_prices[ticker] = update.price
                    payload = {"action": "added", "ticker": ticker, "watchlist": sorted(known_tickers)}
                    yield f"event: watchlist\ndata: {json.dumps(payload)}\n\n"
                    last_event_time = now

                for ticker in sorted(removed):
                    known_tickers.discard(ticker)
                    last_prices.pop(ticker, None)
                    previous_close.pop(ticker, None)
                    payload = {"action": "removed", "ticker": ticker, "watchlist": sorted(known_tickers)}
                    yield f"event: watchlist\ndata: {json.dumps(payload)}\n\n"
                    last_event_time = now

                # --- per-ticker price events ---
                for ticker in sorted(known_tickers):
                    update = all_prices.get(ticker)
                    if update is None:
                        continue
                    if last_prices.get(ticker) != update.price:
                        last_prices[ticker] = update.price
                        prev_close = previous_close.get(ticker, update.price)
                        payload = _price_payload(update, prev_close)
                        yield f"event: price\ndata: {json.dumps(payload)}\n\n"
                        last_event_time = now

            # --- heartbeat when idle ---
            if now - last_event_time >= heartbeat_interval:
                yield "event: heartbeat\ndata: {}\n\n"
                last_event_time = now

            await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
        raise
