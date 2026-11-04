"""Tests for the reconciliation engine (no network)."""
from __future__ import annotations

import pytest

from tapebasis.providers_state import DataProvider, envelope, provider_from_success
from tapebasis.reconcile import (
    TRADINGVIEW,
    FINVIZ,
    Attempt,
    FieldSpec,
    Pair,
    ask,
    compare_numeric,
    compare_symbol,
    finalize,
    merge,
    single,
)


def _ok(provider, value):
    return Attempt(provider, True, value=value)


def _fail(provider, message="boom"):
    return Attempt(provider, False, error={"provider": provider, "code": "PROVIDER_ERROR", "message": message})


# ── provider state ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tv_ok,fv_ok,expected",
    [
        (True, True, DataProvider.BOTH),
        (True, False, DataProvider.TRADINGVIEW),
        (False, True, DataProvider.FINVIZ),
        (False, False, DataProvider.NONE),
    ],
)
def test_provider_from_success(tv_ok, fv_ok, expected):
    assert provider_from_success(tv_ok, fv_ok) is expected


def test_envelope_notice_is_explicit_for_single_source():
    body = envelope({"price": 1}, provider=DataProvider.TRADINGVIEW)
    assert body["data_provider"] == "TRADINGVIEW"
    assert body["verification"] == "UNVERIFIED"
    assert "NOT cross-verified" in body["notice"]


def test_envelope_none_says_do_not_present():
    body = envelope(None, provider=DataProvider.NONE)
    assert body["verification"] == "UNAVAILABLE"
    assert "Do not present" in body["notice"]


# ── comparison ──────────────────────────────────────────────────────────────


def test_compare_numeric_within_tolerance():
    row = compare_numeric(331.90, 331.89, tolerance_pct=0.5)
    assert row["match"] is True
    assert row["diff_pct"] < 0.5


def test_compare_numeric_outside_tolerance():
    row = compare_numeric(100.0, 110.0, tolerance_pct=0.5)
    assert row["match"] is False


def test_compare_numeric_coerces_annotated_strings():
    # Finviz cells like "351.73 -5.64%" must still parse.
    row = compare_numeric(351.73, "351.73 -5.64%", tolerance_pct=0.5)
    assert row["fv"] == "351.73 -5.64%"
    assert row["match"] is True


def test_compare_numeric_non_numeric_is_unknown():
    row = compare_numeric("n/a", 1.0)
    assert row["match"] is None


def test_compare_numeric_absolute_floor_protects_small_values():
    # A 0.01 vs 0.02 difference is 50% relative but trivial in absolute terms.
    strict = compare_numeric(0.01, 0.02, tolerance_pct=5.0)
    floored = compare_numeric(0.01, 0.02, tolerance_pct=5.0, absolute_floor=0.5)
    assert strict["match"] is False
    assert floored["match"] is True


def test_compare_symbol_normalises_case():
    assert compare_symbol("NASDAQ:AAPL", "aapl")["match"] is False
    assert compare_symbol("AAPL", "aapl")["match"] is True


# ── merge ───────────────────────────────────────────────────────────────────


def test_merge_both_reports_agreement_and_mismatch():
    specs = (
        FieldSpec("price", lambda p: p["price"], lambda p: p["price"]),
        FieldSpec("rsi", lambda p: p["rsi"], lambda p: p["rsi"]),
    )
    merged = merge({"price": 10.0, "rsi": 50.0}, {"price": 10.05, "rsi": 70.0}, specs)
    assert merged.provider is DataProvider.BOTH
    assert merged.mismatches == ["rsi"]
    # No timestamps on either side, so the documented Finviz default applies.
    assert merged.data["price"] == 10.05
    assert {c["field"]: c["match"] for c in merged.comparisons} == {"price": True, "rsi": False}


def test_merge_falls_back_to_single_provider():
    specs = (FieldSpec("price", lambda p: p["price"], lambda p: p["price"]),)
    merged = merge(None, {"price": 10.0}, specs)
    assert merged.provider is DataProvider.FINVIZ
    assert merged.data == {"price": 10.0}
    assert merged.fields["price"] is DataProvider.FINVIZ


def test_merge_none_when_both_empty():
    merged = merge(None, None, ())
    assert merged.provider is DataProvider.NONE


def test_merge_keeps_fields_only_one_side_offers():
    merged = merge(
        {"price": 10.0, "tv_only": "x"},
        {"price": 10.0, "fv_only": "y"},
        (FieldSpec("price", lambda p: p["price"], lambda p: p["price"]),),
        tv_shape=lambda p: {"tv_only": p["tv_only"]},
        fv_shape=lambda p: {"fv_only": p["fv_only"]},
    )
    assert merged.data["tv_only"] == "x"
    assert merged.data["fv_only"] == "y"
    assert merged.fields["tv_only"] is DataProvider.TRADINGVIEW
    assert merged.fields["fv_only"] is DataProvider.FINVIZ


# ── pair semantics ──────────────────────────────────────────────────────────


def test_not_attempted_is_not_reported_as_an_error():
    pair = single(FINVIZ, {"a": 1})
    assert pair.provider is DataProvider.FINVIZ
    # Finviz-only capability: TradingView was never attempted -> no error noise.
    assert pair.errors == []


def test_single_tradingview_marks_finviz_not_attempted():
    pair = single(TRADINGVIEW, {"a": 1})
    assert pair.provider is DataProvider.TRADINGVIEW
    assert pair.fv.error["code"] == "NOT_ATTEMPTED"


@pytest.mark.asyncio
async def test_ask_runs_both_and_survives_one_failure():
    async def good():
        return {"v": 1}

    async def bad():
        raise RuntimeError("nope")

    pair = await ask(good, bad)
    assert pair.tv.ok and not pair.fv.ok
    assert pair.provider is DataProvider.TRADINGVIEW
    assert pair.errors[0]["provider"] == FINVIZ


@pytest.mark.asyncio
async def test_ask_with_missing_provider_is_not_an_error():
    async def good():
        return {"v": 1}

    pair = await ask(good, None)
    assert pair.provider is DataProvider.TRADINGVIEW
    assert pair.errors == []


def test_finalize_override_data_and_provider():
    pair = Pair(tv=_ok(TRADINGVIEW, {"a": 1}), fv=_ok(FINVIZ, {"a": 1}))
    body = finalize(pair, data={"custom": True}, provider=DataProvider.BOTH)
    assert body["data"] == {"custom": True}
    assert body["data_provider"] == "BOTH"
