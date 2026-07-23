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
- **Historical archive**: Past campaigns remain searchable and downloadable
- **Search & browse**: Find campaigns by game, organization, channel, or reward
- **Open data**: All data is freely available under CC0

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
4. Rename .env.example to .env (Probably want to use USE_SQLITE=True)
5. Make your changes
6. Run tests with `uv run pytest`
7. Submit a pull request

## TL;DR (Arch Linux + PostgreSQL)

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

### How the duck does permissions work on Linux?

```bash
sudo chown -R ttvdrops:http /home/ttvdrops/.local/share/TTVDrops/media/
sudo find /home/ttvdrops/.local/share/TTVDrops/media -type d -exec chmod 2775 {} \;
sudo find /home/ttvdrops/.local/share/TTVDrops/media -type f -exec chmod 664 {} \;

sudo chown -R ttvdrops:http /home/ttvdrops/.local/share/TTVDrops/datasets/
sudo find /home/ttvdrops/.local/share/TTVDrops/datasets -type d -exec chmod 2775 {} \;
sudo find /home/ttvdrops/.local/share/TTVDrops/datasets -type f -exec chmod 664 {} \;
```
