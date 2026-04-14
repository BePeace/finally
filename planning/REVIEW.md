# Review of `planning/PLAN.md`

## Findings

### 1. Trade execution is underspecified for concurrent requests, which can corrupt cash/position state
`planning/PLAN.md:171-173`, `planning/PLAN.md:275`, `planning/PLAN.md:304-305`, and `planning/PLAN.md:401` define trading against an in-memory price cache with validation on current cash/owned quantity, but the plan never requires atomic database transactions or any per-user locking around the read-modify-write sequence. In practice, two rapid requests from the trade bar, double-clicks, or overlapping chat/manual trades can both pass validation before either write commits, resulting in overspending cash or overselling shares. The plan should explicitly require transactional trade execution and define how concurrent requests are serialized.

### 2. The test plan conflicts with the shared bind-mounted SQLite database, so E2E runs will not be deterministic
`planning/PLAN.md:521` says the repo-root `db/` directory is the single source of truth for runtime persistence in local development and test runs, while `planning/PLAN.md:565-577` expects repeatable E2E scenarios such as a fresh start with the default watchlist and `$10k` cash. Reusing the same host-mounted database across test runs means prior trades/watchlist edits will leak into later runs unless every test manually resets the file. The plan should reserve an isolated database path or disposable volume for test execution instead of sharing the developer runtime database.

### 3. Chat history is persisted but there is no read API to restore it on page reload
`planning/PLAN.md:249-255` defines a persistent `chat_messages` table, and `planning/PLAN.md:397` says the backend reloads recent conversation history for LLM context, but `planning/PLAN.md:315-382` exposes only `POST /api/chat`. That leaves the frontend without a supported way to populate the “scrolling conversation history” after refresh or on first load, despite storing the data. Either the product should explicitly accept ephemeral UI history, or the API section needs a `GET /api/chat/history`-style contract.

### 4. The documented chat response example contradicts the portfolio contract
In the `POST /api/chat` example at `planning/PLAN.md:327-370`, the assistant reports an executed buy of 5 AAPL shares at `191.2`, and the returned `cash_balance` drops to `9044.0`, but the `portfolio.positions` array is still empty (`planning/PLAN.md:364-367`). That directly conflicts with the earlier `/api/portfolio` contract at `planning/PLAN.md:278-296`, where open positions must be returned and `total_value` includes their market value. This kind of example-level inconsistency is likely to leak into implementation and tests unless corrected.

### 5. The watchlist API contract is too thin for the UI it is supposed to power
`planning/PLAN.md:311-313` says `GET /api/watchlist` returns the current watchlist tickers with latest prices, but the frontend requirements at `planning/PLAN.md:473-487` need at least daily change, stale/disconnected state handling, and enough market fields to seed the watchlist immediately before SSE updates arrive. Unlike `/api/portfolio` and `/api/chat`, there is no response schema here, so frontend and backend agents can easily diverge on field names and completeness. The plan should define the watchlist response shape explicitly, ideally aligned with the SSE `snapshot` payload.

## Open Questions

- Is chat history intended to survive refreshes, or is persistence only for LLM context/audit?
- Should test infrastructure use a separate SQLite file under `test/` or a disposable tmp path/volume?
- Is trade execution expected to be safe under overlapping manual and AI-originated requests, or is single-flight enforcement acceptable?
