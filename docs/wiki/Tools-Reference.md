# Tools Reference

33 tools. `providers` is the tool's **possible** provider set, not a promise about any single call —
an individual response can degrade to one provider if the other fails. The per-call truth is always
that response's `data_provider`.

Ask the server for this list at runtime — `marketdata_tool_map()` returns it without any upstream
call.

## Dual-source (9)

Both providers are asked in parallel; overlapping fields are compared. These can return `BOTH`.

### `stock_quote`

US equity quote plus fundamentals snapshot.

| | |
| --- | --- |
| Compared | price, change %, previous close, volume, 52-week high/low, P/E, RSI(14) |
| Tolerances | price/prev-close/52w 0.5 % · change % 2 % · P/E 2 % · RSI 5 % · volume 1 % |

Finviz-only material rides along, labelled per field in `fields`: the 84-field fundamentals
snapshot, company profile, peers, and the after-hours print.

Sources note: the TradingView leg's price comes from Yahoo Finance's chart API. `sources` says so.

### `stock_prices`

Bulk quotes for a ticker list. Each row carries its own `data_provider` and an `agrees` flag — one
ticker can be `BOTH` while another is single-source or `NONE` in the same response.

Each `BOTH` row also carries `price_source` naming which provider's print was used, and its
`comparisons` block with both readings.

### `stock_screener`

US equity screener. TradingView ranks the whole market server-side (`sort_by` ∈ `market_cap`,
`dividend_yield`, `change`, `price`); Finviz adds its own filter language (`finviz_filters`, e.g.
`cap_smallover,sec_technology`). Rows both providers return are compared on change % (3 % tolerance).
`cross_checked_rows` / `agreeing_rows` summarise the result.

### `market_indices`

S&P 500, Dow, Nasdaq Composite, Russell 2000 and VIX levels, keyed by canonical name (`SPX`, `DJI`,
`COMP`, `RUT`, `IWM`, `VIX`). Index levels compared at 0.5 %.

Day-change % is **reported from both but not compared**: each provider derives it from a different
reference close, so a small gap is a formula difference rather than a data conflict.

`RUT` (index) and `IWM` (its tracking ETF) are different instruments at different price levels and
are never cross-compared.

### `stock_news`

