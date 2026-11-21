FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FORGEPULSE_HOST=0.0.0.0 \
    FORGEPULSE_PORT=8080 \
    FORGEPULSE_POLICY_PATH=/app/policies/default.yaml \
    FORGEPULSE_DATABASE=/app/.data/forgepulse.sqlite \
    FORGEPULSE_ALLOW_UNSIGNED=false

WORKDIR /app

COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
COPY policies /app/policies
COPY migrations /app/migrations
COPY demo/playground.html /app/demo/playground.html

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/.data \
    && chown -R appuser:appuser /app/.data

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["uvicorn", "forgepulse.app:app", "--host", "0.0.0.0", "--port", "8080"]
