"""Build Discord component embed payloads for link previews.

Discord renders a custom link preview when a page includes a
``discord:component-embed`` script or link element in its server-rendered
``<head>``. The payload must be a JSON object with a single top-level
Container component and must stay under 3,000 bytes.

See https://docs.discord.com/developers/link-previews/component-embeds
"""

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from core.base_url import build_absolute_uri

if TYPE_CHECKING:
    from collections.abc import Iterable

MAX_EMBED_BYTES = 3000
MAX_BUTTONS = 5
MAX_BUTTON_LABEL_LENGTH = 80
MAX_BUTTON_URL_LENGTH = 512
MAX_IMAGE_URL_LENGTH = 2048
MAX_TITLE_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 300
MAX_GALLERY_ITEMS = 10
MAX_GALLERY_DESCRIPTION_LENGTH = 100

DEFAULT_ACCENT_COLOR = 5793266
TWITCH_ACCENT_COLOR = 0x9146FF
KICK_ACCENT_COLOR = 0x53FC18
CHZZK_ACCENT_COLOR = 0x00FFA3

_MARKDOWN_ESCAPE_CHARS = ("\\", "*", "_", "~", "`", "|", ">", "[", "]", "#")


def _escape_markdown(text: str) -> str:
    """Escape characters that would otherwise be interpreted as markdown.

    Args:
        text: Plain text to escape.

    Returns:
        str: Text with Discord markdown characters escaped.
    """
    for char in _MARKDOWN_ESCAPE_CHARS:
        text = text.replace(char, f"\\{char}")
    return text


