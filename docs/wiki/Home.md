# Tapebasis

<img src="https://raw.githubusercontent.com/giraygokirmak/tapebasis/main/docs/logo.svg" alt="Tapebasis" width="300">

**Multiple tapes, one basis.** An MCP server that serves **TradingView**, **Finviz** and **Yahoo Finance**
market data together, cross-checks every overlapping figure, and labels each response with the
provider that actually produced it. **Bloomberg** is served too, as an explicitly single-source
provider for the data nobody else here carries.

*Basis* is the finance term for the price difference between two markets quoting the same asset.
*Tape* is the ticker tape. Tapebasis is the spread between multiple feeds, measured on every call.

The point is redundancy you can *see*. Every provider that could answer a field is asked, natively
and in parallel. When several answer, the data is cross-checked. When only one answers, you get the
data, clearly marked as single-source. When none answers, you get `NONE` instead of a
plausible-looking number. And because every provider scrapes or calls an undocumented upstream, a
background monitor probes every endpoint and reports API/schema drift inside ordinary tool responses.

Each leg is genuinely independent: TradingView's own scanner and chart websocket, Finviz's HTML and
JSON islands, Yahoo's chart API. A provider that quietly proxies another is worthless as a
cross-check, so none does. Bloomberg is the one provider kept *outside* the cross-check: its equity
quotes are delayed and its statics duplicate Yahoo's, so it is served single-source rather than
counted as a fourth independent reading. See [Bloomberg](#bloomberg-single-source-by-design).

**No tool requires an API key.** Every capability works out of the box; `MARKETAUX_API_TOKEN` only
upgrades news and sentiment to a licensed feed.

---

## Why this exists

One market-data source gives an agent a single number and no way to judge it. Asking several
independent providers the same question and comparing the answers inverts the problem: the server
asks them all, compares, and tells the agent what it is holding.

| Situation | What this server reports | What a single-source feed cannot |
| --- | --- | --- |
| Every asked provider answers, values agree | `ALL`, `comparisons[].match: true` | confirm the number against independent readings |
| They answer but diverge | `ALL` + every value in `comparisons` + a warning | show that a disagreement exists |
| One provider is down | `TRADINGVIEW`/`FINVIZ`/`YAHOO` + "not corroborated" | say the figure is single-source |
| Only Bloomberg has it | `BLOOMBERG` + `cross_checked: false` | say what is uncorroborated and how stale |
| All down | `NONE`, no value presented | avoid a stale or invented number |
| Upstream markup changed | `degraded: true` + a named warning | avoid a silently empty or wrong response |

---

## Documentation map

| Page | What it covers |
| --- | --- |
| [Getting Started](Getting-Started) | Install, run over stdio or HTTP, first calls |
| [Response Contract](Response-Contract) | The envelope, every field, how to read it correctly |
| [Tools Reference](Tools-Reference) | All 35 tools, providers, and what is compared |
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

Every tool result carries exactly one of six values. It states **coverage**, never agreement:

| Value | Meaning |
| --- | --- |
| `ALL` | Every provider that was asked answered. |
| `TRADINGVIEW` | Only TradingView answered. Served, but **not** corroborated. |
| `FINVIZ` | Only Finviz answered. Served, but **not** corroborated. |
| `YAHOO` | Only Yahoo Finance answered. Served, but **not** corroborated. |
| `BLOOMBERG` | Only Bloomberg answered. Single-source by design, never cross-checked. |
| `NONE` | No provider could supply it. No value is presented. |

```jsonc
{
  "data_provider": "ALL",
  "verification": "MULTI_SOURCE",          // MULTI_SOURCE | SINGLE_SOURCE | UNAVAILABLE
  "providers_answered": ["TRADINGVIEW", "FINVIZ", "YAHOO"],
  "notice": "Every provider asked answered ...",   // sentence to relay to the user
  "data": { ... },                         // the reconciled payload, one value per field
  "fields": { "price": "ALL", "peers": "FINVIZ" }, // per-field provenance
  "freshness": {                           // why `data` holds the value it does
    "tradingview": "2026-11-04T00:59:53+00:00",
    "finviz": "2026-11-04T10:42:00+00:00",
    "yahoo": "2026-11-03T21:00:01+00:00",
    "chosen": "FINVIZ",
    "decision": "newest",                  // newest | tie | no_timestamp
    "newest_print": "2026-11-04T10:42:00+00:00"
  },
  "comparisons": [                         // the actual side-by-side numbers
    { "field": "price",
      "values": { "TRADINGVIEW": 331.9, "FINVIZ": 332.33, "YAHOO": 331.9 },
      "match": true, "diff_pct": 0.1294, "tolerance_pct": 0.5 }
  ],
  "resolutions": { "TRADINGVIEW": { "symbol": "NASDAQ:AAPL" }, ... },
  "sources": { "TRADINGVIEW": "scanner.tradingview.com", "FINVIZ": "...", "YAHOO": "..." },
  "errors": [ { "provider": "FINVIZ", "code": "...", "message": "..." } ],
  "warnings": [ "..." ],
  "degraded": false,                       // true when the health monitor saw drift/downtime
  "generated_at": "2026-11-04T10:43:02+00:00"
}
```

**One value per compared field, and it is the newest print.** `data` holds a single price: the
reading whose market print is newest. Ties are broken by the documented default order (`FINVIZ`,
`TRADINGVIEW`, `YAHOO`). Every original reading stays in `comparisons.values`, and
`freshness.decision` states which rule applied.

**`ALL` does not mean "the values agree".** It means every provider asked answered. Agreement is a
separate axis, reported per field in `comparisons[].match` and summarised in `notice`/`warnings`. A
subset of providers answering is still `ALL`; `providers_answered` names which ones.

**Disagreements are never silently resolved.** If the providers report different prices, all values
stay in `comparisons`, the field is named in `notice` and `warnings`, and `data_provider` stays `ALL`.
An agent reading it can see exactly how far apart they are.

**A non-comparison is not a disagreement.** A field the providers measure against different baselines
is reported as `not_comparable`, with the reason in `pairs[].not_comparable`, instead of being called
a conflict. That covers a day change or volume whose prints belong to different sessions, a print
past the staleness ceiling, a provider that exposes no print time at all, and a field with no shared
definition: spot FX has no single official previous close, so an FX day change is always reported
from every provider and never judged.

Full detail: [Response Contract](Response-Contract).

---

## Bloomberg: single-source by design

Bloomberg has its own `data_provider` label but is **not** in `PROVIDER_ORDER`, so it cannot
contribute to `ALL` and never appears in `comparisons`. Three measurements made that decision:

- its equity pages are delayed (`exchangeDelay` reads `15 Minutes`);
- its static fields are penny-identical to Yahoo's, so it is not an independent reading of them;
- `lastUpdate` is a session sentinel, not a print time (`2026-11-05T01:00:00.000Z` on a page whose
  real print was `2026-11-04T20:58:00Z`), so trusting it would let a stale page beat a live print.

`bloomberg_quote` takes `print_time` from the last intraday point and says why when that point does
not match the quoted price. `bloomberg_markets` serves the board where Bloomberg is the only source
here of the government benchmark yield curve, and states that the board carries no per-instrument
print time. Both return `cross_checked: false` and `reconciliation_leg: false`.

Access needs `curl_cffi`: bloomberg.com gates plain HTTP clients on the TLS fingerprint, so `httpx`
gets a 403 robot page. That is also why the health monitor does not probe this provider; the tool
response is the availability signal. Details: [Design Decisions](Design-Decisions).

---

## Symbols are resolved per provider

`XAUUSD` is `OANDA:XAUUSD` on TradingView, `^GSPC`-style aliasing for indices, a suffix for Yahoo FX,
`XAUUSD:CUR` on Bloomberg and simply not carried by Finviz. [`symbols.py`](https://github.com/giraygokirmak/tapebasis/blob/main/src/tapebasis/symbols.py)
resolves each query per provider, caches the result for an hour, and records the derivation in
`resolutions`. A ticker is only returned if the provider can actually quote it, and a missing
instrument becomes `NOT_ATTEMPTED`: a structural fact, not an error.

---

## Quick start

```bash
git clone https://github.com/giraygokirmak/tapebasis.git
cd tapebasis
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

```jsonc
// Claude Desktop / any stdio MCP client
{
  "mcpServers": {
    "tapebasis": {
      "command": "/path/to/.venv/bin/tapebasis"
    }
  }
}
```

```bash
# HTTP
tapebasis streamable-http --host 0.0.0.0 --port 8000
curl -s localhost:8000/health
```

```bash
# Docker (prebuilt image from GHCR)
docker run -d --name tapebasis -p 6013:8000 \
  -v tapebasis-state:/home/mcpuser/.tapebasis \
  ghcr.io/giraygokirmak/tapebasis:latest
curl -s localhost:6013/health
```

Or build from the checkout with `docker compose up -d --build`.

Details: [Getting Started](Getting-Started) · [Docker](Docker).

---

## Note on labelling

Every provider is asked for its own data: TradingView from `scanner.tradingview.com` and its chart
websocket, Finviz from `finviz.com` HTML and JSON islands, Yahoo from `query1.finance.yahoo.com`,
Bloomberg from `bloomberg.com` quote pages and its market board. Nothing is proxied through another
provider, and `sources` records the upstream each leg used.

Where a capability exists on only some providers, that is stated, not simulated: options chains come
from Finviz and Yahoo (TradingView contributes only the underlying price), backtests run twice on
TradingView and Yahoo OHLCV, and Finviz is never asked for crypto, FX or commodity quotes.

---

## Disclaimer

Market data is provided as-is for research and automation. It is not investment advice, and scraped
data can be delayed, incomplete or wrong. Cross-verification reduces the chance of a bad number
reaching you; it does not eliminate it. Sentiment scores are a deterministic summary of headline
tone, not a prediction, and not a trading signal.

## License

MIT. See [LICENSE](https://github.com/giraygokirmak/tapebasis/blob/main/LICENSE).
