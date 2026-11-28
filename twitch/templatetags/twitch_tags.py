"""Template helpers for Twitch pages."""

from django import template

register = template.Library()


@register.filter
def humanize_code(value: str | None) -> str:
    """Turn an API-style identifier into a readable label.

    Args:
        value: Identifier such as ``DIRECT_ENTITLEMENT``.

    Returns:
        Readable text such as ``Direct entitlement``.
    """
    if not value:
        return ""
    return value.replace("_", " ").capitalize()


@register.filter
def is_redundant_name(value: str | None, parent_name: str | None) -> bool:
    """Return True when a child name repeats the parent name.

    Args:
        value: Child name.
        parent_name: Parent name.

    Returns:
        True when the child name is equal to or contained in the parent name.
    """
    if not value or not parent_name:
        return False
    child = value.casefold().strip()
    parent = parent_name.casefold().strip()
    return child == parent or child in parent
