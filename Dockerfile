# Optional application container for the PostgreSQL-native runtime.
# PostgreSQL/pgvector is an external service; this image never embeds a database
# server. Native installation remains the primary deployment path.

# Stage 1: Frontend builder
FROM node:22-slim AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
ARG NPM_REGISTRY=https://registry.npmjs.org/
RUN npm config set registry ${NPM_REGISTRY} \
 && npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000
RUN i=0; until npm ci; do \
      i=$((i+1)); \
      if [ "$i" -ge 5 ]; then echo "npm ci failed after $i attempts"; exit 1; fi; \
      echo "npm ci failed (attempt $i); retrying in 15s"; sleep 15; \
    done

COPY frontend/ ./
RUN npm run build

# Stage 2: Backend builder
FROM python:3.12-slim-trixie AS backend-builder
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=120
COPY pyproject.toml uv.lock ./
COPY open_notebook/__init__.py ./open_notebook/__init__.py
RUN uv sync --frozen --no-dev

ENV TIKTOKEN_CACHE_DIR=/app/tiktoken-cache
RUN mkdir -p /app/tiktoken-cache && \
    .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

# Stage 3: Application runtime. The database is always PostgreSQL + pgvector
# supplied externally (native system service or a separate container).
FROM python:3.12-slim-trixie AS runtime
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ffmpeg \
    supervisor \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY --from=backend-builder /app/.venv /app/.venv
COPY . /app
COPY --from=backend-builder /app/tiktoken-cache /app/tiktoken-cache
COPY --from=frontend-builder /app/frontend/.next/standalone /app/frontend/
COPY --from=frontend-builder /app/frontend/.next/static /app/frontend/.next/static
COPY --from=frontend-builder /app/frontend/public /app/frontend/public
COPY --from=frontend-builder /app/frontend/start-server.js /app/frontend/start-server.js

ENV UV_NO_SYNC=1
ENV VIRTUAL_ENV=/app/.venv
ENV TIKTOKEN_CACHE_DIR=/app/tiktoken-cache
ENV API_HOST=0.0.0.0
ENV UV_CACHE_DIR=/app/data/.cache/uv
ENV PLAYWRIGHT_BROWSERS_PATH=/app/data/.cache/playwright
ENV HF_HOME=/app/data/.cache/huggingface

RUN mkdir -p /app/data /var/log/supervisor \
    && chmod +x /app/scripts/wait-for-api.sh /app/scripts/docker-entrypoint.sh
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8502 5055

# DATABASE_URL (or POSTGRES_URL) must point to a PostgreSQL instance with the
# pgvector extension available. See docs/7-DEVELOPMENT/dockerless-postgresql.md.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
