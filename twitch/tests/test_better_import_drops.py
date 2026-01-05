from __future__ import annotations

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

        benefit: DropBenefit = command._get_or_update_benefit(benefit_schema)  # noqa: SLF001

        benefit.refresh_from_db()
        assert not benefit.distribution_type
