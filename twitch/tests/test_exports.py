import json
from datetime import timedelta

from django.test import Client
from django.test import TestCase
from django.utils import timezone

from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization


class ExportViewsTestCase(TestCase):
    """Test export views for CSV and JSON formats."""

    def setUp(self) -> None:
        """Set up test data."""
        self.client = Client()

        # Create test organization
        self.org = Organization.objects.create(
            twitch_id="org123",
            name="Test Organization",
        )

        # Create test game
        self.game = Game.objects.create(
            twitch_id="game123",
            name="Test Game",
            display_name="Test Game Display",
        )
        self.game.owners.add(self.org)

        # Create test campaign
        now = timezone.now()
        self.campaign = DropCampaign.objects.create(
            twitch_id="campaign123",
            name="Test Campaign",
            description="A test campaign description",
            game=self.game,
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
        )

    def test_export_campaigns_csv(self) -> None:
        """Test CSV export of campaigns."""
        response = self.client.get("/twitch/export/campaigns/csv/")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert b"Twitch ID" in response.content
        assert b"campaign123" in response.content
        assert b"Test Campaign" in response.content

    def test_export_campaigns_json(self) -> None:
        """Test JSON export of campaigns."""
        response = self.client.get("/twitch/export/campaigns/json/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"

        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["twitch_id"] == "campaign123"
        assert data[0]["name"] == "Test Campaign"
        assert data[0]["status"] == "Active"

    def test_export_games_csv(self) -> None:
        """Test CSV export of games."""
        response = self.client.get("/twitch/export/games/csv/")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert b"Twitch ID" in response.content
        assert b"game123" in response.content
        assert b"Test Game Display" in response.content

    def test_export_games_json(self) -> None:
        """Test JSON export of games."""
        response = self.client.get("/twitch/export/games/json/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"

        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["twitch_id"] == "game123"
        assert data[0]["display_name"] == "Test Game Display"

    def test_export_organizations_csv(self) -> None:
        """Test CSV export of organizations."""
        response = self.client.get("/twitch/export/organizations/csv/")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert b"Twitch ID" in response.content
        assert b"org123" in response.content
        assert b"Test Organization" in response.content

    def test_export_organizations_json(self) -> None:
        """Test JSON export of organizations."""
        response = self.client.get("/twitch/export/organizations/json/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"

        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["twitch_id"] == "org123"
        assert data[0]["name"] == "Test Organization"

    def test_export_campaigns_csv_with_filters(self) -> None:
        """Test CSV export of campaigns with status filter."""
        response = self.client.get("/twitch/export/campaigns/csv/?status=active")
        assert response.status_code == 200
        assert b"campaign123" in response.content

    def test_export_campaigns_json_with_filters(self) -> None:
        """Test JSON export of campaigns with status filter."""
        response = self.client.get("/twitch/export/campaigns/json/?status=active")
        assert response.status_code == 200

        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["status"] == "Active"
