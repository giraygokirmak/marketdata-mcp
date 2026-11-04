# Design Decisions

Non-obvious choices, and the reasoning that produced them. Each entry is a rule that is easy to
violate by accident.

---

## 1. Five provider states, not two

**Decision:** `ALL` / `TRADINGVIEW` / `FINVIZ` / `YAHOO` / `NONE`, never a boolean like
`verified: true`.

**Why:** "verified" collapses two different facts: *how many providers answered* and *whether they
agreed*. A boolean loses the case that matters most: several answered and disagreed. With five
states plus `comparisons`, that case is visible instead of hidden.

**Consequence:** `ALL` means "every provider that was asked answered", not "they agree". Agreement
lives in `comparisons[].match`. This is documented in three places because it is the single easiest
thing to misread.

---

## 2. `ALL` is coverage, not agreement

**Decision:** `ALL` is set whenever every provider that was asked produced data, including a
two-of-three response, and `providers_answered` names exactly which providers contributed.

**Why:** the alternative is either a lie or a combinatoric explosion of labels. If `ALL` required
three answers, a tool that legitimately asks only two providers (options chains: Finviz + Yahoo)
could never report complete coverage, and an instrument only two providers carry would look degraded
on every call. Naming the answering set separately keeps both facts exact: `data_provider` says "you
got everything asked for", `providers_answered` says what that was.

**Consequence:** an agent that wants to say "three sources agreed" must read `providers_answered`, not
infer it from `ALL`.

---

## 3. Disagreement is never resolved by fiat

**Decision:** when providers disagree, keep every value in `comparisons`, name the field in `notice`
and `warnings`, and leave `data_provider` as `ALL`.

**Why:** silently picking one turns an arbitrary choice into an authoritative-looking number. An
agent that cannot see the conflict cannot warn its user. The cost is a slightly noisier response; the
benefit is that a wrong number never becomes invisible.

---

## 4. A single value per field, chosen by print time

**Decision:** resolve each compared field to a single value in `data`, the reading whose market
print is **newest**, while `comparisons` keeps every raw reading verbatim.

**Why:** an agent should not have to reconcile two or three numbers to answer "what is the price". But
it must be able to *audit* that choice, which requires the losers to remain one field away.

**Why freshness, not a fixed provider:** preferring one provider unconditionally is arbitrary. During
a session one provider is routinely a minute behind another (Yahoo stamps the close `21:00:01Z`
Finviz renders `3:59 PM ET` = `20:59Z`), so a fixed winner periodically serves the staler price. The
freshest print is the defensible choice, and `freshness.reason` plus the notice state it plainly.
`freshness.decision` makes the rule machine-readable (`newest`, `tie`, `no_timestamp`) so no caller has
to parse the sentence.

Ties, and cases where no provider reports a usable timestamp, fall back to a fixed documented order
(`FINVIZ`, `TRADINGVIEW`, `YAHOO`), not to whichever label happened to be first. Only providers that
share the newest instant are tie-break candidates, so a stale provider can never win against fresher
ones.

---

## 5. A session-relative field is not compared across sessions

**Decision:** `change_percent`, `previous_close` and `volume` carry `max_skew_seconds = 3600`. When
two readings come from prints more than an hour apart, the pair is reported as `not_comparable` with
the gap, and the field is listed in `warnings` as not cross-checked.

**Why:** those fields are measured against the previous close of the session the print belongs to.
A pre-market Finviz quote and a prior-close Yahoo quote legitimately disagree about the day change:
that is two different questions, not two answers to one. Reporting it as a provider conflict produces
a warning on nearly every call, which trains readers to ignore warnings; widening the tolerance would
report a false agreement. Neither is true, so the pair is excluded from the agreement decision and the
reason is stated.

**Consequence:** the price itself is still compared across minutes. A price stays comparable across
print times; a session baseline does not. `require_all_times=True` is the same principle for
providers that expose no observation time at all: agreement cannot be *proven*, so `match` stays
`null` rather than being asserted.

---

## 6. Single-source data is served, not withheld

**Decision:** if one provider answers, serve it, labelled and with an explicit "not corroborated"
notice.

**Why:** withholding one provider's valid data would be worse than serving it with a caveat. The
failure mode we care about is a *number presented as verified when it is not*, not the existence of
uncorroborated numbers. The label, not the omission, is the safeguard.

---

## 7. `NOT_ATTEMPTED` is not an error

**Decision:** a capability a provider simply does not have (Finviz has no futures feed, Finviz and
Yahoo carry no spot gold) produces `NOT_ATTEMPTED`, excluded from `errors`.

**Why:** conflating "this provider cannot answer" with "this provider failed" makes structural facts
look like outages. A reader would see errors on every crypto or metals call and learn to ignore
errors, destroying the signal for real failures.

---

## 8. Symbols are resolved per provider, and verified

**Decision:** resolve each query independently per provider, then verify a candidate ticker against
the provider's own quote endpoint before returning it.

