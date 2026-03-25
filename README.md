# ttvdrops

Get notified when a new drop is available on Twitch

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

Install and enable timers:

```bash
sudo install -m 0644 tools/systemd/ttvdrops-backup.{service,timer} /etc/systemd/system/
sudo install -m 0644 tools/systemd/ttvdrops-import-drops.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ttvdrops-backup.timer ttvdrops-import-drops.timer
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

```bash

```

Start a worker:

```bash
uv run celery -A config worker --loglevel=info
uv run celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
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

### How the duck does permissions work on Linux?

```bash
sudo chown -R ttvdrops:http /home/ttvdrops/.local/share/TTVDrops/media/
sudo chgrp -R http /home/ttvdrops/.local/share/TTVDrops/media
sudo find /home/ttvdrops/.local/share/TTVDrops/media -type d -exec chmod 2775 {} \;
sudo find /home/ttvdrops/.local/share/TTVDrops/media -type f -exec chmod 664 {} \;

sudo chown -R ttvdrops:http /home/ttvdrops/.local/share/TTVDrops/datasets/
sudo chgrp -R http /home/ttvdrops/.local/share/TTVDrops/datasets/
sudo find /home/ttvdrops/.local/share/TTVDrops/datasets -type d -exec chmod 2775 {} \;
sudo find /home/ttvdrops/.local/share/TTVDrops/datasets -type f -exec chmod 664 {} \;
```
