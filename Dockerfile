FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG X1_EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Git is required by the closed development runtime. LibreOffice + poppler are
# required by the document QA/release path; without them a generated DOCX can
# never pass the production render gate. Keep a basic font set installed so PDF
# layout is reproducible instead of depending on accidental host fonts.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       git ca-certificates libreoffice-writer poppler-utils fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

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

# One worker is intentional: in-memory admission/governor state is authoritative
# on a single low-cost node. Uvicorn bounds HTTP tasks before the expensive
# inference queue, so a connection storm cannot create unbounded request tasks.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --limit-concurrency ${X1_HTTP_LIMIT_CONCURRENCY:-128} --backlog ${X1_HTTP_BACKLOG:-2048} --timeout-keep-alive ${X1_HTTP_KEEPALIVE_SECONDS:-5}"]
