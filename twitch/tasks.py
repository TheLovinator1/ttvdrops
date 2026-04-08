from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from celery import shared_task
from django.core.files.base import ContentFile
from django.core.management import call_command
from PIL.Image import Image

if TYPE_CHECKING:
    from urllib.parse import ParseResult

    from django.db.models.fields.files import ImageFieldFile
    from PIL.Image import Image
    from PIL.ImageFile import ImageFile

logger: logging.Logger = logging.getLogger("ttvdrops.tasks")


@shared_task(bind=True, queue="imports", max_retries=3, default_retry_delay=60)
def scan_pending_twitch_files(self) -> None:  # noqa: ANN001
    """Scan TTVDROPS_PENDING_DIR for JSON files and dispatch an import task for each."""
    pending_dir: str = os.getenv("TTVDROPS_PENDING_DIR", "")
    if not pending_dir:
        logger.debug("TTVDROPS_PENDING_DIR not configured; skipping scan.")
        return

    path = Path(pending_dir)
    if not path.is_dir():
        logger.warning("TTVDROPS_PENDING_DIR %r is not a directory.", pending_dir)
        return

    json_files: list[Path] = [
        f for f in path.iterdir() if f.suffix == ".json" and f.is_file()
    ]
    for json_file in json_files:
        import_twitch_file.delay(str(json_file))

    if json_files:
        logger.info("Dispatched %d Twitch file import task(s).", len(json_files))


@shared_task(bind=True, queue="imports", max_retries=3, default_retry_delay=60)
def import_twitch_file(self, file_path: str) -> None:  # noqa: ANN001
    """Import a single Twitch JSON drop file via BetterImportDrops logic."""
    from twitch.management.commands.better_import_drops import Command as Importer  # noqa: I001, PLC0415

    path = Path(file_path)
    if not path.is_file():
        # Already moved to imported/ or broken/ by a prior run.
        logger.debug("File %s no longer exists; skipping.", file_path)
        return

    try:
        Importer().handle(path=path)
    except Exception as exc:
        logger.exception("Failed to import %s.", file_path)
        raise self.retry(exc=exc) from exc


def _download_and_save(url: str, name: str, file_field: ImageFieldFile) -> bool:
    """Download url and save the content to file_field (Django ImageField).

    Files that are already cached (non-empty .name) are skipped.

    Returns:
        True when the image was saved, False when skipped or on error.
    """
    if not url or file_field is None:
        return False

    if getattr(file_field, "name", None):
        return False  # already cached

    parsed: ParseResult = urlparse(url)
    suffix: str = Path(parsed.path).suffix or ".jpg"
    file_name: str = f"{name}{suffix}"

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("HTTP error downloading image for %r.", name)
        return False

    file_field.save(file_name, ContentFile(response.content), save=True)  # type: ignore[union-attr]

    image_path: str | None = getattr(file_field, "path", None)
    if image_path:
        _convert_to_modern_formats(Path(image_path))

    return True


def _convert_to_modern_formats(source: Path) -> None:
    """Convert *source* image to WebP and AVIF alongside the original."""
    if not source.exists() or source.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        return

    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return

    try:
        with Image.open(source) as raw:
            if raw.mode in {"RGBA", "LA"} or (
                raw.mode == "P" and "transparency" in raw.info
            ):
                background: Image = Image.new("RGB", raw.size, (255, 255, 255))
                rgba: Image | ImageFile = (
                    raw.convert("RGBA") if raw.mode == "P" else raw
                )
                mask: Image | None = (
                    rgba.split()[-1] if rgba.mode in {"RGBA", "LA"} else None
                )
                background.paste(rgba, mask=mask)
                img: Image = background
            elif raw.mode != "RGB":
                img = raw.convert("RGB")
            else:
                img: Image = raw.copy()

        for fmt, ext in (("WEBP", ".webp"), ("AVIF", ".avif")):
            out: Path = source.with_suffix(ext)
            try:
                img.save(out, fmt, quality=80)
            except Exception:  # noqa: BLE001
                logger.debug("Could not convert %s to %s.", source, fmt)
    except Exception:  # noqa: BLE001
        logger.debug("Format conversion failed for %s.", source)


# ---------------------------------------------------------------------------
# Per-model image tasks — triggered by post_save signals on new records
# ---------------------------------------------------------------------------


@shared_task(bind=True, queue="image-downloads", max_retries=3, default_retry_delay=300)
def download_game_image(self, game_pk: int) -> None:  # noqa: ANN001
    """Download and cache the box art image for a single Game."""
    from twitch.models import Game  # noqa: PLC0415
    from twitch.utils import is_twitch_box_art_url  # noqa: PLC0415
    from twitch.utils import normalize_twitch_box_art_url  # noqa: PLC0415

    try:
        game = Game.objects.get(pk=game_pk)
    except Game.DoesNotExist:
        return

    if not game.box_art or not is_twitch_box_art_url(game.box_art):
        return

    url = normalize_twitch_box_art_url(game.box_art)
    try:
        _download_and_save(url, game.twitch_id, game.box_art_file)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, queue="image-downloads", max_retries=3, default_retry_delay=300)
def download_campaign_image(self, campaign_pk: int) -> None:  # noqa: ANN001
    """Download and cache the image for a single DropCampaign."""
    from twitch.models import DropCampaign  # noqa: PLC0415

    try:
        campaign = DropCampaign.objects.get(pk=campaign_pk)
    except DropCampaign.DoesNotExist:
        return

    if not campaign.image_url:
        return

    try:
        _download_and_save(campaign.image_url, campaign.twitch_id, campaign.image_file)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, queue="image-downloads", max_retries=3, default_retry_delay=300)
def download_benefit_image(self, benefit_pk: int) -> None:  # noqa: ANN001
    """Download and cache the image for a single DropBenefit."""
    from twitch.models import DropBenefit  # noqa: PLC0415

    try:
        benefit = DropBenefit.objects.get(pk=benefit_pk)
    except DropBenefit.DoesNotExist:
        return

    if not benefit.image_asset_url:
        return

    try:
        _download_and_save(
            url=benefit.image_asset_url,
            name=benefit.twitch_id,
            file_field=benefit.image_file,
        )
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, queue="image-downloads", max_retries=3, default_retry_delay=300)
def download_reward_campaign_image(self, reward_pk: int) -> None:  # noqa: ANN001
    """Download and cache the image for a single RewardCampaign."""
    from twitch.models import RewardCampaign  # noqa: PLC0415

    try:
        reward = RewardCampaign.objects.get(pk=reward_pk)
    except RewardCampaign.DoesNotExist:
        return

    if not reward.image_url:
        return

    try:
        _download_and_save(reward.image_url, reward.twitch_id, reward.image_file)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@shared_task(queue="image-downloads")
def download_all_images() -> None:
    """Weekly full-refresh: download images for all Twitch models."""
    call_command("download_box_art")
    call_command("download_campaign_images", model="all")


# ---------------------------------------------------------------------------
# Twitch API tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, queue="api-fetches", max_retries=3, default_retry_delay=120)
def import_chat_badges(self) -> None:  # noqa: ANN001
    """Fetch and upsert Twitch global chat badges via the Helix API."""
    try:
        call_command("import_chat_badges")
    except Exception as exc:
        raise self.retry(exc=exc) from exc


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


@shared_task(queue="default")
def backup_database() -> None:
    """Create a zstd-compressed SQL backup of the dataset tables."""
    call_command("backup_db")