**Why:** a hardcoded table cannot keep up and, worse, fails as a *wrong-but-plausible* ticker that
breaks six layers later. But trusting provider search is not enough either: TradingView's symbol
search offers `OANDA:BTCUSD`, while its scanner has no row for it. Resolution that stops at search
hands the quote path an unquotable ticker and a real instrument is reported as unavailable. So
candidates are verified against the scanner, and `BTCUSD` resolves to `BITSTAMP:BTCUSD`.

The same rule explains what is *not* returned: Yahoo has no spot-gold pair (`XAUUSD=X` is HTTP 404)
and its nearest series is COMEX gold futures (`GC=F`), a different instrument at a different price.
Substituting it would be a fabricated quote, so the resolution is `none` with a note.

Results are cached per `(provider, query)` for an hour.

---

## 9. Every provider is rate-limited, and the cooldown is shared

**Decision:** every request to every provider passes a `ProviderLimiter` with a minimum interval, a
concurrency cap, a process-wide exponential cooldown after a `429`, and a short TTL cache.

**Why:** retrying harder is the wrong reaction to a rate limit; it deepens the block. Pacing and a
shared pause are what actually recover. A process-wide cooldown means one blocked request protects
every later caller instead of each discovering the block separately, and the TTL cache absorbs the
duplicate questions an agent naturally asks in one turn.

**Consequence:** Yahoo is paced slowest (one at a time, 1 s apart) because it starts answering `429`
after a short burst. TradingView's chart websocket signals a block as `invalid symbol` or
`resolve error` rather than `429`; those are treated as rate limiting too, because they are
IP-scoped, not ticker-scoped. Reporting a valid symbol as nonexistent because of a block would be a
wrong answer, not a bad ticker.

---

## 10. Three independent legs, no proxying

**Decision:** TradingView, Finviz and Yahoo each hit genuinely different upstreams through their own
access module.

**Why:** cross-verification is only meaningful if the sources are independent. An earlier version of
this server took TradingView's price from Yahoo's chart API: two legs, one upstream wearing two
labels, a comparison that could only ever agree. That is worse than no comparison, because it looks
like confirmation.

**Consequence:** `sources` names the upstream each leg actually called, so a reader can check the
independence claim rather than trust it.

---

## 11. Health probes bypass the provider code

**Decision:** `health.py` probes upstreams directly, not through `tv_access`/`fv_access`/`yh_access`.

**Why:** a provider that catches its own exceptions looks healthy from outside. Probing through it
would inherit the same blindness. Independent probes can fail while every tool call succeeds, and
that divergence is the signal.

**Consequence:** a probe must exercise a ticker the provider actually serves. The Yahoo FX and crypto
probes exist because those resolution paths are separate assumptions; a probe using only `AAPL` would
stay green while `EURUSD=X` started 404-ing.

---

## 12. Fingerprints, not just status codes

**Decision:** store a SHA-256 fingerprint of the payload's key paths as a baseline; compare later.

**Why:** a drifted endpoint returns HTTP 200 with a renamed field. Status-only monitoring reports a
broken parser as healthy while it returns `None`. The baseline also prevents the first observation of
an already-drifted shape from being adopted as the new normal.

---

## 13. Health state is persisted to disk

**Decision:** baselines live in `~/.tapebasis/health_state.json`, volume-mounted in Docker.

**Why:** in-memory baselines reset on every restart, so a slow-moving schema change would be adopted
silently at each restart. Persistence is what makes drift detection cumulative.

---

## 14. The monitor starts lazily, not via lifespan

**Decision:** `MONITOR.ensure_started()` is called from tool entry points.

**Why:** FastMCP's streamable-HTTP app builds its own lifespan and ignores `lifespan=`. A
lifespan-only start would work over stdio and silently never run over HTTP: a bug that only appears
in the deployment most people use. Lazy start works on both transports.

---

## 15. Per-field provenance, not a blanket claim

**Decision:** `fields` maps each output field to its provider.

**Why:** most responses legitimately mix sources. `stock_quote` gets price from every provider that
quotes it but the fundamentals snapshot, profile and peers from Finviz alone. A response-level `ALL`
would overclaim for every Finviz-only field riding along.

---

## 16. Sentiment is labelled by data origin, not by code location

**Decision:** local sentiment over Finviz headlines returns `data_provider: FINVIZ`, even though the
tool lives in `tools_tradingview.py`.

**Why:** the numbers are Finviz's; only the scoring is ours. Labelling it `TRADINGVIEW` would assert
a provenance the data does not have: precisely the mislabelling this server exists to prevent. Code
location is an implementation detail; the label must describe the data.

---

## 17. `0.0` and `null` are not interchangeable

**Decision:** `sentiment_score: null` means "nothing could be scored"; `0.0` means "the tone is
balanced". The unavailable path never returns `0.0`.

**Why:** a failed fetch reported as `0.0`/`Neutral` reads as a finding, "the news is balanced",
when the truth is "we could not look". Silent fallbacks to a neutral-looking default are a classic
way for a data gap to become an asserted fact. The same reasoning rejected a fabricated sentiment
score when Marketaux returns an empty article list.

