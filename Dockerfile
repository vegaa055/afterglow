# ─────────────────────────────────────────────────────────────
# Afterglow — Multi-stage Dockerfile
#
# Repo structure:
#   repo/
#   ├── app/
#   │   ├── main.py, scorer.py, solar.py, forecast.py, scheduler.py
#   │   ├── templates/
#   │   └── static/
#   ├── requirements.txt
#   └── Dockerfile          ← build context is repo root
#
# Build:  docker build -t afterglow .
# Run:    docker run -p 8000:8000 afterglow
# ─────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
  gcc \
  libffi-dev \
  && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip

# Install dependencies — copied from repo root (layer-cache friendly)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN groupadd --gid 1001 afterglow \
  && useradd  --uid 1001 --gid afterglow --shell /bin/bash --create-home afterglow

# Copy the venv from builder — no build tools in the final image
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# tzdata is needed by zoneinfo for IANA timezone lookups on slim images
RUN apt-get update && apt-get install -y --no-install-recommends \
  tzdata \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application source from app/ in the build context
# Order: least-changed → most-changed (maximise layer cache hits)
COPY app/templates/  ./templates/
COPY app/static/     ./static/
COPY app/scorer.py   \
  app/solar.py    \
  app/forecast.py \
  app/scheduler.py \
  app/main.py     ./

RUN chown -R afterglow:afterglow /app

USER afterglow

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
  || exit 1

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
