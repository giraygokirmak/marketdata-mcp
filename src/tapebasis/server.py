"""tapebasis — one MCP server that cross-checks TradingView and Finviz.

Run over stdio (default) or streamable HTTP::

    tapebasis                       # stdio
    tapebasis streamable-http --host 0.0.0.0 --port 8000

Every tool response carries ``data_provider`` ∈ ``BOTH`` / ``TRADINGVIEW`` /
``FINVIZ`` / ``NONE``, and any upstream schema drift detected by the health
monitor is reported inside the same response.
"""
from __future__ import annotations

import argparse
import logging
import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from . import __version__
from .health import MONITOR
from .providers_state import PROVIDER_MEANING

logger = logging.getLogger("tapebasis")

INSTRUCTIONS = (
    "Market data from TWO independent providers — TradingView and Finviz — served "
    "together. Every tool result includes `data_provider`, one of BOTH, TRADINGVIEW, "
    "FINVIZ or NONE.\n\n"
    "HOW TO READ IT:\n"
    "- BOTH: both providers answered and the compared values agree. This is the only "
    "state you may call cross-verified.\n"
    "- TRADINGVIEW or FINVIZ: only that provider had the data. You MUST tell the user "
    "this figure is single-source and not cross-verified.\n"
    "- NONE: no data; do not present a value.\n\n"
    "Per-field provenance is in `fields`, raw side-by-side values in `comparisons`, and "
    "provider failures in `errors`. If `degraded` is true or `warnings` names an upstream "
    "change, relay that warning: the provider health monitor detected an API/schema "
    "change or outage. Call `tapebasis_health` for details.\n\n"
    "TOOLS: stock_quote, stock_prices, stock_screener, market_indices, stock_news, "
    "stock_options_chain, stock_earnings, stock_extended_hours, technical_analysis, "
    "stock_filings, stock_ownership, stock_peers, stock_why_moving, stock_chart, "
    "stock_dilution, stock_screener_filters, crypto_* , futures_*, stock_backtest*, "
    "market_snapshot, market_sentiment, egx_market_overview, combined_analysis, "
    "tapebasis_health, tapebasis_tool_map."
)


@asynccontextmanager
async def lifespan(_server: FastMCP):
    """Start the health monitor on transports that honour the lifespan (stdio)."""
    if os.environ.get("TAPEBASIS_HEALTH_ENABLED", "true").lower() != "false":
        await MONITOR.start()
        logger.info(
            "health monitor started (interval=%ss, ttl=%ss)",
            MONITOR.interval,
            MONITOR.ttl,
        )
    try:
        yield {}
    finally:
        await MONITOR.stop()


mcp = FastMCP(
    name="Tapebasis — TradingView + Finviz",
    instructions=INSTRUCTIONS,
    lifespan=lifespan,
)


def _register_all() -> dict[str, int]:
    """Attach every tool family to the server."""
    from . import tools_finviz, tools_health, tools_market, tools_tradingview

    counts = {
        "market": tools_market.register(mcp),
        "finviz": tools_finviz.register(mcp),
        "tradingview": tools_tradingview.register(mcp),
        "health": tools_health.register(mcp),
    }
    return counts


TOOL_COUNTS = _register_all()


@mcp.resource("tapebasis://providers")
def providers_resource() -> str:
    """Explain what each data_provider value means for a caller."""
    return "\n".join(
        f"{provider.value}: {meaning}"
        for provider, meaning in PROVIDER_MEANING.items()
    )


@mcp.resource("tapebasis://health")
async def health_resource() -> str:
    """Cached provider health verdict (no upstream call)."""
    MONITOR.ensure_started()
    summary = MONITOR.summary()
    lines = [
        f"Providers: {summary['providers']}",
        f"Degraded: {summary['degraded']}",
    ]
    for issue in summary["issues"]:
        lines.append(
            f"- {issue['provider']} {issue['status']} {issue['probe']}: {issue['message']}"
        )
    return "\n".join(lines)


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request):
    """Liveness probe for the Docker HEALTHCHECK (cheap, no upstream call)."""
    from starlette.responses import JSONResponse

    # FastMCP's streamable-HTTP app ignores the lifespan passed to FastMCP, so
    # the monitor is started on first request instead of at startup.
    MONITOR.ensure_started()
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "tools": TOOL_COUNTS,
            "providers": MONITOR.summary()["providers"],
            "degraded": MONITOR.degraded(),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="tapebasis server")
    parser.add_argument(
        "transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        nargs="?",
        help="Transport (default stdio)",
    )
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    if os.environ.get("DEBUG_MCP"):
        import sys

        print(
            f"[DEBUG_MCP] pkg cwd={os.getcwd()} argv={sys.argv} file={__file__}",
            file=sys.stderr,
            flush=True,
        )

    if args.transport == "stdio":
        mcp.run()
        return

    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    # Binds to 0.0.0.0 inside a container; DNS-rebinding protection would
    # reject the container's own hostname on the first request.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
