"""Tool catalog metadata: which providers back each tool and how it is checked.

Consumed by ``tapebasis_tool_map`` so an agent can see, before calling
anything, which capabilities are dual-sourced and which are single-source by
nature — and, for the dual-sourced ones, exactly which fields are compared.

Kept as plain data (no imports) so building the map costs nothing.

``providers`` is the *possible* provider set for the tool, not the outcome of
any particular call: an individual response can degrade to a single provider if
one side fails. The per-call truth is always in the response's
``data_provider``.
"""

TOOL_CATALOG: tuple[dict[str, object], ...] = (
    # ── Dual-source: both providers asked, answers compared ──────────────────
    {
        "name": "stock_quote",
        "capability": "US equity quote + fundamentals snapshot",
        "providers": "BOTH",
        "comparison": "price, change %, previous close, volume, 52-week high/low, P/E, RSI(14)",
        "notes": (
            "Price and change are compared within 0.5%, P/E within 2%, RSI within 5%. "
            "Each compared field resolves to ONE value: the reading from whichever "
            "provider printed more recently (see `freshness`); both originals stay in "
            "`comparisons`. Ties fall back to Finviz. Finviz-only fields (the 84-field "
            "snapshot, profile, peers, after-hours) ride along and are labelled per "
            "field in `fields`."
        ),
    },
    {
        "name": "stock_prices",
        "capability": "bulk quotes for a ticker list",
        "providers": "BOTH",
        "comparison": "price and change % per ticker",
        "notes": (
            "Each row carries its own data_provider and an `agrees` flag: a ticker "
            "can be BOTH, single-source or NONE independently of the others. A BOTH "
            "row also carries `price_source` — which provider's fresher print the "
            "single `price` came from — plus its own `comparisons` block."
        ),
    },
    {
        "name": "stock_screener",
        "capability": "US equity screener",
        "providers": "BOTH",
        "comparison": "per-ticker change % for every row both providers return",
        "notes": (
            "TradingView ranks the whole market server-side; Finviz adds its own "
            "filter vocabulary. cross_checked_rows / agreeing_rows summarise agreement."
        ),
    },
    {
        "name": "market_indices",
        "capability": "S&P 500, Dow, Nasdaq Composite, Russell 2000, VIX levels",
        "providers": "BOTH",
        "comparison": "index level (0.5% tolerance)",
        "notes": (
            "Finviz DJI/SPX/COMP map to Yahoo ^DJI/^GSPC/^IXIC. Day-change % is "
            "reported from both but not compared: each provider derives it from a "
            "different reference close, so a small gap is a formula difference."
        ),
    },
    {
        "name": "stock_news",
        "capability": "ticker headlines",
        "providers": "BOTH",
        "comparison": "headline-title overlap between the two feeds",
        "notes": (
            "Finviz supplies price reactions around each headline. The TradingView "
            "leg is the Marketaux feed and needs MARKETAUX_API_TOKEN; without it "
            "the response is FINVIZ-only (still complete and labelled). Sentiment "
            "does NOT need the token — see market_sentiment."
        ),
    },
    {
        "name": "stock_options_chain",
        "capability": "options chain for one expiry",
        "providers": "BOTH",
        "comparison": "per-contract volume matched by strike (open interest when both report it)",
        "notes": (
            "Finviz carries full greeks. Yahoo zeroes open interest on same-day "
            "expiries, so a 0-vs-N pair is counted separately, not as a conflict."
        ),
    },
    {
        "name": "stock_earnings",
        "capability": "earnings dates, history and estimate revisions",
        "providers": "BOTH",
        "comparison": "next release date against the TradingView calendar",
        "notes": (
            "Finviz owns the reported-quarter tables; its earnings_date is the last "
            "*reported* timestamp, which is why it is returned separately from "
            "next_earnings_date."
        ),
    },
    {
        "name": "stock_extended_hours",
        "capability": "pre-/post-market price",
        "providers": "BOTH",
        "comparison": "after-hours price (0.5% tolerance)",
        "notes": (
            "Finviz exposes the after-hours print on the quote page; the TradingView "
            "provider derives it from 1-minute Yahoo candles."
        ),
    },
    {
        "name": "technical_analysis",
        "capability": "multi-indicator TA (RSI, MACD, EMA, Bollinger, ATR, ADX, structure)",
        "providers": "BOTH",
        "comparison": "price, RSI(14), ATR(14), volume against Finviz's snapshot",
        "notes": (
            "TradingView computes the full indicator stack; Finviz independently "
            "confirms the four headline readings in `cross_check`."
        ),
    },
    # ── Finviz-only: served, labelled FINVIZ, explicitly unverified ──────────
    {
        "name": "stock_filings",
        "capability": "SEC filing history",
        "providers": "FINVIZ",
        "comparison": "single-source (Finviz SEC feed)",
        "notes": "TradingView has no filing feed; every response says so.",
    },
    {
        "name": "stock_ownership",
        "capability": "insider + institutional ownership, short interest",
        "providers": "FINVIZ",
        "comparison": "single-source",
        "notes": (
            "Combines three Finviz datasets; a failing dataset is reported in errors "
            "and warnings without losing the others."
        ),
    },
    {
        "name": "stock_peers",
        "capability": "peer-group companies",
        "providers": "FINVIZ",
        "comparison": "single-source",
        "notes": "TradingView's scanner has no curated peer list.",
    },
    {
        "name": "stock_why_moving",
        "capability": "narrative explainer for a price move",
        "providers": "FINVIZ",
        "comparison": "single-source",
        "notes": "Finviz's own editorial feed; no TradingView equivalent.",
    },
    {
        "name": "stock_chart",
        "capability": "rendered PNG candlestick chart",
        "providers": "FINVIZ",
        "comparison": "single-source",
        "notes": "TradingView builds charts as text analysis, not images.",
    },
    {
        "name": "stock_dilution",
        "capability": "dilution risk model (share count, float, cash runway)",
        "providers": "FINVIZ",
        "comparison": "single-source",
        "notes": "Derived from Finviz's dilution dataset.",
    },
    {
        "name": "stock_screener_filters",
        "capability": "Finviz screener filter catalogue",
        "providers": "FINVIZ",
        "comparison": "single-source",
        "notes": "Finviz's proprietary filter vocabulary; TradingView uses different column names.",
    },
    # ── TradingView-only: served, labelled TRADINGVIEW, explicitly unverified ─
    {
        "name": "stock_backtest",
        "capability": "single-strategy backtest",
        "providers": "TRADINGVIEW",
        "comparison": "single-source (no comparable Finviz dataset)",
        "notes": "Finviz has no OHLCV history endpoint, so backtests cannot be cross-verified.",
    },
    {
        "name": "stock_backtest_compare",
        "capability": "strategy leaderboard for one symbol",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Same limitation as stock_backtest.",
    },
    {
        "name": "crypto_top_movers",
        "capability": "crypto gainers/losers on an exchange",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover crypto.",
    },
    {
        "name": "crypto_bollinger_scan",
        "capability": "Bollinger-band squeeze scan",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover crypto.",
    },
    {
        "name": "crypto_volume_breakout",
        "capability": "volume + price breakout scan",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover crypto.",
    },
    {
        "name": "crypto_multi_timeframe",
        "capability": "multi-timeframe crypto analysis",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover crypto.",
    },
    {
        "name": "crypto_multi_agent",
        "capability": "multi-agent bull/bear debate",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover crypto.",
    },
    {
        "name": "futures_market_overview",
        "capability": "futures overview by category",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover futures.",
    },
    {
        "name": "futures_top_movers",
        "capability": "futures gainers/losers",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover futures.",
    },
    {
        "name": "futures_watchlist",
        "capability": "curated futures watchlist with levels",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover futures.",
    },
    {
        "name": "market_snapshot",
        "capability": "global FX / crypto / index / ETF snapshot",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": (
            "Finviz covers only the four US indices; use market_indices for a "
            "cross-verified index read."
        ),
    },
    {
        "name": "bitcoin_market_pulse",
        "capability": "bitcoin trend and volatility pulse",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover crypto.",
    },
    {
        "name": "market_sentiment",
        "capability": "news-based sentiment score",
        "providers": "FINVIZ",
        "comparison": "single-source",
        "notes": (
            "No API key required. Uses Marketaux entity sentiment when "
            "MARKETAUX_API_TOKEN is set (then labelled TRADINGVIEW); otherwise "
            "scores Finviz headlines locally with VADER + a finance lexicon and "
            "labels the response FINVIZ, because the headlines are Finviz's. "
            "Returns sentiment_score=null rather than 0.0 when nothing can be "
            "scored."
        ),
    },
    {
        "name": "egx_market_overview",
        "capability": "Egyptian Exchange overview",
        "providers": "TRADINGVIEW",
        "comparison": "single-source",
        "notes": "Finviz does not cover EGX.",
    },
    # ── Composite ───────────────────────────────────────────────────────────
    {
        "name": "combined_analysis",
        "capability": "technical analysis + sentiment + news, price cross-checked",
        "providers": "BOTH",
        "comparison": "underlying price against Finviz's latest close",
        "notes": (
            "The only composite that can return BOTH: the price leg is dual-sourced, "
            "while the analysis and sentiment legs are TradingView-only and labelled "
            "per field in `fields`."
        ),
    },
    # ── Ops ─────────────────────────────────────────────────────────────────
    {
        "name": "tapebasis_health",
        "capability": "upstream schema-drift and availability monitor",
        "providers": "BOTH",
        "comparison": "independent probes of every upstream endpoint",
        "notes": "Runs in the background; drift is reported in every tool response.",
    },
    {
        "name": "tapebasis_tool_map",
        "capability": "this catalogue",
        "providers": "BOTH",
        "comparison": "n/a (local metadata, no upstream call)",
        "notes": "Returns this table so an agent can plan calls without spending requests.",
    },
)
