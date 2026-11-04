"""marketdata-mcp — one MCP server, two market-data providers, cross-verified.

The server fans every overlapping request out to both upstream providers
(TradingView and Finviz), compares the answers, and labels each response with
exactly one :data:`data_provider` value:

``BOTH``
    Both providers answered and their values agree (within tolerance). This is
    the only state in which data is described as *verified*.
``TRADINGVIEW``
    Only TradingView produced data. Served, but explicitly unverified.
``FINVIZ``
    Only Finviz produced data. Served, but explicitly unverified.
``NONE``
    Neither provider produced data.

See :mod:`marketdata_mcp.reconcile` for the fan-out/comparison engine and
:mod:`marketdata_mcp.health` for the upstream schema-drift monitor.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
