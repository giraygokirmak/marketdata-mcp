"""TradingView-only tools — served, and explicitly labelled single-source.

These capabilities exist only on the TradingView side of this stack: crypto
screening, futures, EGX coverage, strategy backtests, news sentiment and the
global FX/crypto snapshot. Finviz has no equivalent dataset, so the reconciler's
``single()`` helper marks Finviz ``NOT_ATTEMPTED`` and the envelope reads
``TRADINGVIEW`` with a notice that the data is not cross-verified.

One exception: ``combined_analysis`` adds a Finviz cross-check to the
TradingView technical read, so that tool genuinely can reach ``BOTH``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from . import fv_access, tv_access
from .health import FINVIZ, MONITOR, TRADINGVIEW
from .providers_state import DataProvider, envelope, error_code
from .reconcile import ask, finalize, providers_in, single
from .providers.tradingview.core.services import (
    backtest_service,
    bitcoin_market_service,
    futures_service,
    marketaux_service,
    multi_agent_service,
    scanner_service,
    screener_service,
)
from .symbols import strip_exchange, to_finviz

_OFFLOAD = asyncio.to_thread


async def _run(fn, /, *args, **kwargs):
    """Run a blocking provider function in a worker thread (keeps the loop free)."""
    return await _OFFLOAD(fn, *args, **kwargs)


def _single_response(
    data: Any,
    *,
    source: str,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    MONITOR.ensure_started()
    pair = single(TRADINGVIEW, data)
    return finalize(
        pair,
        sources={TRADINGVIEW: source},
        warnings=warnings or [],
        data=data,
        provider=DataProvider.TRADINGVIEW,
        health_warn=MONITOR.warnings_for((TRADINGVIEW,)),
        degraded=any(issue["provider"] == TRADINGVIEW for issue in MONITOR.issues()),
        extra=extra,
    )


def _failed(exc: BaseException, **extra: Any) -> dict[str, Any]:
    if isinstance(exc, dict):
        message = str(exc.get("error") or exc)
        code = "PROVIDER_ERROR"
    else:
        message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
        code = error_code(exc)
    return envelope(
        None,
        provider=DataProvider.NONE,
        errors=[{"provider": TRADINGVIEW, "code": code, "message": message}],
        warnings=[f"TradingView could not serve this request: {message}"],
        extra=extra,
    )


async def _offload_result(fn, *args, source: str, **kwargs) -> dict[str, Any]:
    try:
        data = await _run(fn, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)
    if isinstance(data, dict) and data.get("error"):
        return _failed(data)
    return _single_response(data, source=source)


# ── Crypto ───────────────────────────────────────────────────────────────────


async def crypto_top_movers(
    movement: str = "gainers", exchange: str = "KUCOIN", timeframe: str = "15m", limit: int = 25
) -> dict[str, Any]:
    """Top crypto gainers or losers on an exchange (TradingView-only).

    Args:
        movement: ``gainers`` or ``losers``.
        exchange: TradingView venue (``KUCOIN``, ``BINANCE``, ``BYBIT``, …).
        timeframe: ``5m``, ``15m``, ``1h``, ``4h``, ``1D``, ``1W``, ``1M``.
        limit: Rows to return.

    Returns:
        Envelope labelled ``TRADINGVIEW`` with the ranked rows.
    """
    direction = movement.lower().strip()
    fn = screener_service.fetch_trending_analysis
    return await _offload_result(
        fn,
        exchange.upper(),
        timeframe=timeframe,
        sort="desc" if direction == "gainers" else "asc",
        limit=limit,
        source="TradingView scanner (crypto)",
    )


async def crypto_bollinger_scan(
    exchange: str = "KUCOIN", timeframe: str = "4h", bbw_threshold: float = 0.04, limit: int = 50
) -> dict[str, Any]:
    """Bollinger-band squeeze candidates (TradingView-only).

    Args:
        exchange: TradingView venue.
        timeframe: TradingView interval.
        bbw_threshold: Maximum band-width to qualify as a squeeze.
        limit: Rows to return.

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    return await _offload_result(
        screener_service.fetch_bollinger_analysis,
        exchange.upper(),
        timeframe=timeframe,
        limit=limit,
        bbw_filter=bbw_threshold,
        source="TradingView scanner (Bollinger)",
    )


