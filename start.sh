#!/bin/sh
set -e

echo "Collecting static files..."
uv run python manage.py collectstatic --noinput

echo "Starting Django server..."
exec "$@"
