from django.test import RequestFactory
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from core.views import _build_base_url


@override_settings(ALLOWED_HOSTS=["example.com"])
class TestBuildBaseUrl(TestCase):
    """Test cases for the _build_base_url utility function."""

    def setUp(self) -> None:
        """Set up the test case with a request factory."""
        self.factory = RequestFactory()

    def test_valid_base_url(self) -> None:
        """Test that the base URL is built correctly."""
        base_url = _build_base_url()
        assert base_url == "https://ttvdrops.lovinator.space"


class TestSitemapViews(TestCase):
    """Test cases for sitemap views."""

    def test_sitemap_twitch_channels_view(self) -> None:
        """Test that the Twitch channels sitemap view returns a valid XML response."""
        response = self.client.get(reverse("sitemap-twitch-channels"))
        assert response.status_code == 200
        assert "<urlset" in response.content.decode()

    def test_sitemap_twitch_drops_view(self) -> None:
        """Test that the Twitch drops sitemap view returns a valid XML response."""
        response = self.client.get(reverse("sitemap-twitch-drops"))
        assert response.status_code == 200
        assert "<urlset" in response.content.decode()
