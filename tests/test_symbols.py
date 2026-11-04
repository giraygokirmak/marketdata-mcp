"""Tests for symbol normalisation across provider spellings."""
from __future__ import annotations

import pytest

from tapebasis.symbols import (
    INDEX_ALIASES,
    canonical_index,
    is_equity,
    strip_exchange,
    to_finviz,
    to_tradingview_candidates,
    to_yahoo,
)


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("NASDAQ:AAPL", "AAPL"),
        ("AAPL", "AAPL"),
        ("NASDAQ:BRK.B", "BRK.B"),
    ],
)
def test_strip_exchange(symbol, expected):
    assert strip_exchange(symbol) == expected


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("BRK-B", "BRK.B"),
        ("BRK.B", "BRK.B"),
        ("AAPL", "AAPL"),
        ("^GSPC", "SPX"),
        ("SPX", "SPX"),
    ],
)
def test_to_finviz(symbol, expected):
    assert to_finviz(symbol) == expected


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("BRK.B", "BRK-B"),
        ("BRK-B", "BRK-B"),
        ("AAPL", "AAPL"),
        ("SPX", "^GSPC"),
        ("COMP", "^IXIC"),
        ("DJI", "^DJI"),
    ],
)
def test_to_yahoo(symbol, expected):
    assert to_yahoo(symbol) == expected


@pytest.mark.parametrize(
    "symbol,canonical",
    [
        ("^GSPC", "SPX"),
        ("SPX", "SPX"),
        ("SP:SPX", "SPX"),
        ("^IXIC", "COMP"),
        ("COMP", "COMP"),
        ("^DJI", "DJI"),
        ("^VIX", "VIX"),
    ],
)
def test_canonical_index(symbol, canonical):
    assert canonical_index(symbol) == canonical


def test_russel_index_and_etf_are_not_aliased():
    """^RUT is the index; IWM is the tracking ETF — different price levels."""
    assert canonical_index("^RUT") == "RUT"
    assert canonical_index("IWM") == "IWM"
    assert INDEX_ALIASES["RUT"]["finviz"] is None


@pytest.mark.parametrize(
    "symbol,equity",
    [
        ("AAPL", True),
        ("BRK.B", True),
        ("BTC-USD", False),
        ("EURUSD=X", False),
        ("^GSPC", False),
        ("BTCUSDT", False),
    ],
)
def test_is_equity(symbol, equity):
    assert is_equity(symbol) is equity


def test_tradingview_candidates_cover_us_venues():
    assert to_tradingview_candidates("AAPL") == [
        "NASDAQ:AAPL",
        "NYSE:AAPL",
        "AMEX:AAPL",
    ]
    assert to_tradingview_candidates("NASDAQ:AAPL") == ["NASDAQ:AAPL"]
