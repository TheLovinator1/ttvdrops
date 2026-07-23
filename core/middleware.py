from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest
    from django.http import HttpResponse


class TimezoneMiddleware:
    """Activate the user's timezone from a cookie set by the browser."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the get_response callable."""
        self.get_response: Callable[[HttpRequest], HttpResponse] = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Activate the user's timezone if a valid cookie is present.

        Returns:
            HttpResponse: The response from the next middleware or view.
        """
        tzname: str | None = request.COOKIES.get("timezone")
        if tzname:
            try:
                timezone.activate(ZoneInfo(tzname))
            except KeyError, ValueError, TypeError:
                timezone.deactivate()
        else:
            timezone.deactivate()

        return self.get_response(request)
