# Health Monitor

All three providers depend on things that can move without notice: Finviz's HTML class names and
unversioned `/api/*` JSON islands, TradingView's scanner POST schema and chart websocket protocol,
and Yahoo's chart API shape.

A provider that catches its own exception looks healthy from the outside. So the monitor probes each
upstream **independently of the provider code paths**: it can fail even when every tool call
succeeds, and that failure is the signal.

## Probes

| Probe | Provider | Asserts |
| --- | --- | --- |
| `finviz.indices_api` | FINVIZ | `DJI`, `SPX`, `COMP` keys present in `/api/indices_data` |
| `finviz.quote_html` | FINVIZ | `js-snapshot-table` marker still in the rendered `/stock?t=AAPL` page |
| `finviz.news_api` | FINVIZ | `html` key present in `/api/news/quote/AAPL` |
| `tradingview.scanner_api` | TRADINGVIEW | `totalCount` and `data[0].d` present in the scanner POST response |
| `tradingview.symbol_search` | TRADINGVIEW | the symbol-search endpoint still returns a `symbols` list |
| `yahoo.chart_api` | YAHOO | `chart.result[0].meta` and `chart.result[0].timestamp` present for an equity |
| `yahoo.chart_fx` | YAHOO | the FX resolution path still answers (`EURUSD=X`) |
| `yahoo.chart_crypto` | YAHOO | the crypto resolution path still answers (`BTC-USD`) |

The two extra Yahoo probes exist because the FX and crypto ticker conventions are separate
assumptions. A probe that only checked `AAPL` would keep reporting `OK` while `EURUSD=X` silently
started 404-ing, and the old version of this page documented a `XAUUSD=X` probe, which tested a pair
Yahoo never carried. A probe must exercise a ticker the provider actually serves.

Probes use a browser User-Agent and the same headers a real request would send, so a WAF change is
caught rather than bypassed by an unusual header.

## Fingerprints and baselines

A probe that returns HTTP 200 with a renamed field is still broken. So each successful probe records
a **SHA-256 fingerprint of the payload's key paths** as a baseline:

```json
{
  "probes": {
    "finviz.quote_html": {
      "status": "OK",
      "fingerprint": "3f8a…",
      "checked_at": "2026-11-04T06:20:11+00:00"
    }
  }
}
```

Persisted to `~/.tapebasis/health_state.json` (override: `TAPEBASIS_HEALTH_STATE`). Later
probes compare against it, so a renamed field is caught even though the endpoint still answers 200.

The baseline file matters: without it, the first probe of a shape that already drifted would be
recorded as the new normal. With it, drift is detected on the next round.

## Verdicts

| Status | Meaning |
| --- | --- |
| `OK` | Status and shape match the baseline |
| `DRIFT` | Answered, but the status or shape changed: renamed field, moved route, missing marker |
| `DOWN` | Timeout, connection error, or unexpected HTTP status |
| `UNKNOWN` | Not probed yet |

## How drift reaches a normal tool call

Drift is surfaced, never swallowed. Every tool response gets:

- a `warnings` entry naming the affected upstream
- `degraded: true`
- a `WARNING` appended to `notice`.

```jsonc
{
  "data_provider": "ALL",
  "notice": "Every provider asked answered ... WARNING: the provider health monitor reports an upstream change or outage, see tapebasis_health.",
  "warnings": ["TRADINGVIEW tradingview.scanner_api: DRIFT, payload shape changed"],
  "degraded": true
}
```

`degraded` is scoped to the providers **this call actually used**. A Finviz-only tool does not go
degraded because TradingView drifted. A response that reached `ALL` can still be `degraded: true`:
coverage and upstream health are separate facts, and both are reported.

## Timing

| Setting | Default | Effect |
| --- | --- | --- |
| `TAPEBASIS_HEALTH_ENABLED` | `true` | Turn the monitor on/off |
| `TAPEBASIS_HEALTH_INTERVAL` | `300` | Seconds between background probe rounds |
| `TAPEBASIS_HEALTH_TTL` | `60` | Seconds a cached verdict is reused |
| `TAPEBASIS_HEALTH_TIMEOUT` | `12` | Per-probe HTTP timeout (s) |

The monitor runs in the background; tool calls only read the cached verdict, so **health checking
never adds latency to a data call**. `/health` also reads the cache, so it is cheap enough for a
container healthcheck to hit every 30 s.

## `tapebasis_health`

```
tapebasis_health()                 # cached verdict
tapebasis_health(refresh=True)     # probe every upstream now
```

Returns per-probe status, message, fingerprint, and whether the result matches the baseline, plus a
`rate_limits` block with each provider's limiter state:

```jsonc
{
  "providers": ["TRADINGVIEW", "FINVIZ", "YAHOO"],
  "degraded": false,
  "issues": [ ... ],
  "rate_limits": {
    "TRADINGVIEW": { "min_interval_s": 0.5, "max_inflight": 2, "cooling_down_s": 0.0,
                     "block_streak": 0, "cached_entries": 12 },
    "YAHOO": { "min_interval_s": 1.0, "max_inflight": 1, "cooling_down_s": 4.7,
               "block_streak": 1, "cached_entries": 3 }
  }
}
```

A positive `cooling_down_s` means a provider is in its post-`429` pause right now; that is usually
the explanation for a response that suddenly went single-source. Use `refresh=True` when you suspect
drift but the cache has not rolled over.

## Probe health vs. request health

The probes answer "is the upstream shape still what the parser expects". They cannot answer "was this
particular request rate-limited". Those are separate diagnostics:

| Symptom | Where to look |
| --- | --- |
| A provider silently stopped contributing | `errors` (codes) + `rate_limits` (`cooling_down_s`) |
| A provider answers but fields are missing | probe `DRIFT` for that provider |
| Everything is `NONE` | DNS/network first, then `tapebasis_health(refresh=True)` |

## Resetting a baseline

After an intentional upstream change that you have adapted to (e.g. updating a parser), the stored
baseline is stale and will keep reporting `DRIFT`. Delete the state file and let the next round
re-baseline:

```bash
rm ~/.tapebasis/health_state.json
```

In Docker, the state lives on the `tapebasis-state` volume:

```bash
docker compose exec tapebasis rm /home/mcpuser/.tapebasis/state/health_state.json
```

Only do this when you know the current shape is correct: resetting hides an active drift.

## Why not just retry?

A retry fixes a transient failure. Drift is not transient: the endpoint answers, it just no longer
means what the code assumes. Retrying forever would look healthy while returning wrong numbers. The
monitor's job is to make that state visible to the agent reading the response.

## Related

- [Response Contract](Response-Contract): `degraded` and `warnings`
- [Configuration](Configuration): the health and rate-limit variables
- [Troubleshooting](Troubleshooting): what to do when a probe reports `DRIFT`
