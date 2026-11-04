"""Thin, typed access to the TradingView provider's service layer.

The vendored provider returns several different shapes: the scanner-based
screener, the Yahoo chart quote, and the technical-analysis engine. These
helpers present the shapes the merge layer needs and convert provider
exceptions into :class:`ProviderError` so the reconciler can label them.
"""
from __future__ import annotations

from typing import Any

from .providers_state import NoDataError, ProviderError
from .providers.tradingview.core.services import (
    screener_service,
    stock_screener_service,
    yahoo_finance_service,
)

PROVIDER = "TRADINGVIEW"

#: Scanner columns requested for the dual-source quote leg.
_QUOTE_COLUMNS = (
    "name",
    "description",
    "close",
    "change",
    "volume",
    "market_cap_basic",
    "price_earnings_ttm",
    "earnings_per_share_basic_ttm",
    "dividends_yield_current",
    "sector",
    "industry",
    "price_52_week_high",
    "price_52_week_low",
    "beta_1_year",
    "relative_volume_10d_calc",
    "RSI",
    "ATR",
    "earnings_release_next_date",
)

_PRICE_COLUMNS = (
    "name",
    "description",
    "close",
    "change",
    "volume",
    "currency",
    "market_cap_basic",
)


def _raise_provider_error(exc: BaseException, context: str) -> None:
    raise ProviderError(
        PROVIDER,
        f"{context}: {type(exc).__name__}: {exc}",
        code="PROVIDER_ERROR",
    ) from exc


def _scan(tickers: list[str], columns: tuple[str, ...]) -> list[dict[str, Any]]:
    from tradingview_screener import Query

    try:
        _total, frame = (
            Query()
            .set_tickers(*tickers)
            .select(*columns)
            .limit(len(tickers))
            .get_scanner_data(timeout=25)
        )
        return frame.to_dict("records")
    except Exception as exc:  # noqa: BLE001
        _raise_provider_error(exc, "TradingView scanner")


def scanner_quote(symbol: str) -> dict[str, Any]:
    """Scanner-backed quote for one symbol; raises ``NoDataError`` if absent."""
    candidates = [f"{venue}:{symbol}" for venue in ("NASDAQ", "NYSE", "AMEX")]
    rows = _scan(candidates, _QUOTE_COLUMNS)
    if not rows:
        raise NoDataError(PROVIDER, f"no scanner row for {symbol}")
    row = rows[0]
    row.setdefault("ticker", candidates[0])
    return row


def scanner_metrics(symbol: str) -> dict[str, Any]:
    """Scanner fundamentals (P/E, RSI, ATR, 52-week range) for one symbol.

    Best-effort: returns ``{}`` when the scanner has no row, so the chart quote
    can still stand on its own. This is what makes P/E and RSI genuinely
    dual-sourced rather than Finviz-only.
    """
    try:
        return scanner_quote(symbol)
    except (NoDataError, ProviderError):
        return {}


def scanner_prices(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Scanner-backed prices keyed by bare symbol."""
    rows = _scan(tickers, _PRICE_COLUMNS)
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name") or "").upper()
        if name:
            found[name] = row
    return found


async def chart_quote(symbol: str) -> dict[str, Any]:
    """Yahoo chart quote through the TradingView provider's quote engine."""
    data = await yahoo_finance_service.get_price_async(symbol)
    if not isinstance(data, dict) or data.get("error"):
        message = (
            (data or {}).get("error", "empty quote")
            if isinstance(data, dict)
            else "empty quote"
        )
        raise ProviderError(PROVIDER, f"quote failed: {message}", code="UPSTREAM_ERROR")
    return data


async def extended_hours(symbol: str) -> dict[str, Any]:
    from .providers.tradingview.core.services.extended_hours_service import (
        get_extended_hours_price_async,
    )

    data = await get_extended_hours_price_async(symbol)
    if not isinstance(data, dict) or data.get("error"):
        message = (
            (data or {}).get("error", "empty response")
            if isinstance(data, dict)
            else "empty response"
        )
        raise ProviderError(
            PROVIDER, f"extended-hours failed: {message}", code="UPSTREAM_ERROR"
        )
    return data


def technical_analysis_sync(
    symbol: str, exchange: str = "NASDAQ", timeframe: str = "1D"
) -> dict[str, Any]:
    """Run the provider's technical-analysis engine (blocking; call in a thread)."""
    try:
        return screener_service.analyze_coin(
            symbol, exchange=exchange, timeframe=timeframe
        )
    except Exception as exc:  # noqa: BLE001
        _raise_provider_error(exc, "technical analysis")


def _offload(fn, /, **kwargs):
    import asyncio

    return asyncio.to_thread(fn, **kwargs)


async def stock_screen(
    *,
    country: str = "america",
    stock_type: str = "common",
    limit: int = 50,
    exclude_otc: bool = True,
    sort_by: str = "market_cap",
) -> dict[str, Any]:
    """Whole-market stock screen (TradingView scanner)."""
    try:
        return await _offload(
            stock_screener_service.screen_stocks,
            country=country,
            stock_type=stock_type,
            limit=limit,
            exclude_otc=exclude_otc,
            compact=False,
            sort_by=sort_by,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_provider_error(exc, "stock screener")


async def market_snapshot() -> dict[str, Any]:
    return await yahoo_finance_service.get_market_snapshot_async()


async def options_chain(symbol: str, expiry: str | None = None) -> dict[str, Any]:
    from .providers.tradingview.core.services.options_service import get_options_chain

    data = await _offload(get_options_chain, symbol=symbol, expiry=expiry)
    if isinstance(data, dict) and data.get("error"):
        raise ProviderError(
            PROVIDER, f"options chain failed: {data['error']}", code="UPSTREAM_ERROR"
        )
    return data


async def market_news(
    symbol: str | None, category: str = "stocks", limit: int = 30
) -> dict[str, Any]:
    from .providers.tradingview.core.services.marketaux_service import (
        fetch_news_summary,
    )

    data = await _offload(
        fetch_news_summary, symbol=symbol, category=category, limit=limit
    )
    if isinstance(data, dict) and data.get("error"):
        raise ProviderError(
            PROVIDER, f"news failed: {data['error']}", code="NOT_CONFIGURED"
        )
    return data


def earnings_calendar(symbol: str) -> dict[str, Any]:
    """Next/last earnings timestamp from the scanner."""
    rows = _scan(
        [f"{venue}:{symbol}" for venue in ("NASDAQ", "NYSE", "AMEX")],
        ("name", "earnings_release_next_date", "earnings_release_date"),
    )
    if not rows:
        raise NoDataError(PROVIDER, f"no earnings calendar row for {symbol}")
    return rows[0]
