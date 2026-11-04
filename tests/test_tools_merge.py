"""Hermetic end-to-end tests of the multi-source tools with faked providers.

These assert the *contract* the server promises: multi-source (``ALL``) when
every provider asked answers, single-source (explicitly labelled) when only one
does, ``NONE`` when none do, one price per field, and drift warnings propagated
into ordinary tool responses.
"""
from __future__ import annotations

import pytest

from tapebasis import tools_finviz, tools_market
from tapebasis.symbols import Resolution


def _resolved(provider: str, symbol: str | None, *, query: str = "AAPL") -> Resolution:
    """A synthetic resolution so no test ever touches a symbol-search endpoint."""
    return Resolution(
        query=query,
        provider=provider,
        symbol=symbol,
        method="explicit" if symbol else "none",
        confidence=1.0 if symbol else 0.0,
        note="" if symbol else f"{provider} does not carry {query}",
    )


@pytest.fixture
def fake_providers(monkeypatch, tv_quote, fv_quote, yh_quote):
    """Patch all three access layers with deterministic in-memory providers."""
    from tapebasis import fv_access, tv_access, yh_access

    state = {"tv": tv_quote, "fv": fv_quote, "yh": yh_quote}

    async def tv_quote_call(symbol):
        if isinstance(state["tv"], Exception):
            raise state["tv"]
        return state["tv"]

    async def fv_quote_call(symbol):
        if isinstance(state["fv"], Exception):
            raise state["fv"]
        return state["fv"]

    async def yh_quote_call(symbol):
        if isinstance(state["yh"], Exception):
            raise state["yh"]
        return state["yh"]

    async def fake_resolve(query, provider, **kwargs):
        symbol = {"TRADINGVIEW": "NASDAQ:AAPL", "FINVIZ": "AAPL", "YAHOO": "AAPL"}.get(provider)
        return _resolved(provider, symbol, query=query)

    monkeypatch.setattr(tv_access, "quote", tv_quote_call)
    monkeypatch.setattr(fv_access, "quote", fv_quote_call)
    monkeypatch.setattr(yh_access, "quote", yh_quote_call)
    monkeypatch.setattr(tools_market, "resolve", fake_resolve)
    return state


@pytest.mark.asyncio
async def test_quote_all_when_every_provider_answers(fake_providers):
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "ALL"
    assert result["verification"] == "MULTI_SOURCE"
    assert result["providers_answered"] == ["TRADINGVIEW", "FINVIZ", "YAHOO"]
    assert result["errors"] == []
    fields = {row["field"]: row["match"] for row in result["comparisons"]}
    assert fields["price"] is True
    assert result["data"]["price"] == pytest.approx(331.9)


@pytest.mark.asyncio
async def test_all_is_reported_for_two_of_three(fake_providers):
    """Coverage means every provider *asked* answered, not that all of them exist."""
    fake_providers["yh"] = RuntimeError("yahoo outage")
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "ALL"
    assert result["providers_answered"] == ["TRADINGVIEW", "FINVIZ"]
    assert [e["provider"] for e in result["errors"]] == ["YAHOO"]


@pytest.mark.asyncio
async def test_quote_labels_single_source_when_finviz_and_yahoo_fail(fake_providers):
    fake_providers["fv"] = RuntimeError("finviz outage")
    fake_providers["yh"] = RuntimeError("yahoo outage")
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "TRADINGVIEW"
    assert result["verification"] == "SINGLE_SOURCE"
    assert "single-source" in result["notice"].lower()
    assert {e["provider"] for e in result["errors"]} == {"FINVIZ", "YAHOO"}
    # The surviving provider's value is still served.
    assert result["data"]["price"] == pytest.approx(331.9)


@pytest.mark.asyncio
async def test_quote_can_label_yahoo_as_the_only_source(fake_providers):
    fake_providers["tv"] = RuntimeError("tradingview outage")
    fake_providers["fv"] = RuntimeError("finviz outage")
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "YAHOO"
    assert result["verification"] == "SINGLE_SOURCE"
    assert result["data"]["price"] == pytest.approx(331.9)


@pytest.mark.asyncio
async def test_quote_none_when_every_provider_fails(fake_providers):
    for key in ("tv", "fv", "yh"):
        fake_providers[key] = RuntimeError("down")
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "NONE"
    assert result["verification"] == "UNAVAILABLE"
    assert result["data"] == {}
    assert {e["provider"] for e in result["errors"]} == {
        "TRADINGVIEW",
        "FINVIZ",
        "YAHOO",
    }