def _clean_text(text: str, limit: int) -> str:
    """Collapse whitespace and truncate text on a word boundary.

    Args:
        text: Text to clean.
        limit: Maximum length before escaping markdown.

    Returns:
        str: Cleaned and markdown-escaped text.
    """
    cleaned: str = " ".join((text or "").split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return _escape_markdown(cleaned)


def _valid_url(url: str, *, max_length: int) -> str:
    """Return the URL when it is a usable absolute HTTP(S) URL.

    Args:
        url: URL to validate.
        max_length: Maximum allowed URL length.

    Returns:
        str: The URL, or an empty string when it cannot be used.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    if len(url) > max_length:
        return ""
    return url


def _absolute_http_url(url: str, *, max_length: int) -> str:
    """Resolve a relative URL and validate that the result is HTTP(S).

    URLs with a non-HTTP scheme (for example ``javascript:``) are rejected
    before they can be turned into an absolute URL.

    Args:
        url: Relative or absolute URL.
        max_length: Maximum allowed URL length.

    Returns:
        str: Absolute HTTP(S) URL, or an empty string when unusable.
    """
    url = (url or "").strip()
    if not url:
        return ""

    scheme: str = urlsplit(url).scheme.lower()
    if scheme and scheme not in {"http", "https"}:
        return ""

    return _valid_url(build_absolute_uri(url), max_length=max_length)


def embed_buttons(*entries: tuple[str, str]) -> list[dict[str, str]]:
    """Build a list of link buttons, skipping entries without a usable URL.

    Relative URLs are resolved against ``settings.BASE_URL`` so the resulting
    buttons always point at absolute URLs.

    Args:
        *entries: ``(label, url)`` pairs.

    Returns:
        list[dict[str, str]]: Button definitions for the component embed.
    """
    buttons: list[dict[str, str]] = []
    for label, url in entries:
        absolute_url: str = _absolute_http_url(
            url,
            max_length=MAX_BUTTON_URL_LENGTH,
        )
        if not label or not absolute_url:
            continue
        buttons.append({"label": label[:MAX_BUTTON_LABEL_LENGTH], "url": absolute_url})
    return buttons


def _normalize_buttons(
    buttons: Iterable[Mapping[str, str] | tuple[str, str]],
) -> list[dict[str, str]]:
    """Validate and normalize button definitions.

    Args:
        buttons: Mapping entries with ``label`` and ``url`` keys, or tuples.

    Returns:
        list[dict[str, str]]: At most five valid link buttons.
    """
    normalized: list[dict[str, str]] = []
    for button in buttons:
        if isinstance(button, Mapping):
            label = str(button.get("label", ""))
            url = str(button.get("url", ""))
        else:
            label, url = button

        absolute_url: str = _absolute_http_url(
            url,
            max_length=MAX_BUTTON_URL_LENGTH,
        )
        if not label or not absolute_url:
            continue

        normalized.append({
            "label": label.strip()[:MAX_BUTTON_LABEL_LENGTH],
            "url": absolute_url,
        })
        if len(normalized) >= MAX_BUTTONS:
            break
    return normalized


def _normalize_gallery(
    items: Iterable[Mapping[str, str] | tuple[str, str]],
) -> list[dict[str, str]]:
    """Validate and normalize media gallery items.

    Args:
        items: Mapping entries with ``url`` and optional ``description`` keys,
            or ``(url, description)`` tuples.

    Returns:
        list[dict[str, str]]: At most ten valid media gallery items.
    """
    gallery: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in items:
        if isinstance(item, Mapping):
            url = str(item.get("url", ""))
            description = str(item.get("description", ""))
        else:
            url, description = item

        absolute_url: str = _absolute_http_url(
            url,
            max_length=MAX_IMAGE_URL_LENGTH,
        )
        if not absolute_url or absolute_url in seen_urls:
            continue

        seen_urls.add(absolute_url)
        gallery_item: dict[str, str] = {"url": absolute_url}
        description_text: str = _clean_text(
            description,
            MAX_GALLERY_DESCRIPTION_LENGTH,
        )
        if description_text:
            gallery_item["description"] = description_text

        gallery.append(gallery_item)
        if len(gallery) >= MAX_GALLERY_ITEMS:
            break
    return gallery


def _serialize(payload: dict[str, object]) -> str:
    """Serialize the payload compactly, escaping characters that could break HTML.

    Args:
        payload: Component embed payload.

    Returns:
        str: Compact JSON safe to embed inside a ``<script>`` element.
    """
    data: str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _make_payload(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]
    title: str,
    description: str,
    image_url: str,
    gallery: list[dict[str, str]],
    buttons: list[dict[str, str]],
    accent_color: int,
) -> dict[str, object]:
    """Assemble the Container component for the embed.

    Args:
        title: Markdown-escaped title.
        description: Markdown-escaped description, may be empty.
        image_url: Absolute thumbnail URL, may be empty.
        gallery: Valid media gallery items.
        buttons: Valid link buttons.
        accent_color: Container accent color as a 24-bit integer.

    Returns:
        dict[str, object]: Component embed payload.
    """
    content: str = f"# {title}"
    if description:
        content += f"\n{description}"

    text_display: dict[str, object] = {"type": 10, "content": content}
    components: list[dict[str, object]] = []
    if image_url:
        components.append({
            "type": 9,
            "components": [text_display],
            "accessory": {"type": 11, "media": {"url": image_url}},
        })
    else:
        components.append(text_display)

    if gallery:
        components.extend((
            {"type": 14, "spacing": 1},
            {
                "type": 12,
                "items": [
                    {
                        "media": {"url": item["url"]},
                        **(
                            {"description": item["description"]}
                            if item.get("description")
                            else {}
                        ),
                    }
                    for item in gallery
                ],
            },
        ))

    if buttons:
        components.extend((
            {"type": 14, "spacing": 1},
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": button["label"],
                        "url": button["url"],
                    }
                    for button in buttons
                ],
            },
        ))

    return {
        "component": {
            "type": 17,
            "accent_color": accent_color,
            "components": components,
        },
    }


def build_component_embed(  # ruff:ignore[too-many-arguments]
    *,
    title: str,
    description: str = "",
    image_url: str = "",
    gallery: Iterable[Mapping[str, str] | tuple[str, str]] = (),
    buttons: Iterable[Mapping[str, str] | tuple[str, str]] = (),
    accent_color: int = DEFAULT_ACCENT_COLOR,
    max_bytes: int = MAX_EMBED_BYTES,
) -> str:
    """Build a Discord component embed JSON payload under the size limit.

    Optional parts are dropped in order of least importance until the payload
    fits within Discord's 3,000-byte limit.

    Args:
        title: Embed title.
        description: Short description shown under the title.
        image_url: Optional thumbnail image URL.
        gallery: Optional media gallery images shown below the text.
        buttons: Optional link buttons.
        accent_color: Container accent color as a 24-bit integer.
        max_bytes: Maximum serialized payload size in bytes.

    Returns:
        str: Compact JSON payload, or an empty string if nothing fits.
    """
    image: str = _absolute_http_url(image_url, max_length=MAX_IMAGE_URL_LENGTH)
    normalized_gallery: list[dict[str, str]] = _normalize_gallery(gallery)
    normalized_buttons: list[dict[str, str]] = _normalize_buttons(buttons)

    title_candidates: list[str] = []
    for limit in (MAX_TITLE_LENGTH, 80, 50, 30):
        candidate: str = _clean_text(title, limit)
        if candidate and candidate not in title_candidates:
            title_candidates.append(candidate)
    if not title_candidates:
        title_candidates = ["TTVDrops"]

    description_candidates: list[str] = []
    for limit in (MAX_DESCRIPTION_LENGTH, 200, 140, 90, 50, 0):
        candidate = _clean_text(description, limit) if limit else ""
        if candidate not in description_candidates:
            description_candidates.append(candidate)

    candidates: Iterable[tuple[str, str, bool, int, int]] = (
        (
            title_text,
            description_text,
            keep_image,
            button_count,
            gallery_count,
        )
        for title_text in title_candidates
        for description_text in description_candidates
        for keep_image in (True, False)
        if not (keep_image and not image)
        for button_count in range(len(normalized_buttons), -1, -1)
        for gallery_count in range(len(normalized_gallery), -1, -1)
    )
    for (
        title_text,
        description_text,
        keep_image,
        button_count,
        gallery_count,
    ) in candidates:
        payload: dict[str, object] = _make_payload(
            title_text,
            description_text,
            image if keep_image else "",
            normalized_gallery[:gallery_count],
            normalized_buttons[:button_count],
            accent_color,
        )
        data: str = _serialize(payload)
        if len(data.encode("utf-8")) <= max_bytes:
            return data

    return ""
