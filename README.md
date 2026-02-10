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

## Import Chat Badges

Import Twitch's global chat badges for archival and reference:

```bash
uv run python manage.py import_chat_badges
```

Requires `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` environment variables to be set.

## Create DB Backup

Create a zstd-compressed SQL dump (only `twitch_` tables) in the datasets directory:

```bash
uv run python manage.py backup_db
```

Optional arguments:

```bash
uv run python manage.py backup_db --output-dir "<path>" --prefix "ttvdrops"
```
