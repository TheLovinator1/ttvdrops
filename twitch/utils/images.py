from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings

logger: logging.Logger = logging.getLogger(__name__)


def _sanitize_filename(name: str) -> str:
    """Return a filesystem-safe filename."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:150] or "file"


def _guess_extension(url: str, content_type: str | None) -> str:
    """Guess a file extension from URL or content-type.

    Args:
        url: Source URL.
        content_type: Optional content type from HTTP response.

    Returns:
        File extension including dot, like ".png".
    """
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return ext
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed
    return ".bin"


def cache_remote_image(url: str, subdir: str, *, timeout: float = 10.0) -> str | None:
    """Download a remote image and save it under MEDIA_ROOT, returning storage path.

    The file name is the SHA256 of the content to de-duplicate downloads.

    Args:
        url: Remote image URL.
        subdir: Sub-directory under MEDIA_ROOT to store the file.
        timeout: Network timeout in seconds.

    Returns:
        Relative storage path (under MEDIA_ROOT) suitable for assigning to FileField.name,
        or None if the operation failed.
    """
    url = (url or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return None

    try:
        # Enforce allowed schemes at runtime too
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        req = Request(url, headers={"User-Agent": "TTVDrops/1.0"})  # noqa: S310
        # nosec: B310 - urlopen allowed because scheme is validated (http/https only)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            content: bytes = resp.read()
            content_type = resp.headers.get("Content-Type")
    except OSError as exc:
        logger.debug("Failed to download image %s: %s", url, exc)
        return None

    if not content:
        return None

    sha = hashlib.sha256(content).hexdigest()
    ext = _guess_extension(url, content_type)
    # Shard into two-level directories by hash for scalability
    shard1, shard2 = sha[:2], sha[2:4]
    media_subdir = Path(subdir) / shard1 / shard2
    target_dir: Path = Path(settings.MEDIA_ROOT) / media_subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{sha}{ext}"
    storage_rel_path = str(media_subdir / _sanitize_filename(filename)).replace("\\", "/")
    storage_abs_path = Path(settings.MEDIA_ROOT) / storage_rel_path

    if not storage_abs_path.exists():
        try:
            storage_abs_path.write_bytes(content)
        except OSError as exc:
            logger.debug("Failed to write image %s: %s", storage_abs_path, exc)
            return None

    return storage_rel_path
