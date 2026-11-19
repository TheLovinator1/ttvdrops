# ttvdrops

Track limited-time free rewards across Twitch, Kick, and Chzzk.

Streaming platforms often give away free in-game items, channel perks, and other rewards
(called "drops") to viewers who meet certain conditions, like watching a streamer play a
game for a set amount of time (Time-Based Drops) or subscribing to a participating channel
(Subscription-Based Drops). These offers are organized into "Drops Campaigns":
collections of rewards distributed during a specified time period, each with its own start
and end dates and requirements. Campaigns are only available for a limited window and can
be scattered across different platforms, making them easy to miss.

ttvdrops.lovinator.space monitors these campaigns across multiple platforms, archives their data for
historical reference, and lets you subscribe to RSS/Atom feeds so you never miss a new
offer. All historical campaign data is available as [open datasets](https://github.com/TheLovinator1/ttvdrops)
licensed under CC0.

## Features

- **Multi-platform**: Twitch, Kick, and Chzzk
- **RSS/Atom feeds**: Subscribe to campaigns, games, organizations, and rewards
- **MCP server**: Search drops, rewards, games, organizations, and dates from AI clients at `/mcp`
- **Historical archive**: Past campaigns remain searchable and downloadable
- **Search & browse**: Find campaigns by game, organization, channel, or reward
- **Open data**: All data is freely available under CC0

## MCP server

The site exposes an [MCP](https://modelcontextprotocol.io/) server at
`https://ttvdrops.lovinator.space/mcp` for AI clients. It is read-only and
unauthenticated, matching the public website and API.

Available tools:

- `search_drops`: Search campaigns by text, platform, status, game, organization, and date.
- `list_drops_between_dates`: List campaigns running during an inclusive date range.
- `get_drop`: Get a campaign's rewards, watch requirements, channels, and links.
- `search_games`: Find Twitch games and Kick categories with campaign counts.
- `search_organizations`: Find Twitch and Kick organizations with campaign counts.
- `get_stats`: Get campaign counts by platform and status.

Add it to an MCP client configuration as a Streamable HTTP server:

```json
{
  "mcpServers": {
    "ttvdrops": {
      "type": "http",
      "url": "https://ttvdrops.lovinator.space/mcp"
    }
  }
}
```

## Project links

- **Website**: [ttvdrops.lovinator.space](https://ttvdrops.lovinator.space)
- **Repository**: [github.com/TheLovinator1/ttvdrops](https://github.com/TheLovinator1/ttvdrops)
- **Issue tracker**: [github.com/TheLovinator1/ttvdrops/issues](https://github.com/TheLovinator1/ttvdrops/issues)
- **Donate**: [github.com/sponsors/TheLovinator1](https://github.com/sponsors/TheLovinator1)
- **License**: MIT (code), CC0 (data)

## Similar projects

Other sites that track Twitch drops:

- [twitchdrops.app](https://twitchdrops.app/)
- [Fenrisapps Twitch Drops](https://twitch-drops.fenrisapps.com/)
- [twitch-drops-api](https://github.com/SunkwiBOT/twitch-drops-api) (the API that powers some of these tools)
- [Drop Hunter](https://drophunter.app/) (paid, $3/mo)

## Bug reports & feature requests

Found a bug or have an idea? Please [open an issue](https://github.com/TheLovinator1/ttvdrops/issues/new).
Include as much detail as possible, including what you expected, what happened, and steps to reproduce.

You can also email [tlovinator@gmail.com](mailto:tlovinator@gmail.com) or reach out on Discord
(**TheLovinator** / `TheLovinator#9276`).

## Contributing

Contributions are welcome! Here's how to get started:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Install dependencies with `uv sync`
4. Rename .env.example to .env and configure PostgreSQL
5. Make your changes
6. Run tests with `uv run pytest`
7. Submit a pull request

## Docker (recommended)

Docker Compose runs PostgreSQL, Valkey, Gunicorn, a Celery worker, and Celery Beat. The
image includes `git` and `pg_dump`, so no host-side programs are needed.

1. Create `.env` and set `DJANGO_SECRET_KEY`, the Twitch credentials, and `DEBUG=False`
   for production:

   ```bash
   cp .env.example .env
   ```

   Keep `DEBUG=True` for local testing; otherwise `ALLOWED_HOSTS` is fixed to
   `ttvdrops.lovinator.space`. `POSTGRES_PASSWORD` must be non-empty because the database
   container does not fall back to defaults.

2. `compose.yaml` is pinned to the production layout:

   - `/mnt/Docker/Data/ttvdrops/app` for `media/`, `staticfiles/` and `datasets/`
   - `/mnt/Docker/Data/ttvdrops/postgres` for PostgreSQL (mounted at
     `/var/lib/postgresql`, since PG 18 keeps `PGDATA` in `18/docker`)
   - `/mnt/Docker/Data/ttvdrops/valkey` for Valkey
   - `/mnt/fourteen/Data/Responses` for `pending/`, `imported/` and `broken/`
   - container user `1001:1002` (UID 1001, GID of the `responses` group)

   Make sure those directories exist and are writable by that user, or edit the bind
   mounts and `user:` in `compose.yaml` to match your host.

3. Start the stack. The image comes from the Forgejo registry:

   ```bash
   docker compose pull
   docker compose up -d
   ```

   Log in first if the registry is private:

   ```bash
   docker login git.lovinator.space
   ```

4. Optional: create an admin and follow the logs:

   ```bash
   docker compose exec web python manage.py createsuperuser
   docker compose logs -f web celery-worker celery-beat
   ```

The site is published on `127.0.0.1:8000` (edit the `web` port mapping in
`compose.yaml` to change it). `/static/` and `/media/` are under
`/mnt/Docker/Data/ttvdrops/app`, so point the reverse proxy there. All data is on host
bind mounts, so `docker compose down` and `down -v` leave it untouched.

| Action                     | Command                                                                                                   |
| -------------------------- | --------------------------------------------------------------------------------------------------------- |
| Stop                       | `docker compose down`                                                                                     |
| Stop and remove containers | `docker compose down -v` (data stays on the bind mounts)                                                   |
| Shell in the web container | `docker compose exec web sh`                                                                              |
| Run a management command   | `docker compose exec web python manage.py <command>`                                                      |
| Import pending drops       | `docker compose exec web python manage.py better_import_drops /srv/ttvdrops/responses/pending --recursive` |
| Create a database backup   | `docker compose exec celery-worker python manage.py backup_db`                                            |
| Flower task monitor        | `docker compose --profile monitoring up -d`, then open <http://127.0.0.1:5555>                            |

### Migrating from the host install

Move the host install's data directory into the bind mount before the first start:

```bash
sudo mkdir -p /mnt/Docker/Data/ttvdrops
sudo mv ~/.local/share/TTVDrops /mnt/Docker/Data/ttvdrops/app

# Keep the old proxy path working, or update the proxy roots instead.
sudo ln -s /mnt/Docker/Data/ttvdrops/app ~/.local/share/TTVDrops
```

Restore an existing PostgreSQL dump before starting the app:

```bash
pg_dump -Fc -U ttvdrops ttvdrops > /tmp/ttvdrops.dump
docker compose up -d db
docker compose exec -T db pg_restore -U ttvdrops -d ttvdrops --clean --if-exists < /tmp/ttvdrops.dump
docker compose pull
docker compose up -d
```

## TL;DR (Arch Linux + PostgreSQL, host install)

Install and initialize Postgres, then start the service:

```bash
sudo pacman -S postgresql
sudo -u postgres initdb -D /var/lib/postgres/data
sudo systemctl enable --now postgresql
```

Create a local role and database:

```bash
sudo -u postgres createuser -P ttvdrops
sudo -u postgres createdb -O ttvdrops ttvdrops
```

Point Django at the unix socket used by Arch (`/run/postgresql`):

```bash
POSTGRES_USER=ttvdrops
POSTGRES_PASSWORD=your_password
POSTGRES_DB=ttvdrops
POSTGRES_HOST=/run/postgresql
POSTGRES_PORT=5432
```

### Linux (Systemd)

```bash
sudo useradd --create-home --home-dir /home/ttvdrops --shell /bin/fish ttvdrops
sudo passwd ttvdrops
sudo usermod -aG wheel ttvdrops
su - ttvdrops
git clone https://git.lovinator.space/TheLovinator/ttvdrops.git
cd ttvdrops
uv sync --no-dev

# Modify .env with the correct database credentials and other settings, then run migrations:
uv run python manage.py migrate
```

Install the systemd service from the repo:

```bash
sudo install -m 0644 tools/systemd/ttvdrops.socket /etc/systemd/system/ttvdrops.socket
sudo install -m 0644 tools/systemd/ttvdrops.service /etc/systemd/system/ttvdrops.service
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ttvdrops.socket
sudo systemctl enable --now ttvdrops.service

curl --unix-socket /run/ttvdrops/ttvdrops.sock https://ttvdrops.lovinator.space
```

Enable Celery worker and Beat services:

```bash
sudo install -m 0644 tools/systemd/ttvdrops-celery-worker.service /etc/systemd/system/
sudo install -m 0644 tools/systemd/ttvdrops-celery-beat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ttvdrops-celery-worker.service
sudo systemctl enable --now ttvdrops-celery-beat.service
```

Set `TTVDROPS_PENDING_DIR` in `.env` to the directory where Twitch JSON drop files are dropped:

```env
TTVDROPS_PENDING_DIR=/mnt/fourteen/Data/Responses/pending
```

## Development

```bash
uv run python manage.py createsuperuser
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py collectstatic
uv run python manage.py runserver
uv run pytest
```

## Celery

Celery powers all periodic and background work. Three services are required:

| Service file                     | Queues                              | Purpose                           |
| -------------------------------- | ----------------------------------- | --------------------------------- |
| `ttvdrops-celery-worker.service` | `imports`, `api-fetches`, `default` | Twitch/Kick/Chzzk imports, backup |
| `ttvdrops-celery-beat.service`   | -                                   | Periodic task scheduler (Beat)    |

Start workers manually during development:

```bash
# All-in-one worker (development)
uv run celery -A config worker --queues imports,api-fetches,image-downloads,default --loglevel=info

# Beat scheduler (requires database Beat tables, run migrate first)
uv run celery -A config beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info

# Monitor tasks in the browser (optional)
uv run celery -A config flower
```

**Periodic tasks configured via `CELERY_BEAT_SCHEDULE`:**

| Task                                     | Schedule         | Queue             |
| ---------------------------------------- | ---------------- | ----------------- |
| `twitch.tasks.scan_pending_twitch_files` | every 10 s       | `imports`         |
| `kick.tasks.import_kick_drops`           | :01/:16/:31/:46  | `api-fetches`     |
| `chzzk.tasks.discover_chzzk_campaigns`   | every 2 h        | `api-fetches`     |
| `twitch.tasks.backup_database`           | daily 02:15 UTC  | `default`         |
| `twitch.tasks.download_all_images`       | Sunday 04:00 UTC | `image-downloads` |
| `twitch.tasks.import_chat_badges`        | Sunday 03:00 UTC | `api-fetches`     |

Image downloads also run **immediately** on record creation via `post_save` signals
(`Game`, `DropCampaign`, `DropBenefit`, `RewardCampaign`).

## Import Drops

```bash
uv run python manage.py better_import_drops <file|dir> [--recursive] [--verbose] [--crash-on-error] [--skip-broken-moves] [--keep-files]
```

## Build history from old response files

`build_history` replays historical Twitch JSON files and backdates their
`django-pghistory` events to the timestamp embedded in each filename.

```bash
uv run python manage.py build_history <file|dir> [--recursive] [--dry-run]
```

- The default path is `TTVDROPS_IMPORTED_DIR`.
- Source files are strictly read-only: they are never moved, renamed, or deleted.
- Files without a parsable timestamp are copied to
  `history_missing_timestamp/<relative source path>/` in the project directory and
  skipped. The originals stay where they are.
- Completed files are recorded in `pghistory` context metadata, so a rerun resumes
  automatically without reprocessing them.

Browse the resulting changelog at `/twitch/history/`. Campaign, game,
organization, channel, and reward-campaign detail pages also link to their own
history pages.

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

### How the duck does permissions work on Linux?

```bash
sudo chown -R ttvdrops:http /mnt/Docker/Data/ttvdrops/app/media/
sudo find /mnt/Docker/Data/ttvdrops/app/media -type d -exec chmod 2775 {} \;
sudo find /mnt/Docker/Data/ttvdrops/app/media -type f -exec chmod 664 {} \;

sudo chown -R ttvdrops:http /mnt/Docker/Data/ttvdrops/app/datasets/
sudo find /mnt/Docker/Data/ttvdrops/app/datasets -type d -exec chmod 2775 {} \;
sudo find /mnt/Docker/Data/ttvdrops/app/datasets -type f -exec chmod 664 {} \;
```