@pytest.mark.asyncio
async def test_quote_flags_a_real_mismatch_instead_of_hiding_it(fake_providers):
    fake_providers["tv"]["close"] = 300.0  # 10% off the other two
    result = await tools_market.stock_quote("AAPL")
    assert result["data_provider"] == "ALL"  # everyone answered...
    price_row = next(r for r in result["comparisons"] if r["field"] == "price")
    assert price_row["match"] is False  # ...but they do not agree
    assert "disagree" in result["notice"]
    assert any("disagree" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_symbol_finviz_does_not_carry_is_not_an_error(
    monkeypatch, tv_quote, yh_quote
):
    """XAUUSD: TradingView and Yahoo answer, Finviz has no page. Not a failure."""
    from tapebasis import tv_access, yh_access

    async def tv_quote_call(symbol):
        return {**tv_quote, "close": 4046.42}

    async def yh_quote_call(symbol):
        return {**yh_quote, "price": 4046.42}

    async def fake_resolve(query, provider, **kwargs):
        return {
            "TRADINGVIEW": _resolved(provider, "OANDA:XAUUSD", query=query),
            "FINVIZ": _resolved(provider, None, query=query),
            "YAHOO": _resolved(provider, "XAUUSD=X", query=query),
        }[provider]

    monkeypatch.setattr(tv_access, "quote", tv_quote_call)
    monkeypatch.setattr(yh_access, "quote", yh_quote_call)
    monkeypatch.setattr(tools_market, "resolve", fake_resolve)

    result = await tools_market.stock_quote("XAUUSD")
    assert result["data_provider"] == "ALL"
    assert result["errors"] == []  # Finviz NOT_ATTEMPTED is not an error
    assert result["providers_answered"] == ["TRADINGVIEW", "YAHOO"]
    assert result["resolutions"]["FINVIZ"]["symbol"] is None
    assert result["resolutions"]["TRADINGVIEW"]["symbol"] == "OANDA:XAUUSD"
    assert result["resolutions"]["YAHOO"]["symbol"] == "XAUUSD=X"


@pytest.mark.asyncio
async def test_finviz_only_tool_is_explicitly_single_source(monkeypatch):
    from tapebasis import fv_access

    async def fake_why(symbol):
        return {"ticker": "AAPL", "headline": "AAPL rises on product news"}

    monkeypatch.setattr(fv_access, "why_moving", fake_why)
    result = await tools_finviz.stock_why_moving("AAPL")
    assert result["data_provider"] == "FINVIZ"
    assert result["verification"] == "SINGLE_SOURCE"
    assert "single-source" in result["notice"].lower()
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
    # TradingView and Yahoo print at the close instant; Finviz renders 3:59 PM,
    # one minute earlier, so the newest instant is a two-way tie.
    assert result["freshness"]["chosen"] == "TRADINGVIEW"
    assert result["freshness"]["decision"] == "tie"
    assert result["freshness"]["finviz"] == "2026-11-03T20:59:00+00:00"
    assert "same instant" in result["notice"]


@pytest.mark.asyncio
async def test_quote_prefers_the_provider_that_printed_last(fake_providers):
    """Freshness, not provider identity, decides."""
    fake_providers["tv"]["update_time"] = "2026-11-03T20:00:00+00:00"
    fake_providers["yh"]["market_time"] = "2026-11-03T20:30:00+00:00"
    result = await tools_market.stock_quote("AAPL")
    assert result["freshness"]["chosen"] == "FINVIZ"
    assert result["freshness"]["decision"] == "newest"
    assert "printed more recently" in result["notice"]

    fake_providers["fv"]["last_close"]["date"] = "Nov 03 • 3:00 PM ET"
    result = await tools_market.stock_quote("AAPL")
    assert result["freshness"]["chosen"] == "YAHOO"


@pytest.mark.asyncio
async def test_every_value_survives_in_comparisons_after_selection(fake_providers):
    """Selecting one value must not discard the audit trail."""
    fake_providers["fv"]["last_close"]["price"] = 340.0
    result = await tools_market.stock_quote("AAPL")
    row = next(c for c in result["comparisons"] if c["field"] == "price")
    assert row["values"]["TRADINGVIEW"] == pytest.approx(331.9)
    assert row["values"]["FINVIZ"] == pytest.approx(340.0)
    assert row["values"]["YAHOO"] == pytest.approx(331.9)
    assert row["match"] is False
    assert row["chosen"] == "TRADINGVIEW"
    assert "Providers disagree on: price" in result["warnings"]
