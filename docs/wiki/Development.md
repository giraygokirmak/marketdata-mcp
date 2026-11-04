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
pytest                      # 145 hermetic tests, no network
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
| `test_reconcile.py` | Fan-out, comparison tolerances, fallback, notice text |
| `test_tools_merge.py` | End-to-end tool contract with faked providers |
| `test_shapers.py` | Pure payload shapers |
| `test_symbols.py` | BRK-B ↔ BRK.B, index aliasing, RUT/IWM separation |
| `test_freshness.py` | Print-time parsing, freshest-print selection, tie handling |
| `test_health.py` | Probe verdicts, fingerprints, drift detection |
| `test_sentiment.py` | Local scoring: direction, neutrality, determinism, aggregation |
| `test_sentiment_tool.py` | Token-free path, backend switching, no fabricated scores |
| `test_server.py` | Tool registration, MCP surface |

The drift tests are worth reading: they corrupt a stored baseline and assert the warning reaches an
ordinary tool response — proving the monitor is wired into real responses, not just its own endpoint.

## Layout

```
src/tapebasis/
├── server.py             FastMCP instance, lifespan, /health, CLI
├── providers_state.py    DataProvider enum, errors, response envelope
├── reconcile.py          concurrent fan-out, comparison, fallback
├── merge_helpers.py      pure payload shapers
├── sentiment.py          token-free headline scoring
├── symbols.py            symbol/index normalisation
├── health.py             upstream probes, fingerprints, drift state
├── tv_access.py          typed access to the TradingView provider
├── fv_access.py          typed access to the Finviz provider
├── catalog.py            tool catalogue (plain data)
├── tools_market.py       dual-source tools
├── tools_finviz.py       Finviz-only tools
├── tools_tradingview.py  TradingView-only tools + combined_analysis
├── tools_health.py       health + tool map
└── providers/            provider implementations (TradingView, Finviz)
```

## Adding a dual-source tool

1. **Check both providers can serve it.** If only one can, it is a single-source tool — do not fake a
   comparison.
2. **Add an accessor** to `tv_access.py` / `fv_access.py` that returns a normalised shape and raises
   `ProviderError` on failure.
3. **Add shapers** to `merge_helpers.py` — pure `dict → dict`, no I/O.
4. **Add `FieldSpec`s** with a tolerance and, where useful, an absolute floor.
5. **Write the tool** in `tools_market.py` using `ask()` + `finalize()`.
6. **Register it** in `_TOOLS` and in `catalog.py`.
7. **Test** with faked providers: both-answer, one-fails, both-fail, and a disagreement.

### Choosing a tolerance

Ask what a legitimate difference looks like, then set the threshold just above it:

- Identical fact, different print time → tight (0.5 %).
- Derived from different reference points → looser (2–3 %).
- Different lookback windows → loosest (5–12 %).

Set it too tight and every response warns; too loose and real conflicts pass silently. State the
reason in the `FieldSpec` or a comment — a future reader will want to know why 12 % was acceptable.

## Adding a single-source tool

Use `single(provider, value)` and `finalize(..., data=..., provider=...)`. The other provider is
marked `NOT_ATTEMPTED`, which is excluded from `errors`.

If the data originates from a different provider than the one whose dataset you are calling, **label
the actual origin**. `market_sentiment`'s local path is `FINVIZ` because the headlines are Finviz's,
even though the tool lives in `tools_tradingview.py`.

## House rules

- A response must never raise. Convert at the boundary; let `reconcile.py` assemble.
- `NOT_ATTEMPTED` is not an error. Do not add it to `errors`.
- Never fabricate a value. `None` + a reason beats `0.0` that reads as a real finding.
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
in the repository, not in the wiki UI — the repo is the source of truth. After editing, publish:

```bash
./scripts/publish-wiki.sh           # push docs/wiki/*.md to the wiki repo
./scripts/publish-wiki.sh --dry-run # show what would change
```

Links are written wiki-style (`[Architecture](Architecture)`), which renders in both places. Keep it
that way: a `docs/wiki/X.md` path would break in the wiki, where pages live at the root.

When behaviour changes, update the page that documents it in the same commit. A doc that describes
the previous contract is worse than no doc, because it reads as authoritative.

## Related

- [Architecture](Architecture) — why the layers are ordered this way
- [Design Decisions](Design-Decisions) — rules that are easy to violate by accident
