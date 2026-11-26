from django.test import TestCase

from twitch.models import DropCampaign
from twitch.models import Game


class DropCampaignCleanNameTests(TestCase):
    """Tests for DropCampaign.clean_name."""

    def _make_campaign(self, game_name: str, campaign_name: str) -> DropCampaign:
        game = Game.objects.create(
            twitch_id=f"game-{game_name}",
            name=game_name,
            display_name=game_name,
        )
        return DropCampaign.objects.create(
            twitch_id=f"campaign-{campaign_name}",
            name=campaign_name,
            game=game,
            operation_names=[],
        )

    def test_keeps_game_name_without_an_explicit_separator(self) -> None:
        """A space alone should not turn 'EMPULSE Drops' into 'Drops'."""
        campaign = self._make_campaign("EMPULSE", "EMPULSE Drops")

        assert campaign.clean_name == "EMPULSE Drops"

    def test_strips_game_name_with_an_explicit_separator(self) -> None:
        """Explicit separators still produce a shorter display name."""
        campaign = self._make_campaign("Ravendawn", "Ravendawn - July 2")

        assert campaign.clean_name == "July 2"
