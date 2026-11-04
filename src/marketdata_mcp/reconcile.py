"""Fan-out, comparison and fallback engine.

The rule this module implements:

* Ask both providers, in parallel, for every field both can serve.
* Compare the answers within a documented tolerance.
* Both answered -> data is cross-verified (``BOTH``).
* One answered -> serve it, mark the response ``TRADINGVIEW``/``FINVIZ`` and
  say in the notice that it is *not* verified by the other provider.
* A provider raised -> the other provider's answer still goes out; the failure
  is attached under ``errors``.
* Only when both fail is the response ``NONE``.

Three entry points:

``ask``       run two provider coroutines concurrently, never raise.
``merge``     compare two payloads field-by-field into a ``Merged`` result.
``finalize``  build the public envelope from a pair of attempts plus shapers.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Sequence

from .providers_state import (
    DataProvider,
    NoDataError,
    PROVIDER_MEANING,
    ProviderError,
    envelope,
)

TRADINGVIEW = "TRADINGVIEW"
FINVIZ = "FINVIZ"


# ── Attempts ─────────────────────────────────────────────────────────────────


@dataclass
class Attempt:
    """Outcome of asking one provider one question."""

    provider: str
    ok: bool
    value: Any = None
    error: dict[str, Any] | None = None
    elapsed_ms: float = 0.0

    @property
    def failed(self) -> bool:
        return not self.ok

    @property
    def payload(self) -> dict[str, Any] | None:
        return self.value if self.ok and isinstance(self.value, dict) else None


@dataclass
class Pair:
    """The two attempts that make up one reconciled response."""

    tv: Attempt
    fv: Attempt

    @property
    def provider(self) -> DataProvider:
        flags = [self.tv.ok, self.fv.ok]
        if all(flags):
            return DataProvider.BOTH
        if flags[0]:
            return DataProvider.TRADINGVIEW
        if flags[1]:
            return DataProvider.FINVIZ
        return DataProvider.NONE

    @property
    def errors(self) -> list[dict[str, Any]]:
        """Real failures only.

        ``NOT_ATTEMPTED`` means the provider has no endpoint for this
        capability at all (e.g. Finviz has no futures feed) — that is not an
        error and must not be reported as one.
        """
        return [
            attempt.error
            for attempt in (self.tv, self.fv)
            if attempt.failed
            and attempt.error
            and attempt.error.get("code") != "NOT_ATTEMPTED"
        ]


def _error_dict(provider: str, exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, ProviderError):
        return exc.to_dict()
    if isinstance(exc, NoDataError):
        return {"provider": provider, "code": "NO_DATA", "message": exc.message}
    return {
        "provider": provider,
        "code": "PROVIDER_ERROR",
        "message": f"{type(exc).__name__}: {exc}",
    }


async def _run(provider: str, fn: Callable[[], Awaitable[Any]]) -> Attempt:
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        value = await fn()
        elapsed = round((loop.time() - started) * 1000, 1)
        if value is None:
            return Attempt(
                provider,
                False,
                error={
                    "provider": provider,
                    "code": "NO_DATA",
                    "message": "provider returned no value",
                },
                elapsed_ms=elapsed,
            )
        return Attempt(provider, True, value=value, elapsed_ms=elapsed)
    except Exception as exc:  # noqa: BLE001 - the boundary must never leak
        return Attempt(
            provider,
            False,
            error=_error_dict(provider, exc),
            elapsed_ms=round((loop.time() - started) * 1000, 1),
        )


async def ask(
    tv_fn: Callable[[], Awaitable[Any]] | None,
    fv_fn: Callable[[], Awaitable[Any]] | None,
) -> Pair:
    """Run both provider calls concurrently; a missing callable counts as failed."""

    async def _skip(provider: str, reason: str) -> Attempt:
        return Attempt(
            provider,
            False,
            error={"provider": provider, "code": "NOT_ATTEMPTED", "message": reason},
        )

    tv_task = _run(TRADINGVIEW, tv_fn) if tv_fn else _skip(
        TRADINGVIEW, "TradingView has no endpoint for this request"
    )
    fv_task = _run(FINVIZ, fv_fn) if fv_fn else _skip(
        FINVIZ, "Finviz has no endpoint for this request"
    )
    tv, fv = await asyncio.gather(tv_task, fv_task)
    return Pair(tv=tv, fv=fv)


def single(provider: str, value: Any = None, *, error: dict[str, Any] | None = None) -> Pair:
    """Build a single-source pair (the other provider is marked NOT_ATTEMPTED)."""
    other = FINVIZ if provider == TRADINGVIEW else TRADINGVIEW
    ok = error is None
    attempt = Attempt(provider, ok, value=value, error=error)
    skipped = Attempt(
        other,
        False,
        error={
            "provider": other,
            "code": "NOT_ATTEMPTED",
            "message": f"{other} does not serve this capability",
        },
    )
    pair = Pair(tv=attempt, fv=skipped) if provider == TRADINGVIEW else Pair(
        tv=skipped, fv=attempt
    )
    return pair


# ── Numeric comparison ───────────────────────────────────────────────────────


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if not text:
            return None
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        if text.endswith("%"):
            text = text[:-1]
        multiplier = 1.0
        if text and text[-1].upper() in {"K", "M", "B", "T"}:
            multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1].upper()]
            text = text[:-1]
        try:
            number = float(text) * multiplier
        except ValueError:
            # Finviz annotates some cells ("351.73 -5.64%", "1.2M (3.4%)").
            # The leading token is the value; the rest is distance/context.
            head = text.split(" ", 1)[0]
            try:
                number = float(head) * multiplier
            except ValueError:
                return None
        return -number if negative else number
    return None


def compare_numeric(
    tv_value: Any,
    fv_value: Any,
    *,
    tolerance_pct: float = 0.5,
    absolute_floor: float = 0.0,
) -> dict[str, Any]:
    """Compare two numeric values within a relative tolerance.

    ``absolute_floor`` raises the denominator so small readings (a 0.01 move)
    are not flagged over rounding noise.
    """
    tv_number, fv_number = _as_float(tv_value), _as_float(fv_value)
    row: dict[str, Any] = {
        "tv": tv_value,
        "fv": fv_value,
        "match": None,
        "diff": None,
        "diff_pct": None,
        "tolerance_pct": tolerance_pct,
    }
    if tv_number is None or fv_number is None:
        return row
    difference = abs(tv_number - fv_number)
    denominator = max(abs(tv_number), abs(fv_number), absolute_floor, 1e-12)
    difference_pct = difference / denominator * 100
    row.update(
        match=difference_pct <= tolerance_pct,
        diff=round(difference, 6),
        diff_pct=round(difference_pct, 4),
    )
    return row


def compare_symbol(tv_value: Any, fv_value: Any) -> dict[str, Any]:
    """Case-insensitive symbol/exchange comparison."""
    tv_norm = str(tv_value).strip().upper() if tv_value is not None else None
    fv_norm = str(fv_value).strip().upper() if fv_value is not None else None
    match = None if tv_norm is None or fv_norm is None else tv_norm == fv_norm
    return {"tv": tv_value, "fv": fv_value, "match": match}


# ── Field reconciliation ─────────────────────────────────────────────────────


@dataclass
class FieldSpec:
    """One comparable field: name, extractor on each side, comparison rule."""

    name: str
    tv_get: Callable[[dict[str, Any]], Any] | None = None
    fv_get: Callable[[dict[str, Any]], Any] | None = None
    kind: str = "numeric"
    tolerance_pct: float = 0.5
    absolute_floor: float = 0.0


@dataclass
class Merged:
    """Result of comparing two provider payloads field-by-field."""

    data: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, DataProvider] = field(default_factory=dict)
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    provider: DataProvider = DataProvider.NONE


def merge(
    tv_payload: dict[str, Any] | None,
    fv_payload: dict[str, Any] | None,
    specs: Sequence[FieldSpec],
    *,
    tv_shape: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    fv_shape: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Merged:
    """Compare both payloads; fall back to whichever one exists.

    When both exist, each spec is compared. Matching values are taken from
    Finviz; *disagreeing* values also resolve to Finviz but are listed in
    ``mismatches`` and kept verbatim in ``comparisons`` so nothing is hidden.
    Fields only one side offers are still included, labelled with that side.
    """
    if tv_payload is not None and fv_payload is not None:
        out = Merged(provider=DataProvider.BOTH)
        for spec in specs:
            tv_value = spec.tv_get(tv_payload) if spec.tv_get else None
            fv_value = spec.fv_get(fv_payload) if spec.fv_get else None
            row = (
                compare_symbol(tv_value, fv_value)
                if spec.kind == "symbol"
                else compare_numeric(
                    tv_value,
                    fv_value,
                    tolerance_pct=spec.tolerance_pct,
                    absolute_floor=spec.absolute_floor,
                )
            )
            row["field"] = spec.name
            out.comparisons.append(row)
            if fv_value is not None:
                out.data[spec.name] = fv_value
                out.fields[spec.name] = DataProvider.BOTH
            elif tv_value is not None:
                out.data[spec.name] = tv_value
                out.fields[spec.name] = DataProvider.TRADINGVIEW
            if row["match"] is False:
                out.mismatches.append(spec.name)

        if fv_shape:
            for key, value in fv_shape(fv_payload).items():
                out.data.setdefault(key, value)
                out.fields.setdefault(key, DataProvider.FINVIZ)
        if tv_shape:
            for key, value in tv_shape(tv_payload).items():
                out.data.setdefault(key, value)
                out.fields.setdefault(key, DataProvider.TRADINGVIEW)
        return out

    if fv_payload is not None:
        shaped = fv_shape(fv_payload) if fv_shape else dict(fv_payload)
        return Merged(
            data=shaped,
            fields={key: DataProvider.FINVIZ for key in shaped},
            provider=DataProvider.FINVIZ,
        )

    if tv_payload is not None:
        shaped = tv_shape(tv_payload) if tv_shape else dict(tv_payload)
        return Merged(
            data=shaped,
            fields={key: DataProvider.TRADINGVIEW for key in shaped},
            provider=DataProvider.TRADINGVIEW,
        )

    return Merged(provider=DataProvider.NONE)


# ── Envelope assembly ────────────────────────────────────────────────────────


def notice_for(merged: Merged) -> str:
    """Provider notice, extended with any mismatch explanation."""
    base = PROVIDER_MEANING[merged.provider]
    if merged.provider is DataProvider.BOTH:
        if merged.mismatches:
            base += (
                " NOTE: the two providers disagree on "
                + ", ".join(merged.mismatches)
                + "; both readings appear in `comparisons`. The Finviz reading is "
                "shown in `data` — the conflict is reported, not silently resolved."
            )
        else:
            base += " Compared fields agree within tolerance."
    return base


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def finalize(
    pair: Pair,
    *,
    specs: Sequence[FieldSpec] = (),
    tv_norm: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    fv_norm: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    tv_shape: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    fv_shape: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    sources: dict[str, str] | None = None,
    warnings: Iterable[str] = (),
    degraded: bool = False,
    extra: dict[str, Any] | None = None,
    health_warn: Iterable[str] | None = None,
    data: Any = None,
    provider: DataProvider | None = None,
) -> dict[str, Any]:
    """Reconcile a pair of attempts and wrap the result in the public envelope.

    ``tv_norm``/``fv_norm`` map a raw provider payload into the normalised field
    dict that ``specs`` read from; when given, every remaining normalised field
    is attached to the output labelled with its source provider.

    ``data``/``provider`` override the reconciled payload and label; tools that
    compose a richer payload than the field list use them, and the notice is
    rebuilt so it still states the true provider and any mismatches.
    """
    tv_payload = pair.tv.payload
    fv_payload = pair.fv.payload
    if tv_payload is not None and tv_norm is not None:
        tv_payload = tv_norm(tv_payload)
        if tv_shape is None:
            tv_shape = _identity
    if fv_payload is not None and fv_norm is not None:
        fv_payload = fv_norm(fv_payload)
        if fv_shape is None:
            fv_shape = _identity

    merged = merge(tv_payload, fv_payload, specs, tv_shape=tv_shape, fv_shape=fv_shape)
    if provider is not None:
        merged.provider = provider
    if data is not None:
        merged.data = data

    warn_list = list(warnings)
    if health_warn:
        warn_list.extend(health_warn)
    if degraded:
        warn_list.append(
            "Provider health monitor reports an upstream change/outage — run "
            "marketdata_health for details."
        )
    if merged.provider is not DataProvider.NONE and merged.mismatches:
        warn_list.append(
            "Providers disagree on: " + ", ".join(merged.mismatches)
        )

    body = envelope(
        merged.data,
        provider=merged.provider,
        fields=merged.fields,
        comparisons=merged.comparisons,
        sources=sources or {},
        errors=pair.errors,
        warnings=warn_list,
        degraded=degraded,
        extra=extra,
    )
    body["notice"] = notice_for(merged)
    if degraded:
        body["notice"] += (
            " WARNING: the provider health monitor reports an upstream change or "
            "outage — see marketdata_health."
        )
    return body


def providers_in(pair: Pair) -> tuple[str, ...]:
    """Provider labels that actually answered (used for health-warning scoping)."""
    return tuple(
        label for label, attempt in ((TRADINGVIEW, pair.tv), (FINVIZ, pair.fv)) if attempt.ok
    )
