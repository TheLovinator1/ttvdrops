# Project Guidelines

## Code Style
- Follow PEP 8 with a 120 character line limit.
- Use double quotes for Python strings.
- Keep comments focused on why, not what.

## Build and Test
- Install/update dependencies with `uv sync -U`.
- Run tests with `uv run pytest`.
- Run Django commands with `uv run python manage.py <command>`.
- Typical local sequence: `uv run python manage.py makemigrations`, `uv run python manage.py migrate`, `uv run python manage.py runserver`.
- Before finishing model changes, verify migrations with `uv run python manage.py makemigrations --check --dry-run`.

## Architecture
- This is a Django multi-app project:
  - `twitch/`, `kick/`, `chzzk/`, and `youtube/` hold platform-specific domain logic.
  - `core/` holds shared cross-app utilities (base URL, SEO, shared views/helpers).
  - `config/` holds settings, URLs, WSGI, and Celery app wiring.
- Favor Django's batteries-included approach before adding new third-party dependencies.
- Follow MTV with fat models and thin views; keep template logic simple.

## Conventions
- Models should include `__str__` and explicit `Meta` options (ordering/indexes/verbose names) where relevant.
- Use `blank=True` for optional form fields and `null=True` for optional database fields.
- Prefer `get_object_or_404` over manual DoesNotExist handling in views.
- URL patterns should use descriptive names and trailing slashes.
- For schema models that parse external APIs, prefer Pydantic `extra="forbid"` and normalize variant payloads with validators.
- Add tests for both positive and negative scenarios, especially when handling multiple upstream payload formats.

## Environment and Pitfalls
- `DJANGO_SECRET_KEY` must be set; the app exits if it is missing.
- Database defaults to PostgreSQL unless `USE_SQLITE=true`; tests use in-memory SQLite.
- Redis is used for cache and Celery. Keep `REDIS_URL_CACHE` and `REDIS_URL_CELERY` configured in environments that run background tasks.
- Static and media files are stored under the platform data directory configured in `config/settings.py`, not in the repo tree.

## Documentation
- Link to existing docs instead of duplicating them.
- Use `README.md` as the source of truth for system setup, deployment, Celery, and management command examples.
- Only create new documentation files when explicitly requested.
