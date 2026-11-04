# Getting Started

## Requirements

- Python 3.10 – 3.13
- No API keys, no database, no external services

## Install

```bash
git clone https://github.com/giraygokirmak/marketdata-mcp.git
cd marketdata-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

Verify:

```bash
marketdata-mcp --help
python -c "from marketdata_mcp import server; print('ok')"
```

## Run over stdio (most MCP clients)

stdio is the default transport. The client spawns the process and talks over stdin/stdout.

```jsonc
// Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json
//                 (Linux: ~/.config/Claude/claude_desktop_config.json)
{
  "mcpServers": {
    "marketdata": {
      "command": "/absolute/path/to/.venv/bin/marketdata-mcp",
      "env": {
        "MARKETAUX_API_TOKEN": ""     // optional; leave empty to use local scoring
      }
    }
  }
}
```

Use an **absolute** path: MCP clients do not inherit your shell's `PATH`.

## Run over streamable HTTP

```bash
marketdata-mcp streamable-http --host 0.0.0.0 --port 8000
```

| Flag | Env fallback | Default |
| --- | --- | --- |
| `--host` | `HOST` | `127.0.0.1` |
| `--port` | `PORT` | `8000` |

Endpoints:

- `POST/GET /mcp` — the MCP streamable-HTTP endpoint
- `GET /health` — cached health verdict, no upstream call (safe for container healthchecks)

```bash
curl -s localhost:8000/health | python -m json.tool
```

Bind to `127.0.0.1` unless you are behind a proxy or on a private network. The server has no
authentication of its own.

## First calls

```
stock_quote("AAPL")
market_indices()
stock_news("NVDA")
market_sentiment("TSLA")        # works with no API key
marketdata_health()             # upstream drift/outage status
marketdata_tool_map()           # what is dual-sourced, before calling anything
```

## Reading your first response

```jsonc
{
  "data_provider": "BOTH",
  "verification": "VERIFIED",
  "notice": "Data came from BOTH providers (TradingView and Finviz) and the values agree — cross-verified.",
  "data": { "price": 331.9, "previous_close": 338.32, ... },
  "fields": { "price": "BOTH", "peers": "FINVIZ" },
  "freshness": {
    "tradingview": "2026-11-03T21:00:01+00:00",
    "finviz": "2026-11-03T20:59:00+00:00",
    "chosen": "TRADINGVIEW",
    "reason": "newest market print timestamp"
  },
  "comparisons": [
    { "field": "price", "tv": 331.9, "fv": 331.9, "match": true, "diff_pct": 0.0 }
  ],
  "warnings": [],
  "degraded": false
}
```

Four things to notice:

1. `data_provider` is `BOTH` — safe to quote.
2. `data.price` is **one** number. `freshness` explains where it came from: TradingView printed at
   `21:00:01Z`, Finviz at `20:59Z`, so the newer reading won.
3. `fields` shows `peers` came from Finviz alone. Per-field provenance, not a blanket claim.
4. `comparisons` holds both raw readings. `diff_pct: 0.0` is the actual measured gap.

If `data_provider` were `TRADINGVIEW` or `FINVIZ`, the notice would say the data is
**not cross-verified** and an agent must relay that.

## Next

- [Response Contract](Response-Contract) — every field explained
- [Tools Reference](Tools-Reference) — all 33 tools
- [Configuration](Configuration) — environment variables
- [Docker](Docker) — prebuilt GHCR image or a local build
