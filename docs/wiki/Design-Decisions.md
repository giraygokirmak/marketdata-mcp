# Design Decisions

Non-obvious choices, and the reasoning that produced them. Each entry is a rule that is easy to
violate by accident.

---

## 1. Four provider states, not two

**Decision:** `BOTH` / `TRADINGVIEW` / `FINVIZ` / `NONE` — never a boolean like `verified: true`.

**Why:** "verified" collapses two different facts: *how many providers answered* and *whether they
agreed*. A boolean loses the case that matters most — both answered and disagreed. With four states
plus `comparisons`, that case is visible instead of hidden.

**Consequence:** `BOTH` means "both answered", not "both agree". Agreement lives in
`comparisons[].match`. This is documented in three places because it is the single easiest thing to
misread.

---

## 2. Disagreement is never resolved by fiat

**Decision:** when providers disagree, keep both values in `comparisons`, name the field in `notice`
and `warnings`, and leave `data_provider` as `BOTH`.

**Why:** silently picking one turns an arbitrary choice into an authoritative-looking number. An
agent that cannot see the conflict cannot warn its user. The cost is a slightly noisier response; the
benefit is that a wrong number never becomes invisible.

---

## 3. Single-source data is served, not withheld

**Decision:** if one provider answers, serve it — labelled and with an explicit "not cross-verified"
notice.

**Why:** withholding one provider's valid data would be worse than serving it with a caveat. The
failure mode we care about is a *number presented as verified when it is not*, not the existence of
unverified numbers. The label, not the omission, is the safeguard.

---

## 4. `NOT_ATTEMPTED` is not an error

**Decision:** a capability a provider simply does not have (Finviz has no futures feed) produces
`NOT_ATTEMPTED`, excluded from `errors`.

**Why:** conflating "this provider cannot answer" with "this provider failed" makes structural facts
look like outages. A reader would see errors on every crypto call and learn to ignore errors —
destroying the signal for real failures.

---

## 5. Health probes bypass the provider code

**Decision:** `health.py` probes upstreams directly, not through `tv_access`/`fv_access`.

**Why:** a provider that catches its own exceptions looks healthy from outside. Probing through it
would inherit the same blindness. Independent probes can fail while every tool call succeeds, and
that divergence is the signal.

---

## 6. Fingerprints, not just status codes

**Decision:** store a SHA-256 fingerprint of the payload's key paths as a baseline; compare later.

**Why:** a drifted endpoint returns HTTP 200 with a renamed field. Status-only monitoring reports a
broken parser as healthy while it returns `None`. The baseline also prevents the first observation of
an already-drifted shape from being adopted as the new normal.

---

## 7. Health state is persisted to disk

**Decision:** baselines live in `~/.marketdata-mcp/health_state.json`, volume-mounted in Docker.

**Why:** in-memory baselines reset on every restart, so a slow-moving schema change would be adopted
silently at each restart. Persistence is what makes drift detection cumulative.

---

## 8. The monitor starts lazily, not via lifespan

**Decision:** `MONITOR.ensure_started()` is called from tool entry points.

**Why:** FastMCP's streamable-HTTP app builds its own lifespan and ignores `lifespan=`. A
lifespan-only start would work over stdio and silently never run over HTTP — a bug that only appears
in the deployment most people use. Lazy start works on both transports.

---

## 9. `data` holds one value per field; `comparisons` holds both

**Decision:** resolve each compared field to a single value in `data` — the reading from whichever
provider **printed more recently** — while `comparisons` keeps both raw readings verbatim.

**Why:** an agent should not have to reconcile two numbers to answer "what is the price". But it must
be able to *audit* that choice, which requires the loser to remain one field away.

**Why freshness, not a fixed provider:** preferring Finviz unconditionally is arbitrary. During a
session one provider is routinely a minute behind the other (Yahoo stamps the close `21:00:01Z`,
Finviz renders `3:59 PM ET` = `20:59Z`), so a fixed winner periodically serves the staler price. The
freshest print is the defensible choice, and `freshness.reason` plus the notice state it plainly.
Ties — and cases where neither provider reports a usable timestamp — fall back to Finviz, a fixed
documented default rather than whichever label happened to be first.

---

## 10. Per-field provenance, not a blanket claim

**Decision:** `fields` maps each output field to its provider.

**Why:** most responses legitimately mix sources. `stock_quote` gets price from both but the
fundamentals snapshot, profile and peers from Finviz alone. A response-level `BOTH` would overclaim
for every Finviz-only field riding along.

---

## 11. Sentiment is labelled by data origin, not by code location

**Decision:** local sentiment over Finviz headlines returns `data_provider: FINVIZ`, even though the
tool lives in `tools_tradingview.py`.

**Why:** the numbers are Finviz's; only the scoring is ours. Labelling it `TRADINGVIEW` would assert
a provenance the data does not have — precisely the mislabelling this server exists to prevent. Code
location is an implementation detail; the label must describe the data.

---

## 12. `0.0` and `null` are not interchangeable

