# Vela VPS relay service — container image built with docker compose.
# `docker compose up --build` builds and runs this. See docker-compose.yml.
FROM python:3.11-slim AS base

# Production-friendly runtime defaults.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data/vela.db

WORKDIR /app

# Install runtime dependencies first for better layer caching.
# requirements.txt already pins uvicorn + websockets + PyYAML + firebase-admin + slowapi.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, config and entrypoint. config.yaml ships as the
# in-image default; every value is overridable via env vars (see .env.example)
# so you never need to rebuild the image to reconfigure the relay.
COPY main.py config.yaml ./
COPY app ./app
COPY routers ./routers
COPY services ./services

# Persistent state lives in /app/data (bind-mounted from the host) and the
# Firebase Admin service-account JSON lives in /run/secrets (read-only mount).
# Both directories are created here so the image runs whether or not the host
# has mounted anything yet.
RUN mkdir -p /app/data /run/secrets /app/secrets

VOLUME ["/app/data"]

EXPOSE 8000

# Single-worker uvicorn. The container listen port is intentionally fixed at
# 8000 (the image's contract); vary only the *host* port in docker-compose.yml.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)" \
    || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
