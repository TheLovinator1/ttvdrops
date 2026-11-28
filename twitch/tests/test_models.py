from django.test import TestCase

from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropCampaign
from twitch.models import Game
from twitch.models import TimeBasedDrop


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


class DropCampaignDescriptionLinkTests(TestCase):
    """Tests for DropCampaign.description_with_links."""

    def test_links_allowed_channel_names_to_twitch_profiles(self) -> None:
        """Known channel names in the description become Twitch links."""
        game = Game.objects.create(
            twitch_id="game-description-links",
            name="Description Links Game",
            display_name="Description Links Game",
        )
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-description-links",
            name="Description Links Campaign",
            description=(
                "Watch RainbowWaffles for 30 minutes. "
                "RainbowWafflesTV is a different name."
            ),
            game=game,
            operation_names=[],
        )
        channel = Channel.objects.create(
            twitch_id="channel-description-links",
            name="rainbowwaffles",
            display_name="RainbowWaffles",
        )
        campaign.allow_channels.add(channel)
        campaign = DropCampaign.objects.prefetch_related("allow_channels").get(
            pk=campaign.pk,
        )

        html = str(campaign.description_with_links)

        assert (
            '<a href="https://www.twitch.tv/rainbowwaffles" '
            'target="_blank" rel="noopener">RainbowWaffles</a>'
        ) in html
        assert "RainbowWafflesTV" in html
        assert ">RainbowWafflesTV</a>" not in html

    def test_returns_plain_description_when_no_channel_matches(self) -> None:
        """Descriptions without known channel names stay unchanged."""
        game = Game.objects.create(
            twitch_id="game-description-no-links",
            name="No Links Game",
            display_name="No Links Game",
        )
        campaign = DropCampaign.objects.create(
            twitch_id="campaign-description-no-links",
            name="No Links Campaign",
            description="Watch any participating channel.",
            game=game,
            operation_names=[],
        )

        assert campaign.description_with_links == "Watch any participating channel."


class DropCampaignRewardSummaryTests(TestCase):
    """Tests for DropCampaign.reward_summary."""

    def _make_campaign(self) -> DropCampaign:
        game = Game.objects.create(
            twitch_id="game-reward-summary",
            name="Reward Summary Game",
            display_name="Reward Summary Game",
        )
        return DropCampaign.objects.create(
            twitch_id="campaign-reward-summary",
            name="Reward Summary Campaign",
            game=game,
            operation_names=[],
        )

    def _add_drop(
        self,
        campaign: DropCampaign,
        index: int,
        name: str,
        distribution_type: str,
    ) -> None:
        drop = TimeBasedDrop.objects.create(
            twitch_id=f"reward-summary-drop-{index}",
            name=name,
            campaign=campaign,
            required_minutes_watched=index * 30,
            required_subs=0,
        )
        benefit = DropBenefit.objects.create(
            twitch_id=f"reward-summary-benefit-{index}",
            name=name,
            distribution_type=distribution_type,
        )
        drop.benefits.add(benefit)

    def test_appends_badge_to_badge_reward_names(self) -> None:
        """Badge rewards should be described as badges."""
        campaign = self._make_campaign()
        for index, name in enumerate(
            ["KawaiiDozer", "Clover", "Pearl", "Joy"],
            start=1,
        ):
            self._add_drop(campaign, index, name, "BADGE")
        campaign = DropCampaign.objects.prefetch_related(
            "time_based_drops__benefits",
        ).get(pk=campaign.pk)

        assert campaign.reward_summary == (
            "Campaign gives KawaiiDozer badge, Clover badge, Pearl badge and Joy badge"
        )

    def test_normalizes_fullwidth_parentheses_in_reward_names(self) -> None:
        """Reward names should use normal parentheses instead of full-width ones."""
        campaign = self._make_campaign()
        self._add_drop(
            campaign,
            1,
            "Secret Crate:S15\uff08Epic\uff09",
            "DIRECT_ENTITLEMENT",
        )

        campaign = DropCampaign.objects.prefetch_related(
            "time_based_drops__benefits",
        ).get(pk=campaign.pk)

        assert campaign.reward_summary == ("Campaign gives Secret Crate:S15 (Epic)")

    def test_keeps_direct_entitlement_names_and_order(self) -> None:
        """Non-badge rewards should keep their names and campaign order."""
        campaign = self._make_campaign()
        for index, name in enumerate(
            [
                "1 Moonlight Lucky Bag",
                "5,000 Coppers",
                "1 Sphere of Innocence",
                "1 Sunshine Lucky Bag",
            ],
            start=1,
        ):
            self._add_drop(campaign, index, name, "DIRECT_ENTITLEMENT")
        campaign = DropCampaign.objects.prefetch_related(
            "time_based_drops__benefits",
        ).get(pk=campaign.pk)

        assert campaign.reward_summary == (
            "Campaign gives 1 Moonlight Lucky Bag, 5,000 Coppers, "
            "1 Sphere of Innocence and 1 Sunshine Lucky Bag"
        )
