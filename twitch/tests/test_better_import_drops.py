from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.test import TestCase

from twitch.management.commands.better_import_drops import Command
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
