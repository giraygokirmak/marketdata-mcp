# marketdata-mcp

One MCP server that serves **TradingView** and **Finviz** market data together, cross-checks every
overlapping figure, and labels each response with the provider that actually produced it.

The point is redundancy you can *see*. When both providers answer, the data is cross-verified. When
only one answers, you get the data — clearly marked as single-source. When neither answers, you get
`NONE` instead of a plausible-looking number. And because both providers scrape or call undocumented
upstreams, a background monitor probes every endpoint and reports API/schema drift inside ordinary
tool responses.

**No tool requires an API key.** Every capability works out of the box; `MARKETAUX_API_TOKEN` only
upgrades news and sentiment to a licensed feed.

---

## Why this exists

One market-data source gives an agent a single number and no way to judge it. Asking two independent
providers the same question and comparing the answers inverts the problem: the server asks both,
compares, and tells the agent what it is holding.

| Situation | What this server reports | What a single-source feed cannot |
| --- | --- | --- |
| Both providers answer, values agree | `BOTH` — cross-verified | confirm the number against an independent reading |
| Both answer, values differ | `BOTH` + both values in `comparisons` + a warning | show that a disagreement exists |
| One provider is down | `TRADINGVIEW`/`FINVIZ` + "not cross-verified" | say the figure is single-source |
| Both down | `NONE` — no value presented | avoid a stale or invented number |
| Upstream markup changed | `degraded: true` + a named warning | avoid a silently empty or wrong response |

---

## Documentation map

| Page | What it covers |
| --- | --- |
| [Getting Started](Getting-Started) | Install, run over stdio or HTTP, first calls |
| [Response Contract](Response-Contract) | The envelope, every field, how to read it correctly |
| [Tools Reference](Tools-Reference) | All 33 tools, providers, and what is compared |
| [Architecture](Architecture) | Module layout, data flow, why the pieces sit where they do |
| [Health Monitor](Health-Monitor) | Probes, fingerprints, drift detection, baselines |
| [Sentiment](Sentiment) | Token-free local scoring vs. licensed Marketaux |
| [Configuration](Configuration) | Every environment variable |
| [Docker](Docker) | Standalone image, compose, volumes, healthcheck |
| [Development](Development) | Tests, layout, adding a tool |
| [Troubleshooting](Troubleshooting) | Symptom → cause → fix |
| [Design Decisions](Design-Decisions) | Non-obvious choices and the reasons behind them |

---

## The `data_provider` contract

Every tool result carries exactly one of four values:

| Value | Meaning |
| --- | --- |
| `BOTH` | Both providers answered and the compared values agree. Cross-verified. |
| `TRADINGVIEW` | Only TradingView had this data. Served, but **not** cross-verified. |
| `FINVIZ` | Only Finviz had this data. Served, but **not** cross-verified. |
| `NONE` | Neither provider could supply it. No value is presented. |

```jsonc
{
  "data_provider": "BOTH",
  "verification": "VERIFIED",           // VERIFIED | UNVERIFIED | UNAVAILABLE
  "notice": "Data came from BOTH providers ...",  // sentence to relay to the user
  "data": { ... },                      // the reconciled payload — one value per field
  "fields": { "price": "BOTH", "peers": "FINVIZ" },   // per-field provenance
  "freshness": {                        // why `data` holds the value it does
    "tradingview": "2026-11-03T21:00:01+00:00",
    "finviz": "2026-11-03T20:59:00+00:00",
    "chosen": "TRADINGVIEW",
    "reason": "newest market print timestamp"
  },
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

Full detail: [Response Contract](Response-Contract).

---

## Quick start

```bash
git clone https://github.com/giraygokirmak/marketdata-mcp.git
cd marketdata-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

```jsonc
// Claude Desktop / any stdio MCP client
{
  "mcpServers": {
    "marketdata": {
      "command": "/path/to/.venv/bin/marketdata-mcp"
    }
  }
}
```

```bash
# HTTP
marketdata-mcp streamable-http --host 0.0.0.0 --port 8000
curl -s localhost:8000/health
```

```bash
# Docker
cp .env.example .env      # optional; every setting has a default
docker compose up -d --build
curl -s localhost:6013/health
```

Details: [Getting Started](Getting-Started) · [Docker](Docker).

---

## Note on labelling

The TradingView provider's price and OHLCV data actually come from Yahoo Finance's chart API, and its
options chain from Yahoo's options endpoint. Responses say so in `sources`, and it is still labelled
`TRADINGVIEW` because it is that provider's dataset — but it is not TradingView's own exchange feed,
and the label should not be read as claiming it is.

---

## Disclaimer

Market data is provided as-is for research and automation. It is not investment advice, and scraped
data can be delayed, incomplete or wrong. Cross-verification reduces the chance of a bad number
reaching you; it does not eliminate it. Sentiment scores are a deterministic summary of headline
tone — not a prediction, and not a trading signal.

## License

MIT — see [LICENSE](https://github.com/giraygokirmak/marketdata-mcp/blob/main/LICENSE).
