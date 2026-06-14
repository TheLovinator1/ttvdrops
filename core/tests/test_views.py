from datetime import UTC
from datetime import datetime as dt
from typing import TYPE_CHECKING

from django.test import RequestFactory
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from core.views import _build_base_url
from kick.models import KickCategory
from kick.models import KickDropCampaign
from kick.models import KickOrganization

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse


@override_settings(ALLOWED_HOSTS=["example.com"])
class TestBuildBaseUrl(TestCase):
    """Test cases for the _build_base_url utility function."""

    def setUp(self) -> None:
        """Set up the test case with a request factory."""
        self.factory = RequestFactory()

    def test_valid_base_url(self) -> None:
        """Test that the base URL is built correctly."""
        base_url: str = _build_base_url()
        assert base_url == "https://ttvdrops.lovinator.space"


class TestSitemapViews(TestCase):
    """Test cases for sitemap views."""

    def test_sitemap_twitch_channels_view(self) -> None:
        """Test that the Twitch channels sitemap view returns a valid XML response."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("sitemap-twitch-channels"),
        )
        assert response.status_code == 200
        assert "<urlset" in response.content.decode()

    def test_sitemap_twitch_drops_view(self) -> None:
        """Test that the Twitch drops sitemap view returns a valid XML response."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("sitemap-twitch-drops"),
        )
        assert response.status_code == 200
        assert "<urlset" in response.content.decode()


class TestCoreDashboardKickSection(TestCase):
    """Tests for the Kick campaigns section within the core dashboard."""

    def _make_org(self, kick_id: str, name: str) -> KickOrganization:
        return KickOrganization.objects.create(kick_id=kick_id, name=name)

    def _make_category(self, kick_id: int, name: str) -> KickCategory:
        return KickCategory.objects.create(
            kick_id=kick_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
        )

    def _make_campaign(
        self,
        kick_id: str,
        name: str,
        category: KickCategory | None,
        organization: KickOrganization,
    ) -> KickDropCampaign:
        return KickDropCampaign.objects.create(
            kick_id=kick_id,
            name=name,
            status="active",
            starts_at=dt(2020, 1, 1, tzinfo=UTC),
            ends_at=dt(2099, 12, 31, tzinfo=UTC),
            organization=organization,
            category=category,
            rule_id=1,
            rule_name="Watch to redeem",
            is_fully_imported=True,
        )

    def test_shows_organization_under_game_heading(self) -> None:
        """Organization name should appear under the game heading, not per-campaign."""
        org: KickOrganization = self._make_org("org-test-1", "Test Studio")
        cat: KickCategory = self._make_category(999, "Test Game")
        self._make_campaign("camp-test-1", "Test Campaign", cat, org)

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("core:dashboard"),
        )
        content: str = response.content.decode()

        assert response.status_code == 200
        # Organization should be near the game heading
        assert "Test Studio" in content
        # Game name should appear
        assert "Test Game" in content

    def test_unknown_category_shows_org_link_in_heading(self) -> None:
        """Campaigns without a category should show org name as the heading."""
        org: KickOrganization = self._make_org("org-uncat-1", "Uncategorized Org")
        campaign: KickDropCampaign = self._make_campaign(
            "camp-uncat-1",
            "No Category Campaign",
            None,
            org,
        )

        response: _MonkeyPatchedWSGIResponse = self.client.get(
            reverse("core:dashboard"),
        )
        content: str = response.content.decode()

        assert response.status_code == 200
        assert campaign.name in content
        # Org name should appear as a link to the org detail page
        org_url: str = reverse("kick:organization_detail", args=[org.kick_id])
        assert org_url in content
        assert "Unknown Category" not in content
