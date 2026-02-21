"""Custom template tags for rendering responsive images with modern formats."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

if TYPE_CHECKING:
    from django.utils.safestring import SafeText

register = template.Library()


def get_format_url(image_url: str, fmt: str) -> str:
    """Convert an image URL to a different format.

    Args:
        image_url: The original image URL
        fmt: The target format (webp or avif)

    Returns:
        The URL with the new format extension
    """
    if not image_url:
        return ""

    # Parse the URL to separate the path from query params
    parsed = urlparse(image_url)
    path = parsed.path

    # Only convert jpg, jpeg, and png to modern formats
    if not path.lower().endswith((".jpg", ".jpeg", ".png")):
        return image_url

    # Replace extension with new format using string manipulation to preserve forward slashes
    # (Path would convert to backslashes on Windows)
    dot_index = path.rfind(".")
    new_path = path[:dot_index] + f".{fmt}"

    # Reconstruct URL with new path
    return parsed._replace(path=new_path).geturl()


@register.simple_tag
def picture(  # noqa: PLR0913, PLR0917
    src: str,
    alt: str = "",
    width: int | None = None,
    height: int | None = None,
    loading: str = "lazy",
    css_class: str = "",
    style: str = "",
) -> SafeText:
    """Render a responsive picture element with modern image formats.

    Args:
        src: The source image URL (jpg/png fallback)
        alt: Alt text for the image
        width: Width attribute
        height: Height attribute
        loading: Loading strategy (lazy/eager)
        css_class: CSS class to apply
        style: Inline styles to apply

    Returns:
        SafeText containing the picture element HTML
    """
    if not src:
        return SafeString("")

    # For Twitch CDN URLs, skip format conversion and use simple img tag
    if "static-cdn.jtvnw.net" in src:
        return format_html(
            format_string='<img src="{src}"{width}{height}{loading}{css_class}{style}{alt} />',
            src=src,
            width=format_html(' width="{}"', width) if width else "",
            height=format_html(' height="{}"', height) if height else "",
            loading=format_html(' loading="{}"', loading) if loading else "",
            css_class=format_html(' class="{}"', css_class) if css_class else "",
            style=format_html(' style="{}"', style) if style else "",
            alt=format_html(' alt="{}"', alt) if alt is not None else "",
        )

    # Generate URLs for modern formats
    avif_url: str = get_format_url(src, "avif")
    webp_url: str = get_format_url(src, "webp")

    # Build source elements using format_html for safety
    sources: list[SafeString] = []

    # AVIF first (best compression)
    if avif_url != src:
        sources.append(format_html('<source srcset="{}" type="image/avif" />', avif_url))

    # WebP second (good compression, widely supported)
    if webp_url != src:
        sources.append(format_html('<source srcset="{}" type="image/webp" />', webp_url))

    # Build img tag with format_html
    img_html: SafeString = format_html(
        format_string='<img src="{src}"{width}{height}{loading}{css_class}{style}{alt} />',
        src=src,
        width=format_html(' width="{}"', width) if width else "",
        height=format_html(' height="{}"', height) if height else "",
        loading=format_html(' loading="{}"', loading) if loading else "",
        css_class=format_html(' class="{}"', css_class) if css_class else "",
        style=format_html(' style="{}"', style) if style else "",
        alt=format_html(' alt="{}"', alt) if alt is not None else "",
    )

    # Combine all parts safely
    return format_html("<picture>{}{}</picture>", SafeString("".join(sources)), img_html)