Ticker headlines. Finviz supplies price reactions around each item (day-of move, and the next
session's move when the story broke after the close). The TradingView leg is the Marketaux feed and
needs `MARKETAUX_API_TOKEN`; without it the response is `FINVIZ`-only — complete and labelled.

Headline titles are matched across both feeds to report overlap. Sentiment does **not** need the
token — see [`market_sentiment`](#market_sentiment).

### `stock_options_chain`

Options chain for one expiry. Contracts are matched by strike; volume compared at 5 %, open interest
at 1 % when both report it. Finviz carries full greeks.

Yahoo reports zero open interest for contracts expiring the same day, so a `0 vs N` pair is counted
under `open_interest_unavailable`, not as a mismatch.

### `stock_earnings`

Earnings dates, reported-quarter history, estimate revisions and post-earnings price reactions.

Finviz owns the detailed tables; its `earnings_date` is the **last reported** timestamp, which is
why it is returned as `finviz_earnings_date`, separate from `next_earnings_date` (the upcoming
release, from TradingView's calendar). The two are not the same fact and are never conflated.

### `stock_extended_hours`

Pre-/post-market price. Finviz exposes the after-hours print on the quote page; the TradingView
provider derives it from 1-minute Yahoo candles. Compared at 0.5 %.

### `technical_analysis`

Full indicator stack from TradingView (RSI, MACD, EMAs, Bollinger, ATR, ADX, structure, trade
setup). Finviz's snapshot independently confirms four headline readings in `cross_check`: price
(0.5 %), RSI(14) (12 %), ATR(14) (10 %), volume (2 %).

---

## Finviz-only (8)

Served, labelled `FINVIZ`, explicitly unverified. TradingView has no comparable dataset, so the
TradingView side is marked `NOT_ATTEMPTED` — which is **not** an error and is excluded from `errors`.

| Tool | Capability | Notes |
| --- | --- | --- |
| `stock_filings` | SEC filing history | Finviz's SEC feed |
| `stock_ownership` | Insider + institutional ownership, short interest | Three datasets; a failing one is reported without losing the others |
| `stock_peers` | Peer-group companies | TradingView has no curated peer list |
| `stock_why_moving` | Narrative explainer for a price move | Finviz editorial feed |
| `stock_chart` | Rendered PNG candlestick chart | TradingView produces text analysis, not images |
| `stock_dilution` | Dilution risk model (share count, float, cash runway) | Finviz dilution dataset |
| `stock_screener_filters` | Finviz screener filter catalogue | Finviz's proprietary filter vocabulary |
| `market_sentiment` | News-based sentiment score | **No API key required** — see below |

### `market_sentiment`

Two backends, selected at runtime, same payload shape:

| Condition | Backend | Label |
| --- | --- | --- |
| `MARKETAUX_API_TOKEN` set and usable | Marketaux entity sentiment (licensed) | `TRADINGVIEW` |
| no token, or Marketaux rejected/quota/outage | Finviz headlines scored locally (VADER) | `FINVIZ` |
| neither available | labelled empty payload, `sentiment_score: null` | `NONE` |

The local path is labelled `FINVIZ` because the **headlines are Finviz's** — only the scoring is
ours. Labelling it `TRADINGVIEW` would claim a provenance the data does not have.

`sentiment_score: null` (not `0.0`) means "nothing could be scored". `0.0` means "the tone is
balanced". Conflating them would dress a data gap up as a finding. Full detail:
[Sentiment](Sentiment).

---

## TradingView-only (13)

Served, labelled `TRADINGVIEW`, explicitly unverified — Finviz has no equivalent dataset.

| Tool | Capability | Why Finviz cannot confirm it |
| --- | --- | --- |
| `stock_backtest` | Single-strategy backtest | No OHLCV history endpoint |
| `stock_backtest_compare` | Strategy leaderboard | Same |
| `crypto_top_movers` | Crypto gainers/losers | Finviz does not cover crypto |
| `crypto_bollinger_scan` | Bollinger squeeze scan | No crypto |
| `crypto_volume_breakout` | Volume + price breakout scan | No crypto |
| `crypto_multi_timeframe` | Multi-timeframe crypto analysis | No crypto |
| `crypto_multi_agent` | Multi-agent bull/bear debate | No crypto |
| `futures_market_overview` | Futures overview by category | No futures |
| `futures_top_movers` | Futures gainers/losers | No futures |
| `futures_watchlist` | Curated futures watchlist with levels | No futures |
| `market_snapshot` | Global FX / crypto / index / ETF snapshot | Only covers four US indices |
| `bitcoin_market_pulse` | Bitcoin trend and volatility pulse | No crypto |
| `egx_market_overview` | Egyptian Exchange overview | No EGX coverage |

For a **cross-verified** index read, use `market_indices`, not `market_snapshot`.

---

## Composite (1)

### `combined_analysis`

TradingView technical read + sentiment + news, with the underlying price cross-checked against
Finviz's latest close. The only composite that can legitimately return `BOTH`: the price leg is
dual-sourced while the analysis and sentiment legs remain single-source, labelled per field.

Sentiment needs no API key (same fallback as `market_sentiment`). The `data.news` leg stays
Marketaux-only, so it is empty — and says why — without a token.

---

## Ops (2)

| Tool | Purpose |
| --- | --- |
| `marketdata_health` | Read the cached drift/outage verdict, or `refresh=True` to probe now |
| `marketdata_tool_map` | Return this catalogue with no upstream call |

---

## Choosing a tool

| You want | Use | Cross-verified? |
| --- | --- | --- |
| One stock's price and fundamentals | `stock_quote` | Yes |
| Many stocks' prices | `stock_prices` | Per row |
| A market scan | `stock_screener` | On change %, per row |
| Index levels | `market_indices` | Yes |
| Headlines | `stock_news` | Overlap only |
| How the market feels | `market_sentiment` | No (single-source by nature) |
| Options positioning | `stock_options_chain` | On volume/OI |
| Filings, ownership, peers | `stock_filings`, `stock_ownership`, `stock_peers` | No |
| Crypto / futures | `crypto_*`, `futures_*` | No |
| Everything at once | `combined_analysis` | Price leg only |

## Related

- [Response Contract](Response-Contract) — how to read any of these
- [Design Decisions](Design-Decisions) — why some tools are single-source by nature
