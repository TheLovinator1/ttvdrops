import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import skipIf
from unittest.mock import patch

from django.db import connection
from django.test import TestCase

from twitch.management.commands.better_import_drops import Command
from twitch.management.commands.better_import_drops import detect_error_only_response
from twitch.management.commands.better_import_drops import detect_non_campaign_keyword
from twitch.management.commands.better_import_drops import (
    extract_operation_name_from_parsed,
)
from twitch.management.commands.better_import_drops import move_completed_file
from twitch.management.commands.better_import_drops import move_file_to_broken_subdir
from twitch.management.commands.better_import_drops import repair_partially_broken_json
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import Organization
from twitch.models import TimeBasedDrop
from twitch.schemas import DropBenefitSchema

if TYPE_CHECKING:
    from twitch.models import Channel


class GetOrUpdateBenefitTests(TestCase):
    """Tests for the _get_or_update_benefit method in better_import_drops.Command."""

    def test_defaults_distribution_type_when_missing(self) -> None:
        """Ensure importer sets distribution_type to empty string when absent."""
        command: Command = Command()
        benefit_schema: DropBenefitSchema = DropBenefitSchema.model_validate({
            "id": "benefit-missing-distribution-type",
            "name": "Test Benefit",
            "imageAssetURL": "https://example.com/benefit.png",
            "entitlementLimit": 1,
            "isIosAvailable": False,
            "__typename": "DropBenefit",
        })

        benefit: DropBenefit = command._get_or_update_benefit(benefit_schema)

        benefit.refresh_from_db()
        assert not benefit.distribution_type


class ExtractCampaignsTests(TestCase):
    """Tests for response validation and campaign extraction."""

    def test_validates_top_level_response_with_nested_campaign(self) -> None:
        """Ensure validation handles full responses correctly."""
        command = Command()

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
                        "self": {
                            "isAccountConnected": False,
                            "__typename": "DropCampaignSelfEdge",
                        },
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
            "extensions": {"operationName": "TestOp"},
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
            "extensions": {"operationName": "Inventory"},
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
        campaign: DropCampaign = DropCampaign.objects.get(
            twitch_id="inventory-campaign-1",
        )
        assert campaign.name == "Test Inventory Campaign"
        assert campaign.operation_names == ["Inventory"]

    def test_import_does_not_update_campaign_when_data_unchanged(self) -> None:
        """Ensure repeated imports do not modify the campaign updated_at."""
        command = Command()

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
            "extensions": {"operationName": "Inventory"},
        }

        # First import to create the campaign
        success, _ = command.process_responses(
            responses=[payload],
            file_path=Path("test_inventory.json"),
            options={},
        )
        assert success is True

        campaign: DropCampaign = DropCampaign.objects.get(
            twitch_id="inventory-campaign-1",
        )
        updated_at = campaign.updated_at

        # Second import should not change updated_at since data is identical
        success, _ = command.process_responses(
            responses=[payload],
            file_path=Path("test_inventory.json"),
            options={},
        )
        assert success is True

        campaign.refresh_from_db()
        assert campaign.updated_at == updated_at

    def test_handles_inventory_with_null_campaigns(self) -> None:
        """Ensure Inventory JSON with null dropCampaignsInProgress is handled correctly."""
        command = Command()

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
            "extensions": {"operationName": "Inventory"},
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
            "extensions": {"operationName": "Inventory"},
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
        campaign: DropCampaign = DropCampaign.objects.get(
            twitch_id="inventory-campaign-2",
        )
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
                            {"id": "c1", "name": "Test Campaign"},
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
                    "dropCampaigns": [{"id": "c1", "name": "Test Campaign"}],
                    "__typename": "User",
                },
            },
        }

        structure: str | None = command._detect_campaign_structure(response)
        assert structure == "current_user_drop_campaigns"

    def test_detects_user_drop_campaign_structure(self) -> None:
        """Ensure user.dropCampaign structure is correctly detected."""
        command = Command()

        response: dict[str, object] = {
            "data": {
                "user": {
                    "id": "123",
                    "dropCampaign": {"id": "c1", "name": "Test Campaign"},
                    "__typename": "User",
                },
            },
        }

        structure: str | None = command._detect_campaign_structure(response)
        assert structure == "user_drop_campaign"

    def test_detects_channel_viewer_campaigns_structure(self) -> None:
        """Ensure channel.viewerDropCampaigns structure is correctly detected."""
        command = Command()

        response: dict[str, object] = {
            "data": {
                "channel": {
                    "id": "123",
                    "viewerDropCampaigns": [{"id": "c1", "name": "Test Campaign"}],
                    "__typename": "Channel",
                },
            },
        }

        structure: str | None = command._detect_campaign_structure(response)
        assert structure == "channel_viewer_campaigns"


