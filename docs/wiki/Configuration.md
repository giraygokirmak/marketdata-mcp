# Configuration

Every setting is optional. Defaults are chosen so a bare `pip install` works.

## Server

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Bind address for streamable-http (`--host` overrides) |
| `PORT` | `8000` | Bind port (`--port` overrides) |
| `DEBUG_MCP` | — | Any non-empty value enables debug logging |

## Health monitor

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKETDATA_HEALTH_ENABLED` | `true` | Turn the drift monitor on/off |
| `MARKETDATA_HEALTH_INTERVAL` | `300` | Seconds between background probe rounds |
| `MARKETDATA_HEALTH_TTL` | `60` | Seconds a cached verdict is reused |
| `MARKETDATA_HEALTH_STATE` | `~/.marketdata-mcp/health_state.json` | Baseline + verdict file |
| `MARKETDATA_HEALTH_TIMEOUT` | `12` | Per-probe HTTP timeout (s) |

See [Health Monitor](Health-Monitor).

## Finviz

| Variable | Default | Purpose |
| --- | --- | --- |
| `FINVIZ_CACHE_TTL` | `30` | Scraped-page cache lifetime (s) |
| `FINVIZ_TIMEOUT` | `25` | finviz.com HTTP timeout (s) |
| `FINVIZ_MAX_RETRIES` | `2` | Retries per failed request |
| `FINVIZ_CHART_DIR` | `~/.marketdata-mcp/charts` | Chart PNG output directory |
| `FINVIZ_NEWS_TTL` | `60` | News cache lifetime (s) |
| `FINVIZ_USER_AGENT` | browser UA | Override the User-Agent sent to finviz.com |

Finviz's quote data is scraped from rendered HTML, so a short cache keeps prices fresh at the cost of
more upstream requests. `FINVIZ_CACHE_TTL=30` is a deliberate middle ground.

## TradingView scanner

The scanner is an undocumented public endpoint, so these controls exist to stay politely below its
rate limits.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRADINGVIEW_MCP_MIN_INTERVAL_S` | `0.5` | Minimum seconds between request starts |
| `TRADINGVIEW_MCP_MAX_INFLIGHT` | `2` | Max concurrent analysis calls |
| `TRADINGVIEW_MCP_RETRY_DELAYS` | `1.0,4.0` | Retry backoff schedule (seconds, comma-separated) |
| `TRADINGVIEW_MCP_RETRY_JITTER` | `0.2` | ±20 % jitter so parallel callers do not retry in lockstep |
| `TRADINGVIEW_MCP_SOCKET_TIMEOUT` | `20` | Socket timeout (s) |
| `TRADINGVIEW_MCP_CACHE_TTL` | `60` | Successful-result cache lifetime (s) |
| `TRADINGVIEW_MCP_STALE_TTL` | `21600` | How long a cached result may still be served (6 h) |
| `TRADINGVIEW_MCP_FAILURE_COOLDOWN_S` | `15` | Pause after a failure before retrying |

Raising the interval and lowering `MAX_INFLIGHT` reduces the chance of `UPSTREAM_RATE_LIMIT`;
lowering them increases throughput at that risk.

## Finviz (additional)

| Variable | Default | Purpose |
| --- | --- | --- |
| `FINVIZ_MAX_RETRIES` | (see provider) | Retries per failed Finviz request |
| `FINVIZ_USER_AGENT` | browser UA | Override the User-Agent sent to finviz.com |

## News and sentiment

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKETAUX_API_TOKEN` | — | **Optional.** Licensed Marketaux news + entity sentiment |
| `MARKETAUX_DAILY_BUDGET` | `90` | Marketaux request cap per day |

Neither is required. Without a token, sentiment is scored locally from Finviz headlines. See
[Sentiment](Sentiment).

## No proxy configuration

There is deliberately **no proxy support**. Both providers reach their upstreams directly. If you
need egress control, do it at the network or container level.

## Example `.env`

```bash
# Health monitor
MARKETDATA_HEALTH_ENABLED=true
MARKETDATA_HEALTH_INTERVAL=300
MARKETDATA_HEALTH_TTL=60

# Finviz
FINVIZ_CACHE_TTL=30
FINVIZ_TIMEOUT=25

# TradingView scanner pacing
TRADINGVIEW_MCP_MIN_INTERVAL_S=0.25
TRADINGVIEW_MCP_MAX_INFLIGHT=4

# Optional: licensed news + sentiment
# MARKETAUX_API_TOKEN=
MARKETAUX_DAILY_BUDGET=90
```

Docker reads `.env` automatically. For stdio clients, pass variables in the client's `env` block or
export them in the parent process.

## Data written to disk

| Path | Contents | Safe to delete? |
| --- | --- | --- |
| `~/.marketdata-mcp/health_state.json` | Probe baselines + last verdicts | Yes — re-baselines on next round |
| `~/.marketdata-mcp/charts/` | Generated chart PNGs | Yes |

Both live under `~/.marketdata-mcp` by default; in Docker that is the `marketdata-state` volume.

## Related

- [Health Monitor](Health-Monitor) — what the health variables control
- [Docker](Docker) — volume layout and `.env` handling
- [Troubleshooting](Troubleshooting) — symptoms traced to settings
