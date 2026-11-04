# Response Contract

Every tool returns the same envelope. Learn it once and every tool is readable.

## The four provider states

| `data_provider` | `verification` | Meaning | What an agent should do |
| --- | --- | --- | --- |
| `BOTH` | `VERIFIED` | Both providers answered; compared fields agree | Quote the numbers; they were confirmed twice |
| `TRADINGVIEW` | `UNVERIFIED` | Only TradingView answered | Quote, but say it is single-source and unverified |
| `FINVIZ` | `UNVERIFIED` | Only Finviz answered | Same, from the Finviz side |
| `NONE` | `UNAVAILABLE` | Neither answered | Say no data is available. Do not invent one. |

`verification` is derived, never independent: `BOTH → VERIFIED`, a single provider `→ UNVERIFIED`,
`NONE → UNAVAILABLE`.

**`BOTH` does not mean "the values agree".** It means both providers *answered*. Agreement is a
separate axis, reported per field in `comparisons[].match` and summarised in `notice`/`warnings`.
Both answers being present but diverging is still `BOTH` — with the conflict named.

## Fields

### `data_provider`
`"BOTH" | "TRADINGVIEW" | "FINVIZ" | "NONE"`. The single most important field in the response.

### `verification`
`"VERIFIED" | "UNVERIFIED" | "UNAVAILABLE"`. Convenience mirror of the table above.

### `notice`
One sentence, written to be relayed verbatim to a user. It always names the provider state. When
providers disagree it names the disagreeing fields and says both readings are in `comparisons`. When
the health monitor has seen drift, a `WARNING` is appended.

### `data`
The reconciled payload. Its shape is tool-specific (a dict for `stock_quote`, a list for
`stock_prices`, a nested object for `market_indices`). This is the only place a value should be read
from.

**One value per compared field.** When both providers answered, `data` holds exactly one price — the
reading from whichever provider printed more recently. Both originals stay in `comparisons`, and
`freshness` records which one won and why. An agent never has to reconcile two numbers to answer
"what is the price", but it can always audit the choice.

### `fields`
Per-field provenance: `{"price": "BOTH", "peers": "FINVIZ", "after_hours_price": "FINVIZ"}`.

Needed because most responses legitimately mix sources. `stock_quote` gets price from both providers
but the 84-field fundamentals snapshot, company profile and peer list from Finviz alone — a blanket
`BOTH` on the whole response would overclaim.

### `comparisons`
One row per compared field:

```jsonc
{
  "field": "price",
  "tv": 331.9,          // TradingView reading, verbatim
  "fv": 331.9,          // Finviz reading, verbatim
  "match": true,        // null when either side is non-numeric
  "diff": 0.0,          // absolute difference
  "diff_pct": 0.0,      // relative difference, %
  "tolerance_pct": 0.5, // threshold this field was judged against
  "chosen": "TRADINGVIEW"  // which reading went into `data`
}
```

`match: null` means the comparison was impossible (a non-numeric or missing value) — **not** a
mismatch. `match: false` is a real disagreement.

### `freshness`
Present when both providers answered **and** at least one reported a usable print time. Tools that
compare non-price fields (index levels, row counts) omit it entirely rather than announce that no
timestamp existed:

```jsonc
{
  "tradingview": "2026-11-03T21:00:01+00:00",   // market print time, UTC
  "finviz": "2026-11-03T20:59:00+00:00",
  "chosen": "TRADINGVIEW",
  "reason": "newest market print timestamp"
}
```

These are **print** times, not fetch times. When both timestamps are identical, or neither can be
parsed, `chosen` is `FINVIZ` and `reason` says so — a fixed default, not an implicit preference.

`reason` is one of:

| `reason` | Meaning |
| --- | --- |
| `newest market print timestamp` | One provider printed strictly later; its values were used |
| `timestamps identical; Finviz is the documented default` | Both printed at the same instant |
| `no usable market timestamp from either provider; ...` | Neither side exposed a parseable print time |