class FileMoveUtilityTests(TestCase):
    """Tests for imported/broken file move utility helpers."""

    def test_move_completed_file_sanitizes_operation_directory_name(self) -> None:
        """Ensure operation names are sanitized and campaign structure subdir is respected."""
        with TemporaryDirectory() as tmp_dir:
            root_path = Path(tmp_dir)
            imported_root = root_path / "imported"
            source_file = root_path / "payload.json"
            source_file.write_text("{}", encoding="utf-8")

            with patch(
                "twitch.management.commands.better_import_drops.get_imported_directory_root",
                return_value=imported_root,
            ):
                target_dir = move_completed_file(
                    file_path=source_file,
                    operation_name="My Op/Name\\v1",
                    campaign_structure="inventory_campaigns",
                )

            expected_dir = imported_root / "My_Op_Name_v1" / "inventory_campaigns"
            assert target_dir == expected_dir
            assert not source_file.exists()
            assert (expected_dir / "payload.json").exists()

    def test_move_file_to_broken_subdir_avoids_duplicate_operation_segment(
        self,
    ) -> None:
        """Ensure matching reason and operation names do not create duplicate directories."""
        with TemporaryDirectory() as tmp_dir:
            root_path = Path(tmp_dir)
            broken_root = root_path / "broken"
            source_file = root_path / "broken_payload.json"
            source_file.write_text("{}", encoding="utf-8")

            with patch(
                "twitch.management.commands.better_import_drops.get_broken_directory_root",
                return_value=broken_root,
            ):
                broken_dir = move_file_to_broken_subdir(
                    file_path=source_file,
                    subdir="validation_failed",
                    operation_name="validation_failed",
                )

            path_segments = broken_dir.as_posix().split("/")
            assert path_segments.count("validation_failed") == 1
            assert not source_file.exists()
            assert (broken_dir / "broken_payload.json").exists()


class OperationNameFilteringTests(TestCase):
    """Tests for filtering campaigns by operation_name field."""

    @skipIf(
        connection.vendor == "sqlite",
        reason="SQLite doesn't support JSON contains lookup",
    )
    def test_can_filter_campaigns_by_operation_name(self) -> None:
        """Ensure campaigns can be filtered by operation_name to separate data sources."""
        command = Command()

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
                            "self": {
                                "isAccountConnected": False,
                                "__typename": "DropCampaignSelfEdge",
                            },
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
            "extensions": {"operationName": "ViewerDropsDashboard"},
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
                                "self": {
                                    "isAccountConnected": True,
                                    "__typename": "DropCampaignSelfEdge",
                                },
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
            "extensions": {"operationName": "Inventory"},
        }

        # Process both payloads
        command.process_responses([viewer_drops_payload], Path("viewer.json"), {})
        command.process_responses([inventory_payload], Path("inventory.json"), {})

        # Verify we can filter by operation_names with JSON containment
        viewer_campaigns = DropCampaign.objects.filter(
            operation_names__contains=["ViewerDropsDashboard"],
        )
        inventory_campaigns = DropCampaign.objects.filter(
            operation_names__contains=["Inventory"],
        )

        assert len(viewer_campaigns) >= 1
        assert len(inventory_campaigns) >= 1

        # Verify the correct campaigns are in each list
        viewer_ids = [c.twitch_id for c in viewer_campaigns]
        inventory_ids = [c.twitch_id for c in inventory_campaigns]

        assert "viewer-campaign-1" in viewer_ids
        assert "inventory-campaign-1" in inventory_ids

        # Cross-check: Inventory campaign should not be in viewer campaigns
        assert "inventory-campaign-1" not in viewer_ids
        assert "viewer-campaign-1" not in inventory_ids


