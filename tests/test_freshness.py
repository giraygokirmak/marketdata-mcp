"""Freshness parsing and the "one price, freshest print" selection rule.

The rule these tests pin: when both providers answer, the response carries a
**single** value per compared field, taken from whichever provider printed more
recently. Both originals stay in ``comparisons`` so the choice is auditable.

The distinction that makes this possible is print time vs fetch time. A quote
fetched a second ago can still carry yesterday's close; ordering providers by
fetch time would always pick the one that responded last, which says nothing
about which price is more current.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketdata_mcp.freshness import freshest, parse_market_timestamp
from marketdata_mcp.reconcile import FINVIZ, TRADINGVIEW, FieldSpec, merge


REFERENCE = datetime(2026, 11, 4, 7, 30, tzinfo=timezone.utc)


# ── parsing ─────────────────────────────────────────────────────────────────


def test_parses_yahoo_epoch_seconds():
    # regularMarketTime for the 2026-11-03 US close.
    assert parse_market_timestamp(1793739601) == datetime(
        2026, 11, 3, 21, 0, 1, tzinfo=timezone.utc
    )


def test_parses_epoch_supplied_as_a_string():
    assert parse_market_timestamp("1793739601") == parse_market_timestamp(1793739601)


def test_parses_finviz_rendered_market_time():
    """``Nov 03 • 3:59 PM ET`` — the string Finviz puts next to the price."""
    parsed = parse_market_timestamp("Nov 03 • 3:59 PM ET", reference=REFERENCE)
    # 15:59 EST on 3 Nov 2026 is 20:59 UTC.
    assert parsed == datetime(2026, 11, 3, 20, 59, tzinfo=timezone.utc)


def test_finviz_time_uses_daylight_saving_not_a_fixed_offset():
    """July is EDT (UTC-4), November is EST (UTC-5).

    A fixed -05:00 would put every summer print an hour late, which is enough
    to reverse a genuine freshness comparison.
    """
    summer = parse_market_timestamp(
        "Jul 15 • 3:59 PM ET", reference=datetime(2026, 7, 16, 12, tzinfo=timezone.utc)
    )
    winter = parse_market_timestamp("Dec 15 • 3:59 PM ET", reference=REFERENCE)
    assert summer.hour == 19  # 15:59 EDT -> 19:59 UTC
    assert winter.hour == 20  # 15:59 EST -> 20:59 UTC


def test_parses_iso_timestamps():
    assert parse_market_timestamp("2026-11-03T21:00:01+00:00") == datetime(
        2026, 11, 3, 21, 0, 1, tzinfo=timezone.utc
    )


def test_parses_compact_finviz_index_date():
    assert parse_market_timestamp("20261103") == datetime(
        2026, 11, 3, tzinfo=timezone.utc
    )


def test_resolves_today_and_yesterday_tokens():
    today = parse_market_timestamp("Today 09:00AM ET", reference=REFERENCE)
    yesterday = parse_market_timestamp("Yesterday 09:00AM ET", reference=REFERENCE)
    assert today.date().isoformat() == "2026-11-04"
    assert yesterday.date().isoformat() == "2026-11-03"


def test_rolls_back_a_year_when_the_source_omits_it():
    """A December page read in January belongs to the previous year."""
    parsed = parse_market_timestamp(
        "Dec 30 • 3:59 PM ET", reference=datetime(2027, 1, 2, 12, tzinfo=timezone.utc)
    )
    assert parsed.year == 2026


@pytest.mark.parametrize(
    "raw", [None, "", "   ", "not a date", "—", "Closed", [], {}]
)
def test_unparseable_input_returns_none_instead_of_raising(raw):
    """A provider changing its date rendering must degrade, not crash."""
    assert parse_market_timestamp(raw) is None


# ── freshest ────────────────────────────────────────────────────────────────


def test_freshest_picks_the_later_reading():
    early = datetime(2026, 11, 3, 20, 59, tzinfo=timezone.utc)
    late = datetime(2026, 11, 3, 21, 0, 1, tzinfo=timezone.utc)
    assert freshest((TRADINGVIEW, late), (FINVIZ, early)) == TRADINGVIEW
    assert freshest((TRADINGVIEW, early), (FINVIZ, late)) == FINVIZ


def test_unknown_timestamps_cannot_win():
    known = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)
    assert freshest((TRADINGVIEW, None), (FINVIZ, known)) == FINVIZ


def test_no_known_timestamp_returns_none():
    assert freshest((TRADINGVIEW, None), (FINVIZ, None)) is None


# ── merge resolution ────────────────────────────────────────────────────────


def _specs():
    return (FieldSpec("price", lambda p: p.get("price"), lambda p: p.get("price")),)


def test_recency_decides_the_single_value_not_provider_identity():
    """Finviz is the old hard-coded winner; freshness must be able to override it."""
    tv = {"price": 100.0}
    fv = {"price": 100.0}
    merged = merge(
        tv, fv, _specs(),
        tv_time="2026-11-03T21:00:00+00:00",
        fv_time="Nov 03 • 3:59 PM ET",
    )
    assert merged.chosen == TRADINGVIEW


def test_stale_tradingview_loses_to_a_fresher_finviz_print():
    merged = merge(
        {"price": 100.0}, {"price": 100.0}, _specs(),
        tv_time="Nov 03 • 3:59 PM ET",
        fv_time="2026-11-03T21:00:00+00:00",
    )
    assert merged.chosen == FINVIZ


def test_single_value_is_returned_and_both_readings_are_kept():
    """The response holds one number; the audit trail holds two."""
    merged = merge(
        {"price": 331.0}, {"price": 340.0}, _specs(),
        tv_time="2026-11-03T21:00:00+00:00",
        fv_time="2026-11-03T20:59:00+00:00",
    )
    assert merged.data["price"] == 331.0  # the fresher (TradingView) value
    assert merged.comparisons[0]["tv"] == 331.0
    assert merged.comparisons[0]["fv"] == 340.0
    assert "price" in merged.mismatches  # 2.7 % is well outside 0.5 %
    assert merged.comparisons[0]["match"] is False


def test_a_within_tolerance_disagreement_is_not_a_mismatch():
    merged = merge(
        {"price": 331.0}, {"price": 331.5}, _specs(),
        tv_time="2026-11-03T21:00:00+00:00",
        fv_time="2026-11-03T20:59:00+00:00",
    )
    assert merged.mismatches == []
    assert merged.data["price"] == 331.0


def test_without_timestamps_finviz_remains_the_documented_default():
    merged = merge({"price": 1.0}, {"price": 1.0}, _specs())
    assert merged.chosen == FINVIZ
    assert "documented default" in merged.freshness["reason"]


def test_identical_timestamps_fall_back_to_finviz_deterministically():
    same = "2026-11-03T21:00:00+00:00"
    merged = merge({"price": 1.0}, {"price": 2.0}, _specs(), tv_time=same, fv_time=same)
    assert merged.chosen == FINVIZ
    assert merged.data["price"] == 2.0


def test_winner_missing_a_field_falls_back_to_the_other_side():
    """Freshness decides between competing values, not whether a value exists."""
    tv = {"price": 100.0}          # fresher
    fv = {"price": 100.0, "pe_ratio": 36.9}
    merged = merge(
        tv, fv,
        (
            FieldSpec("price", lambda p: p.get("price"), lambda p: p.get("price")),
            FieldSpec("pe_ratio", lambda p: p.get("pe_ratio"), lambda p: p.get("pe_ratio")),
        ),
        tv_time="2026-11-03T21:00:00+00:00",
        fv_time="2026-11-03T20:59:00+00:00",
    )
    assert merged.chosen == TRADINGVIEW
    assert merged.data["price"] == 100.0
    # Only Finviz has P/E, so it is used despite losing on freshness.
    assert merged.data["pe_ratio"] == 36.9


# ── notice honesty ──────────────────────────────────────────────────────────


def test_notice_claims_newer_print_only_when_it_is_actually_newer():
    """A tie must not be described as 'printed more recently'."""
    from marketdata_mcp.reconcile import notice_for

    same = "2026-11-03T21:00:00+00:00"
    tied = merge({"price": 1.0}, {"price": 2.0}, _specs(), tv_time=same, fv_time=same)
    text = notice_for(tied)
    assert "printed more recently" not in text
    assert "same instant" in text


def test_notice_does_not_speculate_when_no_timestamp_was_available():
    """With no print times, the notice must not claim or explain a choice.

    Saying "no usable print time" would invite the reader to wonder why one
    provider was preferred, when the truth is that freshness never entered the
    decision.
    """
    from marketdata_mcp.reconcile import notice_for

    merged = merge({"price": 1.0}, {"price": 2.0}, _specs())
    text = notice_for(merged)
    assert "printed more recently" not in text
    assert "print time" not in text
    assert "disagree on price" in text


def test_notice_names_the_fresher_provider_when_they_agree():
    from marketdata_mcp.reconcile import notice_for

    merged = merge(
        {"price": 100.0}, {"price": 100.0}, _specs(),
        tv_time="2026-11-03T21:00:01+00:00",
        fv_time="2026-11-03T20:59:00+00:00",
    )
    text = notice_for(merged)
    assert "agree within tolerance" in text
    assert "TRADINGVIEW's because it printed more recently" in text
