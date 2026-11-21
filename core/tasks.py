from __future__ import annotations

import logging

from celery import shared_task
from django.core.management import call_command

logger: logging.Logger = logging.getLogger("ttvdrops.tasks")


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def submit_indexnow_task(self) -> None:  # ruff:ignore[missing-type-function-argument]
    """Submit all site URLs to the IndexNow search index."""
    try:
        call_command("submit_indexnow")
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def publish_bluesky_post(
    self,  # ruff:ignore[missing-type-function-argument]
    platform: str,
    drop_id: str,
) -> None:
    """Publish a newly discovered drop to Bluesky."""
    from core.bluesky import BlueskyNotConfiguredError  # ruff:ignore[import-outside-top-level]
    from core.bluesky import BlueskyRateLimitedError  # ruff:ignore[import-outside-top-level]
    from core.bluesky import publish_drop_to_bluesky  # ruff:ignore[import-outside-top-level]

    try:
        publish_drop_to_bluesky(platform, drop_id)
    except BlueskyNotConfiguredError:
        logger.warning(
            "Bluesky posting is not configured; skipping %s:%s",
            platform,
            drop_id,
        )
    except BlueskyRateLimitedError as exc:
        raise self.retry(countdown=300, exc=exc) from exc
    except Exception as exc:
        logger.exception("Could not publish %s:%s to Bluesky", platform, drop_id)
        raise self.retry(exc=exc) from exc
