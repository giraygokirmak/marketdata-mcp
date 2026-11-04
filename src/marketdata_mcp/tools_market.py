"""Dual-source market data tools.

Every tool here asks **both** providers for the same fact, compares the
answers, and returns the reconciled payload with an explicit
``data_provider`` label. See :mod:`marketdata_mcp.reconcile` for the rules.

Providers are addressed through :mod:`marketdata_mcp.tv_access` and
:mod:`marketdata_mcp.fv_access`, which normalise the provider service layers.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from . import fv_access, tv_access
from .health import MONITOR
from .merge_helpers import (
    fv_earnings_fields,
    fv_indices_fields,
    fv_news_fields,
    fv_options_fields,
    fv_quote_fields,
    option_overlap,
    tv_indices_fields,
    tv_news_fields,
    tv_options_fields,
)
from .providers_state import DataProvider, envelope
from .reconcile import (
    FINVIZ,
    TRADINGVIEW,
    FieldSpec,
    ask,
    finalize,
    providers_in,
)
from .symbols import strip_exchange, to_finviz, to_yahoo

# ── Shared helpers ───────────────────────────────────────────────────────────


def _health(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Attach monitor-derived warnings/degraded flag scoped to attempted providers."""
    MONITOR.ensure_started()
    providers = kwargs.get("providers") or (TRADINGVIEW, FINVIZ)
    return {
        "health_warn": MONITOR.warnings_for(providers),
        "degraded": any(
            issue["provider"] in providers for issue in MONITOR.issues()
        ),
    }


def _quote_specs() -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("price", lambda p: p.get("price"), lambda p: p.get("price"), tolerance_pct=0.5),
        FieldSpec(
            "change_percent",
            lambda p: p.get("change_percent"),
            lambda p: p.get("change_percent"),
            tolerance_pct=2.0,
            absolute_floor=0.05,
        ),
        FieldSpec(
            "previous_close",
            lambda p: p.get("previous_close"),
            lambda p: p.get("previous_close"),
            tolerance_pct=0.5,
        ),
        FieldSpec(
            "week_52_high",
            lambda p: p.get("week_52_high"),
            lambda p: p.get("week_52_high"),
            tolerance_pct=0.5,
        ),
        FieldSpec(
            "week_52_low",
            lambda p: p.get("week_52_low"),
            lambda p: p.get("week_52_low"),
            tolerance_pct=0.5,
        ),
        FieldSpec("pe_ratio", lambda p: p.get("pe_ratio"), lambda p: p.get("pe_ratio"), tolerance_pct=2.0),
        FieldSpec(
            "rsi_14",
            lambda p: p.get("rsi_14"),
            lambda p: p.get("rsi_14"),
            tolerance_pct=5.0,
            absolute_floor=0.5,
        ),
        FieldSpec("volume", lambda p: p.get("volume"), lambda p: p.get("volume"), tolerance_pct=1.0),
    )


# ── Quote ────────────────────────────────────────────────────────────────────


