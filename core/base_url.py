from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from django.conf import settings

if TYPE_CHECKING:
    from urllib.parse import SplitResult

    from django.http import HttpRequest


def _get_base_url() -> str:
    """Get normalized BASE_URL from settings.

    Returns:
        str: The configured BASE_URL without trailing slash.
    """
    base_url: str = getattr(settings, "BASE_URL", "")
    return base_url.rstrip("/") if base_url else ""


def build_absolute_uri(
    location: str | None = None,
    request: HttpRequest | None = None,
) -> str:
    """Build an absolute URI via BASE_URL (preferred) or request fallback.

    Args:
        location: Relative path ('/foo/') or absolute URL.
        request: Optional HttpRequest to resolve path when location is None.

    Returns:
        str: Fully resolved absolute URL.
    """
    base_url: str = _get_base_url()

    if location is None:
        if request is not None:
            location = request.get_full_path()
        else:
            return f"{base_url}/" if base_url else "/"

    parsed: SplitResult = urlsplit(location)
    if parsed.scheme and parsed.netloc:
        return location

    if base_url:
        if location.startswith("/"):
            return f"{base_url}{location}"
        return f"{base_url}/{location.lstrip('/')}"

    if request is not None:
        return request.build_absolute_uri(location)

    return location


def is_secure() -> bool:
    """Return whether the configured BASE_URL uses HTTPS."""
    base_url: str = _get_base_url()
    return base_url.startswith("https://") if base_url else False


@dataclass
class _TTVDropsSite:
    domain: str


def get_current_site(request: HttpRequest | None) -> _TTVDropsSite:
    """Return a site-like object with domain derived from BASE_URL."""
    base_url: str = _get_base_url()
    parts: SplitResult = urlsplit(base_url)
    domain: str = parts.netloc or parts.path
    return _TTVDropsSite(domain=domain)


def apply_base_url_patches() -> None:
    """No-op; use build_absolute_uri() helper explicitly."""
    return
