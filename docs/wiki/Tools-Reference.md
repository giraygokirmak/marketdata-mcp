# Tools Reference

33 tools. `providers` is the tool's **possible** provider set, not a promise about any single call:
an individual response can degrade to fewer providers if some fail. The per-call truth is always that
response's `data_provider` plus its `providers_answered`.

Ask the server for this list at runtime: `tapebasis_tool_map()` returns it without any upstream
call.

## Multi-source (13)

Every provider that can serve the field is asked, in parallel and natively; overlapping fields are
compared. These can return `ALL`.

### `stock_quote`

Quote plus fundamentals snapshot for any instrument class, equity, index, FX, commodity or crypto.

| | |
| --- | --- |
| Providers | TradingView scanner · Finviz quote page · Yahoo chart API |
| Compared | price, change %, previous close, volume, 52-week high/low, P/E, RSI(14) |
| Tolerances | price/prev-close/52w 0.5 % · change % 2 % · P/E 2 % · RSI 5 % · volume 1 % |
| Session-relative | change %, previous close, volume (`max_skew_seconds = 3600`) |

Each compared field resolves to one value, the newest print, while every reading stays in
`comparisons`. Finviz-only material rides along, labelled per field in `fields`: the 84-field
fundamentals snapshot, company profile, peers, and the after-hours print. `resolutions` records the
ticker each provider was asked for.

Because symbols are resolved per provider, `XAUUSD` reaches `OANDA:XAUUSD` on TradingView, is
correctly reported as not carried by Yahoo and Finviz, and the response is `TRADINGVIEW` /
`SINGLE_SOURCE` rather than `NONE`.

### `stock_prices`

Bulk quotes for a ticker list.

Each row carries its own `data_provider`, `providers_answered` and an `agrees` flag, one ticker can
be `ALL` while another is single-source or `NONE` in the same response. Multi-source rows also carry
`price_source` (whose print the single `price` came from) and their own `comparisons` and `freshness`
blocks. Rows where every provider came up empty carry a `misses` map explaining why.

### `stock_screener`

US equity screener, cross-checked on the same instruments.

TradingView ranks the whole market server-side (`sort_by` ∈ `market_cap`, `dividend_yield`, `change`,
`price`). Finviz is then asked about **exactly the tickers TradingView returned**, and the price is
compared at 0.5 %.

Asking Finviz for its own independent ranking would compare two unrelated universes: a market-cap
ranking against a day-%-change ranking shared zero rows on every call, so the "multi-source" screen
was silently single-source. `cross_checked_rows`, `agreeing_rows` and `not_comparable_rows` summarise
the result.

Finviz screener rows carry no print time, so a per-row match cannot be *proven* to describe the same
session. Those rows are reported as `not_comparable` (not as agreement), and the envelope warns that
no row could be confirmed. Yahoo has no screener endpoint, so it is not asked.

### `market_indices`

S&P 500, Dow, Nasdaq Composite, Russell 2000, IWM and VIX levels, keyed by canonical name (`SPX`,
`DJI`, `COMP`, `RUT`, `IWM`, `VIX`).

| | |
| --- | --- |
| Providers | TradingView `SP:SPX`/`DJ:DJI`/`NASDAQ:IXIC`/`TVC:RUT`/`TVC:VIX` · Finviz indices feed · Yahoo `^GSPC`/`^DJI`/`^IXIC`/`^RUT`/`^VIX` |
| Compared | index level, 0.5 % (VIX 3 %, because a volatility gauge moves percent-level within a minute) |
| Not compared | day-change %, reported from every provider, derived from different reference closes |

Each level resolves by the same newest-print rule as a quote, and levels are only compared between
providers whose prints share a session. A provider that has rolled into the next session is *newer*
not wrong; its level is the one shown, and the one-hour-skew guard keeps yesterday's close from being
reported as a data conflict.

Finviz quotes indices only through its indices feed, not a per-ticker page, `/stock?t=SPX` is a 404
and `COMP` there is Compass Inc, a real equity. So `resolutions` reports Finviz as not carrying an
index ticker, while `market_indices` still uses Finviz's indices feed for the level.

