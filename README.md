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