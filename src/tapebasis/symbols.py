"""Symbol normalisation and small parsing helpers shared by the tool layer.

The two providers spell the same instrument differently:

===========  =================  ===============
Instrument   TradingView/Yahoo  Finviz
===========  =================  ===============
Apple        ``AAPL``           ``AAPL``
Berkshire B  ``BRK-B``          ``BRK.B``
S&P 500      ``^GSPC``          ``SPX``
Nasdaq Comp  ``^IXIC``          ``COMP``
Dow          ``^DJI``           ``DJI``
===========  =================  ===============

:func:`to_yahoo`, :func:`to_finviz` and :func:`to_tradingview` bridge those
spellings so a single ``symbol`` argument reaches both providers correctly.
"""
from __future__ import annotations

import re

#: Index spellings, keyed by the canonical short name used in responses.
INDEX_ALIASES: dict[str, dict[str, str]] = {
    "SPX": {"yahoo": "^GSPC", "finviz": "SPX", "tradingview": "SP:SPX", "name": "S&P 500"},
    "COMP": {"yahoo": "^IXIC", "finviz": "COMP", "tradingview": "NASDAQ:IXIC", "name": "NASDAQ Composite"},
    "DJI": {"yahoo": "^DJI", "finviz": "DJI", "tradingview": "DJ:DJI", "name": "Dow Jones Industrial Average"},
    # Russell 2000 (the index) and IWM (the tracking ETF) are different
    # instruments with different price levels; they must never be compared.
    "RUT": {"yahoo": "^RUT", "finviz": None, "tradingview": "TVC:RUT", "name": "Russell 2000"},
    "IWM": {"yahoo": "IWM", "finviz": "IWM", "tradingview": "AMEX:IWM", "name": "iShares Russell 2000 ETF"},
    "VIX": {"yahoo": "^VIX", "finviz": None, "tradingview": "TVC:VIX", "name": "CBOE Volatility Index"},
}

_INDEX_LOOKUP: dict[str, str] = {}
for _canonical, _map in INDEX_ALIASES.items():
    _INDEX_LOOKUP[_canonical.lower()] = _canonical
    for _spelling in ("yahoo", "finviz"):
        _value = _map.get(_spelling)
        if _value:
            _INDEX_LOOKUP[_value.lower()] = _canonical
            _INDEX_LOOKUP[_value.lower().lstrip("^")] = _canonical

#: Yahoo-style crypto/FX tickers that Finviz has no equivalent for.
_NON_EQUITY = re.compile(r"(-USD|=X|^[A-Z]{3,5}USDT$|^\^)", re.IGNORECASE)


def canonical_index(symbol: str) -> str | None:
    """Map any index spelling (``^GSPC``, ``SPX``, ``SP:SPX``) to its short name."""
    key = symbol.strip().lower()
    if key in _INDEX_LOOKUP:
        return _INDEX_LOOKUP[key]
    if ":" in key:
        tail = key.split(":", 1)[1]
        if tail in _INDEX_LOOKUP:
            return _INDEX_LOOKUP[tail]
    return None


def strip_exchange(symbol: str) -> str:
    """``NASDAQ:AAPL`` -> ``AAPL``; leaves plain symbols untouched."""
    return symbol.split(":", 1)[1] if ":" in symbol else symbol


def to_finviz(symbol: str) -> str:
    """Convert a symbol to Finviz's spelling (``BRK-B`` -> ``BRK.B``)."""
    raw = strip_exchange(symbol).strip().upper()
    index = canonical_index(raw)
    if index and INDEX_ALIASES[index].get("finviz"):
        return str(INDEX_ALIASES[index]["finviz"])
    if raw and "-" in raw and len(raw) <= 7 and not raw.endswith("-USD"):
        head, _, tail = raw.partition("-")
        if head.isalpha() and tail.isalpha() and len(tail) <= 2:
            return f"{head}.{tail}"
    return raw


def to_yahoo(symbol: str) -> str:
    """Convert a symbol to Yahoo's spelling (``BRK.B`` -> ``BRK-B``)."""
    raw = strip_exchange(symbol).strip().upper()
    index = canonical_index(raw)
    if index:
        return str(INDEX_ALIASES[index]["yahoo"])
    if raw and "." in raw and len(raw) <= 7:
        head, _, tail = raw.partition(".")
        if head.isalpha() and tail.isalpha() and len(tail) <= 2:
            return f"{head}-{tail}"
    return raw


def is_equity(symbol: str) -> bool:
    """True when Finviz could plausibly have this instrument (US-listed equity/ETF)."""
    raw = strip_exchange(symbol).strip().upper()
    if not raw or _NON_EQUITY.search(raw):
        return False
    # Foreign listings use dotted suffixes Finviz does not carry.
    if "." in raw and not raw.replace(".", "").isalnum():
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", raw))


def to_tradingview_candidates(symbol: str) -> list[str]:
    """Candidate ``EXCHANGE:SYMBOL`` spellings for a TradingView scanner lookup."""
    raw = strip_exchange(symbol).strip().upper()
    if ":" in symbol:
        return [symbol.strip().upper()]
    return [f"{venue}:{raw}" for venue in ("NASDAQ", "NYSE", "AMEX")]
