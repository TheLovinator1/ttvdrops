"""Template tags for Discord link-preview component embeds."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django import template
from django.utils.safestring import SafeString
from django.utils.safestring import mark_safe

from core.base_url import build_absolute_uri
from core.discord_embed import DEFAULT_ACCENT_COLOR
from core.discord_embed import build_component_embed

if TYPE_CHECKING:
    from django.template.context import Context

register = template.Library()

FALLBACK_TITLE = "TTVDrops - Twitch, Kick and Chzzk Drops"
FALLBACK_DESCRIPTION = (
    "Archive of Twitch, Kick and Chzzk drops, campaigns, and rewards."
)


@register.simple_tag(takes_context=True)
def discord_component_embed(context: Context) -> SafeString:
    """Render the Discord component-embed script for the current page.

    The tag reads the same SEO context used for Open Graph tags, plus an
    optional ``discord_embed_buttons`` list and ``discord_embed_accent_color``.
    It renders nothing when a payload cannot fit within Discord's size limit.

    Args:
        context: Template context for the current request.

    Returns:
        SafeString: A ``<script>`` element with the component embed payload.
    """
    title: str = context.get("page_title") or FALLBACK_TITLE
    description: str = context.get("page_description") or FALLBACK_DESCRIPTION
    image_url: str = context.get("page_image") or ""
    if image_url:
        image_url = build_absolute_uri(image_url)

    buttons: Any = context.get("discord_embed_buttons") or []
    gallery: Any = context.get("discord_embed_gallery") or []
    accent_color: int = (
        context.get("discord_embed_accent_color") or DEFAULT_ACCENT_COLOR
    )

    payload: str = build_component_embed(
        title=title,
        description=description,
        image_url=image_url,
        gallery=gallery,
        buttons=buttons,
        accent_color=accent_color,
    )
    if not payload:
        return mark_safe("")

    return mark_safe(  # ruff: ignore[suspicious-mark-safe-usage] - JSON is escaped by build_component_embed
        f'<script id="discord:component-embed" type="application/json">{payload}</script>',
    )
