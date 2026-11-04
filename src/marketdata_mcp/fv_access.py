"""Thin, typed access to the Finviz provider's service layer."""
from __future__ import annotations

from typing import Any

from .providers_state import NoDataError, ProviderError, error_code
from .providers.finviz.core.services import (
    chart_service,
    filings_service,
    insider_service,
    market_service,
    news_service,
    quote_service,
    screener_service,
)
from .providers.finviz.core.services import finviz_client

PROVIDER = "FINVIZ"


def _translate(exc: BaseException, context: str) -> None:
    code_value = error_code(exc)
    message = getattr(exc, "message", None) or str(exc)
    if code_value == "NO_DATA":
        raise NoDataError(PROVIDER, message) from exc
    raise ProviderError(PROVIDER, f"{context}: {message}", code=code_value) from exc


async def _call(coro, context: str):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _translate(exc, context)


def _round_floats(value: Any) -> Any:
    """Collapse float32 noise from Finviz's JSON before it reaches a caller.

    Finviz serialises prices as 32-bit floats, so a real 331.90 arrives as
    331.8999938964844. That is harmless while the number is only compared, but
    it is the number shown to a user when this provider wins on freshness — so
    it is rounded at the boundary, to 4 decimals (well below a cent) rather
    than to 2, which would lose real precision on ratios and percentages.
    """
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


async def quote(symbol: str) -> dict[str, Any]:
    return await _call(quote_service.get_quote(symbol), "quote")


async def screener_rows(tickers: list[str], *, columns: str | None = None) -> list[dict[str, Any]]:
    """Screener rows for an explicit ticker list (bulk quote / cross-check leg)."""
    joined = ",".join(tickers)
    data = await _call(
        screener_service.screen(
            tickers=joined,
            order="-change",
            view="111",
            page=1,
            columns=columns or "0,1,2,3,4,5,6,7,8,9,10",
        ),
        "screener rows",
    )
    return [_round_floats(row) for row in (data.get("rows") or [])]


async def screen(
    *,
    tickers: str | None = None,
    filters: str | None = None,
    order: str = "-change",
    view: str = "111",
    page: int = 1,
    columns: str | None = None,
) -> dict[str, Any]:
    return await _call(
        screener_service.screen(
            tickers=tickers, filters=filters, order=order, view=view, page=page, columns=columns
        ),
        "screener",
    )


async def screener_filters() -> dict[str, Any]:
    return await _call(screener_service.get_screener_filters(), "screener filters")


async def indices() -> dict[str, Any]:
    return await _call(market_service.get_market_indices(), "indices")


async def news(symbol: str, limit: int = 30, include_reactions: bool = True) -> dict[str, Any]:
    return await _call(
        news_service.get_news(symbol, limit=limit, include_reactions=include_reactions),
        "news",
    )


async def options(symbol: str, expiry: str | None = None) -> dict[str, Any]:
    return await _call(market_service.get_options_chain(symbol, expiry), "options")


async def earnings(symbol: str) -> dict[str, Any]:
    return await _call(quote_service.get_earnings(symbol), "earnings")


async def filings(symbol: str, page: int = 1, page_size: int = 30, forms: str | None = None, sort: str = "-filingDate") -> dict[str, Any]:
    return await _call(
        filings_service.get_filings(
            symbol, page=page, page_size=page_size, forms=forms, sort=sort
        ),
        "filings",
    )


async def insider(symbol: str, limit: int = 40) -> dict[str, Any]:
    return await _call(insider_service.get_insider_trading(symbol, limit=limit), "insider")


async def institutional(symbol: str, limit: int = 20) -> dict[str, Any]:
    return await _call(
        insider_service.get_institutional_ownership(symbol, limit=limit), "institutional"
    )


async def short_interest(symbol: str, limit: int = 60) -> dict[str, Any]:
    return await _call(quote_service.get_short_interest(symbol, limit=limit), "short interest")


async def dilution(symbol: str) -> dict[str, Any]:
    return await _call(quote_service.get_dilution(symbol), "dilution")


async def peers(symbol: str, limit: int = 12) -> dict[str, Any]:
    return await _call(quote_service.get_peers(symbol, limit=limit), "peers")


async def snapshot(symbol: str) -> dict[str, Any]:
    return await _call(quote_service.get_snapshot(symbol), "snapshot")


async def why_moving(symbol: str) -> dict[str, Any]:
    return await _call(market_service.get_why_moving(symbol), "why moving")


async def chart(
    symbol: str,
    *,
    period: str = "d",
    chart_type: str = "c",
    scale: str = "l",
    width: int = 900,
    height: int = 500,
    technicals: bool = True,
) -> dict[str, Any]:
    return await _call(
        chart_service.download_chart(
            symbol,
            period=period,
            chart_type=chart_type,
            scale=scale,
            width=width,
            height=height,
            technicals=technicals,
        ),
        "chart",
    )


def cache_size() -> dict[str, int]:
    return finviz_client.cache_size()
