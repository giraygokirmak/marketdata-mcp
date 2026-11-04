# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

# eMudhra/emSign CA incident (Oct 2026): python base images built before the fix ship a pip and
# certifi that still trust the compromised chain. Upgrade both before anything else touches PyPI.
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2.2" "certifi>=2026.10.19"

# Install system deps and uv
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh

WORKDIR /app

# Copy everything (needed for editable install + packaged coinlists)
COPY . .

# Install the package and its dependencies into the system Python
RUN uv pip install --system .

# ---- Stage 2: Runtime ----
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/tapebasis /usr/local/bin/tapebasis

# Copy app source (needed for the packaged coinlist data files)
COPY --from=builder /app /app

# eMudhra/emSign CA incident (Oct 2026): python base images built before the fix ship a pip and
# certifi that still trust the compromised chain. Upgrade both before anything else touches PyPI.
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2.2" "certifi>=2026.10.19"

# Create non-root user for security
RUN useradd -m mcpuser && chown -R mcpuser:mcpuser /app

# Chart PNGs and the health-monitor baselines are written here; mount a volume to
# keep both (and the drift detection history) across restarts.
RUN mkdir -p /home/mcpuser/.tapebasis/charts /home/mcpuser/.tapebasis/state \
    && chown -R mcpuser:mcpuser /home/mcpuser/.tapebasis
ENV FINVIZ_CHART_DIR=/home/mcpuser/.tapebasis/charts
ENV TAPEBASIS_HEALTH_STATE=/home/mcpuser/.tapebasis/state/health_state.json

USER mcpuser

# Expose the HTTP port
EXPOSE 8000

# Health check (cheap: reads the cached monitor verdict, no upstream call)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the MCP server over streamable-http (ideal for Docker/remote deployments)
ENTRYPOINT ["tapebasis"]
CMD ["streamable-http", "--host", "0.0.0.0", "--port", "8000"]
