# Troubleshooting

Symptom → cause → fix.

---

## `data_provider` is `TRADINGVIEW` or `FINVIZ`, not `BOTH`

**Cause:** one provider failed or timed out for this call.

**Diagnose:** read `errors` in the response — it names the failing provider and a code.

| Code | Meaning | Fix |
| --- | --- | --- |
| `UPSTREAM_TIMEOUT` | Provider too slow | Retry; raise `FINVIZ_TIMEOUT` if persistent |
| `UPSTREAM_RATE_LIMIT` | Too many requests | Raise `TRADINGVIEW_MCP_MIN_INTERVAL_S` |
| `UPSTREAM_BLOCKED` | WAF rejected the request | Usually transient; check `marketdata_health` |
| `NO_DATA` | Provider answered, had nothing for this symbol | Check the ticker spelling |
| `PARSE_ERROR` | Markup/JSON shape changed | See [health drift](#marketdata_health-reports-drift) |
| `NOT_CONFIGURED` | Feature needs a token | Add `MARKETAUX_API_TOKEN`, or ignore — data is still served |

A single-source response is **not** a failure. The data is served and labelled; the notice says it is
not cross-verified.

---

## `data_provider` is `NONE`

**Cause:** neither provider answered.

1. Check `errors` — usually two entries.
2. Run `marketdata_health(refresh=True)`.
3. Most common root cause is a lost network path or DNS, not the code.
4. For an unknown symbol: `NO_DATA` from both is correct behaviour, not an outage.

Never present a value when `data_provider` is `NONE`.

---

## `marketdata_health` reports `DRIFT`

**Cause:** an upstream changed shape — a renamed JSON field, a moved route, a missing HTML marker.
The endpoint still answers 200, which is exactly why this is detected separately.

1. Read the probe's `message` — it names the field or marker that vanished.
2. Fetch the endpoint yourself and compare against what the parser expects.
3. If upstream genuinely changed: update the parser or shaper, then reset the baseline.
4. If the change looks temporary/interstitial: wait one interval, re-check with `refresh=True`.

**Do not reset the baseline before fixing the parser.** That converts a detectable break into a
silent wrong answer.

```bash
rm ~/.marketdata-mcp/health_state.json     # only after fixing the parser
```

---

## Prices differ between providers

**Cause:** normally the print times differ, or a field is derived differently.

1. Read `comparisons[].diff_pct` — how far apart, in percent.
2. Compare against `tolerance_pct` for that field.
3. Within tolerance → `match: true`, no warning. Normal.
4. Beyond tolerance → `match: false`, named in `notice` and `warnings`.

**Check `sources` before assuming a bug.** The `TRADINGVIEW` leg's price comes from Yahoo Finance, so
a genuine divergence is a Yahoo-vs-Finviz difference, not TradingView-vs-Finviz. A closed market
should agree to the cent; an intraday read legitimately will not.

If a mismatch is systematic on one field, the tolerance may be wrong — see
[Development § Choosing a tolerance](Development#choosing-a-tolerance).

---

## `change_percent` is consistently slightly off between providers

**Expected.** Not a bug. Each provider computes the day change from its own reference close, so a
small gap is a formula difference. `change %` therefore carries a 2 % tolerance (floor 0.05), and for
indices it is reported from both but deliberately **not** compared.

The underlying `previous_close` *is* compared tightly (0.5 %) — if that disagrees, it is a real
conflict.

---

## `stock_quote` returns `peers` as `FINVIZ` inside a `BOTH` response

**Expected.** `stock_quote` mixes sources: price and core metrics are compared across providers, but
the fundamentals snapshot, company profile, peers and after-hours print exist only on Finviz.

`fields` exists precisely for this. `data_provider: BOTH` means the *compared* fields were confirmed
twice, not that every field was. Per-field truth is in `fields`.

---

## `market_sentiment` returns `provider: local-vader` and `data_provider: FINVIZ`

**Expected** when `MARKETAUX_API_TOKEN` is unset or rejected. The headlines are Finviz's; only the
scoring is local, so the label is `FINVIZ`.

If you expected the licensed path:

1. Check `data.licensed_path_unavailable` — it carries the reason.
2. Most common: no token, expired token, or the daily budget is spent.
3. `MARKETAUX_DAILY_BUDGET` defaults to 90 under a 100/day plan.

To use the licensed path, set `MARKETAUX_API_TOKEN`. To accept local scoring, do nothing.

---

## `market_sentiment` returns `sentiment_score: null`

**Cause:** there was nothing to score — Marketaux unavailable **and** Finviz headlines unavailable.

Read `note` for both reasons. This is deliberate: `null` means "we could not look", `0.0` would mean
"the news is balanced". They are never conflated, because a data gap must not read as a finding.

---

## Sentiment disagrees with my own reading of the news

**Expected sometimes.** Scoring is a lexicon over headline text, not comprehension. It cannot parse
negation or long-range context, so some headlines will land wrong.

Practical notes:

- It is deterministic and cheap, not accurate in an absolute sense — a triage signal.
- `top_posts` shows the highest-conviction headlines, so you can audit what drove the average.
- `posts_analyzed` tells you the sample size; a score over 3 headlines is weak evidence.
- It is not a trading signal, and the payload says so.

If a specific domain word is scored wrong, adding it to the overlay in `sentiment.py` with an
explicit valence is the fix. Use a **float** — VADER adds valences arithmetically and a string raises
`TypeError`.

---

## `combined_analysis` has an empty `news` leg

**Expected** without `MARKETAUX_API_TOKEN` — that leg is Marketaux-only. The sentiment leg still works
(local scoring), and `data.news` says why it is empty. Price cross-check is unaffected.

---

## A tool hangs for ~25 s then returns single-source

**Cause:** one provider hit its HTTP timeout and the other's data still went out. The fan-out waits
for both, so latency is the slowest leg.

Tuning: lower `FINVIZ_TIMEOUT` to fail faster, at the cost of losing slow-but-valid answers.

---

## `/health` says healthy but tool calls fail

`/health` reads the **cached** verdict and makes no upstream call — by design, so a Finviz outage does
not mark the container unhealthy. A stale cache can therefore look green while upstreams are broken.

```bash
marketdata_health(refresh=True)
```

---

## Container is unhealthy

```bash
docker compose ps
docker compose logs --tail=100 marketdata-mcp
```

1. `start_period` is 15 s; a slow first probe can trip an early check. Wait, then re-check.
2. Confirm `HOST=0.0.0.0` inside the container — loopback makes the port mapping useless.
3. Confirm the health path is `/health`, not `/mcp`.

---

## `ModuleNotFoundError: marketdata_mcp`

The package is not installed in the interpreter the MCP client uses.

```bash
pip install -e .
# or point the client at the venv's absolute path:
#   "command": "/absolute/path/.venv/bin/marketdata-mcp"
```

MCP clients do not inherit your shell `PATH`; absolute paths avoid the whole class of problem.

---

## Symbol returns `NO_DATA` but I know it trades

Provider symbol conventions differ:

| Case | Handling |
| --- | --- |
| `BRK.B` ↔ `BRK-B` | Normalised automatically |
| `^GSPC` ↔ `SPX` ↔ `SP:SPX` | Aliased to `SPX` |
| `RUT` vs `IWM` | Index vs its ETF — **different instruments**, never aliased |
| Crypto (`BTC-USD`), FX (`EURUSD=X`) | TradingView side only; Finviz has no coverage |
| Non-US (`.IS`, `.CA`) | TradingView only; Finviz may redirect or return nothing |

If Finviz silently resolves a delisted/renamed ticker, the response carries `ticker_warning` with
both the requested and resolved symbol — never assume they are the same.

---

## Related

- [Health Monitor](Health-Monitor) — drift detection in depth
- [Response Contract](Response-Contract) — reading `errors`/`warnings`
- [Configuration](Configuration) — every tunable
