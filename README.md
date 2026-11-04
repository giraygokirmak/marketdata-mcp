# marketdata-mcp

One MCP server that serves **TradingView** and **Finviz** market data together, cross-checks every
overlapping figure, and labels each response with the provider that actually produced it.

The point is redundancy you can *see*. When both providers answer, the data is cross-verified. When
only one answers, you get the data — clearly marked as single-source. When neither answers, you get
`NONE` instead of a plausible-looking number. And because both providers scrape or call undocumented
upstreams, a background monitor probes every endpoint and reports API/schema drift inside ordinary
tool responses.

---

## The `data_provider` contract

Every tool result carries exactly one of four values:

| Value         | Meaning                                                            |
| ------------- | ------------------------------------------------------------------ |
| `BOTH`        | Both providers answered **and** the compared values agree. Cross-verified. |
| `TRADINGVIEW` | Only TradingView had this data. Served, but **not** cross-verified. |
| `FINVIZ`      | Only Finviz had this data. Served, but **not** cross-verified.      |
| `NONE`        | Neither provider could supply it. No value is presented.            |

The same envelope also contains:

```jsonc
{
  "data_provider": "BOTH",
  "verification": "VERIFIED",           // VERIFIED | UNVERIFIED | UNAVAILABLE
  "notice": "Data came from BOTH providers ...",  // sentence to relay to the user
  "data": { ... },                      // the reconciled payload
  "fields": { "price": "BOTH", "peers": "FINVIZ" },   // per-field provenance
  "comparisons": [                      // the actual side-by-side numbers
    { "field": "price", "tv": 331.9, "fv": 331.9,
      "match": true, "diff": 0.0, "diff_pct": 0.0, "tolerance_pct": 0.5 }
  ],
  "sources": { "TRADINGVIEW": "...", "FINVIZ": "..." },
  "errors": [ { "provider": "FINVIZ", "code": "...", "message": "..." } ],
  "warnings": [ "..." ],
  "degraded": false,                    // true when the health monitor saw drift/downtime
  "generated_at": "2026-11-04T06:25:56+00:00"
}
```

**Disagreements are never silently resolved.** If the providers report different prices, both values
stay in `comparisons`, the field is named in `notice` and `warnings`, and the response stays `BOTH`
(both *did* answer — they just disagree). An agent reading it can see exactly how far apart they are.

Implementation: [`reconcile.py`](src/marketdata_mcp/reconcile.py) (fan-out + comparison),
[`providers_state.py`](src/marketdata_mcp/providers_state.py) (envelope).

---

## Tools (33)

### Dual-source — both providers asked, answers compared

| Tool | Fields cross-checked | Tolerance |
| --- | --- | --- |
| `stock_quote` | price, change %, previous close, 52-w high/low, P/E, RSI(14), volume | 0.5 % price, 2 % P/E, 5 % RSI |
| `stock_prices` | price and change % per ticker (per-row `data_provider`) | 0.5 % / 2 % |
| `stock_screener` | per-ticker change % for every row both providers return | 3 % |
| `market_indices` | S&P 500, Dow, Nasdaq Composite, Russell 2000, VIX levels | 0.5 % |
| `stock_options_chain` | volume per contract, matched by strike (OI when both report it) | 5 % / 1 % |
| `stock_earnings` | next release date; EPS history | same-day match |
| `stock_extended_hours` | after-hours / pre-market price | 0.5 % |
| `technical_analysis` | price, RSI(14), ATR(14), volume vs. Finviz's snapshot | 0.5 %–12 % |
| `stock_news` | headline title overlap between feeds | exact title match |

`stock_news` needs `MARKETAUX_API_TOKEN` for the TradingView leg; without it the response is
`FINVIZ` with `NOT_CONFIGURED` in `errors` — and says so.

### Finviz-only — served, labelled `FINVIZ`, explicitly unverified

