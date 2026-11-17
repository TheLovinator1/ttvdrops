# syntax=docker/dockerfile:1

FROM python:3.14-slim-trixie

# uv from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_PREFERENCE=only-system \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

# git     -> import commands and the historical-drops tool shell out to git
# pg_dump -> backup_db / nightly backup task. Debian trixie ships a PG17 client,
#            so use PGDG to match the PostgreSQL 18 server used on Arch/Compose.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        git \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install --no-install-recommends -y \
        postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached when only project code changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project

COPY . /app

# Install the project itself and assert uv.lock is up to date.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Run as a regular user. Compose overrides this with ${UID}:${GID} so bind
# mounts (responses, datasets) stay writable by the host user.
RUN groupadd --gid 1000 ttvdrops \
    && useradd --uid 1000 --gid ttvdrops --home-dir /app --shell /usr/sbin/nologin ttvdrops \
    && mkdir -p /app/data \
                /srv/ttvdrops/responses/pending \
                /srv/ttvdrops/responses/imported \
                /srv/ttvdrops/responses/broken \
    && chown -R ttvdrops:ttvdrops /app /srv/ttvdrops/responses

USER ttvdrops

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
