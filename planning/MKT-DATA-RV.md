# Market Data Subsystem — Code Review

**Date:** 2026-05-22  
**Reviewer:** Claude (claude-sonnet-4-6)  
**Scope:** `backend/app/market/` (8 source files, ~350 LOC) and `backend/tests/market/` (6 test files, ~350 LOC)  
**Documents reviewed:** `planning/PLAN.md`, `planning/MARKET_DATA_SUMMARY.md`, `planning/MARKET_INTERFACE.md`, `planning/MARKET_SIMULATOR.md`, `planning/MASSIVE_API.md`, `planning/DECISIONS.md`, `planning/archive/MARKET_DATA_DESIGN.md`, `planning/archive/MARKET_DATA_REVIEW.md`

---

## 1. Test Results

**73 / 73 tests passing. Zero failures. Zero skips.**

```
============================= 73 passed in 1.83s ==============================
```

This is a significant improvement over the prior review (archive/MARKET_DATA_REVIEW.md), which recorded 68/73 passing with 5 failures in `test_massive.py`.

### Coverage

```
Name                           Stmts   Miss  Cover
---------------------------------------------------
app/market/__init__.py             6      0   100%
app/market/cache.py               39      0   100%
app/market/factory.py             15      0   100%
app/market/interface.py           13      0   100%
app/market/models.py              26      0   100%
app/market/seed_prices.py          8      0   100%
app/market/massive_client.py      67      4    94%
app/market/simulator.py          139      3    98%
app/market/stream.py              36     24    33%
---------------------------------------------------
TOTAL                            349     31    91%
```

Coverage gaps explained:

- **`massive_client.py` (94%, lines 85–87, 125):** Lines 85–87 are in `_poll_loop` — the second-and-beyond iterations never execute in tests because `stop()` is called before the 10s sleep completes. Line 125 is the real `get_snapshot_all()` call inside `_fetch_snapshots`, which is always mocked. Both gaps are expected and acceptable.
- **`simulator.py` (98%, lines 149, 268–269):** Line 149 is the duplicate-ticker guard inside `_add_ticker_internal` (only hit on batch init of an already-added ticker). Lines 268–269 are the `logger.exception` in the exception handler of `_run_loop`, which requires triggering a step failure. Both are low-risk uncovered lines.
- **`stream.py` (33%, lines 26–87):** The entire SSE endpoint and generator function are untested. See finding 3 below.

### Lint

**Zero warnings.** `ruff check app/ tests/` passes clean.

---

## 2. Fixes Applied Since Last Review

All seven issues from the prior code review (archive/MARKET_DATA_REVIEW.md) were correctly resolved:

| # | Issue | Resolution |
|---|-------|-----------|
| 1 | `pyproject.toml` missing wheel build config | ✅ `[tool.hatch.build.targets.wheel] packages = ["app"]` present |
| 2 | Lazy imports causing test failures | ✅ `massive` and `SnapshotMarketType` moved to module-level imports in `massive_client.py` |
| 3 | `_generate_events` annotated `-> None` instead of `-> AsyncGenerator[str, None]` | ✅ Fixed; correct annotation now present at `stream.py:55` |
| 4 | Unused imports in test files | ✅ All clean; `ruff` passes with zero warnings |
| 5 | `GBMSimulator` lacked public `get_tickers()` | ✅ Added at `simulator.py:140–142` |
| 6 | `SimulatorDataSource.get_tickers()` accessed private `_sim._tickers` | ✅ Now calls `self._sim.get_tickers()` at `simulator.py:258` |
| 7 | Confusing `DEFAULT_CORR` constant duplicating `CROSS_GROUP_CORR` | ✅ Removed; `seed_prices.py` now has a single `CROSS_GROUP_CORR = 0.3` with the comment "Between sectors / unknown tickers" |

---

## 3. New Findings

### 3.1 SSE Protocol Does Not Match Specification (Severity: High)

`stream.py` implements a simple "dump all prices on version change" SSE stream, but `PLAN.md` §6 and `DECISIONS.md` specify a named-event protocol:

