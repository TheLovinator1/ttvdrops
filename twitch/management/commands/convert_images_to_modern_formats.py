"""Management command to convert existing images to WebP and AVIF formats."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image

if TYPE_CHECKING:
    from argparse import ArgumentParser

logger = logging.getLogger("ttvdrops")


class Command(BaseCommand):
    """Convert all existing JPG/PNG images to WebP and AVIF formats."""

    help = "Convert existing images in MEDIA_ROOT to WebP and AVIF formats"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Add command-line arguments."""
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing WebP/AVIF files",
        )
        parser.add_argument(
            "--quality",
            type=int,
            default=85,
            help="Quality for WebP/AVIF encoding (1-100, default: 85)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be converted without actually converting",
        )

    def handle(self, **options) -> None:
        """Execute the command."""
        overwrite: bool = bool(options.get("overwrite"))
        quality: int = int(options.get("quality", 85))
        dry_run: bool = bool(options.get("dry_run"))

        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists():
            self.stdout.write(self.style.WARNING(f"MEDIA_ROOT does not exist: {media_root}"))
            return

        # Find all JPG and PNG files
        image_extensions = {".jpg", ".jpeg", ".png"}
        image_files = [f for f in media_root.rglob("*") if f.is_file() and f.suffix.lower() in image_extensions]

        if not image_files:
            self.stdout.write(self.style.SUCCESS("No images found to convert"))
            return

        self.stdout.write(f"Found {len(image_files)} images to process")

        converted_count = 0
        skipped_count = 0
        error_count = 0

        for image_path in image_files:
            base_path = image_path.with_suffix("")

            webp_path = base_path.with_suffix(".webp")
            avif_path = base_path.with_suffix(".avif")

            # Check if conversion is needed
            needs_webp = overwrite or not webp_path.exists()
            needs_avif = overwrite or not avif_path.exists()

            if not needs_webp and not needs_avif:
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(f"Would convert: {image_path.relative_to(media_root)}")
                if needs_webp:
                    self.stdout.write(f"  → {webp_path.relative_to(media_root)}")
                if needs_avif:
                    self.stdout.write(f"  → {avif_path.relative_to(media_root)}")
                converted_count += 1
                continue

            try:
                result = self._convert_image(
                    image_path,
                    webp_path if needs_webp else None,
                    avif_path if needs_avif else None,
                    quality,
                    media_root,
                )
                if result:
                    converted_count += 1
                else:
                    error_count += 1

            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"✗ Error converting {image_path.relative_to(media_root)}: {e}"),
                )
                logger.exception("Failed to convert image: %s", image_path)

        # Summary
        self.stdout.write("\n" + "=" * 50)
        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Dry run complete. Would convert {converted_count} images"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Converted: {converted_count}"))
        self.stdout.write(f"Skipped (already exist): {skipped_count}")
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f"Errors: {error_count}"))

    def _convert_image(
        self,
        source_path: Path,
        webp_path: Path | None,
        avif_path: Path | None,
        quality: int,
        media_root: Path,
    ) -> bool:
        """Convert a single image to WebP and/or AVIF.

        Args:
            source_path: Path to the source image
            webp_path: Path for WebP output (None to skip)
            avif_path: Path for AVIF output (None to skip)
            quality: Quality setting for encoding
            media_root: Media root path for relative display

        Returns:
            True if conversion succeeded, False otherwise
        """
        with Image.open(source_path) as original_img:
            # Convert to RGB if needed (AVIF doesn't support RGBA well)
            rgb_img = self._prepare_image_for_encoding(original_img)

            # Save as WebP
            if webp_path:
                rgb_img.save(
                    webp_path,
                    "WEBP",
                    quality=quality,
                    method=6,  # Slowest but best compression
                )
                self.stdout.write(
                    self.style.SUCCESS(f"✓ WebP: {webp_path.relative_to(media_root)}"),
                )

            # Save as AVIF
            if avif_path:
                rgb_img.save(
                    avif_path,
                    "AVIF",
                    quality=quality,
                    speed=4,  # 0-10, lower is slower but better compression
                )
                self.stdout.write(
                    self.style.SUCCESS(f"✓ AVIF: {avif_path.relative_to(media_root)}"),
                )

        return True

    def _prepare_image_for_encoding(self, img: Image.Image) -> Image.Image:
        """Prepare an image for WebP/AVIF encoding by converting to RGB.

        Args:
            img: Source PIL Image

        Returns:
            RGB PIL Image ready for encoding
        """
        if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
            # Create white background for transparency
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba_img = img.convert("RGBA") if img.mode == "P" else img
            background.paste(rgba_img, mask=rgba_img.split()[-1] if rgba_img.mode in {"RGBA", "LA"} else None)
            return background
        if img.mode != "RGB":
            return img.convert("RGB")
        return img
