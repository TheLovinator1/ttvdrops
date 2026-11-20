"""Generate pghistory events from archived responses without touching live rows."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

import pghistory

from twitch.management.commands.better_import_drops import Command as Importer
from twitch.management.commands.better_import_drops import detect_error_only_response
from twitch.management.commands.better_import_drops import detect_non_campaign_keyword
from twitch.management.commands.better_import_drops import (
    extract_operation_name_from_parsed,
)
from twitch.management.commands.better_import_drops import repair_partially_broken_json
from twitch.models import Channel
from twitch.models import DropBenefit
from twitch.models import DropBenefitEdge
from twitch.models import DropCampaign
from twitch.models import DropCampaignAllowedChannel
from twitch.models import Game
from twitch.models import GameOwner
from twitch.models import Organization
from twitch.models import Reward
from twitch.models import RewardCampaign
from twitch.models import TimeBasedDrop
from twitch.utils import normalize_twitch_image_url
from twitch.utils import parse_date
from twitch.utils import prefer_display_name

if TYPE_CHECKING:
    from pathlib import Path

    from twitch.schemas import DropBenefitSchema
    from twitch.schemas import DropCampaignSchema
    from twitch.schemas import Reward as RewardSchema
    from twitch.schemas import RewardCampaign as RewardCampaignSchema
    from twitch.schemas import TimeBasedDropSchema


class HistoryReplay:
    """Replay response files into pghistory event rows only.

    Live archive tables are read for object IDs but are never saved.
    """

    def __init__(self) -> None:
        """Create caches for live objects, snapshots, and membership sets."""
        self._importer: Importer = Importer()
        self._live_cache: dict[tuple[type[Any], str], Any] = {}
        self._edge_cache: dict[tuple[int, int], DropBenefitEdge | None] = {}
        self._snapshots: dict[tuple[type[Any], int], dict[str, Any]] = {}
        self._campaign_channels: dict[int, set[int]] = {}
        self._game_owners: dict[int, set[int]] = {}
        self._operation_names: dict[str, list[str]] = {}

    def _live(self, model: type[Any], twitch_id: str) -> Any | None:  # ruff: ignore[any-type]
        key = (model, twitch_id)
        if key not in self._live_cache:
            self._live_cache[key] = model.objects.filter(twitch_id=twitch_id).first()
        return self._live_cache[key]

    def _emit(
        self,
        model: type[Any],
        live_obj: Any,  # ruff: ignore[any-type]
        updates: dict[str, Any],
        *,
        label: str = "updated",
    ) -> None:
        key = (model, int(live_obj.pk))
        previous: dict[str, Any] | None = self._snapshots.get(key)
        if previous is None:
            snapshot: dict[str, Any] = updates
            self._snapshots[key] = snapshot
            self._create_event(model, live_obj, snapshot, "created")
            return
        merged: dict[str, Any] = {**previous, **updates}
        if merged == previous:
            return
        self._snapshots[key] = merged
        self._create_event(model, live_obj, merged, label)

    @staticmethod
    def _create_event(
        model: type[Any],
        live_obj: Any,  # ruff: ignore[any-type]
        snapshot: dict[str, Any],
        label: str,
    ) -> None:
        instance = model(pk=live_obj.pk)
        for field_name, value in snapshot.items():
            setattr(instance, field_name, value)
        pghistory.create_event(instance, label=label)

    def _emit_membership(
        self,
        campaign: DropCampaign,
        channel_ids: set[int],
    ) -> None:
        previous: set[int] = self._campaign_channels.get(int(campaign.pk), set())
        for channel_id in channel_ids - previous:
            row = DropCampaignAllowedChannel(
                id=0,
                dropcampaign_id=campaign.pk,
                channel_id=channel_id,
            )
            pghistory.create_event(row, label="channel_added")
        for channel_id in previous - channel_ids:
            row = DropCampaignAllowedChannel(
                id=0,
                dropcampaign_id=campaign.pk,
                channel_id=channel_id,
            )
            pghistory.create_event(row, label="channel_removed")
        self._campaign_channels[int(campaign.pk)] = channel_ids

    def _emit_owner_membership(self, game: Game, org_ids: set[int]) -> None:
        """Record newly observed game owners without inferring removals.

        Responses are not authoritative about the full owner set, so a missing
        owner only means “not present in this response”, not “removed”.

        Args:
            game: Live game receiving the ownership observation.
            org_ids: Organization IDs observed in this response.
        """
        previous: set[int] = self._game_owners.get(int(game.pk), set())
        for org_id in org_ids - previous:
            row = GameOwner(id=0, game_id=game.pk, organization_id=org_id)
            pghistory.create_event(row, label="owner_added")
        self._game_owners[int(game.pk)] = previous | org_ids

    def _live_edge(
        self,
        drop: TimeBasedDrop,
        benefit: DropBenefit,
    ) -> DropBenefitEdge | None:
        key = (int(drop.pk), int(benefit.pk))
        if key not in self._edge_cache:
            self._edge_cache[key] = DropBenefitEdge.objects.filter(
                drop=drop,
                benefit=benefit,
            ).first()
        return self._edge_cache[key]

    def _resolve_channel_ids(
        self,
        schemas: list[Any],
    ) -> set[int]:
        channel_ids: set[int] = set()
        for schema in schemas:
            channel = self._process_channel(schema)
            if channel is not None:
                channel_ids.add(int(channel.pk))
        return channel_ids

    def _process_organization(self, schema: Any) -> Organization | None:  # ruff:ignore[any-type]
        live_org = self._live(Organization, schema.twitch_id)
        if live_org is not None:
            self._emit(
                Organization,
                live_org,
                {"twitch_id": schema.twitch_id, "name": schema.name},
            )
        return live_org

    def _process_game(
        self,
        schema: Any,  # ruff:ignore[any-type]
    ) -> Game | None:
        live_game = self._live(Game, schema.twitch_id)
        if live_game is None:
            return None
        updates: dict[str, Any] = {"twitch_id": schema.twitch_id}
        if schema.display_name:
            updates["display_name"] = schema.display_name
        if schema.name:
            updates["name"] = schema.name
        if schema.slug:
            updates["slug"] = schema.slug
        if schema.box_art_url:
            updates["box_art"] = normalize_twitch_image_url(schema.box_art_url)
        self._emit(Game, live_game, updates)

        if schema.owner_organization:
            owner_id = str(schema.owner_organization.get("id", ""))
            owner_name = str(schema.owner_organization.get("name", ""))
            live_owner = self._live(Organization, owner_id)
            if live_owner is not None and owner_name:
                self._emit(
                    Organization,
                    live_owner,
                    {"twitch_id": live_owner.twitch_id, "name": owner_name},
                )
        return live_game

    def _process_org_ref(self, org_id: str, org_name: str) -> Organization | None:
        live_org = self._live(Organization, org_id)
        if live_org is not None and org_name:
            self._emit(
                Organization,
                live_org,
                {"twitch_id": org_id, "name": org_name},
            )
        return live_org

    def _process_game_ref(self, game_data: dict[str, Any]) -> Game | None:
        game_id = str(game_data.get("id", ""))
        live_game = self._live(Game, game_id)
        if live_game is None:
            return None
        name = str(game_data.get("displayName") or game_data.get("name") or "")
        updates: dict[str, Any] = {"twitch_id": game_id}
        if name:
            updates["display_name"] = name
        if game_data.get("name"):
            updates["name"] = str(game_data["name"])
        if game_data.get("slug"):
            updates["slug"] = str(game_data["slug"])
        if game_data.get("boxArtURL"):
            updates["box_art"] = normalize_twitch_image_url(
                str(game_data["boxArtURL"]),
            )
        self._emit(Game, live_game, updates)
        return live_game

    def _process_channel(self, schema: Any) -> Channel | None:  # ruff:ignore[any-type]
        live_channel = self._live(Channel, schema.twitch_id)
        if live_channel is None:
            return None
        previous: dict[str, Any] | None = self._snapshots.get(
            (Channel, int(live_channel.pk)),
        )
        existing_display_name: str = (
            str(previous.get("display_name") or "")
            if previous is not None
            else live_channel.display_name
        )
        self._emit(
            Channel,
            live_channel,
            {
                "twitch_id": schema.twitch_id,
                "name": schema.name,
                "display_name": prefer_display_name(
                    existing_display_name,
                    schema.display_name or schema.name,
                ),
            },
        )
        return live_channel

    def _process_campaign(
        self,
        schema: DropCampaignSchema,
        operation_name: str | None,
    ) -> None:
        owner_org: Organization | None = (
            self._process_organization(schema.owner) if schema.owner else None
        )
        game = self._process_game(schema.game)
        if game is None:
            return
        live_campaign = self._live(DropCampaign, schema.twitch_id)
        if live_campaign is None:
            return

        owner_id: int | None = int(owner_org.pk) if owner_org is not None else None

        operation_names: list[str] = self._operation_names.setdefault(
            schema.twitch_id,
            [],
        )
        if operation_name and operation_name not in operation_names:
            operation_names.append(operation_name)

        updates: dict[str, Any] = {
            "twitch_id": schema.twitch_id,
            "name": schema.name,
            "start_at": parse_date(schema.start_at),
            "end_at": parse_date(schema.end_at),
            "api_status": schema.status,
            "is_account_connected": schema.self.is_account_connected,
            "game_id": game.pk,
            "data_source": "TwitchDropsMiner",
            "operation_names": list(operation_names),
        }
        if schema.description:
            updates["description"] = schema.description
        if schema.details_url:
            updates["details_url"] = schema.details_url
        if schema.account_link_url:
            updates["account_link_url"] = schema.account_link_url
        if schema.image_url:
            updates["image_url"] = normalize_twitch_image_url(schema.image_url)
        if schema.event_based_drops is not None:
            updates["event_based_drops"] = schema.event_based_drops
        if owner_id is not None:
            updates["owner_id"] = owner_id
        if schema.allow is not None and schema.allow.is_enabled is not None:
            updates["allow_is_enabled"] = schema.allow.is_enabled

        self._emit(DropCampaign, live_campaign, updates)

        if schema.allow is not None and schema.allow.channels is not None:
            self._emit_membership(
                live_campaign,
                self._resolve_channel_ids(schema.allow.channels),
            )

        game_owner_ids: set[int] = set()
        if schema.game.owner_organization:
            org = self._live(
                Organization,
                str(schema.game.owner_organization.get("id", "")),
            )
            if org is not None:
                game_owner_ids.add(int(org.pk))
        if owner_id is not None:
            game_owner_ids.add(owner_id)
        if game_owner_ids:
            self._emit_owner_membership(game, game_owner_ids)

        if schema.time_based_drops:
            self._process_drops(
                schema.time_based_drops,
                live_campaign,
            )

    def _process_drops(
        self,
        drops: list[TimeBasedDropSchema],
        campaign: DropCampaign,
    ) -> None:
        for schema in drops:
            live_drop = self._live(TimeBasedDrop, schema.twitch_id)
            if live_drop is None:
                continue
            updates: dict[str, Any] = {
                "twitch_id": schema.twitch_id,
                "campaign_id": campaign.pk,
                "name": schema.name,
                "required_subs": schema.required_subs,
            }
            if schema.required_minutes_watched is not None:
                updates["required_minutes_watched"] = schema.required_minutes_watched
            start_at = parse_date(schema.start_at)
            end_at = parse_date(schema.end_at)
            if start_at is not None:
                updates["start_at"] = start_at
            if end_at is not None:
                updates["end_at"] = end_at
            if schema.precondition_drops is not None:
                updates["precondition_drops"] = schema.precondition_drops
            if schema.repeat is not None:
                updates["repeat"] = schema.repeat
            self._emit(TimeBasedDrop, live_drop, updates)

            for edge in schema.benefit_edges:
                self._process_benefit(edge.benefit, live_drop, edge.entitlement_limit)

    def _process_benefit(
        self,
        schema: DropBenefitSchema,
        drop: TimeBasedDrop,
        entitlement_limit: int,
    ) -> None:
        live_benefit = self._live(DropBenefit, schema.twitch_id)
        if live_benefit is None:
            return

        updates: dict[str, Any] = {"twitch_id": schema.twitch_id, "name": schema.name}
        if schema.image_asset_url:
            updates["image_asset_url"] = normalize_twitch_image_url(
                schema.image_asset_url,
            )
        if schema.created_at:
            created_at = parse_date(schema.created_at)
            if created_at is not None:
                updates["created_at"] = created_at
        if schema.entitlement_limit is not None:
            updates["entitlement_limit"] = schema.entitlement_limit
        if schema.is_ios_available is not None:
            updates["is_ios_available"] = schema.is_ios_available
        if schema.distribution_type is not None:
            updates["distribution_type"] = schema.distribution_type
        if schema.game:
            game = self._process_game_ref(schema.game)
            if game is not None:
                updates["game_id"] = game.pk
        if schema.owner_organization:
            org = self._process_org_ref(
                str(schema.owner_organization.get("id", "")),
                str(schema.owner_organization.get("name", "")),
            )
            if org is not None:
                updates["owner_organization_id"] = org.pk
        self._emit(DropBenefit, live_benefit, updates)

        live_edge = self._live_edge(drop, live_benefit)
        if live_edge is not None:
            self._emit(
                DropBenefitEdge,
                live_edge,
                {
                    "drop_id": drop.pk,
                    "benefit_id": live_benefit.pk,
                    "entitlement_limit": entitlement_limit,
                },
            )

    def _process_reward_campaign(
        self,
        schema: RewardCampaignSchema,
    ) -> None:
        live_campaign = self._live(RewardCampaign, schema.twitch_id)
        if live_campaign is None:
            return
        game_id: int | None = None
        if schema.game:
            game = self._process_game_ref(schema.game)
            if game is not None:
                game_id = int(game.pk)

        updates: dict[str, Any] = {
            "twitch_id": schema.twitch_id,
            "name": schema.name,
            "brand": schema.brand,
            "starts_at": parse_date(schema.starts_at),
            "ends_at": parse_date(schema.ends_at),
            "status": schema.status,
            "summary": schema.summary,
            "instructions": schema.instructions,
            "external_url": schema.external_url,
            "reward_value_url_param": schema.reward_value_url_param,
            "about_url": schema.about_url,
            "is_sitewide": schema.is_sitewide,
            "image_url": normalize_twitch_image_url(
                schema.image.image1x_url if schema.image else "",
            ),
            "data_source": "TwitchDropsMiner",
        }
        if game_id is not None:
            updates["game_id"] = game_id
        self._emit(RewardCampaign, live_campaign, updates)

        for reward_schema in schema.rewards:
            self._process_reward(reward_schema, live_campaign)

    def _process_reward(
        self,
        schema: RewardSchema,
        campaign: RewardCampaign,
    ) -> None:
        live_reward = self._live(Reward, schema.twitch_id)
        if live_reward is None:
            return
        updates: dict[str, Any] = {
            "twitch_id": schema.twitch_id,
            "reward_campaign_id": campaign.pk,
            "name": schema.name,
            "banner_image_url": normalize_twitch_image_url(
                schema.banner_image.image1x_url if schema.banner_image else "",
            ),
            "thumbnail_image_url": normalize_twitch_image_url(
                schema.thumbnail_image.image1x_url if schema.thumbnail_image else "",
            ),
            "redemption_instructions": schema.redemption_instructions,
            "redemption_url": schema.redemption_url,
        }
        if schema.earnable_until:
            earnable = parse_date(schema.earnable_until)
            if earnable is not None:
                updates["earnable_until"] = earnable
        self._emit(Reward, live_reward, updates)

    def process_file(
        self,
        file_path: Path,
        options: dict[str, Any],
    ) -> dict[str, bool | str]:
        """Parse a file and write only pghistory events.

        Args:
            file_path: JSON response file.
            options: Importer options used by validation helpers.

        Returns:
            Success dictionary compatible with the build command.
        """
        raw_text: str = file_path.read_text(encoding="utf-8", errors="ignore")
        fixed: str = repair_partially_broken_json(raw_text)
        parsed: Any = json.loads(fixed)
        operation_name: str | None = extract_operation_name_from_parsed(parsed)

        if detect_error_only_response(parsed):
            return {"success": False, "reason": "error-only response"}
        if detect_non_campaign_keyword(raw_text):
            return {"success": False, "reason": "matched non-campaign operation"}
        if "dropCampaign" not in raw_text:
            return {"success": False, "reason": "no dropCampaign present"}

        responses: list[dict[str, Any]] = self._importer._normalize_responses(parsed)  # ruff: ignore[private-member-access]
        valid_responses, _ = self._importer._validate_responses(  # ruff: ignore[private-member-access]
            responses=responses,
            file_path=file_path,
            options=options,
        )
        if not valid_responses:
            return {"success": False, "reason": "validation failed"}

        for response in valid_responses:
            if response.data.reward_campaigns_available_to_user:
                for reward_campaign in response.data.reward_campaigns_available_to_user:
                    self._process_reward_campaign(reward_campaign)

            campaigns: list[DropCampaignSchema] = []
            user_obj = response.data.current_user or response.data.user
            if user_obj and user_obj.drop_campaigns:
                campaigns.extend(user_obj.drop_campaigns)
            channel_obj = response.data.channel
            if channel_obj and channel_obj.viewer_drop_campaigns:
                if isinstance(channel_obj.viewer_drop_campaigns, list):
                    campaigns.extend(channel_obj.viewer_drop_campaigns)
                else:
                    campaigns.append(channel_obj.viewer_drop_campaigns)

            for campaign in campaigns:
                self._process_campaign(campaign, operation_name)

        return {"success": True}
