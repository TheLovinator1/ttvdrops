"""Replace already-cached Twitch images with their highest-resolution variants."""

from concurrent.futures import FIRST_COMPLETED
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import TypedDict
from typing import Unpack
from urllib.parse import urlparse

import httpx
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from tqdm import tqdm

from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.utils import normalize_twitch_image_url

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.core.files.storage import Storage
    from django.core.management.base import CommandParser
    from django.db.models import Model
    from django.db.models import QuerySet


_MODEL_CHOICES = ("games", "campaigns", "benefits", "rewards", "all")


class ReplaceDownloadedImagesOptions(TypedDict, total=False):
    """Options accepted by the replace_downloaded_images command."""

    model: str
    limit: int | None
    dry_run: bool
    include_missing: bool
    workers: int


@dataclass(frozen=True)
class _ImageTask:
    """One image replacement task."""

    item: Model
    source_url: str
    field_name: str
    prefix: str


class Command(BaseCommand):
    """Replace cached local images with normalized high-resolution versions."""

    help = "Replace already-downloaded Twitch images with larger local copies."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command arguments."""
        parser.add_argument(
            "--model",
            choices=_MODEL_CHOICES,
            default="all",
            help="Which images to replace.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit the number of items processed per model.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be replaced without downloading anything.",
        )
        parser.add_argument(
            "--include-missing",
            action="store_true",
            help="Also download images for records that have no local file yet.",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=8,
            help="Number of concurrent downloads (default: 8).",
        )

    def handle(
        self,
        *_args: str,
        **options: Unpack[ReplaceDownloadedImagesOptions],
    ) -> None:
        """Run the image replacement.

        Args:
            *_args: Unused positional arguments.
            **options: Command options.
        """
        model_choice: str = options.get("model") or "all"
        limit: int | None = options.get("limit")
        dry_run: bool = options.get("dry_run", False)
        include_missing: bool = options.get("include_missing", False)
        workers_value: int | None = options.get("workers")
        workers: int = (
            workers_value if workers_value is not None and workers_value > 0 else 8
        )

        stats: dict[str, int] = {
            "replaced": 0,
            "skipped": 0,
            "failed": 0,
        }

        groups: list[tuple[str, QuerySet, str, str, str]] = []
        if model_choice in {"games", "all"}:
            groups.append((
                "games",
                Game.objects.exclude(box_art="").order_by("twitch_id"),
                "box_art_file",
                "box_art",
                "box_art",
            ))
        if model_choice in {"campaigns", "all"}:
            groups.append((
                "campaigns",
                DropCampaign.objects.exclude(image_url="").order_by("twitch_id"),
                "image_file",
                "image_url",
                "image",
            ))
        if model_choice in {"benefits", "all"}:
            groups.append((
                "benefits",
                DropBenefit.objects.exclude(image_asset_url="").order_by("twitch_id"),
                "image_file",
                "image_asset_url",
                "image",
            ))
        if model_choice in {"rewards", "all"}:
            groups.extend((
                (
                    "reward campaigns",
                    RewardCampaign.objects.exclude(image_url="").order_by("twitch_id"),
                    "image_file",
                    "image_url",
                    "image",
                ),
                (
                    "rewards",
                    Reward.objects.order_by("twitch_id"),
                    "image_file",
                    "thumbnail_image_url",
                    "image",
                ),
            ))

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            for label, queryset, field_name, url_field, prefix in groups:
                self.stdout.write(self.style.MIGRATE_HEADING(f"\n{label}"))
                items: Iterable[Model] = queryset[:limit] if limit else queryset
                self._replace_group(
                    client=client,
                    items=items,
                    field_name=field_name,
                    url_field=url_field,
                    prefix=prefix,
                    dry_run=dry_run,
                    include_missing=include_missing,
                    workers=workers,
                    stats=stats,
                )

        if stats["replaced"] and not dry_run:
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    "\nRegenerating missing WebP/AVIF variants...",
                ),
            )
            call_command("convert_images_to_modern_formats")

        self.stdout.write("\n" + "=" * 50)
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Would replace: {stats['replaced']}, "
                    f"skipped: {stats['skipped']}, failed: {stats['failed']}",
                ),
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Replaced: {stats['replaced']}, "
                    f"skipped: {stats['skipped']}, failed: {stats['failed']}",
                ),
            )

    def _replace_group(
        self,
        *,
        client: httpx.Client,
        items: Iterable[Model],
        field_name: str,
        url_field: str,
        prefix: str,
        dry_run: bool,
        include_missing: bool,
        workers: int,
        stats: dict[str, int],
    ) -> None:
        """Replace images for one model group.

        Args:
            client: HTTP client.
            items: Model instances to process.
            field_name: ImageField attribute name.
            url_field: Source URL attribute name.
            prefix: Prefix for width/height/size/mime field names.
            dry_run: Whether to only report.
            include_missing: Whether to download missing files.
            workers: Number of concurrent downloads.
            stats: Shared counters.
        """
        tasks: list[_ImageTask] = []
        for item in items:
            source_url: str = normalize_twitch_image_url(
                str(getattr(item, url_field, "") or ""),
            )
            if not source_url and url_field == "thumbnail_image_url":
                source_url = normalize_twitch_image_url(
                    str(getattr(item, "banner_image_url", "") or ""),
                )
            if not source_url:
                stats["skipped"] += 1
                continue

            field = getattr(item, field_name)
            has_file: bool = bool(field and getattr(field, "name", ""))
            if not has_file and not include_missing:
                stats["skipped"] += 1
                continue

            if dry_run:
                tqdm.write(f"Would replace {item}: {source_url}")
                stats["replaced"] += 1
                continue

            tasks.append(
                _ImageTask(
                    item=item,
                    source_url=source_url,
                    field_name=field_name,
                    prefix=prefix,
                ),
            )

        if not tasks:
            return

        with (
            ThreadPoolExecutor(max_workers=workers) as executor,
            tqdm(total=len(tasks), desc="Replacing", unit="image") as progress,
        ):
            pending: dict[
                Future[httpx.Response | httpx.HTTPError],
                _ImageTask,
            ] = {}
            task_iter = iter(tasks)

            def submit_next() -> None:
                try:
                    task: _ImageTask = next(task_iter)
                except StopIteration:
                    return
                pending[executor.submit(self._download, client, task.source_url)] = task

            for _ in range(workers * 2):
                submit_next()

            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    task = pending.pop(future)
                    result = future.result()
                    if isinstance(result, httpx.HTTPError):
                        stats["failed"] += 1
                        tqdm.write(f"Failed {task.item}: {result}")
                    elif result.url.path.endswith("/ttv-static/404_boxart.jpg"):
                        stats["skipped"] += 1
                    else:
                        self._apply_download(task, result, stats)
                    progress.update(1)
                    submit_next()

    @staticmethod
    def _download(
        client: httpx.Client,
        source_url: str,
    ) -> httpx.Response | httpx.HTTPError:
        """Download one image.

        Args:
            client: HTTP client.
            source_url: Image URL.

        Returns:
            Response on success, HTTP error on failure.
        """
        try:
            response: httpx.Response = client.get(source_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return exc
        return response

    def _apply_download(
        self,
        task: _ImageTask,
        response: httpx.Response,
        stats: dict[str, int],
    ) -> None:
        """Apply a completed download to its model instance.

        Args:
            task: Image task.
            response: Completed HTTP response.
            stats: Shared counters.
        """
        item = task.item
        field = getattr(item, task.field_name)
        suffix: str = Path(urlparse(task.source_url).path).suffix or ".jpg"
        storage = field.storage
        old_name: str = getattr(field, "name", "")
        if old_name:
            # Keep the existing public URL stable so external hotlinks survive.
            # Save through storage directly: field.save() would run upload_to
            # again and turn games/box_art/old.jpg into
            # games/box_art/games/box_art/old.jpg.
            self._delete_file_and_variants(storage, old_name)
            field.name = storage.save(old_name, ContentFile(response.content))
        else:
            file_name: str = f"{getattr(item, 'twitch_id', item.pk)}{suffix}"
            field.save(file_name, ContentFile(response.content), save=False)

        update_fields: list[str] = [task.field_name]
        existing_fields: set[str] = {
            model_field.name
            for model_field in item._meta.get_fields()  # ruff: ignore[private-member-access]
        }
        for suffix_name in ("width", "height", "size_bytes", "mime_type"):
            candidate: str = f"{task.prefix}_{suffix_name}"
            if candidate not in existing_fields:
                continue
            if suffix_name == "size_bytes":
                setattr(item, candidate, len(response.content))
            elif suffix_name == "mime_type":
                setattr(
                    item,
                    candidate,
                    response.headers.get("content-type", "").split(";")[0].strip(),
                )
            update_fields.append(candidate)

        item.save(update_fields=update_fields)
        stats["replaced"] += 1

    @staticmethod
    def _delete_file_and_variants(storage: Storage, name: str) -> None:
        """Delete a stored image and its WebP/AVIF siblings.

        Args:
            storage: Django storage backend.
            name: Stored file name.
        """
        if storage.exists(name):
            storage.delete(name)
        base: Path = Path(name).with_suffix("")
        for extension in (".webp", ".avif"):
            variant: str = str(base) + extension
            if storage.exists(variant):
                storage.delete(variant)