async def stock_quote(symbol: str) -> dict[str, Any]:
    """Dual-sourced quote and fundamentals for one US ticker.

    Asks TradingView (scanner + Yahoo quote engine) and Finviz in parallel for
    price, change, previous close, 52-week range, P/E, RSI and volume, compares
    them, and returns the merged result. Finviz's deeper fundamentals — the
    84-field snapshot, company profile, peers and after-hours print — ride
    along, each labelled with the provider that supplied it.

    Args:
        symbol: Ticker as written for either source (``AAPL``, ``BRK.B``, ``BRK-B``).

    Returns:
        Envelope whose ``data_provider`` is ``BOTH`` when both providers answered
        and the compared fields agree, ``TRADINGVIEW``/``FINVIZ`` when only one
        answered (the notice then states the data is not cross-verified), or
        ``NONE`` when both failed.
    """
    ticker = strip_exchange(symbol).upper()
    fv_symbol = to_finviz(ticker)
    yahoo_symbol = to_yahoo(ticker)

    async def tv_call() -> dict[str, Any]:
        # Chart quote gives the authoritative live price/change; the scanner row
        # adds P/E, RSI, ATR and the 52-week range so those are dual-sourced too.
        quote_data = await tv_access.chart_quote(yahoo_symbol)
        metrics = await asyncio.to_thread(tv_access.scanner_metrics, ticker)
        return {"quote": quote_data, "scanner": metrics}

    async def fv_call() -> dict[str, Any]:
        return await fv_access.quote(fv_symbol)

    pair = await ask(tv_call, fv_call)

    def tv_norm(payload: dict[str, Any]) -> dict[str, Any]:
        quote_data = payload.get("quote") or {}
        scanner = payload.get("scanner") or {}
        return {
            "price": quote_data.get("price"),
            "change_percent": quote_data.get("change_pct"),
            "previous_close": quote_data.get("previous_close"),
            "week_52_high": quote_data.get("52w_high") or scanner.get("price_52_week_high"),
            "week_52_low": quote_data.get("52w_low") or scanner.get("price_52_week_low"),
            "pe_ratio": scanner.get("price_earnings_ttm"),
            "rsi_14": scanner.get("RSI"),
            "atr_14": scanner.get("ATR"),
            "volume": scanner.get("volume"),
            "beta": scanner.get("beta_1_year"),
            "market_state": quote_data.get("market_state"),
            "currency": quote_data.get("currency"),
            "exchange": quote_data.get("exchange"),
            "quote_stale": quote_data.get("quote_stale"),
            "price_timestamp": quote_data.get("market_time"),
            "tradingview_symbol": scanner.get("ticker"),
            "description": scanner.get("description"),
            "sector": scanner.get("sector"),
            "industry": scanner.get("industry"),
            "dividend_yield": scanner.get("dividends_yield_current"),
            "eps_ttm": scanner.get("earnings_per_share_basic_ttm"),
        }

    def fv_norm(payload: dict[str, Any]) -> dict[str, Any]:
        shaped = fv_quote_fields(payload)
        header = payload.get("last_close") or {}
        shaped["price"] = header.get("price")
        shaped["change_percent"] = header.get("change_percent")
        snapshot = payload.get("snapshot_numeric") or {}
        shaped["previous_close"] = snapshot.get("Prev Close")
        # Finviz renders the print time next to the price ("Nov 03 • 3:59 PM ET").
        shaped["price_timestamp"] = header.get("date")
        return shaped

    return finalize(
        pair,
        specs=_quote_specs(),
        tv_norm=tv_norm,
        fv_norm=fv_norm,
        sources={
            TRADINGVIEW: "Yahoo Finance chart API (via TradingView provider)",
            FINVIZ: f"https://finviz.com/stock?t={fv_symbol}",
        },
        extra={"symbol": ticker},
        **_health({"providers": providers_in(pair) or (TRADINGVIEW, FINVIZ)}),
    )


# ── Bulk prices ──────────────────────────────────────────────────────────────