async def crypto_volume_breakout(
    exchange: str = "KUCOIN",
    timeframe: str = "15m",
    volume_multiplier: float = 2.0,
    price_change_min: float = 3.0,
    limit: int = 25,
) -> dict[str, Any]:
    """Volume + price breakout scanner (TradingView-only).

    Args:
        exchange: TradingView venue.
        timeframe: TradingView interval.
        volume_multiplier: Minimum relative volume.
        price_change_min: Minimum absolute % change.
        limit: Rows to return.

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    return await _offload_result(
        scanner_service.volume_breakout_scan,
        exchange.upper(),
        timeframe=timeframe,
        volume_multiplier=volume_multiplier,
        price_change_min=price_change_min,
        limit=limit,
        source="TradingView scanner (volume breakout)",
    )


async def crypto_multi_timeframe(
    symbol: str, exchange: str = "KUCOIN"
) -> dict[str, Any]:
    """Multi-timeframe technical read for a crypto symbol (TradingView-only).

    Args:
        symbol: Coin symbol (``BTC``, ``ETH``).
        exchange: TradingView venue.

    Returns:
        Envelope labelled ``TRADINGVIEW`` with per-timeframe analysis.
    """
    from .providers.tradingview.core.utils.validators import (
        normalize_tradingview_symbol,
    )

    venue = exchange.upper()
    full_symbol = normalize_tradingview_symbol(strip_exchange(symbol).upper(), venue)
    return await _offload_result(
        screener_service.run_multi_timeframe_analysis,
        full_symbol,
        venue,
        source="TradingView technical analysis (multi-timeframe)",
    )


async def crypto_multi_agent(symbol: str, exchange: str = "KUCOIN", timeframe: str = "15m") -> dict[str, Any]:
    """Multi-agent bull/bear debate over a crypto symbol (TradingView-only).

    Args:
        symbol: Coin symbol.
        exchange: TradingView venue.
        timeframe: TradingView interval.

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    from .providers.tradingview.core.utils.validators import (
        normalize_tradingview_symbol,
    )

    venue = exchange.upper()
    full_symbol = normalize_tradingview_symbol(strip_exchange(symbol).upper(), venue)
    return await _offload_result(
        multi_agent_service.run_multi_agent_analysis,
        full_symbol,
        venue,
        timeframe=timeframe,
        source="TradingView multi-agent analysis",
    )


# ── Futures ──────────────────────────────────────────────────────────────────


async def futures_market_overview(
    category: str = "all", exchanges: str = "us", limit: int = 30
) -> dict[str, Any]:
    """Futures contracts ranked by volume (TradingView-only).

    Args:
        category: ``all``, ``equity_index``, ``energy``, ``metals``,
            ``agriculture``, ``rates``, ``forex`` or ``crypto_futures``.
        exchanges: ``us`` or ``global``.
        limit: Rows to return.

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    return await _offload_result(
        futures_service.get_futures_overview,
        category=category,
        exchanges=exchanges,
        limit=limit,
        source="TradingView scanner (futures)",
    )


async def futures_top_movers(
    movement: str = "gainers", exchanges: str = "us", limit: int = 20
) -> dict[str, Any]:
    """Futures gainers/losers by percent change (TradingView-only).

    Args:
        movement: ``gainers`` or ``losers``.
        exchanges: ``us`` or ``global``.
        limit: Rows to return.

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    return await _offload_result(
        futures_service.get_futures_movers,
        direction=movement,
        exchanges=exchanges,
        limit=limit,
        source="TradingView scanner (futures movers)",
    )


