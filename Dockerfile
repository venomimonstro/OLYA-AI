FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG X1_EXTRAS=""
ARG X1_RUNTIME_PROFILE="full"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Git is required by the closed development runtime. Full installations also
# include local document QA binaries. The 6GB starter keeps document rendering
# disabled and omits LibreOffice/poppler to save disk and page-cache pressure.
RUN apt-get update \
    && if [ "$X1_RUNTIME_PROFILE" = "starter_6gb" ]; then \
         DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git ca-certificates; \
       else \
         DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
           git ca-certificates libreoffice-writer poppler-utils fonts-dejavu-core; \
       fi \
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
COPY --chown=x1:x1 model-manifest.json ./model-manifest.json
COPY --chown=x1:x1 regression ./regression

COPY --chown=x1:x1 Dockerfile ./build-inputs/Dockerfile
COPY --chown=x1:x1 docker-compose.yml ./build-inputs/docker-compose.yml
RUN python -m scripts.build_provenance --root /app --write /app/BUILD_PROVENANCE.json --no-manifest >/dev/null \
    && chown x1:x1 /app/BUILD_PROVENANCE.json

USER x1

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --limit-concurrency ${X1_HTTP_LIMIT_CONCURRENCY:-32} --backlog ${X1_HTTP_BACKLOG:-512} --timeout-keep-alive ${X1_HTTP_KEEPALIVE_SECONDS:-5}"]
