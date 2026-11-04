# Health Monitor

Both providers depend on things that can move without notice: Finviz's HTML class names and
unversioned `/api/*` JSON islands, TradingView's scanner POST schema, Yahoo's chart API shape.

A provider that catches its own exception looks healthy from the outside. So the monitor probes each
upstream **independently of the provider code paths** — it can fail even when every tool call
succeeds, and that failure is the signal.

## Probes

| Probe | Provider | Asserts |
| --- | --- | --- |
| `finviz.indices_api` | FINVIZ | `DJI`, `SPX`, `COMP` keys present in `/api/indices_data` |
| `finviz.quote_html` | FINVIZ | `js-snapshot-table` marker still in the rendered `/stock?t=AAPL` page |
| `finviz.news_api` | FINVIZ | `html` key present in `/api/news/quote/AAPL` |
| `tradingview.scanner_api` | TRADINGVIEW | `totalCount` and `data[0].d` present in the scanner POST response |
| `tradingview.yahoo_chart_api` | TRADINGVIEW | `chart.result[0].meta` and `chart.result[0].timestamp` present |

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
| `DRIFT` | Answered, but the status or shape changed — renamed field, moved route, missing marker |
| `DOWN` | Timeout, connection error, or unexpected HTTP status |
| `UNKNOWN` | Not probed yet |

## How drift reaches a normal tool call

Drift is surfaced, never swallowed. Every tool response gets:

- a `warnings` entry naming the affected upstream,
- `degraded: true`,
- a `WARNING` appended to `notice`.

```jsonc
{
  "data_provider": "BOTH",
  "notice": "Data came from BOTH providers ... WARNING: the provider health monitor reports an upstream change or outage — see tapebasis_health.",
  "warnings": ["TRADINGVIEW tradingview.scanner_api: DRIFT — payload shape changed"],
  "degraded": true
}
```

`degraded` is scoped to the providers **this call actually used**. A Finviz-only tool does not go
degraded because TradingView drifted.

## Timing

| Setting | Default | Effect |
| --- | --- | --- |
| `TAPEBASIS_HEALTH_ENABLED` | `true` | Turn the monitor on/off |
| `TAPEBASIS_HEALTH_INTERVAL` | `300` | Seconds between background probe rounds |
| `TAPEBASIS_HEALTH_TTL` | `60` | Seconds a cached verdict is reused |

The monitor runs in the background; tool calls only read the cached verdict, so **health checking
never adds latency to a data call**. `/health` also reads the cache — it is cheap enough for a
container healthcheck to hit every 30 s.

## `tapebasis_health`

```
tapebasis_health()                 # cached verdict
tapebasis_health(refresh=True)     # probe every upstream now
```

Returns per-probe status, message, fingerprint, and whether the result matches the baseline. Use
`refresh=True` when you suspect drift but the cache has not rolled over.

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

Only do this when you know the current shape is correct — resetting hides an active drift.

## Why not just retry?

A retry fixes a transient failure. Drift is not transient: the endpoint answers, it just no longer
means what the code assumes. Retrying forever would look healthy while returning wrong numbers. The
monitor's job is to make that state visible to the agent reading the response.

## Related

- [Response Contract](Response-Contract) — `degraded` and `warnings`
- [Troubleshooting](Troubleshooting) — what to do when a probe reports `DRIFT`
