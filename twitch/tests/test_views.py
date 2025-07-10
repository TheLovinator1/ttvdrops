from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone

from twitch.models import DropCampaign, Game, Organization

if TYPE_CHECKING:
    from django.test import Client


@pytest.mark.django_db
class TestGameDetailView:
    """Test cases for GameDetailView."""

    def test_expired_campaigns_filtering(self, client: Client) -> None:
        """Test that expired campaigns are correctly filtered."""
        # Create test data
        game = Game.objects.create(
            id="123",
            slug="test-game",
            display_name="Test Game",
        )

        organization = Organization.objects.create(
            id="456",
            name="Test Organization",
        )

        now = timezone.now()

        # Create an active campaign
        active_campaign = DropCampaign.objects.create(
            id="active-campaign",
            name="Active Campaign",
            game=game,
            owner=organization,
            start_at=now - timezone.timedelta(days=1),
            end_at=now + timezone.timedelta(days=1),
            status="ACTIVE",
        )

        # Create an expired campaign (end date in the past)
        expired_by_date = DropCampaign.objects.create(
            id="expired-by-date",
            name="Expired By Date",
            game=game,
            owner=organization,
            start_at=now - timezone.timedelta(days=3),
            end_at=now - timezone.timedelta(days=1),
            status="ACTIVE",  # Still marked as active but date is expired
        )

        # Create an expired campaign (status is EXPIRED)
        expired_by_status = DropCampaign.objects.create(
            id="expired-by-status",
            name="Expired By Status",
            game=game,
            owner=organization,
            start_at=now - timezone.timedelta(days=3),
            end_at=now + timezone.timedelta(days=1),
            status="EXPIRED",  # Explicitly expired
        )

        # Get the view context
        url = reverse("twitch:game_detail", kwargs={"pk": game.id})
        response = client.get(url)

        # Check that active_campaigns only contains the active campaign
        active_campaigns = response.context["active_campaigns"]
        assert len(active_campaigns) == 1
        assert active_campaigns[0].id == active_campaign.id

        # Check that expired_campaigns contains only the expired campaigns
        expired_campaigns = response.context["expired_campaigns"]
        assert len(expired_campaigns) == 2
        expired_campaign_ids = [c.id for c in expired_campaigns]
        assert expired_by_date.id in expired_campaign_ids
        assert expired_by_status.id in expired_campaign_ids
        assert active_campaign.id not in expired_campaign_ids
