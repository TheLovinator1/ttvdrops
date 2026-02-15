# syntax=docker/dockerfile:1
FROM python:3.14-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV UV_NO_DEV=1
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_NO_CACHE=1

WORKDIR /app
COPY . /app/

RUN uv sync
RUN chmod +x /app/start.sh

ENV PATH="/app/.venv/bin:$PATH"

RUN groupadd -g 1000 ttvdrops \
 && useradd -m -u 1000 -g 1000 -d /home/ttvdrops -s /bin/sh ttvdrops \
 && mkdir -p /home/ttvdrops/.local/share/TTVDrops \
 && chown -R ttvdrops:ttvdrops /home/ttvdrops /app

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

VOLUME ["/home/ttvdrops/.local/share/TTVDrops"]
EXPOSE 8000
USER ttvdrops
ENTRYPOINT [ "/app/start.sh" ]
CMD ["uv", "run", "gunicorn", "config.wsgi:application", "--workers", "27", "--bind", "0.0.0.0:8000"]