**Decision:** `sentiment_score: null` means "nothing could be scored"; `0.0` means "the tone is
balanced". The unavailable path never returns `0.0`.

**Why:** a failed fetch reported as `0.0`/`Neutral` reads as a finding — "the news is balanced" —
when the truth is "we could not look". Silent fallbacks to a neutral-looking default are a classic
way for a data gap to become an asserted fact. The same reasoning rejected a fabricated sentiment
score when Marketaux returns an empty article list.

---

## 13. A rejected token falls back rather than failing

**Decision:** an invalid/expired token falls back to local scoring, recording the cause in
`licensed_path_unavailable`.

**Why:** the tool's job is to produce sentiment, not to enforce a billing relationship. A hard
failure would make an expired token look like a broken server. But the fallback must be visible — a
silent switch from licensed to locally-computed numbers would misrepresent the output's quality.

---

## 14. Two independent providers, not two code paths to one upstream

**Decision:** TradingView and Finviz legs hit genuinely different upstreams.

**Why:** cross-verification is only meaningful if the sources are independent. This is also why the
TradingView provider's Yahoo-backed data is stated in `sources`: it is still a separate dataset from
Finviz, but a reader deserves to know it is Yahoo, not TradingView's own exchange feed.

**Consequence for news:** the obvious token-free news sources (Google News, Yahoo) were evaluated and
largely rejected — see below.

---

## 15. News sources were evaluated for independence, not just availability

**Decision:** keep Finviz as the news spine; use Marketaux as the optional second leg; score
sentiment locally. Do not add a scraped third-party news feed.

**Why:** candidate feeds were tested live and rejected on concrete grounds:

| Candidate | Rejected because |
| --- | --- |
| Nasdaq RSS (`rssoutbound?symbol=`) | Does not actually filter by symbol — `THYAO.IS` and `BTC-USD` returned identical items |
| Yahoo `/v1/finance/search` | Same upstream as the TradingView price leg, so it is not independent; also `Disallow: /` in robots.txt |
| Yahoo RSS feeds | User-Agent sensitive — browser UA got `429`, Windows UA got `200`; too brittle to depend on |
| Google News RSS | 100 items and genuinely independent, but article links are unresolvable Google redirects and `/rss/` is not in its robots allow-list |
| Reddit / StockTwits | Blocked (`403`) from this network |
| Finnhub / Polygon / Tiingo / AlphaVantage | All require a token — the opposite of the goal |

The result: no tool needs a key, and no third party's terms are strained to achieve it.

---

## 16. Index and ETF are never aliased

**Decision:** `RUT` (Russell 2000 index) and `IWM` (its tracking ETF) are separate canonical
instruments.

**Why:** they are different instruments at different price levels. Aliasing them would produce a
comparison that always "disagrees" and a canonical key that silently mixes two things.

---

## 17. `finviz_earnings_date` and `next_earnings_date` are separate fields

**Decision:** never merge them into one `earnings_date`.

**Why:** Finviz's is the **last reported** quarter's timestamp (the anchor for its results tables);
TradingView's is the **upcoming** release. They answer different questions. Merging them would
produce a date that is right for neither.

---

## 18. Options open interest: `0 vs N` is not a mismatch

**Decision:** count `0 vs N` under `open_interest_unavailable`, not as a conflict.

**Why:** Yahoo reports zero OI for contracts expiring the same day. Treating a provider convention as
a data conflict would generate false mismatches on exactly the contracts people look at most.

---

## 19. Day-change % is reported from both but not compared (indices)

**Decision:** compare index levels tightly (0.5 %), but not day-change percentages.

**Why:** each provider derives the change from its own reference close. A small gap is a formula
difference, not a data conflict. Comparing it would warn on every response and train readers to
ignore warnings — the same failure mode as a too-loose tolerance, from the other direction.

---

## 20. No proxy support

**Decision:** the vendored providers' proxy layer was removed entirely; no proxy variables exist.

**Why:** an explicit requirement, and it simplifies the failure surface. Egress control belongs at
the network or container layer, where it can be applied consistently and audited.

---

## 21. Docker fixes CA trust before installing anything

**Decision:** both build stages upgrade `pip` and `certifi` as the first action.

**Why:** base images built before the eMudhra/emSign CA incident (Oct 2026) trust a compromised
chain. An install through that trust store either fails or, worse, succeeds against the wrong host.
Pinning the fix into the build makes the image reproducible regardless of when it is built.

---

## 22. Tolerances are per-field and documented

**Decision:** no global tolerance constant.

**Why:** fields differ in how a legitimate discrepancy arises. Price on a closed market should match
to the cent (0.5 %); RSI computed over different lookbacks can differ by percent (5 %); ATR across
providers can differ more (10 %). A single number would be simultaneously too tight for one field and
too loose for another.

---

## Related

- [Architecture](Architecture) — how these decisions are implemented
- [Response Contract](Response-Contract) — the observable result
- [Development](Development) — house rules derived from these decisions