### `sources`
Where each provider's data actually came from: upstream URL or dataset name.

```jsonc
{ "TRADINGVIEW": "Yahoo Finance chart API (via TradingView provider)",
  "FINVIZ": "https://finviz.com/stock?t=AAPL" }
```

This is the honesty field. The `TRADINGVIEW` label names the provider *dataset*, and `sources` states
when that dataset is itself Yahoo Finance.

### `errors`
Real failures only:

```jsonc
{ "provider": "FINVIZ", "code": "UPSTREAM_TIMEOUT", "message": "..." }
```

`NOT_ATTEMPTED` is **excluded**. It means "this provider has no such capability" (Finviz has no
futures feed), which is a structural fact, not an error.

Common codes: `UPSTREAM_TIMEOUT`, `UPSTREAM_RATE_LIMIT`, `UPSTREAM_BLOCKED`, `UPSTREAM_ERROR`,
`NO_DATA`, `PARSE_ERROR`, `INVALID_TICKER`, `NOT_CONFIGURED`, `PROVIDER_ERROR`.

### `warnings`
Human-readable cautions. A disagreement adds `"Providers disagree on: price"`. Health drift adds a
warning naming the affected upstream.

### `degraded`
`true` when the health monitor reports drift or an outage **for a provider this call actually used**.
Health problems on an unused provider do not mark the response degraded.

### `generated_at`
UTC ISO-8601 timestamp of response construction.

## Tolerance

Comparisons are relative (`diff_pct <= tolerance_pct`), with an optional absolute floor so tiny
readings are not flagged over rounding noise.

| Field | Tolerance | Why |
| --- | --- | --- |
| price | 0.5 % | Same instrument, different print times |
| change % | 2 % (floor 0.05) | Derived from different reference closes |
| previous close | 0.5 % | Should be identical; a gap is meaningful |
| P/E | 2 % | Rounding in the Finviz snapshot |
| RSI(14) | 5 % (floor 0.5) | Different lookback windows |
| volume | 1 % | Intraday accumulation timing |
| options volume | 5 % ; open interest 1 % | Different snapshot moments |

Tolerances are per-field in `tools_market._quote_specs()` and the comparison sites, and are echoed
back in each `comparisons` row.

## A worked disagreement

```jsonc
{
  "data_provider": "BOTH",
  "verification": "VERIFIED",
  "notice": "Data came from BOTH providers ... NOTE: the two providers disagree on price; both readings appear in `comparisons` and the response holds a single value from TRADINGVIEW (the newer print). The conflict is reported, not silently resolved.",
  "data": { "price": 331.9, ... },          // one value: the fresher print
  "fields": { "price": "BOTH", ... },
  "freshness": {
    "tradingview": "2026-11-03T21:00:01+00:00",
    "finviz": "2026-11-03T20:59:00+00:00",
    "chosen": "TRADINGVIEW",
    "reason": "newest market print timestamp"
  },
  "comparisons": [
    { "field": "price", "tv": 331.9, "fv": 300.0,
      "match": false, "diff_pct": 9.6, "chosen": "TRADINGVIEW" }
  ],
  "warnings": ["Providers disagree on: price"],
  "degraded": false
}
```

Read it as: *both providers answered, they contradict each other by 9.6 %, and nothing was hidden.*
`data.price` holds **one** value — the fresher print (TradingView's here) — the losing value is one
field away in `comparisons`, and the notice says exactly that.

## Why not just return one number?

It does return one number — but the envelope keeps the second one visible next to it. Silently
discarding a contradicting reading is how a wrong number becomes authoritative: if an agent cannot
see that a second independent source disagreed, it cannot warn its user. One value in `data`, both
values in `comparisons`, and the reason for the choice in `freshness`.

## Related

- [Tools Reference](Tools-Reference) — which fields each tool compares
- [Health Monitor](Health-Monitor) — how `degraded` is decided
