# Backend Dockerfile (Production & Cloud-Ready)
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# System deps (psycopg2 + curl for container healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY app /app/app
COPY wsgi.py /app/wsgi.py

RUN useradd --create-home --shell /bin/bash finmind \
  && mkdir -p /tmp/prometheus_multiproc \
  && chown -R finmind:finmind /app /tmp/prometheus_multiproc

USER finmind

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/health || exit 1

CMD ["sh", "-c", "export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc && rm -rf $PROMETHEUS_MULTIPROC_DIR && mkdir -p $PROMETHEUS_MULTIPROC_DIR && gunicorn -w ${WEB_CONCURRENCY:-2} -k gthread --threads 4 -b 0.0.0.0:${PORT:-8000} wsgi:app"]