| Event | Trigger | Status |
|-------|---------|--------|
| `snapshot` | Immediately on connect — full current watchlist payload | **Missing** |
| `price` | Per changed ticker | **Not implemented** (all tickers sent as one unnamed event) |
| `watchlist` | After add/remove — so client doesn't need to reconnect | **Missing** |
| `heartbeat` | Every ~15s when idle | **Missing** |

The current implementation sends:
```
data: {"AAPL": {...}, "GOOGL": {...}, ...}

```

The plan calls for:
```
event: snapshot
data: {"tickers": [...]}

event: price
data: {"ticker": "AAPL", "price": 190.50, ...}

event: heartbeat
data: {}
```

The absence of named events means the frontend `EventSource` handler cannot distinguish between initial state, incremental updates, watchlist changes, and heartbeats. The frontend team will be blocked on implementing their SSE client until this is resolved.

Additionally, the plan specifies that the `price` event includes `previous_close` and `change_pct` (daily, vs. previous close). The current implementation only stores `previous_price` per tick, not a session-start previous close. The MARKET_SIMULATOR.md doc explicitly states: "This is handled in `stream.py`" — but it is not.

**Recommendation:** `stream.py` needs a significant revision before the frontend connects to it. The simplest path is to:
1. Track `previous_close` per ticker as a dict inside `_generate_events` (set from seed prices on connect, never updated)
2. Emit a `snapshot` event immediately on connect
3. Emit `price` events per-changed-ticker (rather than bulk updates)
4. Add a heartbeat timer for when no prices change (Massive free-tier at 15s intervals)
5. Emit `watchlist` events when the watchlist changes — this will require a coordination mechanism between the watchlist API route and the SSE generator

### 3.2 `stream.py` Has No Tests (Severity: Medium)

At 33% coverage, the SSE streaming path — which is the primary real-time data delivery mechanism for the entire frontend — has zero functional tests. The covered lines are only imports and the function signature.

The `_generate_events` generator handles client disconnect, version polling, JSON serialization, and SSE framing. Any regression in this code would be invisible to the test suite.

**Recommendation:** Add at least a basic integration test using `httpx.AsyncClient` with the ASGI app. A minimal test should verify:
- The response is `text/event-stream`
- A `retry:` directive is present
- At least one data event is emitted after a cache update

Given finding 3.1 above, this test should be written against the corrected named-event protocol, not the current implementation.

### 3.3 Module-Level Router Singleton (Severity: Low)

`stream.py:17` creates a module-level `router = APIRouter(...)`. When `create_stream_router(price_cache)` is called, it registers `/prices` on this shared router instance. If the function is called a second time (e.g., in a test that creates a fresh app), the route is registered twice on the same router object, causing duplicate route warnings and potentially undefined behavior.

```python
# stream.py:17 — problematic
router = APIRouter(prefix="/api/stream", tags=["streaming"])

def create_stream_router(price_cache: PriceCache) -> APIRouter:
    @router.get("/prices")   # registered on the shared singleton
    async def stream_prices(...):
        ...
    return router
```

**Recommendation:** Create the router inside `create_stream_router()` rather than at module level:
```python
def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])
    @router.get("/prices")
    ...
    return router
```

### 3.4 `PriceCache.version` Not Read Under Lock (Severity: Low)

`cache.py:65–67`:
```python
@property
def version(self) -> int:
    return self._version
```

All other reads go through `with self._lock`. On CPython (GIL), reading a single `int` is atomic, so this is safe today. However, the project `requires-python = ">=3.12"` and the tests run on Python 3.13.7. Python 3.13 includes experimental free-threaded builds (PEP 703). If the project is ever run on a no-GIL Python binary, this becomes a genuine race between the writer incrementing `_version` and the SSE reader observing a stale value.

**Recommendation:** Acquire the lock in the `version` property for correctness, or add a comment explaining the CPython-GIL assumption.

### 3.5 `timestamp or time.time()` Masks Zero Timestamp (Severity: Low)

`cache.py:29`:
```python
ts = timestamp or time.time()
```

