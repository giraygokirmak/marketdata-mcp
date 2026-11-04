"""Tests for the upstream health monitor (mocked HTTP, no network)."""
from __future__ import annotations

import httpx
import pytest

from marketdata_mcp import health
from marketdata_mcp.health import (
    FINVIZ,
    TRADINGVIEW,
    STATUS_DOWN,
    STATUS_DRIFT,
    STATUS_OK,
    HealthMonitor,
    Probe,
    fingerprint,
    key_paths,
)


def test_key_paths_and_fingerprint_are_stable():
    payload = {"a": 1, "b": {"c": 2}, "d": [{"e": 3}]}
    paths = key_paths(payload)
    assert "a" in paths and "b.c" in paths and "d[].e" in paths
    assert fingerprint(paths) == fingerprint(list(reversed(paths)))


async def _noop(*_args, **_kwargs):
    return None


def _monitor(tmp_path, probes):
    import os

    os.environ["MARKETDATA_HEALTH_STATE"] = str(tmp_path / "health.json")
    return HealthMonitor(probes)


@pytest.mark.asyncio
async def test_probe_ok_records_baseline(tmp_path, monkeypatch):
    monitor = _monitor(
        tmp_path,
        [
            Probe(
                id="p",
                provider=FINVIZ,
                description="d",
                url="https://example.test/api",
                required_keys=("value",),
            )
        ],
    )

    def handler(request):
        return httpx.Response(200, json={"value": 1})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(health.httpx, "AsyncClient", fake_client)
    results = await monitor.run_once(force=True)
    assert results["p"].status == STATUS_OK
    assert monitor.provider_status(FINVIZ) == STATUS_OK
    assert not monitor.degraded()


@pytest.mark.asyncio
async def test_missing_key_is_reported_as_drift(tmp_path, monkeypatch):
    monitor = _monitor(
        tmp_path,
        [
            Probe(
                id="p",
                provider=TRADINGVIEW,
                description="d",
                url="https://example.test/api",
                required_keys=("chart.result",),
            )
        ],
    )

    def handler(request):
        return httpx.Response(200, json={"something_else": True})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    monkeypatch.setattr(
        health.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )
    results = await monitor.run_once(force=True)
    assert results["p"].status == STATUS_DRIFT
    assert results["p"].drift
    assert monitor.degraded()
    assert "chart.result" in results["p"].missing


@pytest.mark.asyncio
async def test_shape_change_against_baseline_is_drift(tmp_path, monkeypatch):
    probe = Probe(
        id="p", provider=FINVIZ, description="d", url="https://example.test/api",
        required_keys=("value",),
    )
    monitor = _monitor(tmp_path, [probe])

    state = {"payload": {"value": 1, "extra": 2}}

    def handler(request):
        return httpx.Response(200, json=state["payload"])

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        health.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )

    await monitor.run_once(force=True)  # records baseline
    assert monitor.provider_status(FINVIZ) == STATUS_OK

    state["payload"] = {"value": 1, "renamed": 2}  # schema drift
    monitor2 = _monitor(tmp_path, [probe])  # new process, same state file
    results = await monitor2.run_once(force=True)
    assert results["p"].status == STATUS_DRIFT
    assert "shape changed" in results["p"].message


@pytest.mark.asyncio
async def test_http_500_is_down_not_drift(tmp_path, monkeypatch):
    monitor = _monitor(
        tmp_path,
        [Probe(id="p", provider=FINVIZ, description="d", url="https://example.test/api")],
    )

    def handler(request):
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        health.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )
    results = await monitor.run_once(force=True)
    assert results["p"].status == STATUS_DOWN
    assert results["p"].http_status == 503


@pytest.mark.asyncio
async def test_transport_error_is_down(tmp_path, monkeypatch):
    monitor = _monitor(
        tmp_path,
        [Probe(id="p", provider=FINVIZ, description="d", url="https://example.test/api")],
    )

    def handler(request):
        raise httpx.ConnectError("dns failure")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        health.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )
    results = await monitor.run_once(force=True)
    assert results["p"].status == STATUS_DOWN


def test_warnings_are_scoped_to_the_provider():
    monitor = HealthMonitor([])
    monitor.results = {
        "x": health.ProbeResult(
            id="x", provider=TRADINGVIEW, description="", url="",
            status=STATUS_DRIFT, message="changed",
        ),
        "y": health.ProbeResult(
            id="y", provider=FINVIZ, description="", url="", status=STATUS_OK,
        ),
    }
    assert monitor.warnings_for((FINVIZ,)) == []
    assert len(monitor.warnings_for((TRADINGVIEW,))) == 1
    assert len(monitor.warnings_for(("BOTH",))) == 1
