"""Shared fixtures for the hermetic test suite.

No test in this directory touches a live upstream: provider calls are replaced
with deterministic fakes, and the health state is redirected to a temp directory.
"""
from __future__ import annotations

import os

import pytest

# Health state must never be written to the real home directory from tests.
os.environ.setdefault("MARKETDATA_HEALTH_STATE", "/tmp/marketdata-mcp-test/health.json")
os.environ.setdefault("MARKETDATA_HEALTH_ENABLED", "false")


@pytest.fixture(autouse=True)
def _isolate_health_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETDATA_HEALTH_STATE", str(tmp_path / "health.json"))
    from marketdata_mcp import health

    monitor = health.HealthMonitor()
    monkeypatch.setattr(health, "HealthMonitor", lambda *a, **k: monitor)

    from marketdata_mcp import tools_market, tools_finviz, tools_tradingview

    for module in (tools_market, tools_finviz, tools_tradingview):
        monkeypatch.setattr(module, "MONITOR", monitor, raising=False)
    yield monitor


@pytest.fixture
def tv_quote():
    """A representative TradingView/Yahoo quote payload."""
    return {
        "price": 331.9,
        "previous_close": 338.32,
        "change": -6.42,
        "change_pct": -1.9,
        "currency": "USD",
        "exchange": "NMS",
        "market_state": "CLOSED",
        "52w_high": 351.73,
        "52w_low": 243.42,
        "price_source": "quote",
        "quote_stale": False,
        "source": "Yahoo Finance",
        # regularMarketTime for the 2026-11-03 US close.
        "market_time": "2026-11-03T21:00:01+00:00",
    }


@pytest.fixture
def fv_quote():
    """A representative Finviz ``get_quote`` payload (trimmed to what matters)."""
    return {
        "ticker": "AAPL",
        "company": "Apple Inc",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "USA",
        "last_close": {
            "label": "Last Close",
            "price": 331.9,
            "date": "Nov 03 • 3:59 PM ET",
            "change": -6.42,
            "change_percent": -1.9,
        },
        "after_hours": {"price": 329.8, "change_percent": -0.63},
        "snapshot": {},
        "snapshot_numeric": {
            "Prev Close": 338.32,
            "Market Cap": 4.84381e12,
            "P/E": 36.98,
            "RSI (14)": 41.7,
            "Volume": 63160515.0,
            "52W High": 351.73,
            "52W Low": 243.42,
            "ATR (14)": 7.44,
        },
        "profile": "Apple Inc. designs and sells consumer electronics.",
        "peers": [{"ticker": "MSFT", "company": "Microsoft Corp"}],
    }
