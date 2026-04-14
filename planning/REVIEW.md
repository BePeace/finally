# Review of `planning/PLAN.md`

## Findings

### High

1. **The environment variable contract contradicts the documented mock-mode workflow.**  
   `OPENROUTER_API_KEY` is marked as required in the environment section, but later the plan says `LLM_MOCK=true` supports development and E2E runs without an API key. Those two statements produce different bootstrap behavior for the backend and Docker scripts. The plan should explicitly say whether the API key is required only when `LLM_MOCK=false`.  
   References: `planning/PLAN.md:123-140`, `planning/PLAN.md:363-382`

2. **The Docker persistence model is internally inconsistent.**  
   The plan first specifies a named Docker volume with `docker run -v finally-data:/app/db ...`, then immediately says the project-root `db/` directory maps to `/app/db` in the container. Those are different deployment models with different local-development behavior. Agents implementing scripts and compose files will make incompatible choices unless one source of truth is selected.  
   References: `planning/PLAN.md:101-114`, `planning/PLAN.md:432-440`

3. **The chat API is underspecified relative to the frontend and storage requirements.**  
   The frontend depends on inline confirmations for executed trades/watchlist changes, and the database stores `actions` JSON on assistant messages, but `/api/chat` only says “message + executed actions” with no request/response schema. That leaves unresolved whether the response includes persisted message IDs, per-action success/error states, partial failures, updated portfolio/watchlist data, and whether assistant text is returned before or after execution. This is likely to create frontend/backend contract churn.  
   References: `planning/PLAN.md:241-242`, `planning/PLAN.md:293-296`, `planning/PLAN.md:313-351`, `planning/PLAN.md:398`

### Medium

4. **The zero-quantity position rule leaves average-cost reset behavior undefined.**  
   The plan keeps `positions` rows after a full sell by setting `quantity=0`, but it does not define what happens to `avg_cost` when the user later buys the same ticker again. If the old average cost is reused, unrealized P&L will be wrong after re-entry; if it is reset, that needs to be part of the trade logic contract.  
   References: `planning/PLAN.md:212-220`, `planning/PLAN.md:470`

5. **Trade validation rules are too loose for a shared implementation contract.**  
   Manual and LLM-driven trades both depend on “the same validation,” but the plan never defines normalization and rejection rules for invalid input such as lowercase tickers, unsupported symbols, zero quantity, negative quantity, excessive decimal precision, or NaN/non-numeric values. Without this, UI validation, API behavior, and test expectations will diverge.  
   References: `planning/PLAN.md:263`, `planning/PLAN.md:340-351`, `planning/PLAN.md:470-472`

6. **The SSE/watchlist behavior is specified at a UX level but not at the protocol level.**  
   The plan requires an existing `EventSource` connection to become “watchlist-aware” when the watchlist changes, but it does not define how the backend communicates non-price events such as `added`, `removed`, `snapshot`, heartbeat, or stale/reconnected state. The frontend also needs an initial snapshot to avoid rendering empty prices/charts until the next tick. Without explicit event types and payloads, both sides will infer different SSE semantics.  
   References: `planning/PLAN.md:177-182`, `planning/PLAN.md:289-291`, `planning/PLAN.md:392-406`

## Open Questions

- Should `OPENROUTER_API_KEY` be optional whenever `LLM_MOCK=true`, or should startup fail unless a key is present regardless of mode?
- Is persistence supposed to use a named Docker volume, a bind mount to repo `db/`, or one for scripts and the other for tests?
- What is the exact `/api/chat` response shape, especially for partial trade failures and inline action rendering?
- After selling a position down to zero, should the next buy recreate cost basis from scratch?