async def futures_watchlist() -> dict[str, Any]:
    """Curated futures watchlist with levels (TradingView-only).

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    return await _offload_result(
        futures_service.get_futures_watchlist,
        source="TradingView scanner (futures watchlist)",
    )


# ── Backtests ────────────────────────────────────────────────────────────────


async def stock_backtest(
    symbol: str,
    strategy: str,
    period: str = "1y",
    initial_capital: float = 10000.0,
    interval: str = "1d",
    include_trade_log: bool = False,
    include_equity_curve: bool = False,
) -> dict[str, Any]:
    """Backtest a strategy on a symbol (TradingView-only, single-source).

    Finviz has no OHLCV history endpoint, so this cannot be cross-verified. The
    response is labelled ``TRADINGVIEW`` and the notice says so.

    Args:
        symbol: Ticker or coin.
        strategy: One of ``rsi``, ``bollinger``, ``macd``, ``ema_cross``,
            ``supertrend``, ``donchian``, ``rsi_pullback``, ``keltner_breakout``,
            ``triple_ema``.
        period: ``6mo``, ``1y``, ``2y``, ``5y``, …
        initial_capital: Starting cash.
        interval: ``1d`` or ``1h``.
        include_trade_log: Include the per-trade log.
        include_equity_curve: Include the equity curve.

    Returns:
        Envelope labelled ``TRADINGVIEW`` with performance metrics.
    """
    return await _offload_result(
        backtest_service.run_backtest,
        strip_exchange(symbol).upper(),
        strategy,
        period=period,
        initial_capital=initial_capital,
        interval=interval,
        include_trade_log=include_trade_log,
        include_equity_curve=include_equity_curve,
        source="TradingView backtest engine (Yahoo OHLCV)",
    )


async def stock_backtest_compare(
    symbol: str, period: str = "1y", initial_capital: float = 10000.0
) -> dict[str, Any]:
    """Compare every strategy on one symbol (TradingView-only).

    Args:
        symbol: Ticker.
        period: Lookback period.
        initial_capital: Starting cash.

    Returns:
        Envelope labelled ``TRADINGVIEW`` with the strategy leaderboard.
    """
    return await _offload_result(
        backtest_service.compare_strategies,
        strip_exchange(symbol).upper(),
        period=period,
        initial_capital=initial_capital,
        source="TradingView backtest engine (Yahoo OHLCV)",
    )


# ── Market-wide ──────────────────────────────────────────────────────────────


async def market_snapshot() -> dict[str, Any]:
    """Global FX, crypto, index and ETF snapshot (TradingView-only).

    Finviz covers only the four US indices, so the broad snapshot is
    single-source. Use ``market_indices`` for a cross-verified index read.

    Returns:
        Envelope labelled ``TRADINGVIEW`` grouped by asset class.
    """
    try:
        data = await tv_access.market_snapshot()
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)
    return _single_response(data, source="Yahoo Finance snapshot (via TradingView provider)")


async def bitcoin_market_pulse() -> dict[str, Any]:
    """Bitcoin market health: trend, dominance proxies and volatility.

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    return await _offload_result(
        bitcoin_market_service.get_bitcoin_market_pulse,
        source="TradingView/Yahoo bitcoin pulse",
    )


async def market_sentiment(symbol: str, category: str = "all", limit: int = 20) -> dict[str, Any]:
    """News-based sentiment for a symbol (TradingView-only).

    Sentiment requires a Marketaux API token (``MARKETAUX_API_TOKEN``). Without
    it the tool reports that clearly instead of inventing a score.

    Args:
        symbol: Ticker.
        category: News category.
        limit: Articles to analyse.

    Returns:
        Envelope labelled ``TRADINGVIEW`` with the sentiment score and label.
    """
    return await _offload_result(
        marketaux_service.analyze_sentiment,
        strip_exchange(symbol).upper(),
        category=category,
        limit=limit,
        source="Marketaux sentiment (via TradingView provider)",
    )


async def egx_market_overview(timeframe: str = "1D", limit: int = 10) -> dict[str, Any]:
    """Egyptian Exchange overview (TradingView-only).

    Returns:
        Envelope labelled ``TRADINGVIEW``.
    """
    from .providers.tradingview.core.services.egx_service import get_egx_market_overview

    return await _offload_result(
        get_egx_market_overview,
        timeframe=timeframe,
        limit=limit,
        source="TradingView scanner (EGX)",
    )


