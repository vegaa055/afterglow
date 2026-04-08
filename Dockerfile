# ─────────────────────────────────────────────────────────────
# Afterglow — Multi-stage Dockerfile
#
# Stage 1 (builder): install dependencies into a clean venv
# Stage 2 (runtime): copy only the venv + app source, no build tools
#
# Build:  docker build -t afterglow .
# Run:    docker run -p 8000:8000 afterglow
# ─────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

# System deps needed to compile any C extensions (none currently,
# but requests / uvicorn pull in a few optional extras)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create and activate a venv so the runtime stage can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip first — avoids resolver warnings on newer packages
RUN pip install --upgrade pip==24.0

# Install dependencies (no project code yet — layer-cache friendly)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN groupadd --gid 1001 afterglow \
 && useradd  --uid 1001 --gid afterglow --shell /bin/bash --create-home afterglow

# Copy the venv from builder — no gcc, no build cache in the final image
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# tzdata is needed by zoneinfo for IANA timezone lookups
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# App directory
WORKDIR /app

# Copy application source
# Order: least-changed → most-changed (maximise layer cache hits)
COPY templates/ ./templates/
COPY static/     ./static/
COPY scorer.py   \
     solar.py    \
     forecast.py \
     scheduler.py \
     main.py     ./

# requests-cache writes a SQLite file at runtime — needs a writable dir
# APScheduler also writes nothing to disk, but keep /app writable just in case
RUN chown -R afterglow:afterglow /app

USER afterglow

# Gunicorn + Uvicorn workers in production.
# Override CMD to use `uvicorn main:app --reload` for local dev.
EXPOSE 8000

# ── Health check ─────────────────────────────────────────────
# Docker / ECS will mark the container unhealthy if /health fails.
# Start period gives APScheduler time to complete its first cache warm.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
  || exit 1

# ── Default command ───────────────────────────────────────────
# 2 Uvicorn workers via Gunicorn. Tune WEB_CONCURRENCY at runtime:
#   docker run -e WEB_CONCURRENCY=4 -p 8000:8000 afterglow
CMD ["sh", "-c", \
  "gunicorn main:app \
     --worker-class uvicorn.workers.UvicornWorker \
     --workers ${WEB_CONCURRENCY:-2} \
     --bind 0.0.0.0:8000 \
     --timeout 120 \
     --keep-alive 5 \
     --access-logfile - \
     --error-logfile - \
     --log-level info"]