`stock_filings` · `stock_ownership` (insider + institutional + short interest) · `stock_peers` ·
`stock_why_moving` · `stock_chart` (PNG) · `stock_dilution` · `stock_screener_filters`

TradingView has no equivalent dataset for any of these, so the envelope marks the TradingView side
`NOT_ATTEMPTED` (not an error) and the notice states the data is not cross-verified.

### TradingView-only — served, labelled `TRADINGVIEW`, explicitly unverified

`crypto_top_movers` · `crypto_bollinger_scan` · `crypto_volume_breakout` ·
`crypto_multi_timeframe` · `crypto_multi_agent` · `futures_market_overview` · `futures_top_movers` ·
`futures_watchlist` · `stock_backtest` · `stock_backtest_compare` · `market_snapshot` ·
`bitcoin_market_pulse` · `market_sentiment` · `egx_market_overview`

### Composite

- `combined_analysis` — TradingView technical read + sentiment + news, with the underlying price
  cross-checked against Finviz. The only composite that can return `BOTH`.

### Ops

- `marketdata_health` — run the probes now (`refresh=True`) or read the cached verdict.
- `marketdata_tool_map` — every tool with its providers and comparison strategy, without any
  upstream call.

---

## Upstream change detection

Both providers depend on things that can move without notice: Finviz's HTML class names and
unversioned `/api/*` JSON islands, TradingView's scanner POST schema, Yahoo's chart API shape.

The monitor in [`health.py`](src/marketdata_mcp/health.py) probes each upstream **independently of
the provider code paths**, so a provider can't hide a break by catching its own exception:

| Probe | Asserts |
| --- | --- |
| `finviz.indices_api` | `DJI`, `SPX`, `COMP` keys present |
| `finviz.quote_html` | `js-snapshot-table` marker still in the rendered page |
| `finviz.news_api` | `html` key present |
| `tradingview.scanner_api` | `totalCount` and `data[0].d` present |
| `tradingview.yahoo_chart_api` | `chart.result[0].meta` / `.timestamp` present |

Each successful probe records a SHA-256 fingerprint of the payload's key paths as a **baseline**,
persisted to `~/.marketdata-mcp/health_state.json`. Later probes compare against it, so a renamed
field is caught even if the endpoint still returns 200. Verdicts:

- `OK` — status and shape match the baseline
- `DRIFT` — answered, but the status/shape changed (renamed field, moved route, missing marker)
- `DOWN` — timeout, connection error or unexpected HTTP status
- `UNKNOWN` — not probed yet

Drift is surfaced, not swallowed: every tool response gets a `warnings` entry, `degraded: true`, and
a `WARNING` appended to `notice`. The monitor runs in the background (`MARKETDATA_HEALTH_INTERVAL`,
default 300 s) and tool calls only read the cached verdict.

---

## Install

Requires Python 3.10–3.13.

```bash
git clone https://github.com/giraygokirmak/marketdata-mcp.git
cd marketdata-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### stdio (Claude Desktop, most MCP clients)

```json
{
  "mcpServers": {
    "marketdata": {
      "command": "/path/to/.venv/bin/marketdata-mcp",
      "env": { "MARKETAUX_API_TOKEN": "" }
    }
  }
}
```

### Streamable HTTP

```bash
marketdata-mcp streamable-http --host 0.0.0.0 --port 8000
curl -s localhost:8000/health
```

## Docker

```bash
cp .env.example .env      # optional; every setting has a default
docker compose up -d --build
curl -s localhost:6013/health
```

Port `6013` on the host follows the same convention as the two servers this replaces
(`tradingview-mcp` = 6001, `finviz-mcp` = 6007). The named volume `marketdata-state` persists chart
PNGs **and** the health baselines, so drift detection survives restarts.

Standalone:

```bash
docker build -t marketdata-mcp:latest .
docker run -d --name marketdata-mcp -p 6013:8000 \
  -v marketdata-state:/home/mcpuser/.marketdata-mcp \
  marketdata-mcp:latest
