from __future__ import annotations

import logging

from celery import shared_task
from django.core.management import call_command

logger: logging.Logger = logging.getLogger("ttvdrops.tasks")


@shared_task(bind=True, queue="imports", max_retries=3, default_retry_delay=60)
def import_chzzk_campaign_task(self, campaign_no: int) -> None:  # noqa: ANN001
    """Import a single Chzzk campaign by its campaign number."""
    try:
        call_command("import_chzzk_campaign", str(campaign_no))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, queue="api-fetches", max_retries=3, default_retry_delay=120)
def discover_chzzk_campaigns(self) -> None:  # noqa: ANN001
    """Discover and import the latest Chzzk campaigns (equivalent to --latest flag)."""
    try:
        call_command("import_chzzk_campaign", latest=True)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
