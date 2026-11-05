"""Bloomberg tools, served from bloomberg.com and explicitly single-source.

Bloomberg is not a reconciliation leg, and these tools do not pretend
otherwise. Every response is labelled ``BLOOMBERG`` with
``verification: SINGLE_SOURCE``, because Bloomberg would not strengthen a
cross-check even if it were asked to join one:

* equity quotes there are delayed (``exchangeDelay`` is ``15 Minutes``),
* its static fields are penny-identical to Yahoo's, so it is not an independent
  reading of the same number,
* its ``lastUpdate`` is a session sentinel, so using it as a print time would
  make a stale page look fresher than a live print.

What it does add is data no other provider here publishes: the government
benchmark yield curve, plus index, commodity and FX levels on the same board.
That is why it earns a label of its own instead of being folded into ``ALL``.
"""
from __future__ import annotations

from typing import Any

from . import bb_access
from .health import MONITOR
from .providers_state import DataProvider, NoDataError, envelope, error_code

#: Bloomberg is outside the reconciliation set, so no health probe exists for
#: it (httpx cannot reproduce the TLS fingerprint that gets past the bot wall).
#: Callers are told that, instead of being handed a silent "OK".
_NO_PROBE = (
    "Bloomberg is not covered by the upstream health monitor: its site gates "
    "plain HTTP clients on the TLS fingerprint, so an httpx probe would report a "
    "false outage. This response is the availability signal."
)


def _label(
    data: Any,
    *,
    source: str,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a Bloomberg payload in the standard envelope, labelled BLOOMBERG."""
    MONITOR.ensure_started()
    return envelope(
        data,
        provider=DataProvider.BLOOMBERG,
        sources={bb_access.PROVIDER: source},
        warnings=[_NO_PROBE, *(warnings or [])],
        providers_answered=[bb_access.PROVIDER],
        extra={
            "cross_checked": False,
            "reconciliation_leg": False,
            **(extra or {}),
        },
    )


def _failed(exc: BaseException, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    code = error_code(exc)
    if isinstance(exc, NoDataError):
        # ``NoDataError`` carries no code attribute; a page that exists but has
        # nothing to quote is "no data", not a provider failure.
        code = "NO_DATA"
    message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
    return envelope(
        None,
        provider=DataProvider.NONE,
        errors=[{"provider": bb_access.PROVIDER, "code": code, "message": message}],
        warnings=[f"Bloomberg could not serve this request: {message}"],
        extra={"cross_checked": False, "reconciliation_leg": False, **(extra or {})},
    )


async def bloomberg_quote(symbol: str) -> dict[str, Any]:
    """Bloomberg's own quote page for one instrument (single-source).

    Ask for this when the caller wants Bloomberg's reading specifically. For a
    corroborated price, use ``stock_quote``: Bloomberg's equity quotes are
    delayed and are not counted as an independent cross-check here.

    ``print_time`` comes from the last intraday point, never from
    ``lastUpdate``, which is a session sentinel on equity pages. When the
    intraday series does not end at the quoted price, ``print_time`` is null
    with the reason in ``print_time_missing_reason``, rather than a timestamp
    that describes a different number.

    Args:
        symbol: Anything the instrument is commonly called (``AAPL``, ``BRK.B``,
            ``SPX``, ``EURUSD``, ``CL1``, ``BTCUSD``, ``XAUUSD``) or an id in
            Bloomberg's own spelling (``AAPL:US``, ``SPX:IND``).

    Returns:
        Envelope labelled ``BLOOMBERG`` / ``SINGLE_SOURCE`` whose ``data``
        carries the price, day change, ranges, volume, ratios and the delay
        fields ``exchange_delay`` / ``is_exchange_delayed``.
    """
    try:
        data, source = await bb_access.quote(symbol)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc, extra={"symbol": symbol})
    warnings = []
    if data.get("print_time") is None and data.get("print_time_missing_reason"):
        warnings.append(
            "No usable print time: " + str(data["print_time_missing_reason"])
        )
    if data.get("is_exchange_delayed"):
        warnings.append(
            "Bloomberg marks this quote as exchange-delayed"
            + (f" ({data['exchange_delay']})" if data.get("exchange_delay") else "")
            + "; do not present it as a live price."
        )
    return _label(data, source=source, warnings=warnings, extra={"symbol": symbol})


async def bloomberg_markets() -> dict[str, Any]:
    """Bloomberg's market board: index levels, government yields, commodities, FX.

    The government benchmark yields are the reason this tool exists: no other
    provider in this server publishes a yield curve. Index, commodity and FX
    levels ride along as context and are labelled single-source like the rest.

    The board carries no per-instrument print time, so the response reports
    ``print_time: null`` rather than implying a live tape, and no level here is
    aged or ordered.

    Returns:
        Envelope labelled ``BLOOMBERG`` / ``SINGLE_SOURCE`` whose ``data`` holds
        ``indices``, ``government_yields``, ``commodities`` and ``fx`` lists.
    """
    try:
        data, source = await bb_access.board()
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)
    return _label(
        data,
        source=source,
        warnings=[
            "Bloomberg's market board carries no per-instrument print time, so no "
            "reading here is aged or ordered; treat every level as a snapshot."
        ],
        extra={"print_time": None},
    )


_TOOLS = (
    (bloomberg_quote, "Bloomberg-only: quote page (single-source, delayed)"),
    (bloomberg_markets, "Bloomberg-only: index, government yield, commodity and FX board"),
)


def register(mcp) -> int:
    for fn, title in _TOOLS:
        mcp.add_tool(fn, name=fn.__name__, title=title, description=fn.__doc__)
    return len(_TOOLS)
