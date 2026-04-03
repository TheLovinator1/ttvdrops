from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from django.http import HttpRequest


def base_url(request: HttpRequest) -> dict[str, str]:
    """Provide BASE_URL to templates for deterministic absolute URL creation.

    Returns:
        dict[str, str]: A dictionary containing the BASE_URL.
    """
    return {
        "BASE_URL": getattr(settings, "BASE_URL", ""),
    }