class GameImportTests(TestCase):
    """Tests for importing and persisting Game fields from campaign data."""

    def test_imports_game_slug_from_campaign(self) -> None:
        """Ensure Game.slug is imported from DropCampaign game data when provided."""
        command = Command()

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
                        "self": {
                            "isAccountConnected": True,
                            "__typename": "DropCampaignSelfEdge",
                        },
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

        repo_root: Path = Path(__file__).resolve().parents[2]
        example_path: Path = repo_root / "twitch" / "tests" / "example.json"

        responses = json.loads(example_path.read_text(encoding="utf-8"))

        success, broken_dir = command.process_responses(
            responses=responses,
            file_path=example_path,
            options={},
        )

        assert success is True
        assert broken_dir is None

        campaign: DropCampaign = DropCampaign.objects.get(
            twitch_id="3b965979-ecd2-11f0-876e-0a58a9feac02",
        )

        # Core campaign fields
        assert campaign.name == "Jan Drops Week 2"
        assert "Viewers will receive 50 Wandering Market Coins" in campaign.description
        assert (
            campaign.details_url
            == "https://www.smite2.com/news/closed-alpha-twitch-drops/"
        )
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
        assert campaign.operation_names == ["DropCampaignDetails"]

        # Related game/org normalization
        game: Game = Game.objects.get(twitch_id="2094865572")
        assert game.display_name == "SMITE 2"
        assert game.slug == "smite-2"

        org: Organization = Organization.objects.get(
            twitch_id="51a157a0-674a-4863-b120-7bb6ee2466a8",
        )
        assert org.name == "Hi-Rez Studios"
        assert game.owners.filter(pk=org.pk).exists()

        # Drops + benefits
        assert TimeBasedDrop.objects.filter(campaign=campaign).count() == 6
        first_drop: TimeBasedDrop = TimeBasedDrop.objects.get(
            twitch_id="933c8f91-ecd2-11f0-b3fd-0a58a9feac02",
        )
        assert first_drop.name == "Market Coins Bundle 1"
        assert first_drop.required_minutes_watched == 120
        assert DropBenefit.objects.count() == 1
        benefit: DropBenefit = DropBenefit.objects.get(
            twitch_id="ccb3fb7f-e59b-11ef-aef0-0a58a9feac02",
        )
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
                        "self": {
                            "isAccountConnected": False,
                            "__typename": "DropCampaignSelfEdge",
                        },
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


