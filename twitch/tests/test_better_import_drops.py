from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.test import TestCase

from twitch.management.commands.better_import_drops import Command
from twitch.models import DropCampaign
from twitch.schemas import DropBenefitSchema

if TYPE_CHECKING:
    from twitch.models import DropBenefit


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

        payload = {
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
        payload = {
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
        campaign = DropCampaign.objects.get(twitch_id="inventory-campaign-1")
        assert campaign.name == "Test Inventory Campaign"
        assert campaign.operation_name == "Inventory"

    def test_handles_inventory_with_null_campaigns(self) -> None:
        """Ensure Inventory JSON with null dropCampaignsInProgress is handled correctly."""
        command = Command()
        command.pre_fill_cache()

        # Inventory response with null dropCampaignsInProgress
        payload = {
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


class CampaignStructureDetectionTests(TestCase):
    """Tests for campaign structure detection in _detect_campaign_structure method."""

    def test_detects_inventory_campaigns_structure(self) -> None:
        """Ensure inventory campaign structure is correctly detected."""
        command = Command()

        # Inventory format with dropCampaignsInProgress
        response = {
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

        structure = command._detect_campaign_structure(response)
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

        structure = command._detect_campaign_structure(response)
        # Should return None since there are no actual campaigns
        assert structure is None

    def test_detects_current_user_drop_campaigns_structure(self) -> None:
        """Ensure currentUser.dropCampaigns structure is correctly detected."""
        command = Command()

        response = {
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

        structure = command._detect_campaign_structure(response)
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
        inventory_payload = {
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
        viewer_campaigns = DropCampaign.objects.filter(operation_name="ViewerDropsDashboard")
        inventory_campaigns = DropCampaign.objects.filter(operation_name="Inventory")

        assert viewer_campaigns.count() >= 1
        assert inventory_campaigns.count() >= 1

        # Verify the correct campaigns are in each queryset
        assert viewer_campaigns.filter(twitch_id="viewer-campaign-1").exists()
        assert inventory_campaigns.filter(twitch_id="inventory-campaign-1").exists()

        # Cross-check: Inventory campaign should not be in viewer campaigns
        assert not viewer_campaigns.filter(twitch_id="inventory-campaign-1").exists()
        assert not inventory_campaigns.filter(twitch_id="viewer-campaign-1").exists()
