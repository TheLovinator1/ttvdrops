from __future__ import annotations

from pathlib import Path

from django.test import TestCase

from twitch.management.commands.better_import_drops import Command
from twitch.models import Game
from twitch.models import Organization


class GameOwnerOrganizationTests(TestCase):
    """Tests for correct precedence of game owner organization during import."""

    def test_game_owner_organization_precedence(self) -> None:
        """If both owner and ownerOrganization are present, game owner should be ownerOrganization."""
        command = Command()
        command.pre_fill_cache()

        payload = {
            "data": {
                "user": {
                    "id": "17658559",
                    "dropCampaign": {
                        "id": "test-campaign-1",
                        "name": "Rustmas 2025",
                        "description": "Test campaign desc",
                        "startAt": "2025-12-08T18:00:00Z",
                        "endAt": "2026-01-01T07:59:59.999Z",
                        "accountLinkURL": "https://www.twitch.tv/",
                        "detailsURL": "https://help.twitch.tv/s/article/twitch-chat-badges-guide",
                        "imageURL": "https://static-cdn.jtvnw.net/twitch-quests-assets/CAMPAIGN/495ebb6b-8134-4e51-b9d0-1f4a221b4f8d.png",
                        "status": "ACTIVE",
                        "self": {"isAccountConnected": True, "__typename": "DropCampaignSelfEdge"},
                        "game": {
                            "id": "263490",
                            "slug": "rust",
                            "displayName": "Rust",
                            "__typename": "Game",
                            "ownerOrganization": {
                                "id": "d32de13d-937e-4196-8198-1a7f875f295a",
                                "name": "Twitch Gaming",
                                "__typename": "Organization",
                            },
                        },
                        "owner": {"id": "other-org-id", "name": "Other Org", "__typename": "Organization"},
                        "timeBasedDrops": [],
                        "eventBasedDrops": [],
                        "allow": {"channels": None, "isEnabled": False, "__typename": "DropCampaignACL"},
                        "__typename": "DropCampaign",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {"operationName": "DropCampaignDetails"},
        }

        # Run import logic
        success, broken_dir = command.process_responses(
            responses=[payload],
            file_path=Path("test_owner_org.json"),
            options={},
        )
        assert success is True
        assert broken_dir is None

        # Check game owners include Twitch Gaming and Other Org
        game: Game = Game.objects.get(twitch_id="263490")
        org1: Organization = Organization.objects.get(twitch_id="d32de13d-937e-4196-8198-1a7f875f295a")
        org2: Organization = Organization.objects.get(twitch_id="other-org-id")
        owners = list(game.owners.all())
        assert org1 in owners
        assert org2 in owners
        assert any(o.name == "Twitch Gaming" for o in owners)
        assert any(o.name == "Other Org" for o in owners)
