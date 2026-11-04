"""
Quote + fundamentals services — the ``/stock?t={ticker}`` document route.

finviz has no JSON endpoint for a single ticker's quote (``/api/quote`` answers
``{"error":"invalid quote query"}`` for every parameter spelling), so this is
the one place where the server must read the rendered page. A single fetch of
``/stock?t=VCIG&ta=1&p=d`` carries all of it:

* the ticker header (last close / after-hours, sector/industry/country chips),
* the 84-field snapshot table (6 ``table.js-snapshot-table`` blocks),
* the company profile paragraph,
* the Peers row,
* ``why-stock-moving-init-data`` / ``institutional-ownership-init-data`` blocks.

The tab routes layer on the same page with ``ty=``:

======================  ==========================================
``ty=si``               Short interest history (route-init-data)
``ty=ea``               Earnings history + revisions + FA series
``ty=oc``               Options chain (route-init-data)
``ty=lf``               Latest filings (route-init-data)
``ty=di``               Dilution risk model (route-init-data)
======================  ==========================================
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from marketdata_mcp.providers.finviz.core.errors import ErrorCode, FinvizServiceError
from marketdata_mcp.providers.finviz.core.services import finviz_client
from marketdata_mcp.providers.finviz.core.services.html_utils import (
    extract_script_json,
    parse_peers,
    parse_profile,
    parse_quote_header,
    parse_snapshot,
)
from marketdata_mcp.providers.finviz.core.utils.validators import validate_ticker

#: Snapshot labels that are genuinely numeric. Everything else stays a string
#: because finviz renders ranges ("-25.75% 32.62%") and suffixes ("0.56M") that
#: do not survive a blind float() cast.
_NUMERIC_SUFFIXES = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _stock_url(ticker: str, *, tab: str | None = None) -> str:
    url = f"{finviz_client.BASE_URL}/stock?t={ticker}&ta=1&p=d"
    if tab:
        url += f"&ty={tab}"
    return url


def coerce_snapshot_value(raw: str) -> Any:
    """Best-effort numeric coercion for one snapshot cell.

    Rules, in order:
        * ``-`` / ``--`` / empty          → ``None``
        * ``0.56M``, ``1.2B``, ``12K``    → scaled float
        * ``-25.75%``                     → float (percent, sign preserved)
        * ``-25.75% 32.62%``              → list of floats (past 3Y / 5Y cells)
        * ``Nov 02 3:45 PM``              → untouched string
    """
    if raw is None:
        return None
    text = raw.strip()
    if text in {"", "-", "--", "N/A", "n/a"}:
        return None

    def _one(token: str) -> Any:
        token = token.strip()
        if token in {"", "-", "--"}:
            return None
        negative = token.startswith("(") and token.endswith(")")
        if negative:
            token = token[1:-1]
        if token.endswith("%"):
            token = token[:-1]
        multiplier = 1.0
        if token and token[-1].upper() in _NUMERIC_SUFFIXES and token[:-1].replace(
            ".", "", 1
        ).replace("-", "", 1).replace(",", "").isdigit():
            multiplier = _NUMERIC_SUFFIXES[token[-1].upper()]
            token = token[:-1]
        try:
            value = float(token.replace(",", "")) * multiplier
        except ValueError:
            return text
        return -value if negative else value

    parts = text.split()
    if len(parts) > 1 and all(
        p.endswith("%") or p in {"-", "--"} for p in parts
    ):
        coerced = [_one(p) for p in parts]
        return coerced
    return _one(text)


def _numbers(fields: dict[str, str]) -> dict[str, Any]:
    return {label: coerce_snapshot_value(value) for label, value in fields.items()}


def _resolved_warning(requested: str, header: dict[str, Any]) -> dict[str, Any] | None:
    """finviz silently redirects delisted/renamed symbols (XXXX → SPYU)."""
    actual = (header.get("ticker") or "").upper()
    if actual and actual != requested:
        return {
            "requested_ticker": requested,
            "resolved_ticker": actual,
            "note": "finviz resolved this symbol to a different instrument "
            "(renamed, merged or reused ticker)",
        }
    return None


async def get_quote(ticker: str) -> dict[str, Any]:
    """Full overview for one ticker: header + snapshot + profile + peers.

    Costs exactly one upstream page fetch (cached for ``FINVIZ_CACHE_TTL``s).

    Raises:
        FinvizServiceError: ``INVALID_TICKER`` or ``NO_DATA`` for unknown symbols.
    """
    symbol = validate_ticker(ticker)
    html = await finviz_client.fetch_html(_stock_url(symbol), elite_check=False)

    header = parse_quote_header(html)
    snapshot = parse_snapshot(html)
    profile = parse_profile(html)
    peers = parse_peers(html)

    if not header.get("ticker") or not snapshot["fields"]:
        raise FinvizServiceError(
            ErrorCode.NO_DATA,
            f"finviz.com returned no quote data for {symbol}",
            ticker=symbol,
        )

    payload: dict[str, Any] = {
        "ticker": header["ticker"],
        "company": header.get("company"),
        "website": header.get("website"),
        "logo_url": header.get("logo_url"),
        "exchange": header.get("exchange"),
        "sector": header.get("sector"),
        "industry": header.get("industry"),
        "country": header.get("country"),
        "market_cap_class": header.get("market_cap_class"),
        "last_close": header.get("last_close"),
        "after_hours": header.get("after_hours"),
        "snapshot": snapshot["fields"],
        "snapshot_numeric": _numbers(snapshot["fields"]),
        "snapshot_pairs": snapshot["pairs"],
        "descriptions": snapshot["descriptions"],
        "profile": profile,
        "peers": peers,
        "source": _stock_url(symbol),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    warning = _resolved_warning(symbol, header)
    if warning:
        payload["ticker_warning"] = warning
    return payload


async def get_snapshot(ticker: str) -> dict[str, Any]:
    """The 84 snapshot metrics only, with finviz's own metric descriptions.

    Use this when an agent needs fundamentals but not prices/peers — the
    payload is a third of :func:`get_quote` and far easier to reason about.
    """
    symbol = validate_ticker(ticker)
    html = await finviz_client.fetch_html(_stock_url(symbol))
    snapshot = parse_snapshot(html)
    if not snapshot["fields"]:
        raise FinvizServiceError(
            ErrorCode.NO_DATA, f"No snapshot table for {symbol}", ticker=symbol
        )
    return {
        "ticker": symbol,
        "fields": snapshot["fields"],
        "numeric": _numbers(snapshot["fields"]),
        "pairs": snapshot["pairs"],
        "descriptions": snapshot["descriptions"],
        "field_count": len(snapshot["fields"]),
    }


async def get_peers(ticker: str, limit: int = 12) -> dict[str, Any]:
    """Peer group from the page's Peers row.

    Note: ``/api/related-tickers`` is **not** used here. That endpoint ignores
    its ``ticker`` parameter and returns the same mega-cap list (AAPL, NVDA,
    MSFT, ...) for every symbol, so it is useless as a peer source.
    """
    symbol = validate_ticker(ticker)
    html = await finviz_client.fetch_html(_stock_url(symbol))
    peers = parse_peers(html)[: max(1, limit)]
    if not peers:
        raise FinvizServiceError(
            ErrorCode.NO_DATA, f"finviz.com lists no peers for {symbol}", ticker=symbol
        )
    return {"ticker": symbol, "peers": peers, "count": len(peers)}


async def get_short_interest(ticker: str, limit: int = 60) -> dict[str, Any]:
    """Short-interest history from the ``ty=si`` tab's ``route-init-data`` block.

    Each series point is a bi-weekly FINRA settlement snapshot:
    ``shortInterest`` (shares short, in thousands), ``sharesFloat`` (thousands)
    and ``averageVolume``. Take the last element for the current reading.

    Raises:
        FinvizServiceError: ``NO_DATA`` when the tab carries no series (common
            for very recent IPOs).
    """
    symbol = validate_ticker(ticker)
    html = await finviz_client.fetch_html(_stock_url(symbol, tab="si"))
    series = extract_script_json(html, "route-init-data")
    if not isinstance(series, list) or not series:
        raise FinvizServiceError(
            ErrorCode.NO_DATA,
            f"No short-interest series for {symbol} (tab may be empty for recent IPOs)",
            ticker=symbol,
        )

    points = []
    for row in series[-max(1, limit) :]:
        stamp = row.get("timestamp")
        points.append(
            {
                "date": datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
                if isinstance(stamp, (int, float))
                else None,
                "short_interest_shares_k": row.get("shortInterest"),
                "shares_float_k": row.get("sharesFloat"),
                "average_volume_k": row.get("averageVolume"),
            }
        )

    latest = points[-1] if points else None
    floats = latest["shares_float_k"] if latest else None
    short_pct = None
    if latest and latest["short_interest_shares_k"] and floats:
        short_pct = round(latest["short_interest_shares_k"] / floats * 100, 2)

    return {
        "ticker": symbol,
        "latest": latest,
        "short_float_percent": short_pct,
        "history": points,
        "points": len(points),
        "source": _stock_url(symbol, tab="si"),
    }


def _has_fundamental_series(fa: Any) -> bool:
    """True when the FA chart island actually carries points.

    finviz renders an empty island (``{"annual": {"values": [[],[],[]]}, ...}``)
    for tickers with no reported statements — common for recent IPOs and
    foreign issuers filing 6-K only. That shape is truthy but meaningless, so it
    is rejected explicitly instead of returning zeros.
    """
    if not isinstance(fa, dict):
        return False
    for period in fa.values():
        if not isinstance(period, dict):
            continue
        for series in period.get("values") or []:
            if isinstance(series, list) and series:
                return True
    return False


async def get_earnings(ticker: str) -> dict[str, Any]:
    """Earnings calendar + reported results + estimate revisions (``ty=ea``).

    The page carries a 2 MB ``route-init-data`` island for large caps; the
    payload is trimmed here to the parts an agent actually uses:

    * ``next_earnings_date`` / ``earnings_date``
    * ``reported``  — last 12 quarterly rows (actual vs estimate)
    * ``annual``    — annual history
    * ``revisions`` — most recent 200 estimate-revision rows
    * ``price_reactions`` — post-earnings moves
    * ``fundamental_series`` — the ``fa-init-data-0`` chart series
    """
    symbol = validate_ticker(ticker)
    html = await finviz_client.fetch_html(_stock_url(symbol, tab="ea"))
    data = extract_script_json(html, "route-init-data")
    fa = extract_script_json(html, "fa-init-data-0")
    if not isinstance(data, dict):
        raise FinvizServiceError(
            ErrorCode.NO_DATA, f"No earnings data for {symbol}", ticker=symbol
        )

    quarterly = data.get("earningsData") or []
    annual = data.get("earningsAnnualData") or []
    revisions = data.get("earningsRevisionsData") or []
    if isinstance(revisions, dict):  # legacy shape
        revisions = []
    reactions = data.get("priceReactionData") or []

    if not quarterly and not annual and not _has_fundamental_series(fa):
        raise FinvizServiceError(
            ErrorCode.NO_DATA,
            f"finviz.com has no earnings history for {symbol} "
            "(no reported quarters, no annual history and an empty "
            "fundamental series — typical for a recent IPO or a foreign "
            "private issuer that only files 6-K)",
            ticker=symbol,
        )

    return {
        "ticker": symbol,
        "earnings_date": data.get("earningsDate"),
        "quarterly": quarterly[-12:],
        "annual": annual,
        "revisions": revisions[-200:],
        "price_reactions": reactions,
        "fundamental_series": fa if _has_fundamental_series(fa) else None,
        "counts": {
            "quarterly": len(quarterly),
            "annual": len(annual),
            "revisions": len(revisions),
        },
        "source": _stock_url(symbol, tab="ea"),
    }


async def get_dilution(ticker: str) -> dict[str, Any]:
    """Dilution-risk model from the ``ty=di`` tab (share count, float, cash runway).

    Finviz ships this as a versioned JSON island (``schema_version: 1``) aimed
    at the Dilution page, but only for companies whose share count Finviz
    considers dilution-prone: large caps return ``null`` for the island and
    render the *income statement breakdown* instead
    (``income-breakdown-init-data-0``). Both shapes are handled — the response
    always carries ``company`` (possibly ``None``) and adds whichever data the
    tab actually had.
    """
    symbol = validate_ticker(ticker)
    html = await finviz_client.fetch_html(_stock_url(symbol, tab="di"))
    data = extract_script_json(html, "route-init-data")
    breakdown = extract_script_json(html, "income-breakdown-init-data-0")

    if isinstance(data, dict) and "company" in data:
        data.setdefault("source", _stock_url(symbol, tab="di"))
        data.setdefault("model_available", True)
        return data

    if isinstance(breakdown, dict) and breakdown.get("statement"):
        return {
            "ticker": symbol,
            "company": None,
            "model_available": False,
            "note": "finviz did not flag this company as dilution-prone, so the "
            "Dilution model is empty; the tab's income-statement breakdown is "
            "returned instead. Run finviz_quote / finviz_snapshot for share "
            "count and float, or finviz_filings for S-1 / 424B5 offerings.",
            "income_breakdown": breakdown,
            "source": _stock_url(symbol, tab="di"),
        }

    raise FinvizServiceError(
        ErrorCode.NO_DATA,
        f"finviz.com has neither a dilution model nor an income breakdown for {symbol}",
        ticker=symbol,
    )
