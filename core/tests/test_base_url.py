from typing import TYPE_CHECKING

from django.test import RequestFactory

from core.base_url import build_absolute_uri
from core.base_url import get_current_site

if TYPE_CHECKING:
    from config.tests.test_seo import WSGIRequest
    from core.base_url import _TTVDropsSite


def test_build_absolute_uri_uses_base_url() -> None:
    """Test that build_absolute_uri uses the base URL from settings."""
    request: WSGIRequest = RequestFactory().get("/test-path/")
    assert (
        build_absolute_uri(request=request)
        == "https://ttvdrops.lovinator.space/test-path/"
    )


def test_get_current_site_from_base_url() -> None:
    """Test that get_current_site returns the correct site based on the base URL."""
    site: _TTVDropsSite = get_current_site(None)
    assert site.domain == "ttvdrops.lovinator.space"
