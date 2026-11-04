"""Provider states, error types and the shared response envelope.

Every tool in marketdata-mcp returns a :class:`dict` shaped by
:func:`envelope`. The first-class field is ``data_provider`` — an LLM reading
the tool result must always be able to tell whether the numbers it is about to
quote were confirmed by both upstreams (``BOTH``) or only one of them
(``TRADINGVIEW`` / ``FINVIZ``), or are absent entirely (``NONE``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable


class DataProvider(str, Enum):
    """Which provider(s) supplied the data in a response."""

    BOTH = "BOTH"
    TRADINGVIEW = "TRADINGVIEW"
    FINVIZ = "FINVIZ"
    NONE = "NONE"


class Verification(str, Enum):
    """How strongly the payload is corroborated.

    ``VERIFIED``      two independent providers agree (``BOTH``).
    ``UNVERIFIED``    exactly one provider answered (``TRADINGVIEW``/``FINVIZ``).
    ``UNAVAILABLE``   no provider answered (``NONE``).
    """

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


VERIFICATION_BY_PROVIDER: dict[DataProvider, Verification] = {
    DataProvider.BOTH: Verification.VERIFIED,
    DataProvider.TRADINGVIEW: Verification.UNVERIFIED,
    DataProvider.FINVIZ: Verification.UNVERIFIED,
    DataProvider.NONE: Verification.UNAVAILABLE,
}

#: Human-readable sentence the LLM is expected to relay to the user.
PROVIDER_MEANING: dict[DataProvider, str] = {
    DataProvider.BOTH: (
        "Data came from BOTH providers (TradingView and Finviz) and the values "
        "agree — cross-verified."
    ),
    DataProvider.TRADINGVIEW: (
        "Data came from TRADINGVIEW only. Finviz did not supply this field, so "
        "the value is NOT cross-verified by a second provider."
    ),
    DataProvider.FINVIZ: (
        "Data came from FINVIZ only. TradingView did not supply this field, so "
        "the value is NOT cross-verified by a second provider."
    ),
    DataProvider.NONE: (
        "Neither TradingView nor Finviz could supply this data. Do not present "
        "any value for it."
    ),
}

#: Provider labels used by the health monitor.
PROVIDER_LABELS = ("TRADINGVIEW", "FINVIZ")


class ProviderError(Exception):
    """A single provider failed for a single call."""

    def __init__(self, provider: str, message: str, *, code: str = "PROVIDER_ERROR", **extra: Any):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message
        self.code = code
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "message": self.message,
            **({"detail": self.extra} if self.extra else {}),
        }


def error_code(exc: BaseException, default: str = "PROVIDER_ERROR") -> str:
    """Extract a stable error code from any provider exception.

    Provider errors carry either an ``ErrorCode`` enum or a plain string; both
    must survive to the envelope unchanged.
    """
    code = getattr(exc, "code", None)
    if code is None:
        return default
    value = getattr(code, "value", None)
    return str(value if value is not None else code)


class NoDataError(Exception):
    """A provider answered but had nothing for this request (not a failure)."""

    def __init__(self, provider: str, message: str):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(
    data: Any,
    *,
    provider: DataProvider,
    verification: Verification | None = None,
    fields: dict[str, DataProvider] | None = None,
    comparisons: Iterable[dict[str, Any]] = (),
    sources: dict[str, str] | None = None,
    errors: Iterable[dict[str, Any]] | dict[str, Any] = (),
    warnings: Iterable[str] | str = (),
    degraded: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical response envelope.

    Args:
        data: The payload itself (already reconciled).
        provider: Overall :class:`DataProvider` for the response.
        verification: Defaults to the documented value for ``provider``.
        fields: Per-field provider map, e.g. ``{"price": DataProvider.BOTH}``.
        comparisons: Per-field comparison rows from the reconciler.
        sources: Provider -> upstream URL/identifier that produced the data.
        errors: One or more provider error dicts.
        warnings: Human-readable caution strings (promoted into ``notice``).
        degraded: True when the health monitor reports an upstream schema drift
            or an outage relevant to this call.
        extra: Additional top-level keys to merge into the envelope.

    Returns:
        ``dict`` carrying ``data_provider``, ``verification``, ``notice``,
        ``data`` plus diagnostics.
    """
    verification = verification or VERIFICATION_BY_PROVIDER[provider]
    if isinstance(errors, dict):
        errors = [errors]
    if isinstance(warnings, str):
        warnings = [warnings]

    notice = PROVIDER_MEANING[provider]
    if provider in (DataProvider.TRADINGVIEW, DataProvider.FINVIZ):
        notice += " Treat this as single-source data and say so when reporting it."
    if degraded:
        notice += " WARNING: the provider health monitor reports an upstream change/outage — see health."

    payload: dict[str, Any] = {
        "data_provider": provider.value,
        "verification": verification.value,
        "notice": notice,
        "data": data,
        "fields": {k: v.value for k, v in (fields or {}).items()},
        "comparisons": list(comparisons),
        "sources": sources or {},
        "errors": list(errors),
        "warnings": list(warnings),
        "degraded": degraded,
        "generated_at": _utcnow(),
    }
    if extra:
        payload.update(extra)
    return payload


def provider_from_success(tv_ok: bool, fv_ok: bool) -> DataProvider:
    """Map which providers succeeded onto a :class:`DataProvider`."""
    if tv_ok and fv_ok:
        return DataProvider.BOTH
    if tv_ok:
        return DataProvider.TRADINGVIEW
    if fv_ok:
        return DataProvider.FINVIZ
    return DataProvider.NONE