```

## Configuration

Everything is optional. See [`.env.example`](.env.example) for the full list.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKETDATA_HEALTH_ENABLED` | `true` | Turn the drift monitor on/off |
| `MARKETDATA_HEALTH_INTERVAL` | `300` | Seconds between background probe rounds |
| `MARKETDATA_HEALTH_TTL` | `60` | Seconds a cached verdict is reused |
| `MARKETDATA_HEALTH_STATE` | `~/.marketdata-mcp/health_state.json` | Baseline file |
| `FINVIZ_CACHE_TTL` | `30` | Scraped-page cache lifetime (s) |
| `FINVIZ_TIMEOUT` | `25` | finviz.com HTTP timeout (s) |
| `FINVIZ_CHART_DIR` | `~/.marketdata-mcp/charts` | Chart PNG output |
| `TRADINGVIEW_MCP_MIN_INTERVAL_S` | `0.25` | Scanner request spacing |
| `MARKETAUX_API_TOKEN` | — | Enables news + sentiment (TradingView leg) |

No proxy configuration exists: both providers reach their upstreams directly.

## Reading a response correctly

```
1. Check data_provider.
   BOTH        -> quote the numbers; they were confirmed twice.
   TRADINGVIEW -> quote them, but say the figure is single-source and unverified.
   FINVIZ      -> same, from the Finviz side.
   NONE        -> say no data is available. Do not invent one.

2. Check degraded / warnings.
   Non-empty -> tell the user an upstream API changed or is down, and that
                provider's figures may be stale or incomplete.

3. Check fields for per-field provenance when the response mixes sources
   (e.g. price is BOTH while peers is FINVIZ).

4. Check comparisons when you need to show how far apart the two sources are.
```

## Development

```bash
pip install -e ".[dev]"     # or: uv pip install -e . --group dev
pytest                      # 74 hermetic tests, no network
pytest -m live              # opt-in: hits real upstreams
```

Tests mock at the HTTP boundary (`httpx.MockTransport`) and the provider-access boundary, so the
suite runs offline and deterministically — including the drift-detection tests that corrupt a stored
baseline and assert the warning reaches a normal tool response.

## Layout

```
src/marketdata_mcp/
├── server.py             FastMCP instance, lifespan, /health, CLI
├── providers_state.py    DataProvider enum, errors, response envelope
├── reconcile.py          concurrent fan-out, comparison, fallback
├── merge_helpers.py      pure payload shapers (raw provider dict -> fields)
├── symbols.py            BRK-B <-> BRK.B, ^GSPC <-> SPX, venue mapping
├── health.py             upstream probes, fingerprints, drift state
├── tv_access.py          typed access to the TradingView provider
├── fv_access.py          typed access to the Finviz provider
├── tools_market.py       dual-source tools
├── tools_finviz.py       Finviz-only tools
├── tools_tradingview.py  TradingView-only tools
├── tools_health.py       health + tool map
└── providers/            vendored provider code (see NOTICE)
```

## Provenance of the vendored providers

`src/marketdata_mcp/providers/tradingview` and `.../finviz` are vendored from the author's own
`tradingview-mcp` and `finviz-mcp` servers, with imports rewritten and the proxy layer removed. Their
upstream licenses and attribution are preserved in [`NOTICE`](NOTICE).

Note on labelling: the TradingView provider's price and OHLCV data actually come from Yahoo Finance's
chart API, and its options chain from Yahoo's options endpoint. Responses say so in `sources`, and it
is still labelled `TRADINGVIEW` because it is that provider's dataset — but it is not TradingView's
own exchange feed, and the label should not be read as claiming it is.

## Disclaimer

Market data is provided as-is for research and automation. It is not investment advice, and
scraped data can be delayed, incomplete or wrong. Cross-verification reduces the chance of a bad
number reaching you; it does not eliminate it.

## License

MIT — see [LICENSE](LICENSE).
