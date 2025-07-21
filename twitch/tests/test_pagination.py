"""Tests for pagination functionality in the campaign list view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone

from twitch.models import DropCampaign, Game, Organization

if TYPE_CHECKING:
    from django.test import Client


@pytest.mark.django_db
class TestCampaignListPagination:
    """Test cases for campaign list pagination."""

    def test_default_pagination(self, client: Client) -> None:
        """Test that pagination works with default settings."""
        # Create test data - enough to require pagination
        game = Game.objects.create(id="test-game", slug="test-game", display_name="Test Game")
        org = Organization.objects.create(id="test-org", name="Test Org")

        # Create 30 campaigns to test pagination
        now = timezone.now()
        campaigns = []
        for i in range(30):
            campaign = DropCampaign.objects.create(
                id=f"campaign-{i}",
                name=f"Campaign {i}",
                game=game,
                owner=org,
                start_at=now,
                end_at=now + timezone.timedelta(days=1),
                status="ACTIVE",
            )
            campaigns.append(campaign)

        # Test first page
        response = client.get(reverse("twitch:campaign_list"))
        assert response.status_code == 200
        assert "page_obj" in response.context
        assert response.context["page_obj"].number == 1
        assert len(response.context["campaigns"]) == 24  # Default paginate_by
        assert response.context["page_obj"].paginator.count == 30

        # Test second page
        response = client.get(reverse("twitch:campaign_list") + "?page=2")
        assert response.status_code == 200
        assert response.context["page_obj"].number == 2
        assert len(response.context["campaigns"]) == 6  # Remaining campaigns

    def test_custom_per_page(self, client: Client) -> None:
        """Test that custom per_page parameter works."""
        # Create test data
        game = Game.objects.create(id="test-game", slug="test-game", display_name="Test Game")
        org = Organization.objects.create(id="test-org", name="Test Org")

        now = timezone.now()
        for i in range(25):
            DropCampaign.objects.create(
                id=f"campaign-{i}",
                name=f"Campaign {i}",
                game=game,
                owner=org,
                start_at=now,
                end_at=now + timezone.timedelta(days=1),
                status="ACTIVE",
            )

        # Test with per_page=12
        response = client.get(reverse("twitch:campaign_list") + "?per_page=12")
        assert response.status_code == 200
        assert len(response.context["campaigns"]) == 12
        assert response.context["selected_per_page"] == 12

        # Test with per_page=48
        response = client.get(reverse("twitch:campaign_list") + "?per_page=48")
        assert response.status_code == 200
        assert len(response.context["campaigns"]) == 25  # All campaigns fit on one page

    def test_invalid_per_page_fallback(self, client: Client) -> None:
        """Test that invalid per_page values fall back to default."""
        # Create test data
        game = Game.objects.create(id="test-game", slug="test-game", display_name="Test Game")
        org = Organization.objects.create(id="test-org", name="Test Org")

        now = timezone.now()
        for i in range(30):
            DropCampaign.objects.create(
                id=f"campaign-{i}",
                name=f"Campaign {i}",
                game=game,
                owner=org,
                start_at=now,
                end_at=now + timezone.timedelta(days=1),
                status="ACTIVE",
            )

        # Test with invalid per_page value
        response = client.get(reverse("twitch:campaign_list") + "?per_page=999")
        assert response.status_code == 200
        assert len(response.context["campaigns"]) == 24  # Falls back to default
        assert response.context["selected_per_page"] == 24

        # Test with non-numeric per_page value
        response = client.get(reverse("twitch:campaign_list") + "?per_page=invalid")
        assert response.status_code == 200
        assert len(response.context["campaigns"]) == 24  # Falls back to default

    def test_pagination_with_filters(self, client: Client) -> None:
        """Test that pagination works correctly with filters."""
        # Create test data with different statuses
        game = Game.objects.create(id="test-game", slug="test-game", display_name="Test Game")
        org = Organization.objects.create(id="test-org", name="Test Org")

        now = timezone.now()
        # Create 20 active campaigns
        for i in range(20):
            DropCampaign.objects.create(
                id=f"active-{i}",
                name=f"Active Campaign {i}",
                game=game,
                owner=org,
                start_at=now,
                end_at=now + timezone.timedelta(days=1),
                status="ACTIVE",
            )

        # Create 10 expired campaigns
        for i in range(10):
            DropCampaign.objects.create(
                id=f"expired-{i}",
                name=f"Expired Campaign {i}",
                game=game,
                owner=org,
                start_at=now - timezone.timedelta(days=2),
                end_at=now - timezone.timedelta(days=1),
                status="EXPIRED",
            )

        # Test filtering by active status with pagination
        response = client.get(reverse("twitch:campaign_list") + "?status=ACTIVE&per_page=12")
        assert response.status_code == 200
        assert len(response.context["campaigns"]) == 12
        assert response.context["page_obj"].paginator.count == 20  # Only active campaigns
        assert all(c.status == "ACTIVE" for c in response.context["campaigns"])

        # Test second page of active campaigns
        response = client.get(reverse("twitch:campaign_list") + "?status=ACTIVE&per_page=12&page=2")
        assert response.status_code == 200
        assert len(response.context["campaigns"]) == 8  # Remaining active campaigns
        assert response.context["page_obj"].number == 2

    def test_context_variables(self, client: Client) -> None:
        """Test that all necessary context variables are present."""
        response = client.get(reverse("twitch:campaign_list"))
        assert response.status_code == 200

        # Check for pagination-related context
        context = response.context
        assert "per_page_options" in context
        assert "selected_per_page" in context
        assert context["per_page_options"] == [12, 24, 48, 96]
        assert context["selected_per_page"] == 24  # Default value
