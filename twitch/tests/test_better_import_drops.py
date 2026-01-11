from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from django.test import TestCase

from twitch.management.commands.better_import_drops import Command
from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop
from twitch.schemas import DropBenefitSchema

if TYPE_CHECKING:
    from debug_toolbar.panels.templates.panel import QuerySet


class GetOrUpdateBenefitTests(TestCase):
    """Tests for the _get_or_update_benefit method in better_import_drops.Command."""

    def test_defaults_distribution_type_when_missing(self) -> None:
        """Ensure importer sets distribution_type to empty string when absent."""
        command = Command()
        command.benefit_cache = {}

        benefit_schema: DropBenefitSchema = DropBenefitSchema.model_validate(
            {
                "id": "benefit-missing-distribution-type",
                "name": "Test Benefit",
                "imageAssetURL": "https://example.com/benefit.png",
                "entitlementLimit": 1,
                "isIosAvailable": False,
                "__typename": "DropBenefit",
            },
        )

        benefit: DropBenefit = command._get_or_update_benefit(benefit_schema)

        benefit.refresh_from_db()
        assert not benefit.distribution_type


class ExtractCampaignsTests(TestCase):
    """Tests for response validation and campaign extraction."""

    def test_validates_top_level_response_with_nested_campaign(self) -> None:
        """Ensure validation handles full responses correctly."""
        command = Command()
        command.pre_fill_cache()

        payload: dict[str, object] = {
            "data": {
                "user": {
                    "id": "123",
                    "dropCampaign": {
                        "id": "c1",
                        "name": "Test Campaign",
                        "description": "",
                        "startAt": "2025-01-01T00:00:00Z",
                        "endAt": "2025-01-02T00:00:00Z",
                        "accountLinkURL": "http://example.com",
                        "detailsURL": "http://example.com",
                        "imageURL": "",
                        "status": "ACTIVE",
                        "self": {"isAccountConnected": False, "__typename": "DropCampaignSelfEdge"},
                        "game": {
                            "id": "g1",
                            "displayName": "Test Game",
                            "boxArtURL": "http://example.com/art.png",
                            "__typename": "Game",
                        },
                        "owner": {
                            "id": "o1",
                            "name": "Test Org",
                            "__typename": "Organization",
                        },
                        "timeBasedDrops": [],
                        "__typename": "DropCampaign",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {
                "operationName": "TestOp",
            },
        }

        # Validate response
        valid_responses, broken_dir = command._validate_responses(
            responses=[payload],
            file_path=Path("test.json"),
            options={},
        )

        assert len(valid_responses) == 1
        assert broken_dir is None
        assert valid_responses[0].data.user is not None

    def test_imports_inventory_response_and_sets_operation_name(self) -> None:
        """Ensure Inventory JSON imports work and operation_name is set correctly."""
        command = Command()
        command.pre_fill_cache()

        # Inventory response with dropCampaignsInProgress
        payload: dict[str, object] = {
            "data": {
                "currentUser": {
                    "id": "17658559",
                    "inventory": {
                        "dropCampaignsInProgress": [
                            {
                                "id": "inventory-campaign-1",
                                "name": "Test Inventory Campaign",
                                "description": "Campaign from Inventory operation",
                                "startAt": "2025-01-01T00:00:00Z",
                                "endAt": "2025-12-31T23:59:59Z",
                                "accountLinkURL": "https://example.com/link",
                                "detailsURL": "https://example.com/details",
                                "imageURL": "https://example.com/campaign.png",
                                "status": "ACTIVE",
                                "self": {
                                    "isAccountConnected": True,
                                    "__typename": "DropCampaignSelfEdge",
                                },
                                "game": {
                                    "id": "inventory-game-1",
                                    "displayName": "Inventory Game",
                                    "boxArtURL": "https://example.com/boxart.png",
                                    "slug": "inventory-game",
                                    "name": "Inventory Game",
                                    "__typename": "Game",
                                },
                                "owner": {
                                    "id": "inventory-org-1",
                                    "name": "Inventory Organization",
                                    "__typename": "Organization",
                                },
                                "timeBasedDrops": [],
                                "eventBasedDrops": None,
                                "__typename": "DropCampaign",
                            },
                        ],
                        "gameEventDrops": None,
                        "__typename": "Inventory",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {
                "operationName": "Inventory",
            },
        }

        # Validate and process response
        success, broken_dir = command.process_responses(
            responses=[payload],
            file_path=Path("test_inventory.json"),
            options={},
        )

        assert success is True
        assert broken_dir is None

        # Check that campaign was created with operation_name
        campaign: DropCampaign = DropCampaign.objects.get(twitch_id="inventory-campaign-1")
        assert campaign.name == "Test Inventory Campaign"
        assert campaign.operation_name == "Inventory"

    def test_handles_inventory_with_null_campaigns(self) -> None:
        """Ensure Inventory JSON with null dropCampaignsInProgress is handled correctly."""
        command = Command()
        command.pre_fill_cache()

        # Inventory response with null dropCampaignsInProgress
        payload: dict[str, object] = {
            "data": {
                "currentUser": {
                    "id": "17658559",
                    "inventory": {
                        "dropCampaignsInProgress": None,
                        "gameEventDrops": None,
                        "__typename": "Inventory",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {
                "operationName": "Inventory",
            },
        }

        # Should validate successfully even with null campaigns
        valid_responses, broken_dir = command._validate_responses(
            responses=[payload],
            file_path=Path("test_inventory_null.json"),
            options={},
        )

        assert len(valid_responses) == 1
        assert broken_dir is None
        assert valid_responses[0].data.current_user is not None
        assert valid_responses[0].data.current_user.inventory is not None

    def test_handles_inventory_with_allow_acl_url_and_missing_is_enabled(self) -> None:
        """Ensure ACL with url field and missing isEnabled is handled correctly."""
        command = Command()
        command.pre_fill_cache()

        # Inventory response with allow ACL containing url field and no isEnabled
        payload: dict[str, object] = {
            "data": {
                "currentUser": {
                    "id": "17658559",
                    "inventory": {
                        "dropCampaignsInProgress": [
                            {
                                "id": "inventory-campaign-2",
                                "name": "Test ACL Campaign",
                                "description": "",
                                "startAt": "2025-01-01T00:00:00Z",
                                "endAt": "2025-12-31T23:59:59Z",
                                "accountLinkURL": "https://example.com/link",
                                "detailsURL": "https://example.com/details",
                                "imageURL": "https://example.com/campaign.png",
                                "status": "ACTIVE",
                                "self": {
                                    "isAccountConnected": True,
                                    "__typename": "DropCampaignSelfEdge",
                                },
                                "game": {
                                    "id": "inventory-game-2",
                                    "displayName": "Test Game",
                                    "boxArtURL": "https://example.com/boxart.png",
                                    "slug": "test-game",
                                    "name": "Test Game",
                                    "__typename": "Game",
                                },
                                "owner": {
                                    "id": "inventory-org-2",
                                    "name": "Test Organization",
                                    "__typename": "Organization",
                                },
                                "allow": {
                                    "channels": [
                                        {
                                            "id": "91070599",
                                            "name": "testchannel",
                                            "url": "https://www.twitch.tv/testchannel",
                                            "__typename": "Channel",
                                        },
                                    ],
                                    "__typename": "DropCampaignACL",
                                },
                                "timeBasedDrops": [],
                                "eventBasedDrops": None,
                                "__typename": "DropCampaign",
                            },
                        ],
                        "gameEventDrops": None,
                        "__typename": "Inventory",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {
                "operationName": "Inventory",
            },
        }

        # Validate and process response
        success, broken_dir = command.process_responses(
            responses=[payload],
            file_path=Path("test_acl.json"),
            options={},
        )

        assert success is True
        assert broken_dir is None

        # Check that campaign was created and allow_is_enabled defaults to True
        campaign: DropCampaign = DropCampaign.objects.get(twitch_id="inventory-campaign-2")
        assert campaign.name == "Test ACL Campaign"
        assert campaign.allow_is_enabled is True  # Should default to True

        # Check that the channel was created and linked
        assert campaign.allow_channels.count() == 1
        channel: Channel | None = campaign.allow_channels.first()
        assert channel is not None

        assert channel.name == "testchannel"
        assert channel.twitch_id == "91070599"


class CampaignStructureDetectionTests(TestCase):
    """Tests for campaign structure detection in _detect_campaign_structure method."""

    def test_detects_inventory_campaigns_structure(self) -> None:
        """Ensure inventory campaign structure is correctly detected."""
        command = Command()

        # Inventory format with dropCampaignsInProgress
        response: dict[str, object] = {
            "data": {
                "currentUser": {
                    "id": "123",
                    "inventory": {
                        "dropCampaignsInProgress": [
                            {
                                "id": "c1",
                                "name": "Test Campaign",
                            },
                        ],
                        "__typename": "Inventory",
                    },
                    "__typename": "User",
                },
            },
        }

        structure: str | None = command._detect_campaign_structure(response)
        assert structure == "inventory_campaigns"

    def test_detects_inventory_campaigns_structure_with_null(self) -> None:
        """Ensure inventory format is NOT detected when dropCampaignsInProgress is null."""
        command = Command()

        # Inventory format with null dropCampaignsInProgress - should not detect as inventory_campaigns
        response = {
            "data": {
                "currentUser": {
                    "id": "123",
                    "inventory": {
                        "dropCampaignsInProgress": None,
                        "__typename": "Inventory",
                    },
                    "__typename": "User",
                },
            },
        }

        structure: str | None = command._detect_campaign_structure(response)
        # Should return None since there are no actual campaigns
        assert structure is None

    def test_detects_current_user_drop_campaigns_structure(self) -> None:
        """Ensure currentUser.dropCampaigns structure is correctly detected."""
        command = Command()

        response: dict[str, object] = {
            "data": {
                "currentUser": {
                    "id": "123",
                    "dropCampaigns": [
                        {
                            "id": "c1",
                            "name": "Test Campaign",
                        },
                    ],
                    "__typename": "User",
                },
            },
        }

        structure: str | None = command._detect_campaign_structure(response)
        assert structure == "current_user_drop_campaigns"


class OperationNameFilteringTests(TestCase):
    """Tests for filtering campaigns by operation_name field."""

    def test_can_filter_campaigns_by_operation_name(self) -> None:
        """Ensure campaigns can be filtered by operation_name to separate data sources."""
        command = Command()
        command.pre_fill_cache()

        # Import a ViewerDropsDashboard campaign
        viewer_drops_payload = {
            "data": {
                "currentUser": {
                    "id": "123",
                    "dropCampaigns": [
                        {
                            "id": "viewer-campaign-1",
                            "name": "Viewer Campaign",
                            "description": "",
                            "startAt": "2025-01-01T00:00:00Z",
                            "endAt": "2025-12-31T23:59:59Z",
                            "accountLinkURL": "https://example.com",
                            "detailsURL": "https://example.com",
                            "imageURL": "",
                            "status": "ACTIVE",
                            "self": {"isAccountConnected": False, "__typename": "DropCampaignSelfEdge"},
                            "game": {
                                "id": "game-1",
                                "displayName": "Game 1",
                                "boxArtURL": "https://example.com/art.png",
                                "__typename": "Game",
                            },
                            "owner": {
                                "id": "org-1",
                                "name": "Org 1",
                                "__typename": "Organization",
                            },
                            "timeBasedDrops": [],
                            "__typename": "DropCampaign",
                        },
                    ],
                    "__typename": "User",
                },
            },
            "extensions": {
                "operationName": "ViewerDropsDashboard",
            },
        }

        # Import an Inventory campaign
        inventory_payload: dict[str, object] = {
            "data": {
                "currentUser": {
                    "id": "123",
                    "inventory": {
                        "dropCampaignsInProgress": [
                            {
                                "id": "inventory-campaign-1",
                                "name": "Inventory Campaign",
                                "description": "",
                                "startAt": "2025-01-01T00:00:00Z",
                                "endAt": "2025-12-31T23:59:59Z",
                                "accountLinkURL": "https://example.com",
                                "detailsURL": "https://example.com",
                                "imageURL": "",
                                "status": "ACTIVE",
                                "self": {"isAccountConnected": True, "__typename": "DropCampaignSelfEdge"},
                                "game": {
                                    "id": "game-2",
                                    "displayName": "Game 2",
                                    "boxArtURL": "https://example.com/art2.png",
                                    "__typename": "Game",
                                },
                                "owner": {
                                    "id": "org-2",
                                    "name": "Org 2",
                                    "__typename": "Organization",
                                },
                                "timeBasedDrops": [],
                                "eventBasedDrops": None,
                                "__typename": "DropCampaign",
                            },
                        ],
                        "gameEventDrops": None,
                        "__typename": "Inventory",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {
                "operationName": "Inventory",
            },
        }

        # Process both payloads
        command.process_responses([viewer_drops_payload], Path("viewer.json"), {})
        command.process_responses([inventory_payload], Path("inventory.json"), {})

        # Verify we can filter by operation_name
        viewer_campaigns: QuerySet[DropCampaign, DropCampaign] = DropCampaign.objects.filter(
            operation_name="ViewerDropsDashboard",
        )
        inventory_campaigns: QuerySet[DropCampaign, DropCampaign] = DropCampaign.objects.filter(
            operation_name="Inventory",
        )

        assert viewer_campaigns.count() >= 1
        assert inventory_campaigns.count() >= 1

        # Verify the correct campaigns are in each queryset
        assert viewer_campaigns.filter(twitch_id="viewer-campaign-1").exists()
        assert inventory_campaigns.filter(twitch_id="inventory-campaign-1").exists()

        # Cross-check: Inventory campaign should not be in viewer campaigns
        assert not viewer_campaigns.filter(twitch_id="inventory-campaign-1").exists()
        assert not inventory_campaigns.filter(twitch_id="viewer-campaign-1").exists()


class GameImportTests(TestCase):
    """Tests for importing and persisting Game fields from campaign data."""

    def test_imports_game_slug_from_campaign(self) -> None:
        """Ensure Game.slug is imported from DropCampaign game data when provided."""
        command = Command()
        command.pre_fill_cache()

        payload: dict[str, object] = {
            "data": {
                "user": {
                    "id": "17658559",
                    "dropCampaign": {
                        "id": "campaign-with-slug",
                        "name": "Slug Campaign",
                        "description": "",
                        "startAt": "2025-01-01T00:00:00Z",
                        "endAt": "2025-12-31T23:59:59Z",
                        "accountLinkURL": "https://example.com/link",
                        "detailsURL": "https://example.com/details",
                        "imageURL": "",
                        "status": "ACTIVE",
                        "self": {"isAccountConnected": True, "__typename": "DropCampaignSelfEdge"},
                        "game": {
                            "id": "497057",
                            "slug": "destiny-2",
                            "displayName": "Destiny 2",
                            "boxArtURL": "https://example.com/boxart.png",
                            "__typename": "Game",
                        },
                        "owner": {
                            "id": "bungie-org",
                            "name": "Bungie",
                            "__typename": "Organization",
                        },
                        "timeBasedDrops": [],
                        "__typename": "DropCampaign",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {"operationName": "DropCampaignDetails"},
        }

        success, broken_dir = command.process_responses(
            responses=[payload],
            file_path=Path("slug.json"),
            options={},
        )

        assert success is True
        assert broken_dir is None

        game = Game.objects.get(twitch_id="497057")
        assert game.slug == "destiny-2"
        assert game.display_name == "Destiny 2"


class ExampleJsonImportTests(TestCase):
    """Regression tests based on the real-world `example.json` payload."""

    def test_imports_drop_campaign_details_and_persists_urls(self) -> None:
        """Ensure `imageURL` and other URL-ish fields are persisted from DropCampaignDetails."""
        command = Command()
        command.pre_fill_cache()

        repo_root: Path = Path(__file__).resolve().parents[2]
        example_path: Path = repo_root / "example.json"

        responses = json.loads(example_path.read_text(encoding="utf-8"))

        success, broken_dir = command.process_responses(
            responses=responses,
            file_path=example_path,
            options={},
        )

        assert success is True
        assert broken_dir is None

        campaign: DropCampaign = DropCampaign.objects.get(twitch_id="3b965979-ecd2-11f0-876e-0a58a9feac02")

        # Core campaign fields
        assert campaign.name == "Jan Drops Week 2"
        assert "Viewers will receive 50 Wandering Market Coins" in campaign.description
        assert campaign.details_url == "https://www.smite2.com/news/closed-alpha-twitch-drops/"
        assert campaign.account_link_url == "https://link.smite2.com/"

        # The regression: ensure imageURL makes it into DropCampaign.image_url
        assert (
            campaign.image_url
            == "https://static-cdn.jtvnw.net/twitch-quests-assets/CAMPAIGN/47db66e8-933c-484f-ab5a-30ba09093098.png"
        )

        # Allow ACL normalization
        assert campaign.allow_is_enabled is False
        assert campaign.allow_channels.count() == 0

        # Operation name provenance
        assert campaign.operation_name == "DropCampaignDetails"

        # Related game/org normalization
        game: Game = Game.objects.get(twitch_id="2094865572")
        assert game.display_name == "SMITE 2"
        assert game.slug == "smite-2"

        org: Organization = Organization.objects.get(twitch_id="51a157a0-674a-4863-b120-7bb6ee2466a8")
        assert org.name == "Hi-Rez Studios"
        assert game.owners.filter(pk=org.pk).exists()

        # Drops + benefits
        assert TimeBasedDrop.objects.filter(campaign=campaign).count() == 6
        first_drop: TimeBasedDrop = TimeBasedDrop.objects.get(twitch_id="933c8f91-ecd2-11f0-b3fd-0a58a9feac02")
        assert first_drop.name == "Market Coins Bundle 1"
        assert first_drop.required_minutes_watched == 120
        assert DropBenefit.objects.count() == 1
        benefit: DropBenefit = DropBenefit.objects.get(twitch_id="ccb3fb7f-e59b-11ef-aef0-0a58a9feac02")
        assert (
            benefit.image_asset_url
            == "https://static-cdn.jtvnw.net/twitch-quests-assets/REWARD/903496ad-de97-41ff-ad97-12f099e20ea8.jpeg"
        )


class ImporterRobustnessTests(TestCase):
    """Tests for importer resiliency against real-world payload quirks."""

    def test_normalize_responses_accepts_json_repair_tuple(self) -> None:
        """Ensure tuple payloads from json_repair don't crash the importer."""
        command = Command()

        parsed = (
            {
                "data": {
                    "currentUser": {
                        "id": "123",
                        "dropCampaigns": [],
                        "__typename": "User",
                    },
                },
                "extensions": {"operationName": "ViewerDropsDashboard"},
            },
            [{"json_repair": "log"}],
        )

        normalized = command._normalize_responses(parsed)
        assert isinstance(normalized, list)
        assert len(normalized) == 1
        assert normalized[0]["extensions"]["operationName"] == "ViewerDropsDashboard"

    def test_allows_null_image_url_and_persists_empty_string(self) -> None:
        """Ensure null imageURL doesn't fail validation and results in empty string in DB."""
        command = Command()
        command.pre_fill_cache()

        payload: dict[str, object] = {
            "data": {
                "user": {
                    "id": "123",
                    "dropCampaign": {
                        "id": "campaign-null-image",
                        "name": "Null Image Campaign",
                        "description": "",
                        "startAt": "2025-01-01T00:00:00Z",
                        "endAt": "2025-01-02T00:00:00Z",
                        "accountLinkURL": "https://example.com/link",
                        "detailsURL": "https://example.com/details",
                        "imageURL": None,
                        "status": "ACTIVE",
                        "self": {"isAccountConnected": False, "__typename": "DropCampaignSelfEdge"},
                        "game": {
                            "id": "g-null-image",
                            "displayName": "Test Game",
                            "boxArtURL": "https://example.com/box.png",
                            "__typename": "Game",
                        },
                        "timeBasedDrops": [],
                        "__typename": "DropCampaign",
                    },
                    "__typename": "User",
                },
            },
            "extensions": {"operationName": "DropCampaignDetails"},
        }

        success, broken_dir = command.process_responses(
            responses=[payload],
            file_path=Path("null_image.json"),
            options={},
        )

        assert success is True
        assert broken_dir is None

        campaign = DropCampaign.objects.get(twitch_id="campaign-null-image")
        assert not campaign.image_url
