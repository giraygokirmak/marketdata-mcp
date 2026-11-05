# Development

## Setup

```bash
git clone https://github.com/giraygokirmak/tapebasis.git
cd tapebasis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Tests

```bash
pytest                      # 222 hermetic tests, no network
pytest -m live              # opt-in: hits real upstreams
pytest tests/test_reconcile.py -v
```

The suite is **hermetic**. Provider calls are replaced at the access boundary, HTTP is mocked with
`httpx.MockTransport`, and health state is redirected to a temp directory. No test reaches the
internet, and the suite is deterministic.

That matters for a project whose whole subject is unreliable upstreams: a test that needed a live
Finviz page would fail for reasons unrelated to the code.

### Test files

| File | Covers |
| --- | --- |
| `test_reconcile.py` | Fan-out, multi-provider comparison, tolerances, precedence, notice text |
| `test_tools_merge.py` | End-to-end tool contract with faked providers (`ALL` vs single-source, `not_comparable`) |
| `test_upstream_contract.py` | Regression tests for every upstream behaviour found by live probing (see below) |
| `test_shapers.py` | Pure payload shapers |
| `test_symbols.py` | BRK-B ↔ BRK.B, index aliasing, RUT/IWM separation, per-provider `none` resolutions |
| `test_freshness.py` | Print-time parsing, freshest-print selection, tie and `no_timestamp` handling |
| `test_health.py` | Probe verdicts, fingerprints, drift detection, `rate_limits` block |
| `test_sentiment.py` | Local scoring: direction, neutrality, determinism, aggregation |
| `test_sentiment_tool.py` | Token-free path, backend switching, no fabricated scores |
| `test_bloomberg.py` | Symbol mapping, `print_time` from the intraday series, sentinel rejection, single-source envelope |
| `test_server.py` | Tool registration, MCP surface |

`test_upstream_contract.py` is the file to extend when live probing finds a provider behaving
surprisingly. Every case in it was found by calling the real upstream and getting something the
contract did not expect:

| Regression | What live probing revealed |
| --- | --- |
| Scanner partitioning | A mixed-market scanner batch silently dropped tickers; unresolved symbols are retried in `global` |
| Yahoo fabricated FX ticker | `XAUUSD=X` is a 404; the suffix rule now requires two real ISO currency codes |
| Finviz index 404 | `SPX` and `DJI` have no Finviz quote page, and `COMP` is Compass Inc, a real equity |
| Blocking screener | `screen_stocks` is sync pandas/requests and must run under `asyncio.to_thread` |
| TradingView search lies | Search offers `OANDA:BTCUSD`; the scanner has no row. Candidates are verified against the scanner, so BTCUSD resolves to `BITSTAMP:BTCUSD` |
| Silent per-provider misses | `stock_prices` must report a `misses` map rather than dropping a ticker |
| Session-relative fields | A pre-market Finviz close against a prior-close Yahoo print produced a false `change_percent` conflict, now `not_comparable` |
| Snapshot row duplication | Yahoo answers `USDJPY=X` with `meta.symbol: JPY=X`; rows must join on the requested ticker |
| Unrelated screener universes | Ranking independently gave zero shared rows; Finviz is now asked about exactly TradingView's tickers |

The drift tests are worth reading too: they corrupt a stored baseline and assert the warning reaches
an ordinary tool response, proving the monitor is wired into real responses, not just its own
endpoint.

## Layout

```
src/tapebasis/
├── server.py             FastMCP instance, lifespan, /health, CLI
├── providers_state.py    DataProvider enum, errors, response envelope
├── reconcile.py          concurrent fan-out, comparison, freshness arbitration, notice text
├── freshness.py          market-print timestamps + freshest-print selection
├── ratelimit.py          per-provider pacing, shared cooldown, TTL cache
├── merge_helpers.py      pure payload shapers
├── sentiment.py          token-free headline scoring
├── symbols.py            dynamic per-provider symbol resolution
├── health.py             upstream probes, fingerprints, drift state
├── tv_access.py          typed access to TradingView's own data
├── fv_access.py          typed access to the Finviz provider
├── yh_access.py          typed access to the Yahoo Finance provider
├── bb_access.py          typed access to bloomberg.com (single-source, curl_cffi)
├── catalog.py            tool catalogue (plain data)
├── tools_market.py       multi-source tools
├── tools_finviz.py       Finviz-only tools
├── tools_tradingview.py  TradingView-only tools + combined_analysis
├── tools_bloomberg.py    Bloomberg-only tools (single-source)
├── tools_health.py       health + tool map
└── providers/            provider implementations (TradingView, Finviz)
```

## Adding a multi-source tool

1. **Check which providers can serve it.** Ask every provider that genuinely can. If only one can
   it is a single-source tool, do not fake a comparison.
2. **Add an accessor** to `tv_access.py` / `fv_access.py` / `yh_access.py` that returns a normalised
   shape and raises `ProviderError` on failure. Every accessor must go through
   `ratelimit.limiter(PROVIDER)` so the new call inherits pacing and cooldown.
3. **Add shapers** to `merge_helpers.py`, pure `dict → dict`, no I/O.
4. **Add `FieldSpec`s** with `tv_get`, `fv_get` **and** `yh_get` where the provider can supply the
   field, plus a tolerance. Mark session-relative fields with `max_skew_seconds`,
   `session_boundary=True` and, when the tool can see the clock, `max_age_seconds`.
5. **Write the tool** in `tools_market.py` using `ask(tv, fv, yh)` + `finalize()`.
6. **Register it** in `_TOOLS` and in `catalog.py` with the real provider set.
7. **Test** with faked providers: all-answer, partial, one-answers, all-fail, a disagreement, and
   a session-skew case.

### Choosing a tolerance

Ask what a legitimate difference looks like, then set the threshold just above it:

- Identical fact, different print time → tight (0.5 %).
- Derived from different reference points → looser (2–3 %).
- Different lookback windows → loosest (5–12 %).
- A volatility gauge that moves percent-level within a minute → loosest of all (VIX, 3 %).

Set it too tight and every response warns; too loose and real conflicts pass silently. State the
reason in the `FieldSpec` or a comment: a future reader will want to know why 12 % was acceptable.

### When readings are not comparable

Do not widen a tolerance to hide a legitimate difference. If two readings are measured against
different baselines (a day change against a previous close, a session volume), set the guards
described in Design Decisions 28: `max_skew_seconds` for distance, `session_boundary` for a roll,
and `max_age_seconds` against the clock. The pair is then reported as `not_comparable` with the
reason, and the field is named in `warnings`. Widening the tolerance would report a false agreement;
leaving it tight would report a false conflict. Neither is true.

Use `require_all_times=True` when a provider does not expose observation times at all: agreement
cannot be *proven*, so `match` stays `null`.

Use `not_comparable_reason` when the field has no shared definition at all, so no timestamp check can
help. FX is the case in the code: spot FX has no single official previous close, each provider picks
its own daily roll, so `change_percent` is reported from every provider but never judged. Do not use
this to paper over a tolerance you cannot justify: it makes a claim about the field, not about the
data quality.

## Adding a single-source tool

Use `single(provider, value)` and `finalize(... data=... provider=...)`. The other providers are
marked `NOT_ATTEMPTED`, which is excluded from `errors`.

If the data originates from a different provider than the one whose dataset you are calling, **label
the actual origin**. `market_sentiment`'s local path is `FINVIZ` because the headlines are Finviz's
even though the tool lives in `tools_tradingview.py`.

A provider that cannot strengthen a cross-check does **not** get a place in `PROVIDER_ORDER`. Build
it like `tools_bloomberg.py`: its own label in `DataProvider`, its own accessor, `envelope(...)` with
`provider=DataProvider.<NAME>` so the result reads `SINGLE_SOURCE`, plus `cross_checked: false` and
`reconciliation_leg: false`. Keep it out of `single()` too: that helper iterates `PROVIDER_ORDER`, so
a label outside it produces no attempt row at all. Add the label to `PROVIDER_NAMES` and
`PROVIDER_MEANING`, and add its limiter to `ratelimit.LIMITERS` so `GET /health` still reports its
pacing. Needs and needs-not for the label itself are in Design Decisions 30.

## House rules

- A response must never raise. Convert at the boundary; let `reconcile.py` assemble.
- `NOT_ATTEMPTED` is not an error. Do not add it to `errors`.
- Never fabricate a value or a ticker. `None` + a reason beats `0.0` that reads as a real finding, and
  `none` beats a plausible-looking symbol the provider cannot quote.
- Every upstream call goes through a limiter. A new provider path that bypasses `ratelimit.py`
  reintroduces the burst that gets the process blocked.
- Velocities and percentages need a sign convention; state it where it is computed.
- Provider code does not import from the orchestration layer. Keep the dependency one-way.

## Checks before pushing

```bash
pytest
python -m pyflakes src/tapebasis tests        # provider code has pre-existing noise
python -m compileall -q src/tapebasis
```

## CI

Two GitHub Actions workflows:

| Workflow | Purpose |
| --- | --- |
| `test.yml` | Install on 3.10–3.13, run `pytest`, smoke-test the tool surface |
| `publish-image.yml` | Build and publish the multi-arch image to `ghcr.io/<owner>/<repo>` |

## Editing these docs

These pages are authored in [`docs/wiki/`](https://github.com/giraygokirmak/tapebasis/tree/main/docs/wiki)
in the repository, not in the wiki UI: the repo is the source of truth. After editing, publish:

```bash
./scripts/publish-wiki.sh           # push docs/wiki/*.md to the wiki repo
./scripts/publish-wiki.sh --dry-run # show what would change
```

Links are written wiki-style (`[Architecture](Architecture)`), which renders in both places. Keep it
that way: a `docs/wiki/X.md` path would break in the wiki, where pages live at the root.

When behaviour changes, update the page that documents it in the same commit. A doc that describes
the previous contract is worse than no doc, because it reads as authoritative.

## Related

- [Architecture](Architecture): why the layers are ordered this way
- [Design Decisions](Design-Decisions): rules that are easy to violate by accident