---

## 18. A rejected token falls back rather than failing

**Decision:** an invalid/expired token falls back to local scoring, recording the cause in
`licensed_path_unavailable`.

**Why:** the tool's job is to produce sentiment, not to enforce a billing relationship. A hard
failure would make an expired token look like a broken server. But the fallback must be visible: a
silent switch from licensed to locally-computed numbers would misrepresent the output's quality.

---

## 19. News sources were evaluated for independence, not just availability

**Decision:** keep Finviz as the news spine; use Yahoo as the second free leg and Marketaux as the
optional licensed one; score sentiment locally.

**Why:** candidate feeds were tested live and rejected on concrete grounds:

| Candidate | Rejected because |
| --- | --- |
| Nasdaq RSS (`rssoutbound?symbol=`) | Does not actually filter by symbol, `THYAO.IS` and `BTC-USD` returned identical items |
| Yahoo `/v1/finance/search` as a *primary* news spine | `Disallow: /` in robots.txt and a brittle feed; kept only as a secondary leg behind Finviz |
| Yahoo RSS feeds | User-Agent sensitive, browser UA got `429`, Windows UA got `200`; too brittle to depend on |
| Google News RSS | 100 items and genuinely independent, but article links are unresolvable Google redirects and `/rss/` is not in its robots allow-list |
| Reddit / StockTwits | Blocked (`403`) from this network |
| Finnhub / Polygon / Tiingo / AlphaVantage | All require a token, the opposite of the goal |

Yahoo's *chart* API is used for prices and its `/v1/finance/search` feed for headlines. Both are
separate upstreams from Finviz and from TradingView's scanner, so Yahoo counts as an independent
leg. The result: no tool needs a key, and no third party's terms are strained to achieve it.

---

## 20. Index and ETF are never aliased

**Decision:** `RUT` (Russell 2000 index) and `IWM` (its tracking ETF) are separate canonical
instruments.

**Why:** they are different instruments at different price levels. Aliasing them would produce a
comparison that always "disagrees" and a canonical key that silently mixes two things.

---

## 21. `finviz_earnings_date` and `next_earnings_date` are separate fields

**Decision:** never merge them into one `earnings_date`.

**Why:** Finviz's is the **last reported** quarter's timestamp (the anchor for its results tables);
TradingView's is the **upcoming** release. They answer different questions. Merging them would
produce a date that is right for neither.

---

## 22. Options open interest: `0 vs N` is not a mismatch

**Decision:** count `0 vs N` under `open_interest_unavailable`, not as a conflict.

**Why:** Yahoo reports zero OI for contracts expiring the same day. Treating a provider convention as
a data conflict would generate false mismatches on exactly the contracts people look at most.

---

## 23. The screener compares the same instruments, not two rankings

**Decision:** TradingView ranks the market; Finviz is then asked about **exactly those tickers** and
the price is compared per row.

**Why:** the obvious design, ask both providers for their own top-N and compare, compares two
unrelated universes. A market-cap ranking and a day-%-change ranking share no rows, so the "multi-
source" screen was silently single-source. It looked cross-checked while nothing was checked.

**Consequence:** Finviz screener rows carry no print time, so a per-row match cannot be *proven* to
describe the same session. Those rows are reported as `not_comparable` (with `require_all_times=True`)
rather than as agreement, and the envelope says no row could be confirmed.

---

## 24. Day-change % is reported from every provider but not compared (indices)

**Decision:** compare index levels tightly (0.5 %, VIX 3 %), but not day-change percentages.

**Why:** each provider derives the change from its own reference close. A small gap is a formula
difference, not a data conflict. Comparing it would warn on every response and train readers to
ignore warnings: the same failure mode as a too-loose tolerance, from the other direction.

---

## 25. No proxy support

**Decision:** there is no proxy layer; no proxy variables exist.

**Why:** an explicit requirement, and it simplifies the failure surface. Egress control belongs at
the network or container layer, where it can be applied consistently and audited.

---

## 26. Docker fixes CA trust before installing anything

**Decision:** both build stages upgrade `pip` and `certifi` as the first action.

**Why:** base images built before the eMudhra/emSign CA incident (Oct 2026) trust a compromised
chain. An install through that trust store either fails or, worse, succeeds against the wrong host.
Pinning the fix into the build makes the image reproducible regardless of when it is built.

---

## 27. Tolerances are per-field and documented

**Decision:** no global tolerance constant.

**Why:** fields differ in how a legitimate discrepancy arises. Price on a closed market should match
to the cent (0.5 %); RSI computed over different lookbacks can differ by percent (5 %); ATR across
providers can differ more (10 %); VIX moves percent-level within a minute (3 %). A single number
would be simultaneously too tight for one field and too loose for another.

---

## Related

- [Architecture](Architecture): how these decisions are implemented
- [Response Contract](Response-Contract): the observable result
- [Development](Development): house rules derived from these decisions
