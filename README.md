# FinAlly — AI Trading Workstation

An AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course.

## Features

- Live price streaming via SSE with green/red flash animations
- Simulated portfolio — $10k virtual cash, market orders, instant fills
- Portfolio visualizations — heatmap (treemap), P&L chart, positions table
- AI chat assistant — analyzes holdings, suggests and auto-executes trades
- Watchlist management — track tickers manually or via AI
- Dark terminal aesthetic — Bloomberg-inspired, data-dense layout

## Quick Start

```bash
cp .env.example .env
# Add OPENROUTER_API_KEY to .env

./scripts/start_mac.sh        # macOS/Linux
# or: scripts\start_windows.ps1  (Windows PowerShell)

# Open http://localhost:8000
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for AI chat |
| `MASSIVE_API_KEY` | No | Real market data (Polygon.io); omit to use built-in simulator |
| `LLM_MOCK` | No | `true` for deterministic mock LLM responses (testing/CI) |

## Architecture

Single Docker container on port 8000:

- **Frontend**: Next.js static export, TypeScript, Tailwind CSS
- **Backend**: FastAPI (Python/uv), SSE streaming, SQLite
- **AI**: LiteLLM → OpenRouter (Cerebras) with structured outputs
- **Market data**: GBM simulator (default) or Massive API (optional)

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation
├── test/        # Playwright E2E tests
├── scripts/     # Start/stop helpers
└── db/          # SQLite volume mount (runtime)
```
