"""Hermetic end-to-end tests of the dual-source tools with faked providers.

These assert the *contract* the server promises: dual-source when both answer,
single-source (explicitly labelled) when one fails, ``NONE`` when both fail,
and drift warnings propagated into ordinary tool responses.
"""
from __future__ import annotations

import pytest

from tapebasis import tools_finviz, tools_market


@pytest.fixture
def fake_providers(monkeypatch, tv_quote, fv_quote):
    """Patch both access layers with deterministic in-memory providers."""
    from tapebasis import fv_access, tv_access

    state = {"tv": tv_quote, "fv": fv_quote}

    async def tv_chart_quote(symbol):
        if isinstance(state["tv"], Exception):
            raise state["tv"]
        return state["tv"]

    async def fv_quote_call(symbol):
        if isinstance(state["fv"], Exception):
            raise state["fv"]
        return state["fv"]

    monkeypatch.setattr(tv_access, "chart_quote", tv_chart_quote)
    monkeypatch.setattr(tv_access, "scanner_metrics", lambda symbol: {})
    monkeypatch.setattr(fv_access, "quote", fv_quote_call)
    return state


@pytest.mark.asyncio
async def test_quote_both_when_providers_agree(fake_providers):
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "BOTH"
    assert result["verification"] == "VERIFIED"
    assert result["errors"] == []
    fields = {row["field"]: row["match"] for row in result["comparisons"]}
    assert fields["price"] is True
    assert result["data"]["price"] == pytest.approx(331.9)


@pytest.mark.asyncio
async def test_quote_labels_single_source_when_finviz_fails(fake_providers):
    fake_providers["fv"] = RuntimeError("finviz outage")
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "TRADINGVIEW"
    assert result["verification"] == "UNVERIFIED"
    assert "NOT cross-verified" in result["notice"]
    assert result["errors"][0]["provider"] == "FINVIZ"
    # The surviving provider's value is still served.
    assert result["data"]["price"] == pytest.approx(331.9)


@pytest.mark.asyncio
async def test_quote_labels_single_source_when_tradingview_fails(fake_providers):
    fake_providers["tv"] = RuntimeError("tradingview outage")
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "FINVIZ"
    assert result["verification"] == "UNVERIFIED"
    assert "NOT cross-verified" in result["notice"]
    assert result["data"]["price"] == pytest.approx(331.9)


@pytest.mark.asyncio
async def test_quote_none_when_both_fail(fake_providers):
    fake_providers["tv"] = RuntimeError("tv down")
    fake_providers["fv"] = RuntimeError("fv down")
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "NONE"
    assert result["verification"] == "UNAVAILABLE"
    assert result["data"] == {}
    assert {e["provider"] for e in result["errors"]} == {"TRADINGVIEW", "FINVIZ"}


@pytest.mark.asyncio
async def test_quote_flags_a_real_mismatch_instead_of_hiding_it(fake_providers):
    fake_providers["tv"]["price"] = 300.0  # 10% off Finviz's 331.9
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "BOTH"  # both answered...
    price_row = next(r for r in result["comparisons"] if r["field"] == "price")
    assert price_row["match"] is False  # ...but they do not agree
    assert "disagree" in result["notice"]
    assert any("disagree" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_finviz_only_tool_is_explicitly_single_source(monkeypatch):
    from tapebasis import fv_access

    async def fake_why(symbol):
        return {"ticker": "AAPL", "headline": "AAPL rises on product news"}

    monkeypatch.setattr(fv_access, "why_moving", fake_why)
    result = await tools_finviz.stock_why_moving("AAPL")
    assert result["data_provider"] == "FINVIZ"
    assert result["verification"] == "UNVERIFIED"
    assert "NOT cross-verified" in result["notice"]
    assert result["data"]["headline"].startswith("AAPL rises")


@pytest.mark.asyncio
async def test_finviz_only_tool_reports_none_on_failure(monkeypatch):
    from tapebasis import fv_access
    from tapebasis.providers_state import ProviderError

    async def broken(symbol):
        raise ProviderError("FINVIZ", "upstream blocked", code="UPSTREAM_BLOCKED")

    monkeypatch.setattr(fv_access, "why_moving", broken)
    result = await tools_finviz.stock_why_moving("AAPL")
    assert result["data_provider"] == "NONE"
    assert result["errors"][0]["code"] == "UPSTREAM_BLOCKED"


@pytest.mark.asyncio
async def test_drift_warning_reaches_normal_tool_responses(fake_providers, _isolate_health_state):
    from tapebasis import health

    _isolate_health_state.results = {
        "p": health.ProbeResult(
            id="p",
            provider="TRADINGVIEW",
            description="scanner",
            url="https://scanner",
            status=health.STATUS_DRIFT,
            message="payload shape changed",
        )
    }
    result = await tools_market.stock_quote("AAPL")
    assert result["degraded"] is True
    assert any("DRIFT" in warning for warning in result["warnings"])
    assert "WARNING" in result["notice"]


# ── one price, freshest print ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quote_returns_a_single_price_and_explains_which(fake_providers):
    """One price, and the reason it is that one, in the notice."""
    result = await tools_market.stock_quote("AAPL")
    assert isinstance(result["data"]["price"], (int, float))
    assert result["freshness"]["chosen"] == "TRADINGVIEW"
    assert result["freshness"]["tradingview"] == "2026-11-03T21:00:01+00:00"
    assert result["freshness"]["finviz"] == "2026-11-03T20:59:00+00:00"
    assert "printed more recently than the other provider" in result["notice"]


@pytest.mark.asyncio
async def test_quote_prefers_finviz_when_its_print_is_newer(fake_providers):
    """Freshness, not provider identity, decides. Reverse the timestamps."""
    fake_providers["tv"]["market_time"] = "2026-11-03T20:00:00+00:00"
    fake_providers["fv"]["last_close"]["date"] = "Nov 03 • 3:59 PM ET"
    result = await tools_market.stock_quote("AAPL")
    assert result["freshness"]["chosen"] == "FINVIZ"
    assert result["data"]["price"] == pytest.approx(331.9)


@pytest.mark.asyncio
async def test_both_values_survive_in_comparisons_after_selection(fake_providers):
    """Selecting one value must not discard the audit trail."""
    fake_providers["fv"]["last_close"]["price"] = 340.0
    result = await tools_market.stock_quote("AAPL")
    row = next(c for c in result["comparisons"] if c["field"] == "price")
    assert row["tv"] == pytest.approx(331.9)
    assert row["fv"] == pytest.approx(340.0)
    assert row["match"] is False
    assert row["chosen"] == "TRADINGVIEW"
    # The fresher value is the one presented.
    assert result["data"]["price"] == pytest.approx(331.9)
    assert "Providers disagree on: price" in result["warnings"]
