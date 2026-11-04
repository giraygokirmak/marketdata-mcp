# Architecture

## Data flow

```
  MCP client
      │  tool call
      ▼
  tools_*.py ──────────────┐
      │                    │  health warnings for the providers used
      │                    ▼
      │              health.MONITOR
      │                    │  cached verdict (no upstream call per request)
      │                    ▼
      │              health_state.json  (baselines + last verdicts)
      │
      ▼
  reconcile.ask()  ──►  asyncio.gather(tv_fn, fv_fn)   both in parallel, never raises
      │
      ├──► tv_access ──► providers/tradingview ──► scanner.tradingview.com
      │                                          └► query1.finance.yahoo.com
      └──► fv_access ──► providers/finviz ───────► finviz.com (HTML + /api/*)
      │
      ▼
  Pair(tv=Attempt, fv=Attempt)
      │
      ▼
  reconcile.finalize() ──► merge() ──► comparisons + per-field fields
                              │  freshness.py decides which reading resolves
      │
      ▼
  providers_state.envelope()
      │
      ▼
  {data_provider, verification, notice, data, fields, comparisons,
   sources, errors, warnings, degraded, generated_at}
```

## Modules

```
src/marketdata_mcp/
├── server.py             FastMCP instance, lifespan, /health, CLI, MCP instructions
├── providers_state.py    DataProvider / Verification enums, error types, envelope()
├── reconcile.py          concurrent fan-out, field comparison, fallback, notice text
├── freshness.py          market-print timestamps + freshest-print selection
├── merge_helpers.py      pure payload shapers (raw provider dict → normalised fields)
├── sentiment.py          token-free headline scoring (VADER + finance lexicon)
├── symbols.py            BRK-B ↔ BRK.B, ^GSPC ↔ SPX, index/ETF separation
├── health.py             upstream probes, fingerprints, drift state, background loop
├── tv_access.py          typed access to the TradingView provider
├── fv_access.py          typed access to the Finviz provider
├── catalog.py            plain-data tool catalogue (providers + comparison strategy)
├── tools_market.py       the 9 dual-source tools
├── tools_finviz.py       the 8 Finviz-only tools
├── tools_tradingview.py  the 14 TradingView-only tools + combined_analysis
├── tools_health.py       marketdata_health + marketdata_tool_map
└── providers/
    ├── tradingview/      provider layer (scanner, Yahoo quote, TA, backtest, …)
    └── finviz/           provider layer (quote HTML, screener, ownership, …)
```

## Why the layering looks like this

### `providers/` is kept as an isolated layer

Provider implementations live under `providers/` with their upstream handling — HTML parsers, retry
logic, rate limiting — intact and import paths rewritten. Keeping them self-contained means tested
behaviour stays stable and upstream fixes can be carried over independently. The orchestration layer
is the only code that defines the tool contract; it never reaches into provider internals.

Provider code does not import anything from the orchestration layer. That one-way dependency is what
keeps the boundary real rather than decorative. It is why the token-free sentiment fallback lives in
`tools_tradingview.py` and not inside `marketaux_service.py`.

### `tv_access.py` / `fv_access.py` are adapters, not wrappers

The two providers return different shapes for the same concept: Finviz uses
`lastVolume`/`openInterest`/`bidPrice`, Yahoo uses `volume`/`openInterest`/`bid`. The adapters
normalise shapes *and* convert provider exceptions into `ProviderError`. That single conversion point
is why `reconcile.py` can promise never to raise: by the time an exception reaches it, it is already
an envelope-ready dict.

### `reconcile.py` owns the rules

One module decides what "agree" means, what happens on partial failure, what the notice says, and
which reading a field resolves to. If comparison semantics lived in each tool, `stock_quote` and
`market_indices` would drift apart.

### `freshness.py` decides *which* number

Resolving to a single value needs a tie-break that is defensible, not arbitrary. `freshness.py`
parses both providers' **print** timestamps — Yahoo's `regularMarketTime` epoch, Finviz's rendered
`"Nov 03 • 3:59 PM ET"` — into UTC and hands the newer one to `reconcile.py`.

Print time, not fetch time: a quote fetched a second ago can still carry yesterday's close, so
ordering by fetch time would select whichever provider answered last, which says nothing about which
price is current. Parsing is deliberately forgiving and returns `None` rather than raising, because a
provider changing its date rendering must degrade to "no freshness arbitration" — never to a crash
or a wrong ordering.

### `merge_helpers.py` is pure

Every shaper is a pure `dict → dict` function: no I/O, no clock, no randomness. That makes field
mapping unit-testable without network, and auditable — you can read exactly which raw key becomes
which output field.

### `catalog.py` is plain data

No imports, no side effects. `marketdata_tool_map` costs nothing to serve, so an agent can inspect
capabilities before spending upstream requests.

## The fan-out contract

```python
async def ask(tv_fn, fv_fn) -> Pair
```

- Both coroutines run concurrently via `asyncio.gather`.
- A `None` callable is not a failure — it becomes `NOT_ATTEMPTED` ("this provider has no such
  capability"), which is excluded from `errors`.
- Any exception is caught and converted. `ask` **never** raises.
- The slowest provider determines latency; a fast failure does not cancel the other leg.

## Three ways a response is built

| Entry point | When | Result |
| --- | --- | --- |
| `finalize(pair, specs=...)` | Both providers *could* answer | Field-by-field comparison, one value per field from the fresher print |
| `finalize(pair, data=..., provider=...)` | Tool composes a richer payload than the field list | Same envelope, custom payload + explicit label |
| `single(provider, value)` | Only one provider serves this capability at all | `TRADINGVIEW`/`FINVIZ`, other side `NOT_ATTEMPTED` |

## Health monitor lifecycle

`MONITOR.ensure_started()` is called lazily from each tool. This is deliberate: FastMCP's
streamable-HTTP app builds its own lifespan and ignores `lifespan=`, so a lifespan-only start would
silently never run under HTTP. Lazy start works on both transports.

Tool calls only **read** the cached verdict, so a health probe never adds latency to a data call.

## Concurrency and blocking I/O

The provider layer contains both sync and async code paths. Anything sync is pushed to a worker
thread with `asyncio.to_thread`, so a blocking Yahoo `/v8/chart` call cannot stall the event loop for
other in-flight tool calls.

## Related

- [Response Contract](Response-Contract) — the output shape
- [Health Monitor](Health-Monitor) — probes and drift detection
- [Design Decisions](Design-Decisions) — the reasoning behind non-obvious choices