class ErrorOnlyResponseDetectionTests(TestCase):
    """Tests for detecting responses that only contain GraphQL errors without data."""

    def test_detects_error_only_response_with_service_timeout(self) -> None:
        """Ensure error-only response with service timeout is detected."""
        parsed_json = {
            "errors": [
                {
                    "message": "service timeout",
                    "path": ["currentUser", "dropCampaigns"],
                },
            ],
        }

        result = detect_error_only_response(parsed_json)
        assert result == "error_only: service timeout"

    def test_detects_error_only_response_with_null_data(self) -> None:
        """Ensure error-only response with null data field is detected."""
        parsed_json = {
            "errors": [{"message": "internal server error", "path": ["data"]}],
            "data": None,
        }

        result = detect_error_only_response(parsed_json)
        assert result == "error_only: internal server error"

    def test_detects_error_only_response_with_empty_data(self) -> None:
        """Ensure error-only response with empty data dict is allowed through."""
        parsed_json = {"errors": [{"message": "unauthorized"}], "data": {}}

        result = detect_error_only_response(parsed_json)
        # Empty dict {} is considered "data exists" so this should pass
        assert result is None

    def test_detects_error_only_response_without_data_key(self) -> None:
        """Ensure error-only response without data key is detected."""
        parsed_json = {"errors": [{"message": "missing data"}]}

        result = detect_error_only_response(parsed_json)
        assert result == "error_only: missing data"

    def test_allows_response_with_both_errors_and_data(self) -> None:
        """Ensure responses with both errors and valid data are not flagged."""
        parsed_json = {
            "errors": [{"message": "partial failure"}],
            "data": {"currentUser": {"dropCampaigns": []}},
        }

        result = detect_error_only_response(parsed_json)
        assert result is None

    def test_allows_response_with_no_errors(self) -> None:
        """Ensure normal responses without errors are not flagged."""
        parsed_json = {"data": {"currentUser": {"dropCampaigns": []}}}

        result = detect_error_only_response(parsed_json)
        assert result is None

    def test_detects_error_only_in_list_of_responses(self) -> None:
        """Ensure error-only detection works with list of responses."""
        parsed_json = [{"errors": [{"message": "rate limit exceeded"}]}]

        result = detect_error_only_response(parsed_json)
        assert result == "error_only: rate limit exceeded"

    def test_handles_json_repair_tuple_format(self) -> None:
        """Ensure error-only detection works with json_repair tuple format."""
        parsed_json = (
            {
                "errors": [
                    {
                        "message": "service timeout",
                        "path": ["currentUser", "dropCampaigns"],
                    },
                ],
            },
            [{"json_repair": "log"}],
        )

        result = detect_error_only_response(parsed_json)
        assert result == "error_only: service timeout"

    def test_returns_none_for_non_dict_input(self) -> None:
        """Ensure non-dict input is handled gracefully."""
        result = detect_error_only_response("invalid")
        assert result is None

    def test_returns_none_for_empty_errors_list(self) -> None:
        """Ensure empty errors list is not flagged as error-only."""
        parsed_json = {"errors": []}

        result = detect_error_only_response(parsed_json)
        assert result is None

    def test_handles_error_without_message_field(self) -> None:
        """Ensure errors without message field use default text."""
        parsed_json = {"errors": [{"path": ["data"]}]}

        result = detect_error_only_response(parsed_json)
        assert result == "error_only: unknown error"


class NonCampaignKeywordDetectionTests(TestCase):
    """Tests for non-campaign operation keyword detection."""

    def test_detects_known_non_campaign_operation(self) -> None:
        """Ensure known operationName values are detected as non-campaign payloads."""
        raw_text = json.dumps({"extensions": {"operationName": "PlaybackAccessToken"}})

        result = detect_non_campaign_keyword(raw_text)
        assert result == "PlaybackAccessToken"

    def test_returns_none_for_unknown_operation(self) -> None:
        """Ensure unrelated operation names are not flagged."""
        raw_text = json.dumps({"extensions": {"operationName": "DropCampaignDetails"}})

        result = detect_non_campaign_keyword(raw_text)
        assert result is None


class OperationNameExtractionTests(TestCase):
    """Tests for operation name extraction across supported payload shapes."""

    def test_extracts_operation_name_from_json_repair_tuple(self) -> None:
        """Ensure extraction supports tuple payloads returned by json_repair."""
        payload = (
            {"extensions": {"operationName": "ViewerDropsDashboard"}},
            [{"json_repair": "log"}],
        )

        result = extract_operation_name_from_parsed(payload)
        assert result == "ViewerDropsDashboard"

    def test_extracts_operation_name_from_list_payload(self) -> None:
        """Ensure extraction inspects the first response in list payloads."""
        payload = [
            {"extensions": {"operationName": "Inventory"}},
            {"extensions": {"operationName": "IgnoredSecondItem"}},
        ]

        result = extract_operation_name_from_parsed(payload)
        assert result == "Inventory"

    def test_returns_none_for_empty_list_payload(self) -> None:
        """Ensure extraction returns None for empty list payloads."""
        result = extract_operation_name_from_parsed([])
        assert result is None