# ── Dual/cross-checked composite ─────────────────────────────────────────────


async def combined_analysis(symbol: str, exchange: str = "NASDAQ", timeframe: str = "1D") -> dict[str, Any]:
    """Technical analysis, news and sentiment in one call, cross-checked on price.

    Runs the TradingView technical read plus the provider's news sentiment, then
    pulls Finviz's latest close to confirm the price the analysis was built on.
    This is the one composite that can legitimately return ``BOTH``: the price
    leg is dual-sourced, while the analysis and sentiment legs remain
    TradingView-only and are labelled per field.

    Args:
        symbol: Ticker.
        exchange: TradingView exchange prefix.
        timeframe: TradingView interval.

    Returns:
        Envelope with ``data.analysis``, ``data.sentiment``, ``data.news`` and
        ``data.price_cross_check``.
    """
    ticker = strip_exchange(symbol).upper()

    async def tv_call() -> dict[str, Any]:
        analysis = await _run(
            tv_access.technical_analysis_sync,
            ticker,
            exchange=exchange,
            timeframe=timeframe,
        )
        sentiment = await _run(
            marketaux_service.analyze_sentiment, ticker, limit=20
        )
        news = await _run(
            marketaux_service.fetch_news_summary, ticker, limit=10
        )
        return {"analysis": analysis, "sentiment": sentiment, "news": news}

    async def fv_call() -> dict[str, Any]:
        return await fv_access.snapshot(to_finviz(ticker))

    pair = await ask(tv_call, fv_call)

    from .reconcile import compare_numeric

    tv_payload = pair.tv.payload or {}
    fv_payload = pair.fv.payload or {}
    analysis = tv_payload.get("analysis") or {}
    tv_price = (analysis.get("price_data") or {}).get("current_price")
    fv_price = (fv_payload.get("numeric") or {}).get("Price")

    data = {
        "symbol": ticker,
        "analysis": analysis,
        "sentiment": tv_payload.get("sentiment"),
        "news": tv_payload.get("news"),
        "price_cross_check": compare_numeric(tv_price, fv_price, tolerance_pct=0.5),
    }
    return finalize(
        pair,
        sources={
            TRADINGVIEW: "TradingView technical analysis + Marketaux sentiment",
            FINVIZ: f"https://finviz.com/stock?t={to_finviz(ticker)}",
        },
        data=data,
        health_warn=MONITOR.warnings_for(providers_in(pair) or (TRADINGVIEW, FINVIZ)),
        degraded=any(
            issue["provider"] in providers_in(pair) for issue in MONITOR.issues()
        ),
    )


_TOOLS = (
    (crypto_top_movers, "TradingView-only: crypto gainers/losers"),
    (crypto_bollinger_scan, "TradingView-only: Bollinger squeeze scan"),
    (crypto_volume_breakout, "TradingView-only: volume breakout scan"),
    (crypto_multi_timeframe, "TradingView-only: multi-timeframe crypto analysis"),
    (crypto_multi_agent, "TradingView-only: multi-agent debate"),
    (futures_market_overview, "TradingView-only: futures overview"),
    (futures_top_movers, "TradingView-only: futures movers"),
    (futures_watchlist, "TradingView-only: futures watchlist"),
    (stock_backtest, "TradingView-only: strategy backtest"),
    (stock_backtest_compare, "TradingView-only: strategy comparison"),
    (market_snapshot, "TradingView-only: global market snapshot"),
    (bitcoin_market_pulse, "TradingView-only: bitcoin pulse"),
    (market_sentiment, "TradingView-only: news sentiment"),
    (egx_market_overview, "TradingView-only: EGX overview"),
    (combined_analysis, "Cross-checked: TA + sentiment + price"),
)


def register(mcp) -> int:
    for fn, title in _TOOLS:
        mcp.add_tool(fn, name=fn.__name__, title=title, description=fn.__doc__)
    return len(_TOOLS)
