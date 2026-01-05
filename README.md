# ttvdrops

Get notified when a new drop is available on Twitch

## Development

```bash
uv run python manage.py createsuperuser
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py collectstatic
uv run python manage.py runserver
uv run pytest
```

## Import Drops

```bash
uv run python manage.py better_import_drops <file|dir> [--recursive] [--verbose] [--crash-on-error] [--skip-broken-moves]
```
