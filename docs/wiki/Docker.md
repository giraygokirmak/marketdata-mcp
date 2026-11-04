# Docker

The image runs the server over streamable HTTP as a non-root user, with a healthcheck and a named
volume for state.

## Prebuilt image (GHCR)

Every push to `main` publishes a multi-arch image to the GitHub Container Registry, so a deployment
can skip the build entirely:

```bash
docker pull ghcr.io/giraygokirmak/tapebasis:latest
docker run -d --name tapebasis -p 6013:8000 \
  -v tapebasis-state:/home/mcpuser/.tapebasis \
  ghcr.io/giraygokirmak/tapebasis:latest
curl -s localhost:6013/health
```

| Tag | Points at |
| --- | --- |
| `latest`, `main` | Head of `main` |
| `sha-<shortsha>` | One exact commit |
| `<major>.<minor>.<patch>`, `<major>.<minor>`, `<major>` | A pushed `v*` tag |

One manifest covers `linux/amd64` and `linux/arm64`. The package is public, so no login is needed to
pull it. If you fork the repository, the workflow publishes to your own namespace automatically
(`images: ghcr.io/${{ github.repository }}`) using the built-in `GITHUB_TOKEN` — no extra secret.

## Compose (recommended)

```bash
cp .env.example .env      # optional; every setting has a default
docker compose up -d --build
docker compose logs -f
curl -s localhost:6013/health
```

`--build` builds from this checkout. To run the published image through compose instead, drop the
`build:` block (or set `image:` to the GHCR reference) and run `docker compose up -d`.

Host port `6013` is the published host port; change the mapping in `docker-compose.yml` if it
conflicts with something already running.

## Standalone build

```bash
docker build -t tapebasis:latest .
docker run -d --name tapebasis -p 6013:8000 \
  -v tapebasis-state:/home/mcpuser/.tapebasis \
  tapebasis:latest
```

## What the image does

| Stage | Purpose |
| --- | --- |
| builder | Upgrades pip/certifi, installs `uv`, installs the package system-wide |
| runtime | Copies installed site-packages and app source, upgrades pip/certifi again, drops to `mcpuser` |

Both stages upgrade `pip` and `certifi` before touching PyPI. This is deliberate: base images built
before the eMudhra/emSign CA incident (Oct 2026) ship a `certifi` that still trusts the compromised
chain, and a `pip install` through it would fail or, worse, succeed against the wrong host.

The container runs as `mcpuser`, never root.

## Volumes

| Path in container | Volume | Contents |
| --- | --- | --- |
| `/home/mcpuser/.tapebasis` | `tapebasis-state` | Chart PNGs + health baselines |

Persisting the health state is not cosmetic: the drift baselines live there. Without the volume,
every restart re-baselines and a slow-moving schema change could be adopted as the new normal.

```bash
docker volume inspect tapebasis_tapebasis-state
```

## Healthcheck

Both the Dockerfile and compose define the same check:

```bash
python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
```

It hits `/health`, which reads the **cached** monitor verdict and makes no upstream call. That keeps
the check cheap and independent of third-party availability — a Finviz outage should not mark the
container unhealthy.

| Setting | Value |
| --- | --- |
| interval | 30 s |
| timeout | 5 s |
| start_period | 15 s |
| retries | 3 |

## Environment

Compose sets the values that matter in a container:

```yaml
environment:
  - PORT=8000
  - HOST=0.0.0.0
  - FINVIZ_CACHE_TTL=30
  - FINVIZ_TIMEOUT=25
  - TAPEBASIS_HEALTH_ENABLED=true
  - TAPEBASIS_HEALTH_INTERVAL=300
  - TAPEBASIS_HEALTH_TTL=60
  - TAPEBASIS_HEALTH_STATE=/home/mcpuser/.tapebasis/state/health_state.json
  - FINVIZ_CHART_DIR=/home/mcpuser/.tapebasis/charts
```

`HOST=0.0.0.0` is required inside the container — binding to loopback would make the port mapping
useless.

`.env` is loaded with `required: false`, so a missing file is not an error.

Add `MARKETAUX_API_TOKEN` to `.env` only if you want the licensed news/sentiment path. Nothing
requires it.

## Verifying a deployment

```bash
# 1. Process up, health endpoint answering
curl -s localhost:6013/health | python -m json.tool

# 2. Container healthy
docker compose ps

# 3. Drift verdicts
curl -s localhost:6013/health | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('status'), d.get('degraded'))"
```

A `degraded: true` from a fresh container usually means an upstream is genuinely down or has changed
shape — check `docker compose logs` and then [Troubleshooting](Troubleshooting).

## Notes

- **No proxy support** is built in. Use network-level egress control if you need it.
- The image has no authentication. Do not expose port 6013 to the public internet without a reverse
  proxy that adds auth.
- Image size is dominated by pandas and the provider layer; it is not a slim image.

## Related

- [Configuration](Configuration) — every variable
- [Health Monitor](Health-Monitor) — what `/health` reports