class JsonRepairTests(TestCase):
    """Tests for partial JSON repair fallback behavior."""

    def test_repair_filters_non_graphql_items_from_list(self) -> None:
        """Ensure repaired list output only keeps GraphQL-like response objects."""
        raw_text = '[{"foo": 1}, {"data": {"currentUser": {"id": "1"}}}]'

        repaired = repair_partially_broken_json(raw_text)
        parsed = json.loads(repaired)

        assert parsed == [{"data": {"currentUser": {"id": "1"}}}]


class ProcessFileWorkerTests(TestCase):
    """Tests for process_file_worker early-return behaviors."""

    def test_returns_reason_when_drop_campaign_key_missing(self) -> None:
        """Ensure files without dropCampaign are marked failed with clear reason."""
        command = Command()

        repo_root: Path = Path(__file__).resolve().parents[2]
        temp_path: Path = repo_root / "twitch" / "tests" / "tmp_no_drop_campaign.json"
        temp_path.write_text(
            json.dumps({"data": {"currentUser": {"id": "123"}}}),
            encoding="utf-8",
        )

        try:
            result = command.process_file_worker(
                file_path=temp_path,
                options={"crash_on_error": False, "skip_broken_moves": True},
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

        assert result["success"] is False
        assert result["broken_dir"] == "(skipped)"
        assert result["reason"] == "no dropCampaign present"

    def test_returns_reason_for_error_only_response(self) -> None:
        """Ensure error-only responses are marked failed with extracted reason."""
        command = Command()

        repo_root: Path = Path(__file__).resolve().parents[2]
        temp_path: Path = repo_root / "twitch" / "tests" / "tmp_error_only.json"
        temp_path.write_text(
            json.dumps(
                {
                    "errors": [{"message": "service timeout"}],
                    "data": None,
                },
            ),
            encoding="utf-8",
        )

        try:
            result = command.process_file_worker(
                file_path=temp_path,
                options={"crash_on_error": False, "skip_broken_moves": True},
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

        assert result["success"] is False
        assert result["broken_dir"] == "(skipped)"
        assert result["reason"] == "error_only: service timeout"

    def test_returns_reason_for_known_non_campaign_keyword(self) -> None:
        """Ensure known non-campaign operation payloads are rejected with reason."""
        command = Command()

        repo_root: Path = Path(__file__).resolve().parents[2]
        temp_path: Path = (
            repo_root / "twitch" / "tests" / "tmp_non_campaign_keyword.json"
        )
        temp_path.write_text(
            json.dumps(
                {
                    "data": {"currentUser": {"id": "123"}},
                    "extensions": {"operationName": "PlaybackAccessToken"},
                },
            ),
            encoding="utf-8",
        )

        try:
            result = command.process_file_worker(
                file_path=temp_path,
                options={"crash_on_error": False, "skip_broken_moves": True},
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

        assert result["success"] is False
        assert result["broken_dir"] == "(skipped)"
        assert result["reason"] == "matched 'PlaybackAccessToken'"


class NormalizeResponsesTests(TestCase):
    """Tests for response normalization across supported payload formats."""

    def test_normalizes_batched_responses_wrapper(self) -> None:
        """Ensure batched payloads under responses key are unwrapped and filtered."""
        command = Command()

        parsed_json: dict[str, object] = {
            "responses": [
                {"data": {"currentUser": {"id": "1"}}},
                "invalid-item",
                {"extensions": {"operationName": "Inventory"}},
            ],
        }

        normalized = command._normalize_responses(parsed_json)
        assert len(normalized) == 2
        assert normalized[0]["data"]["currentUser"]["id"] == "1"
        assert normalized[1]["extensions"]["operationName"] == "Inventory"

    def test_returns_empty_list_for_empty_tuple_payload(self) -> None:
        """Ensure empty tuple payloads from json_repair produce no responses."""
        command = Command()

        normalized = command._normalize_responses(())  # type: ignore[arg-type]
        assert normalized == []
