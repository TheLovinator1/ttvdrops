from __future__ import annotations

import logging

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger("ttvdrops.tasks")


@shared_task(bind=True, queue="default", max_retries=3, default_retry_delay=300)
def submit_indexnow_task(self) -> None:  # noqa: ANN001
    """Submit all site URLs to the IndexNow search index."""
    try:
        call_command("submit_indexnow")
    except Exception as exc:
        raise self.retry(exc=exc) from exc