`RUT` (index) and `IWM` (its tracking ETF) are different instruments at different price levels and
are never cross-compared.

### `stock_news`

Ticker headlines, matched by title across the feeds.

Finviz supplies price reactions around each item; Yahoo supplies its own search feed. The TradingView
leg is the provider's licensed Marketaux feed and needs `MARKETAUX_API_TOKEN`; without it that leg is
`NOT_ATTEMPTED` and the response is labelled with the feeds that did answer.

Sentiment does **not** need the token, see [`market_sentiment`](#market_sentiment).

### `stock_options_chain`

Options chain for one expiry, from Finviz and Yahoo, with the underlying price confirmed by
TradingView's scanner (TradingView publishes no chain).

Contracts are matched by strike; volume compared at 5 %, open interest at 1 % when both report it.
Finviz carries full greeks.

Yahoo reports zero open interest for contracts expiring the same day, so a `0 vs N` pair is counted
under `open_interest_unavailable`, not as a mismatch.

### `stock_earnings`

Earnings dates, reported-quarter history, estimate revisions and post-earnings price reactions.

Finviz owns the detailed tables; its `earnings_date` is the **last reported** timestamp, which is why
it is returned as `finviz_earnings_date`, separate from `next_earnings_date` (the upcoming release
from TradingView's calendar). The two are not the same fact and are never conflated.

### `stock_extended_hours`

Pre-/post-market price from three independent sources: TradingView's `premarket_*`/`postmarket_*`
scanner columns, Finviz's after-hours print, and Yahoo's 1-minute extended-hours candles. The
after-hours reading is compared at 0.5 %; each provider's raw reading stays in
`after_hours_readings`.

### `technical_analysis`

Full indicator stack from TradingView (RSI, MACD, EMAs, Bollinger, ATR, ADX, structure, trade setup).
Finviz's snapshot and Yahoo's chart API independently confirm four headline readings in `cross_check`:
price (0.5 %), RSI(14) (12 %), ATR(14) (10 %), volume (2 %).

### `stock_backtest`

A single strategy, run **twice**, once on TradingView's chart-websocket OHLCV, once on Yahoo's chart
OHLCV, then compared. Finviz is not asked: it has no history endpoint.

Compared: total return (1 %), win rate (2 %), trade count (exact), max drawdown (2 %), Sharpe (5 %),
buy-and-hold return (1 %). Identical candles produce identical metrics; a divergence means the two
feeds disagree about the instrument's history, which is worth knowing before trusting either.

### `stock_backtest_compare`

The full strategy leaderboard, also computed on both feeds. Adds `winner_comparison`, the winning
strategy on TradingView, the winner on Yahoo, and whether the two feeds pick the same one.

### `market_snapshot`

Global FX, crypto, index and ETF snapshot from TradingView's scanner and Yahoo's chart API, group by
group. Finviz has no equivalent breadth, so it is not asked.

Rows join by the *requested* ticker, not by the symbol the upstream reports back: Yahoo answers both
`JPY=X` and `USDJPY=X` with `meta.symbol: "JPY=X"`, which used to split one instrument into two rows.
Each row carries both providers' prints and an `agree` flag. `all_groups_agree` summarises the whole
snapshot.

For a cross-checked *index* read, use `market_indices`.

### `combined_analysis`

TradingView technical read + sentiment + news, with the underlying price cross-checked against
Finviz's snapshot and Yahoo's chart API. One request, not three.

The price leg is genuinely multi-source; the analysis and sentiment legs remain single-source and are
labelled per field in `fields`. `sentiment_source` states whether the sentiment came from Marketaux or
local scoring.

---

## Finviz-only (7)

Served, labelled `FINVIZ`, explicitly single-source. No other provider has a comparable dataset, so
those legs are marked `NOT_ATTEMPTED`, which is **not** an error and is excluded from `errors`.

| Tool | Capability | Notes |
| --- | --- | --- |
| `stock_filings` | SEC filing history | Finviz's SEC feed |
| `stock_ownership` | Insider + institutional ownership, short interest | Three datasets; a failing one is reported without losing the others |
| `stock_peers` | Peer-group companies | No provider has a curated peer list |
| `stock_why_moving` | Narrative explainer for a price move | Finviz editorial feed |
| `stock_chart` | Rendered PNG candlestick chart | TradingView produces text analysis, not images |
| `stock_dilution` | Dilution risk model (share count, float, cash runway) | Finviz dilution dataset |
| `stock_screener_filters` | Finviz screener filter catalogue | Finviz's proprietary filter vocabulary |

### `market_sentiment`

Two backends, selected at runtime, same payload shape:

| Condition | Backend | Label |
| --- | --- | --- |
| `MARKETAUX_API_TOKEN` set and usable | Marketaux entity sentiment (licensed) | `TRADINGVIEW` |
| no token, or Marketaux rejected/quota/outage | Finviz headlines scored locally (VADER) | `FINVIZ` |
| neither available | labelled empty payload, `sentiment_score: null` | `NONE` |

Both paths are single-source by nature: there is nothing to cross-check a sentiment score against,
so the label names whichever feed supplied the headlines.

The local path is labelled `FINVIZ` because the **headlines are Finviz's**, only the scoring is
ours. Labelling it `TRADINGVIEW` would claim a provenance the data does not have.

`sentiment_score: null` (not `0.0`) means "nothing could be scored". `0.0` means "the tone is
balanced". Conflating them would dress a data gap up as a finding. Full detail:
[Sentiment](Sentiment).

---

## TradingView-only (10)

Served, labelled `TRADINGVIEW`, explicitly single-source, no other provider has the dataset.

| Tool | Capability | Why nobody else can confirm it |
| --- | --- | --- |
| `crypto_top_movers` | Crypto gainers/losers | Finviz does not cover crypto; Yahoo has no movers endpoint |
| `crypto_bollinger_scan` | Bollinger squeeze scan | Same |
| `crypto_volume_breakout` | Volume + price breakout scan | Same |
| `crypto_multi_timeframe` | Multi-timeframe crypto analysis | Same |
| `crypto_multi_agent` | Multi-agent bull/bear debate | Same |
| `futures_market_overview` | Futures overview by category | Neither other provider has futures |
| `futures_top_movers` | Futures gainers/losers | Same |
| `futures_watchlist` | Curated futures watchlist with levels | Same |
| `bitcoin_market_pulse` | Bitcoin trend and volatility pulse | Composite of the crypto scans above |
| `egx_market_overview` | Egyptian Exchange overview | No EGX coverage elsewhere |

Note that crypto and futures *quotes* are multi-source, `stock_quote("BTCUSD")` asks TradingView and
Yahoo. It is the scan/analysis endpoints that are TradingView-only.

---

## Ops (2)

| Tool | Purpose |
| --- | --- |
| `tapebasis_health` | Read the cached drift/outage verdict and per-provider rate-limit state, or `refresh=True` to probe now |
| `tapebasis_tool_map` | Return this catalogue with no upstream call |

---

## Choosing a tool

| You want | Use | Multi-source? |
| --- | --- | --- |
| One stock's price and fundamentals | `stock_quote` | Yes, on the quote fields |
| A crypto or FX quote | `stock_quote` | Yes where both carry it |
| Many stocks' prices | `stock_prices` | Per row |
| A market scan | `stock_screener` | On price, per row |
| Index levels | `market_indices` | Yes |
| Headlines | `stock_news` | Overlap only |
| How the market feels | `market_sentiment` | No (single-source by nature) |
| Options positioning | `stock_options_chain` | On volume/OI |
| Filings, ownership, peers | `stock_filings`, `stock_ownership`, `stock_peers` | No |
| Crypto / futures scans | `crypto_*`, `futures_*` | No |
| Strategy validation | `stock_backtest`, `stock_backtest_compare` | Yes, two OHLCV feeds |
| Everything about one stock | `combined_analysis` | Price leg only |

## Related

- [Response Contract](Response-Contract): how to read any of these
- [Design Decisions](Design-Decisions): why some tools are single-source by nature
