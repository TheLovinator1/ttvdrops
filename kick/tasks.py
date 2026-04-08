from __future__ import annotations

import logging

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger("ttvdrops.tasks")


@shared_task(bind=True, queue="api-fetches", max_retries=3, default_retry_delay=120)
def import_kick_drops(self) -> None:  # noqa: ANN001
    """Fetch and upsert Kick drop campaigns from the public API."""
    try:
        call_command("import_kick_drops")
    except Exception as exc:
        raise self.retry(exc=exc) from exc
