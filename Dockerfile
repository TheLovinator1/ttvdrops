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

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

VOLUME ["/root/.local/share/TTVDrops"]
EXPOSE 8000
ENTRYPOINT [ "/app/start.sh" ]
CMD ["uv", "run", "gunicorn", "config.wsgi:application", "--workers", "27", "--bind", "0.0.0.0:8000"]