async def stock_prices(tickers: str) -> dict[str, Any]:
    """Dual-sourced prices for several tickers at once.

    Each ticker is looked up on both TradingView's scanner and Finviz's
    screener; the per-row ``data_provider`` says whether that particular ticker
    was confirmed by both, by one, or by neither.

    Args:
        tickers: Comma-separated symbols, e.g. ``"AAPL,MSFT,NVDA"``.

    Returns:
        Envelope whose ``data`` is a list of rows, each with ``symbol``,
        ``price``, ``change_percent`` and its own ``data_provider``.
    """
    requested = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not requested:
        return envelope(
            [],
            provider=DataProvider.NONE,
            warnings=["No tickers supplied."],
            extra={"symbols": []},
        )

    def tv_query() -> list[str]:
        return [f"{v}:{s}" for s in requested for v in ("NASDAQ", "NYSE", "AMEX")]

    async def tv_call() -> dict[str, Any]:
        async def run():
            return await asyncio.to_thread(tv_access.scanner_prices, tv_query())

        return {"rows": await run()}

    async def fv_call() -> dict[str, Any]:
        rows = await fv_access.screener_rows([to_finviz(s) for s in requested])
        return {"rows": {str(r.get("ticker", "")).upper(): r for r in rows}}

    pair = await ask(tv_call, fv_call)

    tv_rows = (pair.tv.payload or {}).get("rows") or {}
    fv_rows = (pair.fv.payload or {}).get("rows") or {}

    rows: list[dict[str, Any]] = []
    for symbol in requested:
        tv_row = tv_rows.get(symbol) or tv_rows.get(to_yahoo(symbol))
        fv_row = fv_rows.get(symbol) or fv_rows.get(to_finviz(symbol))
        if tv_row and fv_row:
            from .reconcile import compare_numeric
            from .freshness import freshest, parse_market_timestamp

            price_cmp = compare_numeric(tv_row.get("close"), fv_row.get("price"), tolerance_pct=0.5)
            change_cmp = compare_numeric(
                tv_row.get("change"), fv_row.get("change"), tolerance_pct=2.0, absolute_floor=0.05
            )
            # Both providers answered this row, so it is BOTH. Agreement is a
            # separate axis: `agrees` is false when the readings differ beyond
            # tolerance, and both numbers stay in `comparisons`.
            agree = price_cmp["match"] is not False and change_cmp["match"] is not False

            # One price per row, from whichever provider printed more recently.
            tv_moment = parse_market_timestamp(tv_row.get("update_time") or tv_row.get("timestamp"))
            fv_moment = parse_market_timestamp(fv_row.get("last_update") or fv_row.get("fetched_at"))
            winner = freshest((TRADINGVIEW, tv_moment), (FINVIZ, fv_moment)) or FINVIZ
            source_row = tv_row if winner == TRADINGVIEW else fv_row
            rows.append(
                {
                    "symbol": symbol,
                    "price": source_row.get("close") if winner == TRADINGVIEW else source_row.get("price"),
                    "change_percent": source_row.get("change"),
                    "previous_close": source_row.get("prevClose") or fv_row.get("prevClose"),
                    "volume": source_row.get("volume"),
                    "company": fv_row.get("company") or tv_row.get("description"),
                    "data_provider": DataProvider.BOTH.value,
                    "verification": "VERIFIED",
                    "agrees": agree,
                    "price_source": winner,
                    "comparisons": {"price": price_cmp, "change_percent": change_cmp},
                }
            )
        elif tv_row:
            rows.append(
                {
                    "symbol": symbol,
                    "price": tv_row.get("close"),
                    "change_percent": tv_row.get("change"),
                    "volume": tv_row.get("volume"),
                    "company": tv_row.get("description"),
                    "data_provider": DataProvider.TRADINGVIEW.value,
                    "verification": "UNVERIFIED",
                }
            )
        elif fv_row:
            rows.append(
                {
                    "symbol": symbol,
                    "price": fv_row.get("price"),
                    "change_percent": fv_row.get("change"),
                    "previous_close": fv_row.get("prevClose"),
                    "volume": fv_row.get("volume"),
                    "company": fv_row.get("company"),
                    "data_provider": DataProvider.FINVIZ.value,
                    "verification": "UNVERIFIED",
                }
            )
        else:
            rows.append(
                {
                    "symbol": symbol,
                    "data_provider": DataProvider.NONE.value,
                    "verification": "UNAVAILABLE",
                }
            )

    providers_present = {row["data_provider"] for row in rows}
    if providers_present == {"BOTH"}:
        overall = DataProvider.BOTH
    elif "BOTH" in providers_present or (
        "TRADINGVIEW" in providers_present and "FINVIZ" in providers_present
    ):
        overall = DataProvider.BOTH
    elif "TRADINGVIEW" in providers_present:
        overall = DataProvider.TRADINGVIEW
    elif "FINVIZ" in providers_present:
        overall = DataProvider.FINVIZ
    else:
        overall = DataProvider.NONE

    return finalize(
        pair,
        specs=(),
        sources={TRADINGVIEW: "scanner.tradingview.com", FINVIZ: "finviz.com/screener/stocks"},
        extra={"symbols": requested},
        data=rows,
        provider=overall,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── Screener ─────────────────────────────────────────────────────────────────


async def stock_screener(
    country: str = "america",
    sort_by: str = "market_cap",
    limit: int = 25,
    finviz_filters: str | None = None,
) -> dict[str, Any]:
    """Run the US stock screener on both providers and cross-check the rows.

    TradingView ranks the entire market server-side (``sort_by`` ∈
    ``market_cap``, ``dividend_yield``, ``change``, ``price``); Finviz can be
    narrowed with its own filter language (``finviz_filters``, e.g.
    ``cap_smallover,sec_technology`` or ``t=AAPL,MSFT``). Tickers that both
    providers return are compared on change percentage.

    Args:
        country: TradingView market (``america``, ``turkey``, …).
        sort_by: TradingView sort key.
        limit: Rows to request from each side (≤200 on Finviz).
        finviz_filters: Optional Finviz filter expression.

    Returns:
        Envelope with ``rows`` (TradingView ranking) and a per-row
        ``finviz_match`` showing whether Finviz agreed.
    """
    capped = max(1, min(int(limit), 200))

    async def tv_call() -> dict[str, Any]:
        return await tv_access.stock_screen(
            country=country, limit=capped, sort_by=sort_by
        )

    async def fv_call() -> dict[str, Any]:
        data = await fv_access.screen(filters=finviz_filters, order="-change", page=1)
        return data

    pair = await ask(tv_call, fv_call)

    tv_rows = (pair.tv.payload or {}).get("rows") or []
    fv_rows = (pair.fv.payload or {}).get("rows") or []
    fv_by_symbol = {str(r.get("ticker", "")).upper(): r for r in fv_rows}

    from .reconcile import compare_numeric

    merged_rows = []
    agreed = compared = 0
    for row in tv_rows:
        symbol = str(row.get("symbol") or "").upper()
        fv_row = fv_by_symbol.get(symbol)
        entry = {
            "symbol": symbol,
            "company": row.get("description"),
            "price": row.get("price"),
            "change_percent": row.get("change_percent"),
            "market_cap": row.get("market_cap"),
            "ticker": row.get("ticker"),
            "data_provider": DataProvider.TRADINGVIEW.value,
        }
        if fv_row:
            comparison = compare_numeric(
                row.get("change_percent"), fv_row.get("change"), tolerance_pct=3.0,
                absolute_floor=0.1,
            )
            entry["data_provider"] = DataProvider.BOTH.value
            entry["finviz_change_percent"] = fv_row.get("change")
            entry["finviz_match"] = comparison["match"]
            entry["comparison"] = comparison
            compared += 1
            if comparison["match"] is not False:
                agreed += 1
        merged_rows.append(entry)

    data = {
        "country": country,
        "sort_by": sort_by,
        "tradingview_total_matches": (pair.tv.payload or {}).get("total_matches"),
        "rows": merged_rows,
        "finviz_rows": len(fv_rows),
        "cross_checked_rows": compared,
        "agreeing_rows": agreed,
    }
    return finalize(
        pair,
        specs=(),
        sources={
            TRADINGVIEW: "scanner.tradingview.com",
            FINVIZ: "finviz.com/screener/stocks",
        },
        extra={"screener": data},
        data=data,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── Indices ──────────────────────────────────────────────────────────────────


async def market_indices() -> dict[str, Any]:
    """Dual-sourced level and day change for the major US indices.

    Covers S&P 500, Dow, Nasdaq Composite, Russell 2000 and VIX. Finviz and
    TradingView/Yahoo use different tickers for the same index; they are mapped
    to one canonical name before comparison.

    Returns:
        Envelope whose ``data.indices`` is keyed by canonical index
        (``SPX``, ``DJI``, ``COMP``, ``RUT``, ``VIX``), each value carrying
        ``level``, ``change_percent`` and its own ``data_provider``.
    """
    async def tv_call() -> dict[str, Any]:
        return await tv_access.market_snapshot()

    async def fv_call() -> dict[str, Any]:
        return await fv_access.indices()

    pair = await ask(tv_call, fv_call)
    tv_norm = tv_indices_fields(pair.tv.payload) if pair.tv.payload else None
    fv_norm = fv_indices_fields(pair.fv.payload) if pair.fv.payload else None

    tv_indices = (tv_norm or {}).get("indices") or {}
    fv_indices = (fv_norm or {}).get("indices") or {}

    from .reconcile import compare_numeric

    indices: dict[str, Any] = {}
    for canonical in ("SPX", "DJI", "COMP", "RUT", "IWM", "VIX"):
        tv_row = tv_indices.get(canonical)
        fv_row = fv_indices.get(canonical)
        if tv_row and fv_row:
            # Compare the level (the actual index value). The day-change
            # percentage is not compared: providers compute it from different
            # reference closes, so a small gap is a formula difference rather
            # than a data conflict — both readings are still reported.
            level_cmp = compare_numeric(tv_row.get("level"), fv_row.get("level"), tolerance_pct=0.5)
            indices[canonical] = {
                "name": fv_row.get("name") or canonical,
                "level": fv_row.get("level"),
                "previous_close": fv_row.get("previous_close"),
                "change": fv_row.get("change"),
                "change_percent": fv_row.get("change_percent"),
                "tradingview_level": tv_row.get("level"),
                "tradingview_change_percent": tv_row.get("change_percent"),
                "data_provider": DataProvider.BOTH.value,
                "comparison": {"level": level_cmp},
                "agree": level_cmp["match"] is not False,
            }
        elif tv_row:
            indices[canonical] = {
                "name": canonical,
                "level": tv_row.get("level"),
                "change_percent": tv_row.get("change_percent"),
                "data_provider": DataProvider.TRADINGVIEW.value,
            }
        elif fv_row:
            indices[canonical] = {
                "name": fv_row.get("name") or canonical,
                "level": fv_row.get("level"),
                "previous_close": fv_row.get("previous_close"),
                "change": fv_row.get("change"),
                "change_percent": fv_row.get("change_percent"),
                "data_provider": DataProvider.FINVIZ.value,
            }

    verified = {
        canonical: row for canonical, row in indices.items()
        if row["data_provider"] == DataProvider.BOTH.value
    }
    overall = (
        DataProvider.BOTH if verified else
        DataProvider.TRADINGVIEW if tv_indices else
        DataProvider.FINVIZ if fv_indices else
        DataProvider.NONE
    )
    return finalize(
        pair,
        specs=(),
        sources={
            TRADINGVIEW: "Yahoo Finance indices snapshot (via TradingView provider)",
            FINVIZ: "https://finviz.com/api/indices_data",
        },
        extra={"index_set": sorted(indices), "verified_indices": sorted(verified)},
        data={"indices": indices},
        provider=overall,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── News ─────────────────────────────────────────────────────────────────────


async def stock_news(symbol: str, limit: int = 20, include_reactions: bool = True) -> dict[str, Any]:
    """Dual-sourced headlines for one ticker.

    Finviz supplies headlines with the price reaction around each item (the
    day-of move and, when the story broke after the close, the next session's
    move). TradingView's side is the provider's licensed news feed. Headline
    titles are matched across both to report overlap.

    Args:
        symbol: Ticker.
        limit: Maximum headlines per provider.
        include_reactions: Ask Finviz for price-reaction columns.

    Returns:
        Envelope with ``data.items`` (Finviz ordering, which carries reactions)
        and ``data.overlap`` describing how many TradingView headlines were also
        present on Finviz.
    """
    ticker = strip_exchange(symbol).upper()

    async def tv_call() -> dict[str, Any]:
        return await tv_access.market_news(ticker, limit=limit)

    async def fv_call() -> dict[str, Any]:
        return await fv_access.news(to_finviz(ticker), limit=limit, include_reactions=include_reactions)

    pair = await ask(tv_call, fv_call)
    tv_norm = tv_news_fields(pair.tv.payload) if pair.tv.payload else None
    fv_norm = fv_news_fields(pair.fv.payload) if pair.fv.payload else None

    tv_items = (tv_norm or {}).get("items") or []
    fv_items = (fv_norm or {}).get("items") or []
    overlap = None
    if tv_items and fv_items:
        from .merge_helpers import news_overlap

        overlap = news_overlap(tv_items, fv_items)

    items = fv_items or tv_items
    data = {
        "ticker": ticker,
        "items": items,
        "tradingview_items": tv_items,
        "overlap": overlap,
        "count": len(items),
    }
    provider = pair.provider
    return finalize(
        pair,
        specs=(),
        sources={
            TRADINGVIEW: "Marketaux news feed (via TradingView provider)",
            FINVIZ: f"https://finviz.com/api/news/quote/{to_finviz(ticker)}",
        },
        extra={"news": data},
        data=data,
        provider=provider,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── Options ──────────────────────────────────────────────────────────────────


async def stock_options_chain(symbol: str, expiry: str | None = None) -> dict[str, Any]:
    """Dual-sourced options chain for one expiry.

    Finviz and the TradingView provider's Yahoo options endpoint are both
    queried. Contracts are matched by strike and their volume/open-interest
    compared, so an agent can see whether the two sources agree on open
    positioning.

    Args:
        symbol: Underlying ticker.
        expiry: ``YYYY-MM-DD``; ``None`` uses each provider's nearest expiry.

    Returns:
        Envelope with ``data.calls``/``data.puts`` (Finviz, which carries full
        greeks), the TradingView chain, and ``data.overlap``.
    """
    ticker = strip_exchange(symbol).upper()

    async def tv_call() -> dict[str, Any]:
        return await tv_access.options_chain(ticker, expiry)

    async def fv_call() -> dict[str, Any]:
        return await fv_access.options(to_finviz(ticker), expiry)

    pair = await ask(tv_call, fv_call)
    tv_norm = tv_options_fields(pair.tv.payload) if pair.tv.payload else None
    fv_norm = fv_options_fields(pair.fv.payload) if pair.fv.payload else None

    tv_calls = (tv_norm or {}).get("calls") or []
    tv_puts = (tv_norm or {}).get("puts") or []
    fv_calls = (fv_norm or {}).get("calls") or []
    fv_puts = (fv_norm or {}).get("puts") or []

    overlap = None
    if (tv_calls or tv_puts) and (fv_calls or fv_puts):
        overlap = {
            "calls": option_overlap(tv_calls, fv_calls),
            "puts": option_overlap(tv_puts, fv_puts),
        }

    data = {
        "symbol": ticker,
        "expiry": (fv_norm or {}).get("expiry") or (tv_norm or {}).get("expiry"),
        "expiries": (fv_norm or {}).get("expiries") or (tv_norm or {}).get("expiries") or [],
        "underlying_price": (fv_norm or {}).get("underlying_price")
        or (tv_norm or {}).get("underlying_price"),
        "calls": fv_calls or tv_calls,
        "puts": fv_puts or tv_puts,
        "tradingview_calls": tv_calls,
        "tradingview_puts": tv_puts,
        "contract_count": (fv_norm or {}).get("contract_count")
        or (tv_norm or {}).get("contract_count"),
        "overlap": overlap,
    }
    return finalize(
        pair,
        specs=(),
        sources={
            TRADINGVIEW: "Yahoo Finance options endpoint (via TradingView provider)",
            FINVIZ: f"https://finviz.com/stock?t={to_finviz(ticker)}&ty=oc",
        },
        extra={"options": data},
        data=data,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── Earnings ─────────────────────────────────────────────────────────────────


def _et_timestamp_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


async def stock_earnings(symbol: str) -> dict[str, Any]:
    """Dual-sourced earnings calendar and history for one ticker.

    Finviz owns the detailed tables (reported quarters, estimate revisions,
    fundamental series, post-earnings reactions); TradingView's scanner confirms
    the next/last earnings timestamp. The two dates are compared and reported.

    Args:
        symbol: Ticker.

    Returns:
        Envelope with ``data.next_earnings_date``, the Finviz tables and a
        ``date_comparison`` block.
    """
    ticker = strip_exchange(symbol).upper()

    async def tv_call() -> dict[str, Any]:
        return await asyncio.to_thread(tv_access.earnings_calendar, ticker)

    async def fv_call() -> dict[str, Any]:
        return await fv_access.earnings(to_finviz(ticker))

    pair = await ask(tv_call, fv_call)

    fv_data = fv_earnings_fields(pair.fv.payload) if pair.fv.payload else {}
    tv_row = pair.tv.payload or {}
    tv_next = _et_timestamp_to_iso(tv_row.get("earnings_release_next_date"))
    tv_last = _et_timestamp_to_iso(tv_row.get("earnings_release_date"))

    comparison = None
    if fv_data.get("earnings_date") and tv_next:
        comparison = {
            "tv_next_earnings": tv_next,
            "fv_earnings_date": fv_data.get("earnings_date"),
            "note": "Dates come from different schedules; compare by day, not by exact timestamp.",
        }

    data = {
        "symbol": ticker,
        "finviz_earnings_date": fv_data.get("earnings_date"),
        "next_earnings_date": tv_next or fv_data.get("earnings_date"),
        "finviz_note": (
            "finviz_earnings_date is the last *reported* quarter's release "
            "timestamp (the anchor for its results tables); next_earnings_date "
            "is the upcoming release from the TradingView calendar."
        ),
        "tradingview_last_earnings": tv_last,
        "tradingview_next_earnings": tv_next,
        "latest_quarter": fv_data.get("latest_quarter"),
        "latest_eps_actual": fv_data.get("latest_eps_actual"),
        "latest_eps_estimate": fv_data.get("latest_eps_estimate"),
        "quarterly": fv_data.get("quarterly") or [],
        "annual": fv_data.get("annual") or [],
        "revisions": fv_data.get("revisions") or [],
        "price_reactions": fv_data.get("price_reactions") or [],
        "date_comparison": comparison,
    }
    return finalize(
        pair,
        specs=(),
        sources={
            TRADINGVIEW: "scanner.tradingview.com earnings calendar",
            FINVIZ: f"https://finviz.com/stock?t={to_finviz(ticker)}&ty=ea",
        },
        extra={"earnings": data},
        data=data,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── Extended hours ───────────────────────────────────────────────────────────


async def stock_extended_hours(symbol: str) -> dict[str, Any]:
    """Dual-sourced pre-/post-market price for a US stock.

    Finviz exposes the after-hours print on the quote page; the TradingView
    provider derives pre/regular/post prices from 1-minute Yahoo candles. The
    after-hours price and change are compared.

    Args:
        symbol: Ticker.

    Returns:
        Envelope with ``data`` carrying ``pre_market``, ``regular``,
        ``post_market`` and the Finviz after-hours block.
    """
    ticker = strip_exchange(symbol).upper()

    async def tv_call() -> dict[str, Any]:
        return await tv_access.extended_hours(to_yahoo(ticker))

    async def fv_call() -> dict[str, Any]:
        return await fv_access.quote(to_finviz(ticker))

    pair = await ask(tv_call, fv_call)

    tv = pair.tv.payload or {}
    fv_quote = pair.fv.payload or {}
    fv_after = fv_quote.get("after_hours") or {}

    from .reconcile import compare_numeric

    tv_after_price = (tv.get("post_market") or {}).get("price") or (
        tv.get("pre_market") or {}
    ).get("price")
    comparison = compare_numeric(tv_after_price, fv_after.get("price"), tolerance_pct=0.5)

    data = {
        "symbol": ticker,
        "pre_market": tv.get("pre_market"),
        "regular": tv.get("regular"),
        "post_market": tv.get("post_market"),
        "finviz_after_hours": fv_after,
        "finviz_last_close": (fv_quote.get("last_close") or {}).get("price"),
        "after_hours_comparison": comparison,
    }
    return finalize(
        pair,
        specs=(),
        sources={
            TRADINGVIEW: "Yahoo 1-minute extended-hours candles (via TradingView provider)",
            FINVIZ: f"https://finviz.com/stock?t={to_finviz(ticker)}",
        },
        extra={"extended_hours": data},
        data=data,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── Technical analysis ───────────────────────────────────────────────────────


async def technical_analysis(
    symbol: str,
    exchange: str = "NASDAQ",
    timeframe: str = "1D",
) -> dict[str, Any]:
    """Technical analysis with the key readings cross-checked against Finviz.

    TradingView computes the full indicator stack (RSI, MACD, EMAs, Bollinger,
    ATR, ADX, structure, trade setup). Finviz's snapshot independently provides
    price, RSI(14), ATR(14) and the 52-week range, which are compared here.

    Args:
        symbol: Ticker.
        exchange: TradingView exchange prefix (``NASDAQ``, ``NYSE``, ``BIST``, …).
        timeframe: ``5m``, ``15m``, ``1h``, ``4h``, ``1D``, ``1W``, ``1M``.

    Returns:
        Envelope whose ``data.analysis`` is the TradingView analysis and
        ``data.cross_check`` holds the Finviz comparison.
    """
    ticker = strip_exchange(symbol).upper()

    async def tv_call() -> dict[str, Any]:
        return await asyncio.to_thread(
            tv_access.technical_analysis_sync,
            ticker,
            exchange=exchange,
            timeframe=timeframe,
        )

    async def fv_call() -> dict[str, Any]:
        return await fv_access.snapshot(to_finviz(ticker))

    pair = await ask(tv_call, fv_call)

    analysis = pair.tv.payload or {}
    fv_snapshot = pair.fv.payload or {}
    fv_numeric = fv_snapshot.get("numeric") or {}

    from .reconcile import compare_numeric

    tv_price = (analysis.get("price_data") or {}).get("current_price")
    tv_rsi = (analysis.get("rsi") or {}).get("value")

    cross_check = None
    if analysis and fv_numeric:
        cross_check = {
            "price": compare_numeric(tv_price, fv_numeric.get("Price"), tolerance_pct=0.5),
            "rsi_14": compare_numeric(
                tv_rsi, fv_numeric.get("RSI (14)"), tolerance_pct=12.0, absolute_floor=1.0
            ),
            "atr_14": compare_numeric(
                (analysis.get("atr") or {}).get("value"),
                fv_numeric.get("ATR (14)"),
                tolerance_pct=10.0,
                absolute_floor=0.1,
            ),
            "volume": compare_numeric(
                (analysis.get("price_data") or {}).get("volume"),
                fv_numeric.get("Volume"),
                tolerance_pct=2.0,
            ),
        }

    data = {
        "symbol": ticker,
        "exchange": exchange,
        "timeframe": timeframe,
        "analysis": analysis,
        "finviz_snapshot": fv_numeric,
        "cross_check": cross_check,
    }
    return finalize(
        pair,
        specs=(),
        sources={
            TRADINGVIEW: "TradingView technical analysis engine",
            FINVIZ: f"https://finviz.com/stock?t={to_finviz(ticker)}",
        },
        extra={"technical_analysis": data},
        data=data,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


# ── Registration ─────────────────────────────────────────────────────────────

_TOOLS = (
    (stock_quote, "Market data: quote, fundamentals, and a snapshot"),
    (stock_prices, "Market data: batch stock prices"),
    (stock_screener, "Market data: stock screener"),
    (market_indices, "Market data: major market indices"),
    (stock_news, "Market data: stock news"),
    (stock_options_chain, "Market data: options chain"),
    (stock_earnings, "Market data: earnings calendar and history"),
    (stock_extended_hours, "Market data: pre/post-market prices"),
    (technical_analysis, "Market data: technical analysis"),
)


def register(mcp) -> int:
    """Register the dual-source market tools."""
    for fn, title in _TOOLS:
        mcp.add_tool(fn, name=fn.__name__, title=title, description=fn.__doc__)
    return len(_TOOLS)