If `timestamp=0.0` is passed (the Unix epoch — January 1, 1970), this evaluates as falsy and is replaced with `time.time()`. The Massive API returns millisecond timestamps that are divided by 1000 before storage — these will never be 0.0 for real data. Still, the semantics are wrong: `None` and `0.0` should not be treated identically.

**Recommendation:** `ts = timestamp if timestamp is not None else time.time()`

---

## 4. Architecture Assessment

### 4.1 Strengths

- **Strategy pattern is clean and well-executed.** `SimulatorDataSource` and `MassiveDataSource` both conform fully to the `MarketDataSource` ABC. Downstream code (factory, SSE, future portfolio routes) is entirely source-agnostic.
- **GBM math is correct.** The formula `S(t+dt) = S(t) * exp((mu - 0.5*sigma²)*dt + sigma*sqrt(dt)*Z)` is the standard discretized GBM. The tiny `dt` (~8.5e-8) produces sub-cent moves per tick that accumulate naturally — exactly what the plan calls for.
- **Cholesky correlation is realistic.** Sector-based correlation (tech 0.6, finance 0.5, cross 0.3, TSLA 0.3) produces believable co-movement. The matrix is rebuilt on add/remove, which is correct.
- **`PriceCache` is the right architectural choice.** Single point of truth, thread-safe (important for the Massive client which runs in `asyncio.to_thread`), and the `version` counter efficiently drives SSE change detection without comparing individual prices.
- **Defensive error handling in both data sources.** Both `_run_loop` and `_poll_once` catch exceptions and continue — essential for a long-running background service.
- **Immediate cache seeding.** Both `SimulatorDataSource.start()` and the Massive client's initial `_poll_once()` write to the cache before the background loop starts, so the first SSE client connection gets data immediately.
- **Test quality is high.** Tests are well-named, properly isolated, cover edge cases (duplicate add, nonexistent remove, malformed snapshots, API errors), and don't over-mock. The asyncio integration tests correctly use `pytest-asyncio`.

### 4.2 Missing Coverage Areas

- **No concurrent/thread-safety test for `PriceCache`.** Correctness under concurrent writes is asserted by inspection of the lock usage, not by a test. A stress test with multiple writer threads would catch any future regression.
- **No test for all 10 default tickers together.** All simulator tests use 1–2 tickers. A test confirming the full 10-ticker Cholesky decomposition succeeds and produces well-behaved prices would add confidence that the correlation matrix is positive-definite in the default configuration.
- **No SSE integration test.** See finding 3.2.

---

## 5. Verdict

| Category | Status |
|----------|--------|
| Tests | **73/73 passing** |
| Lint | **Clean (0 warnings)** |
| Coverage | **91% overall** |
| Prior review issues | **All 7 resolved** |
| Architecture | **Solid** |
| Production readiness | **Not yet — SSE protocol mismatch blocks frontend** |

The market data backend is well-implemented. The simulator, price cache, abstract interface, factory, and Massive client are all production-quality and fully tested.

**The single blocking issue is `stream.py`**: the current SSE implementation diverges from the protocol contract specified in `PLAN.md` and `DECISIONS.md`. The frontend team cannot complete their SSE client against the current endpoint — it lacks named events, `snapshot` on connect, `watchlist` event support, heartbeats, and `previous_close` tracking for daily change percentage.

### Priority Order

**Must fix before frontend integration:**
1. Revise `stream.py` to emit named SSE events (`snapshot`, `price`, `watchlist`, `heartbeat`) per `PLAN.md` §6 and `DECISIONS.md` — and add `previous_close` tracking for daily change %

**Should fix:**
2. Fix the module-level router singleton in `stream.py` (create `router` inside the factory function)
3. Add at least one SSE integration test using `httpx.AsyncClient`
4. Fix `timestamp or time.time()` → `timestamp if timestamp is not None else time.time()` in `cache.py`

**Nice to have:**
5. Lock the `version` property read in `PriceCache` (or document the CPython-GIL assumption)
6. Add a full 10-ticker Cholesky test to `test_simulator.py`
7. Add a concurrent-write stress test to `test_cache.py`
