FROM python:3.12-slim

ARG X1_EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
RUN if [ -n "$X1_EXTRAS" ]; then \
        pip install --no-cache-dir ".[${X1_EXTRAS}]"; \
    else \
        pip install --no-cache-dir .; \
    fi

RUN groupadd --gid 10001 x1 \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/x1 x1 \
    && mkdir -p /app/data /app/backups \
    && chown -R x1:x1 /app /home/x1

COPY --chown=x1:x1 alembic.ini ./
COPY --chown=x1:x1 alembic ./alembic
COPY --chown=x1:x1 app ./app
COPY --chown=x1:x1 scripts ./scripts
COPY --chown=x1:x1 tests ./tests

USER x1

# One worker is intentional: in-memory inference admission/governor state must be
# authoritative on a single low-cost node. Uvicorn bounds HTTP tasks before the
# much more expensive inference queue, so a connection storm cannot allocate an
# unbounded number of Python request tasks.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --limit-concurrency ${X1_HTTP_LIMIT_CONCURRENCY:-128} --backlog ${X1_HTTP_BACKLOG:-2048} --timeout-keep-alive ${X1_HTTP_KEEPALIVE_SECONDS:-5}"]
