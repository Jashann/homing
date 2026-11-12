# Homing as one container: the React app is built here, then served by the FastAPI server on $PORT.
#   docker build -t homing . && docker run -p 8080:8080 homing
# Nothing secret goes into the image: PayPal keys arrive as environment variables at run time (see deploy/).

# ---- 1. build the web app (web/dist)
FROM node:22-bookworm-slim AS web
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm_config_update_notifier=false npm_config_fund=false
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit
COPY web/ ./
RUN npm run build

# ---- 2. the server
FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1
WORKDIR /app

# dependencies first, so a code change does not reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY scripts/ scripts/
COPY data/ data/
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist web/dist

# The SQLite files and uploaded documents are written under data/ on the container's own disk (lost on restart).
RUN useradd --uid 10001 --home-dir /app --no-create-home homing \
    && mkdir -p data/files \
    && chown -R homing:homing data
USER homing

ENV HOMING_HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "HOMING_PORT=\"${PORT:-8080}\" exec /app/.venv/bin/python -m homing.server"]
