# Getting Started

## Requirements

- Python 3.10 – 3.13
- No API keys, no database, no external services

## Install

```bash
git clone https://github.com/giraygokirmak/tapebasis.git
cd tapebasis
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

Verify:

```bash
tapebasis --help
python -c "from tapebasis import server; print('ok')"
```

## Run over stdio (most MCP clients)

stdio is the default transport. The client spawns the process and talks over stdin/stdout.

```jsonc
// Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json
//                 (Linux: ~/.config/Claude/claude_desktop_config.json)
{
  "mcpServers": {
    "tapebasis": {
      "command": "/absolute/path/to/.venv/bin/tapebasis",
      "env": {
        "MARKETAUX_API_TOKEN": "",     // optional; leave empty to use local scoring
      }
    }
  }
}
```

Use an **absolute** path: MCP clients do not inherit your shell's `PATH`.

## Run over streamable HTTP

```bash
tapebasis streamable-http --host 0.0.0.0 --port 8000
```

| Flag | Env fallback | Default |
| --- | --- | --- |
| `--host` | `HOST` | `127.0.0.1` |
| `--port` | `PORT` | `8000` |

Endpoints:

- `POST/GET /mcp`: the MCP streamable-HTTP endpoint
- `GET /health`: cached health verdict plus per-provider rate-limit state, no upstream call (safe for
  container healthchecks)

```bash
curl -s localhost:8000/health | python -m json.tool
```

Bind to `127.0.0.1` unless you are behind a private network. The server has no authentication of its
own.

## First calls

```
stock_quote("AAPL")            # every provider that carries it
stock_quote("XAUUSD")          # gold: TradingView only, labelled as such
market_indices()               # S&P 500, Dow, Nasdaq, Russell 2000, VIX
stock_news("NVDA")
market_sentiment("TSLA")       # works with no API key
tapebasis_health()             # upstream drift/outage + rate-limit state
tapebasis_tool_map()           # what is multi-sourced, before calling anything
```

## Reading your first response

```jsonc
{
  "data_provider": "ALL",
  "verification": "MULTI_SOURCE",
  "providers_answered": ["TRADINGVIEW", "FINVIZ", "YAHOO"],
  "notice": "Every provider asked answered (TradingView, Finviz and Yahoo Finance). Coverage is ALL; agreement is a separate axis. Compared fields agree within tolerance. The value shown is FINVIZ's because it printed more recently than the other providers'.",
  "data": { "price": 332.33, "previous_close": 331.9, ... },
  "fields": { "price": "ALL", "peers": "FINVIZ" },
  "freshness": {
    "tradingview": "2026-11-04T00:59:53+00:00",
    "finviz": "2026-11-04T10:42:00+00:00",
    "yahoo": "2026-11-03T21:00:01+00:00",
    "chosen": "FINVIZ",
    "decision": "newest",
    "newest_print": "2026-11-04T10:42:00+00:00"
  },
  "comparisons": [
    { "field": "price", "match": true,
      "values": { "TRADINGVIEW": 331.9, "FINVIZ": 332.33, "YAHOO": 331.9 },
      "diff_pct": 0.1294 }
  ],
  "resolutions": {
    "TRADINGVIEW": { "symbol": "NASDAQ:AAPL", "method": "search" },
    "FINVIZ": { "symbol": "AAPL", "method": "normalise" },
    "YAHOO": { "symbol": "AAPL", "method": "alias" }
  },
  "warnings": [
    "Not cross-checked (providers' prints belong to different sessions): change_percent, previous_close, volume"
  ],
  "degraded": false
}
```

Five things to notice:

1. `data_provider` is `ALL` and `providers_answered` names every provider that answered.
2. `data.price` is **one** number. `freshness` explains where it came from: Finviz printed at
   `10:42Z`, TradingView at `00:59:53Z`, Yahoo at `21:00:01Z`, so the newest reading won. Every raw
   price is still in `comparisons`.
3. `fields` shows `peers` came from Finviz alone. Per-field provenance, not a blanket claim.
4. The `warnings` entry is not an error: Finviz is mid-session while the others are at the prior
   close, so the session-relative fields (day change, previous close, volume) were **not** compared.
   Reporting that is more honest than calling it a conflict, or silently implying agreement.
5. `resolutions` shows the ticker each provider was asked for. This is where a symbol surprise
   becomes explicable.

If `data_provider` were `TRADINGVIEW`, `FINVIZ` or `YAHOO`, the notice would say the data is
**not corroborated** and an agent must relay that.

## A single-source instrument

```
stock_quote("XAUUSD")
```

```jsonc
{
  "data_provider": "TRADINGVIEW",
  "verification": "SINGLE_SOURCE",
  "providers_answered": ["TRADINGVIEW"],
  "notice": "Data came from TRADINGVIEW only. The other providers did not supply this field, so the value is single-source and NOT corroborated.",
  "data": { "price": 4042.04, ... },
  "resolutions": {
    "TRADINGVIEW": { "symbol": "OANDA:XAUUSD", "method": "search" },
    "FINVIZ": { "symbol": null, "note": "Finviz carries US equities/ETFs only" },
    "YAHOO": { "symbol": null, "note": "Yahoo Finance has no spot gold pair; its nearest series is COMEX gold futures (GC=F), a different instrument, not substituted." }
  },
  "errors": []
}
```

Gold is a good demonstration of the rules: the data is served rather than withheld, and the label
says it is single-source. `errors` is empty because no provider *failed*: two simply do not carry
spot gold, and Yahoo's note refuses to substitute a futures contract for a spot pair. A different
instrument at a different price is not the same quote.

## Next

- [Response Contract](Response-Contract): every field explained
- [Tools Reference](Tools-Reference): all 33 tools
- [Configuration](Configuration): environment variables
- [Docker](Docker): prebuilt GHCR image or a local build
