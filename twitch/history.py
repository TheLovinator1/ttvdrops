"""Helpers for presenting django-pghistory events as a SteamDB-style changelog."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from pghistory.models import Context

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

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import Model
    from django.db.models import QuerySet


TRACKED_MODELS: tuple[type[Model], ...] = (
    Organization,
    Game,
    Channel,
    DropCampaign,
    TimeBasedDrop,
    DropBenefit,
    DropBenefitEdge,
    RewardCampaign,
    Reward,
    DropCampaignAllowedChannel,
    GameOwner,
)

MODEL_INFO: dict[str, dict[str, str]] = {
    "twitch.OrganizationEvent": {"label": "Organization", "kind": "organization"},
    "twitch.GameEvent": {"label": "Game", "kind": "game"},
    "twitch.ChannelEvent": {"label": "Channel", "kind": "channel"},
    "twitch.DropCampaignEvent": {"label": "Campaign", "kind": "campaign"},
    "twitch.TimeBasedDropEvent": {"label": "Drop", "kind": "drop"},
    "twitch.DropBenefitEvent": {"label": "Benefit", "kind": "benefit"},
    "twitch.DropBenefitEdgeEvent": {"label": "Benefit link", "kind": "edge"},
    "twitch.RewardCampaignEvent": {
        "label": "Reward campaign",
        "kind": "reward_campaign",
    },
    "twitch.RewardEvent": {"label": "Reward", "kind": "reward"},
    "twitch.DropCampaignAllowedChannelEvent": {
        "label": "Channel access",
        "kind": "channel_access",
    },
    "twitch.GameOwnerEvent": {"label": "Game owner", "kind": "game_owner"},
}

MODEL_FILTERS: dict[str, tuple[type[Model], ...]] = {
    "campaigns": (DropCampaign,),
    "drops": (TimeBasedDrop,),
    "benefits": (DropBenefit, DropBenefitEdge),
    "games": (Game,),
    "organizations": (Organization,),
    "channels": (Channel,),
    "reward_campaigns": (RewardCampaign,),
    "rewards": (Reward,),
    "memberships": (DropCampaignAllowedChannel, GameOwner),
}

MODEL_FILTER_LABELS: dict[str, str] = {
    "": "All objects",
    "campaigns": "Campaigns",
    "drops": "Drops",
    "benefits": "Benefits",
    "games": "Games",
    "organizations": "Organizations",
    "channels": "Channels",
    "reward_campaigns": "Reward campaigns",
    "rewards": "Rewards",
    "memberships": "Memberships",
}

LABEL_LABELS: dict[str, str] = {
    "created": "Created",
    "updated": "Updated",
    "deleted": "Deleted",
    "channel_added": "Channel added",
    "channel_removed": "Channel removed",
    "owner_added": "Owner added",
    "owner_removed": "Owner removed",
}

KIND_THEMES: dict[str, str] = {
    "campaign": "primary",
    "drop": "success",
    "benefit": "warning",
    "edge": "secondary",
    "game": "accent",
    "organization": "info",
    "channel": "secondary",
    "reward_campaign": "accent",
    "reward": "accent",
    "channel_access": "secondary",
    "game_owner": "secondary",
}

ACTION_FILTERS: dict[str, tuple[str, ...]] = {
    "created": ("created",),
    "updated": ("updated",),
    "deleted": ("deleted",),
    "membership_added": ("channel_added", "owner_added"),
    "membership_removed": ("channel_removed", "owner_removed"),
}

FIELD_LABELS: dict[str, str] = {
    "account_link_url": "Account link",
    "allow_is_enabled": "Allowed",
    "api_status": "Status",
    "claim_count": "Claim count",
    "details_url": "Details URL",
    "distribution_type": "Distribution",
    "end_at": "End",
    "entitlement_limit": "Entitlement limit",
    "event_based_drops": "Event drops",
    "image_asset_url": "Image URL",
    "image_file": "Image file",
    "is_account_connected": "Account connected",
    "is_ios_available": "iOS available",
    "precondition_drops": "Precondition drops",
    "required_minutes_watched": "Required minutes",
    "required_subs": "Required subs",
    "start_at": "Start",
}

FK_FIELDS: dict[str, str] = {
    "benefit_id": "benefit",
    "campaign_id": "campaign",
    "channel_id": "channel",
    "drop_id": "drop",
    "dropcampaign_id": "campaign",
    "game_id": "game",
    "organization_id": "organization",
    "owner_id": "organization",
    "owner_organization_id": "organization",
    "reward_campaign_id": "reward_campaign",
}


@dataclass(frozen=True)
class _Value:
    text: str
    url: str = ""
    image_url: str = ""


def event_models_for_filter(model_filter: str) -> list[type[Model]]:
    """Return tracked models matching a UI model filter.

    Args:
        model_filter: Filter key from ``MODEL_FILTERS``.

    Returns:
        Matching tracked models.
    """
    if not model_filter:
        return list(TRACKED_MODELS)
    return list(MODEL_FILTERS.get(model_filter, TRACKED_MODELS))


EVENT_MODEL_BY_LABEL: dict[str, Any] = {
    model.pgh_event_model._meta.label: model.pgh_event_model  # ruff: ignore[private-member-access]
    for model in TRACKED_MODELS
}

_SEARCH_FIELDS: tuple[str, ...] = ("name", "display_name", "twitch_id", "slug")


@dataclass
class HistoryPage:
    """A cursor-paginated page of aggregate history events."""

    rows: list[dict[str, Any]]
    older_cursor: str | None = None
    newer_cursor: str | None = None


def encode_event_cursor(event: Any) -> str:  # ruff:ignore[any-type]
    """Encode an event's position as an opaque pagination cursor.

    Args:
        event: Event model instance.

    Returns:
        URL-safe cursor string.
    """
    payload: str = (
        f"{event.pgh_created_at.isoformat()}|"
        f"{event._meta.label}|"  # ruff:ignore[private-member-access]
        f"{event.pk}"
    )
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_event_cursor(cursor: str | None) -> tuple[datetime, str, int] | None:
    """Decode an opaque pagination cursor.

    Args:
        cursor: Cursor string from the query string.

    Returns:
        Tuple of timestamp, event model label, and event ID, or None.
    """
    if not cursor:
        return None
    try:
        raw: str = base64.urlsafe_b64decode(cursor.encode()).decode()
        timestamp_raw, model_label, event_id_raw = raw.split("|")
        return (
            datetime.fromisoformat(timestamp_raw),
            model_label,
            int(event_id_raw),
        )
    except ValueError, TypeError, UnicodeDecodeError:
        return None


def _search_q(event_model: Any, q: str) -> Q:  # ruff:ignore[any-type]
    field_names: set[str] = {
        field.name
        for field in event_model._meta.fields  # ruff:ignore[private-member-access]
    }
    query: Q = Q(pk__in=[])
    for field_name in _SEARCH_FIELDS:
        if field_name in field_names:
            query |= Q(**{f"{field_name}__icontains": q})
    return query


def _event_querysets(
    *,
    model_filter: str = "",
    action_filter: str = "",
    q: str = "",
    since: datetime | None = None,
) -> list[QuerySet]:
    labels: tuple[str, ...] = (
        ACTION_FILTERS.get(action_filter, (action_filter,)) if action_filter else ()
    )
    querysets: list[QuerySet] = []
    for model in event_models_for_filter(model_filter):
        event_model: Any = model.pgh_event_model
        queryset: QuerySet = event_model.objects.all()
        if since is not None:
            queryset = queryset.filter(pgh_created_at__gte=since)
        if labels:
            queryset = queryset.filter(pgh_label__in=labels)
        if q:
            queryset = queryset.filter(_search_q(event_model, q))
        querysets.append(queryset)
    return querysets


def _event_data(event: Any) -> dict[str, Any]:  # ruff:ignore[any-type]
    return {
        field.attname: getattr(event, field.attname)
        for field in event._meta.fields  # ruff:ignore[private-member-access]
        if not field.name.startswith("pgh_")
    }


def _load_previous_events(events: list[Any]) -> dict[tuple[type[Any], int], Any]:
    """Load the previous event for each updated event in one query per model.

    Args:
        events: Materialized event model instances for the current page.

    Returns:
        Mapping of ``(event_model, event_id)`` to the previous event instance.
    """
    grouped: dict[type[Any], list[Any]] = {}
    for event in events:
        if event.pgh_label != "updated":
            continue
        obj_id: int | None = getattr(event, "pgh_obj_id", None)
        if obj_id is not None:
            grouped.setdefault(event.__class__, []).append(event)

    previous_events: dict[tuple[type[Any], int], Any] = {}
    for event_model, model_events in grouped.items():
        earliest_by_obj: dict[int, tuple[datetime, int]] = {}
        for event in model_events:
            obj_id = event.pgh_obj_id
            key = (event.pgh_created_at, event.pgh_id)
            current_min: tuple[datetime, int] = earliest_by_obj.get(obj_id, key)
            earliest_by_obj[obj_id] = min(current_min, key)

        values_sql: str = ", ".join("(%s, %s, %s)" for _ in earliest_by_obj)
        params: list[Any] = []
        for obj_id, (created_at, event_id) in earliest_by_obj.items():
            params.extend((obj_id, created_at, event_id))

        table: str = event_model._meta.db_table  # ruff:ignore[private-member-access]
        sql: str = (
            f"SELECT DISTINCT ON (e.pgh_obj_id) e.* "  # ruff: ignore[hardcoded-sql-expression]
            f"FROM {table} e "
            f"JOIN (VALUES {values_sql}) AS v(obj_id, min_created_at, min_id) "
            "ON e.pgh_obj_id = v.obj_id "
            "AND (e.pgh_created_at, e.pgh_id) < (v.min_created_at, v.min_id) "
            "ORDER BY e.pgh_obj_id, e.pgh_created_at DESC, e.pgh_id DESC"
        )
        previous_before: dict[int, Any] = {
            row.pgh_obj_id: row for row in event_model.objects.raw(sql, params)
        }

        selected_by_obj: dict[int, list[Any]] = {}
        for event in model_events:
            selected_by_obj.setdefault(event.pgh_obj_id, []).append(event)

        for obj_id, obj_events in selected_by_obj.items():
            chain: list[Any] = []
            if previous_before.get(obj_id) is not None:
                chain.append(previous_before[obj_id])
            chain.extend(
                sorted(
                    obj_events,
                    key=lambda event: (event.pgh_created_at, event.pgh_id),
                ),
            )
            for index in range(1, len(chain)):
                previous_events[event_model, chain[index].pk] = chain[index - 1]

    return previous_events


def _event_diff(event: Any, previous: Any | None) -> dict[str, list[Any]] | None:  # ruff:ignore[any-type]
    if event.pgh_label != "updated" or previous is None:
        return None

    diff: dict[str, list[Any]] = {}
    for field in event._meta.fields:  # ruff:ignore[private-member-access]
        name: str = field.attname
        if name.startswith("pgh_") or name == "id":
            continue
        old_value: Any = getattr(previous, name, None)
        new_value: Any = getattr(event, name, None)
        if old_value != new_value:
            diff[name] = [old_value, new_value]
    return diff or None


def _materialize_events(events: Iterable[Any]) -> list[dict[str, Any]]:
    event_list: list[Any] = list(events)
    context_ids: set[Any] = {
        event.pgh_context_id for event in event_list if event.pgh_context_id
    }
    contexts: dict[Any, dict[str, Any]] = dict(
        Context.objects.filter(pk__in=context_ids).values_list("pk", "metadata"),
    )
    previous_events: dict[tuple[type[Any], int], Any] = _load_previous_events(
        event_list,
    )

    rows: list[dict[str, Any]] = []
    for event in event_list:
        tracked_model: Any = getattr(event, "pgh_tracked_model", None)
        rows.append({
            "pgh_slug": f"{event._meta.label}:{event.pk}",  # ruff:ignore[private-member-access]
            "pgh_model": event._meta.label,  # ruff:ignore[private-member-access]
            "pgh_id": event.pk,
            "pgh_created_at": event.pgh_created_at,
            "pgh_label": event.pgh_label,
            "pgh_obj_model": (
                tracked_model._meta.label  # ruff:ignore[private-member-access]
                if tracked_model is not None
                else ""
            ),
            "pgh_obj_id": getattr(event, "pgh_obj_id", None),
            "pgh_data": _event_data(event),
            "pgh_diff": _event_diff(
                event,
                previous_events.get((event.__class__, event.pk)),
            ),
            "pgh_context": contexts.get(event.pgh_context_id, {}),
        })
    return rows


def history_page_for_querysets(
    querysets: Iterable[QuerySet],
    *,
    cursor: str | None = None,
    direction: str = "older",
    per_page: int = 100,
) -> HistoryPage:
    """Build a cursor-paginated history page from per-event-model querysets.

    Args:
        querysets: Querysets, one per event model.
        cursor: Opaque cursor for the current page.
        direction: ``older`` or ``newer`` relative to the cursor.
        per_page: Number of rows per page.

    Returns:
        Materialized history page.
    """
    queryset_list: list[QuerySet] = list(querysets)
    cursor_data: tuple[datetime, str, int] | None = decode_event_cursor(cursor)
    events: list[Any] = []

    for base_queryset in queryset_list:
        label: str = base_queryset.model._meta.label  # ruff:ignore[private-member-access]
        queryset: QuerySet = base_queryset
        if cursor_data is not None:
            timestamp, cursor_label, cursor_id = cursor_data
            if direction == "newer":
                if label == cursor_label:
                    queryset = queryset.filter(
                        Q(pgh_created_at__gt=timestamp)
                        | Q(pgh_created_at=timestamp, pgh_id__gt=cursor_id),
                    )
                elif label > cursor_label:
                    queryset = queryset.filter(pgh_created_at__gte=timestamp)
                else:
                    queryset = queryset.filter(pgh_created_at__gt=timestamp)
            elif label == cursor_label:
                queryset = queryset.filter(
                    Q(pgh_created_at__lt=timestamp)
                    | Q(pgh_created_at=timestamp, pgh_id__lt=cursor_id),
                )
            elif label < cursor_label:
                queryset = queryset.filter(pgh_created_at__lte=timestamp)
            else:
                queryset = queryset.filter(pgh_created_at__lt=timestamp)

        if direction == "newer":
            events.extend(
                queryset.order_by("pgh_created_at", "pgh_id")[: per_page + 1],
            )
        else:
            events.extend(
                queryset.order_by("-pgh_created_at", "-pgh_id")[: per_page + 1],
            )

    if direction == "newer":
        events.sort(
            key=lambda event: (
                event.pgh_created_at,
                event._meta.label,  # ruff:ignore[private-member-access]
                event.pgh_id,
            ),
        )
    else:
        events.sort(
            key=lambda event: (
                event.pgh_created_at,
                event._meta.label,  # ruff:ignore[private-member-access]
                event.pgh_id,
            ),
            reverse=True,
        )

    has_more: bool = len(events) > per_page
    selected: list[Any] = events[:per_page]
    if direction == "newer":
        selected.reverse()

    older_cursor: str | None = None
    newer_cursor: str | None = None
    if selected:
        if direction == "older":
            newer_cursor = cursor if cursor_data is not None else None
            if has_more:
                older_cursor = encode_event_cursor(selected[-1])
        else:
            older_cursor = encode_event_cursor(selected[-1])
            if has_more:
                newer_cursor = encode_event_cursor(selected[0])

    return HistoryPage(
        rows=_materialize_events(selected),
        older_cursor=older_cursor,
        newer_cursor=newer_cursor,
    )


def history_page(
    *,
    model_filter: str = "",
    action_filter: str = "",
    q: str = "",
    since: datetime | None = None,
    cursor: str | None = None,
    direction: str = "older",
    per_page: int = 100,
) -> HistoryPage:
    """Build the global/event-filtered history page.

    Args:
        model_filter: Optional UI model filter.
        action_filter: Optional UI action filter.
        q: Optional name or ID search string.
        since: Optional lower bound for pgh_created_at.
        cursor: Opaque cursor for the current page.
        direction: ``older`` or ``newer`` relative to the cursor.
        per_page: Number of rows per page.

    Returns:
        Materialized history page.
    """
    return history_page_for_querysets(
        _event_querysets(
            model_filter=model_filter,
            action_filter=action_filter,
            q=q,
            since=since,
        ),
        cursor=cursor,
        direction=direction,
        per_page=per_page,
    )


def event_detail_by_slug(slug: str) -> dict[str, Any] | None:
    """Fetch and materialize one event by its aggregate slug.

    Args:
        slug: Aggregate event slug in ``app.ModelEvent:id`` form.

    Returns:
        Materialized event row, or None when not found.
    """
    try:
        model_label, raw_id = slug.rsplit(":", 1)
        event_id: int = int(raw_id)
    except ValueError, TypeError:
        return None

    event_model: Any | None = EVENT_MODEL_BY_LABEL.get(model_label)
    if event_model is None:
        return None
    event = event_model.objects.filter(pk=event_id).first()
    if event is None:
        return None
    return _materialize_events([event])[0]


def campaign_history_querysets(campaign: DropCampaign) -> list[QuerySet]:
    """Return per-event-model querysets for a campaign and its children.

    Args:
        campaign: Campaign whose history should be returned.

    Returns:
        Querysets for campaign, drop, benefit, edge, and channel ACL events.
    """
    drop_ids: list[int] = list(
        TimeBasedDrop.objects.filter(campaign=campaign).values_list("pk", flat=True),
    )
    benefit_ids: list[int] = list(
        DropBenefitEdge.objects
        .filter(drop__campaign=campaign)
        .values_list("benefit_id", flat=True)
        .distinct(),
    )
    return [
        DropCampaign.pgh_event_model.objects.filter(pgh_obj_id=campaign.pk),
        TimeBasedDrop.pgh_event_model.objects.filter(campaign_id=campaign.pk),
        DropBenefitEdge.pgh_event_model.objects.filter(drop_id__in=drop_ids),
        DropBenefit.pgh_event_model.objects.filter(pgh_obj_id__in=benefit_ids),
        DropCampaignAllowedChannel.pgh_event_model.objects.filter(
            dropcampaign_id=campaign.pk,
        ),
    ]


def game_history_querysets(game: Game) -> list[QuerySet]:
    """Return per-event-model querysets for a game and its direct children.

    Args:
        game: Game whose history should be returned.

    Returns:
        Querysets for game, campaign, reward campaign, benefit, and owner events.
    """
    return [
        Game.pgh_event_model.objects.filter(pgh_obj_id=game.pk),
        DropCampaign.pgh_event_model.objects.filter(game_id=game.pk),
        RewardCampaign.pgh_event_model.objects.filter(game_id=game.pk),
        DropBenefit.pgh_event_model.objects.filter(game_id=game.pk),
        GameOwner.pgh_event_model.objects.filter(game_id=game.pk),
    ]


def organization_history_querysets(organization: Organization) -> list[QuerySet]:
    """Return per-event-model querysets for an organization and its games.

    Args:
        organization: Organization whose history should be returned.

    Returns:
        Querysets for organization, game, owner, and campaign events.
    """
    game_ids: list[int] = list(
        organization.games.values_list("pk", flat=True),
    )
    return [
        Organization.pgh_event_model.objects.filter(pgh_obj_id=organization.pk),
        Game.pgh_event_model.objects.filter(pgh_obj_id__in=game_ids),
        GameOwner.pgh_event_model.objects.filter(organization_id=organization.pk),
        DropCampaign.pgh_event_model.objects.filter(owner_id=organization.pk),
        DropBenefit.pgh_event_model.objects.filter(
            owner_organization_id=organization.pk,
        ),
    ]


def channel_history_querysets(channel: Channel) -> list[QuerySet]:
    """Return per-event-model querysets for a channel.

    Args:
        channel: Channel whose history should be returned.

    Returns:
        Querysets for channel and channel-access events.
    """
    return [
        Channel.pgh_event_model.objects.filter(pgh_obj_id=channel.pk),
        DropCampaignAllowedChannel.pgh_event_model.objects.filter(
            channel_id=channel.pk,
        ),
    ]


def reward_campaign_history_querysets(
    reward_campaign: RewardCampaign,
) -> list[QuerySet]:
    """Return per-event-model querysets for a reward campaign.

    Args:
        reward_campaign: Reward campaign whose history should be returned.

    Returns:
        Querysets for reward campaign and reward events.
    """
    return [
        RewardCampaign.pgh_event_model.objects.filter(
            pgh_obj_id=reward_campaign.pk,
        ),
        Reward.pgh_event_model.objects.filter(
            reward_campaign_id=reward_campaign.pk,
        ),
    ]


def _model_kind(event_model: str) -> str:
    return MODEL_INFO.get(event_model, {}).get("kind", "")


def _model_label(event_model: str) -> str:
    return MODEL_INFO.get(event_model, {}).get("label", event_model.rsplit(".", 1)[-1])


_MAX_PREVIEW_LENGTH: int = 280


def _json_value(value: Any) -> str:  # ruff:ignore[any-type]
    if isinstance(value, (dict, list)):
        rendered: str = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(rendered) > _MAX_PREVIEW_LENGTH:
            return rendered[: _MAX_PREVIEW_LENGTH - 3] + "..."
        return rendered
    return str(value)


def _format_datetime(value: datetime) -> str:
    return value.strftime("%b %d, %Y %H:%M")


_IMAGE_FIELDS: set[str] = {
    "banner_image_url",
    "box_art",
    "image_asset_url",
    "image_file",
    "image_url",
    "thumbnail_image_url",
}


def _image_preview_url(value: Any) -> str:  # ruff:ignore[any-type]
    """Return a browser-loadable URL for an image field value.

    Args:
        value: Raw field value.

    Returns:
        Image URL, or an empty string when there is no image.
    """
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith(("https://", "http://", "/")):
        return normalize_twitch_image_url(value)
    media_url: str = settings.MEDIA_URL or "/media/"
    return f"{media_url.rstrip('/')}/{value.lstrip('/')}"


def _format_generic(value: Any) -> str:  # ruff:ignore[any-type]
    if value is None or (isinstance(value, str) and not value):
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, str):
        if "T" in value and (value.endswith("Z") or "+" in value):
            try:
                parsed: datetime = datetime.fromisoformat(value)
            except ValueError:
                return value
            return _format_datetime(parsed)
        if len(value) > _MAX_PREVIEW_LENGTH:
            return value[: _MAX_PREVIEW_LENGTH - 3] + "..."
        return value
    return _json_value(value)


class HistoryResolver:
    """Resolve database IDs in event payloads to names and links with batched queries."""

    def __init__(self, events: Iterable[dict[str, Any]]) -> None:
        """Resolve relationships for a batch of event rows."""
        self.events: list[dict[str, Any]] = list(events)
        self.campaigns: dict[int, dict[str, Any]] = {}
        self.games: dict[int, dict[str, Any]] = {}
        self.organizations: dict[int, dict[str, Any]] = {}
        self.channels: dict[int, dict[str, Any]] = {}
        self.drops: dict[int, dict[str, Any]] = {}
        self.benefits: dict[int, dict[str, Any]] = {}
        self.reward_campaigns: dict[int, dict[str, Any]] = {}
        self._load()

    def _collect_ids(self, field: str) -> set[int]:
        ids: set[int] = set()
        for event in self.events:
            data: dict[str, Any] = event.get("pgh_data") or {}
            value: Any = data.get(field)
            if isinstance(value, int):
                ids.add(value)
        return ids

    def _load(self) -> None:
        drop_ids: set[int] = self._collect_ids("drop_id")
        campaign_ids: set[int] = self._collect_ids("campaign_id") | self._collect_ids(
            "dropcampaign_id",
        )
        if drop_ids:
            self.drops = {
                row["pk"]: row
                for row in TimeBasedDrop.objects.filter(pk__in=drop_ids).values(
                    "pk",
                    "name",
                    "campaign_id",
                )
            }
            campaign_ids |= {
                row["campaign_id"]
                for row in self.drops.values()
                if row.get("campaign_id")
            }
        if campaign_ids:
            self.campaigns = {
                row["pk"]: row
                for row in DropCampaign.objects.filter(pk__in=campaign_ids).values(
                    "pk",
                    "twitch_id",
                    "name",
                )
            }
        game_ids: set[int] = self._collect_ids("game_id")
        if game_ids:
            self.games = {
                row["pk"]: row
                for row in Game.objects.filter(pk__in=game_ids).values(
                    "pk",
                    "twitch_id",
                    "display_name",
                    "name",
                    "slug",
                )
            }
        org_ids: set[int] = (
            self._collect_ids("organization_id")
            | self._collect_ids("owner_id")
            | self._collect_ids("owner_organization_id")
        )
        if org_ids:
            self.organizations = {
                row["pk"]: row
                for row in Organization.objects.filter(pk__in=org_ids).values(
                    "pk",
                    "twitch_id",
                    "name",
                )
            }
        channel_ids: set[int] = self._collect_ids("channel_id")
        if channel_ids:
            self.channels = {
                row["pk"]: row
                for row in Channel.objects.filter(pk__in=channel_ids).values(
                    "pk",
                    "twitch_id",
                    "name",
                    "display_name",
                )
            }
        benefit_ids: set[int] = self._collect_ids("benefit_id")
        if benefit_ids:
            self.benefits = {
                row["pk"]: row
                for row in DropBenefit.objects.filter(pk__in=benefit_ids).values(
                    "pk",
                    "name",
                )
            }
        reward_campaign_ids: set[int] = self._collect_ids("reward_campaign_id")
        if reward_campaign_ids:
            self.reward_campaigns = {
                row["pk"]: row
                for row in RewardCampaign.objects.filter(
                    pk__in=reward_campaign_ids,
                ).values("pk", "twitch_id", "name")
            }

    def campaign_name(self, pk: int | None) -> str:
        """Return a campaign name for a database ID.

        Args:
            pk: Campaign primary key.

        Returns:
            Campaign name, or the ID as a string when not found.
        """
        row: dict[str, Any] | None = self.campaigns.get(pk or 0)
        return str(row["name"]) if row else str(pk or "")

    def campaign_url(self, pk: int | None) -> str:
        """Return the detail URL for a campaign ID.

        Args:
            pk: Campaign primary key.

        Returns:
            Campaign detail URL, or an empty string.
        """
        row: dict[str, Any] | None = self.campaigns.get(pk or 0)
        if not row:
            return ""
        return reverse("twitch:campaign_detail", args=[row["twitch_id"]])

    def drop_name(self, pk: int | None) -> str:
        """Return a drop name for a database ID.

        Args:
            pk: Drop primary key.

        Returns:
            Drop name, or the ID as a string when not found.
        """
        row: dict[str, Any] | None = self.drops.get(pk or 0)
        return str(row["name"]) if row else str(pk or "")

    def drop_campaign_url(self, pk: int | None) -> str:
        """Return the campaign URL for a drop ID.

        Args:
            pk: Drop primary key.

        Returns:
            Campaign detail URL, or an empty string.
        """
        row: dict[str, Any] | None = self.drops.get(pk or 0)
        if not row:
            return ""
        return self.campaign_url(row.get("campaign_id"))

    def value(self, field: str, value: Any) -> _Value:  # ruff:ignore[any-type]
        """Format an event value, resolving foreign keys when possible.

        Args:
            field: Event field name.
            value: Raw event value.

        Returns:
            Formatted value with an optional link.
        """
        if field in _IMAGE_FIELDS:
            preview_url: str = _image_preview_url(value)
            if preview_url:
                return _Value(_format_generic(value), image_url=preview_url)

        kind: str = FK_FIELDS.get(field, "")
        if kind == "campaign" and isinstance(value, int):
            return _Value(self.campaign_name(value), self.campaign_url(value))
        if kind == "drop" and isinstance(value, int):
            return _Value(self.drop_name(value), self.drop_campaign_url(value))
        if kind == "benefit" and isinstance(value, int):
            row: dict[str, Any] | None = self.benefits.get(value)
            if row:
                return _Value(str(row["name"]))
        if kind == "game" and isinstance(value, int):
            row = self.games.get(value)
            if row:
                name: str = str(row["display_name"] or row["name"] or row["slug"])
                return _Value(
                    name,
                    reverse("twitch:game_detail", args=[row["twitch_id"]]),
                )
        if kind == "organization" and isinstance(value, int):
            row = self.organizations.get(value)
            if row:
                return _Value(
                    str(row["name"]),
                    reverse("twitch:organization_detail", args=[row["twitch_id"]]),
                )
        if kind == "channel" and isinstance(value, int):
            row = self.channels.get(value)
            if row:
                return _Value(
                    str(row["display_name"] or row["name"]),
                    reverse("twitch:channel_detail", args=[row["twitch_id"]]),
                )
        if kind == "reward_campaign" and isinstance(value, int):
            row = self.reward_campaigns.get(value)
            if row:
                return _Value(
                    str(row["name"]),
                    reverse("twitch:reward_campaign_detail", args=[row["twitch_id"]]),
                )
        return _Value(_format_generic(value))

    def object_name_and_url(self, event: dict[str, Any]) -> tuple[str, str]:
        """Return a display name and URL for the event's object.

        Args:
            event: Raw aggregate event row.

        Returns:
            Tuple of object name and detail URL.
        """
        event_model: str = str(event.get("pgh_model") or "")
        kind: str = _model_kind(event_model)
        data: dict[str, Any] = event.get("pgh_data") or {}
        name: str = str(
            data.get("name") or data.get("display_name") or data.get("twitch_id") or "",
        )
        url: str = ""

        if kind in {"campaign", "game", "organization", "channel", "reward_campaign"}:
            route_by_kind: dict[str, str] = {
                "campaign": "twitch:campaign_detail",
                "game": "twitch:game_detail",
                "organization": "twitch:organization_detail",
                "channel": "twitch:channel_detail",
                "reward_campaign": "twitch:reward_campaign_detail",
            }
            twitch_id: Any = data.get("twitch_id")
            if twitch_id:
                url = reverse(route_by_kind[kind], args=[twitch_id])
        elif kind == "drop":
            url = self.drop_campaign_url(data.get("campaign_id"))
        elif kind == "edge":
            url = self.drop_campaign_url(data.get("drop_id"))
            name = f"{self.value('benefit_id', data.get('benefit_id')).text} on {self.drop_name(data.get('drop_id'))}"
        elif kind == "channel_access":
            campaign_pk: Any = data.get("dropcampaign_id")
            channel_pk: Any = data.get("channel_id")
            url = self.campaign_url(campaign_pk)
            name = (
                f"{self.value('channel_id', channel_pk).text} in "
                f"{self.campaign_name(campaign_pk)}"
            )
        elif kind == "game_owner":
            game_pk: Any = data.get("game_id")
            org_pk: Any = data.get("organization_id")
            game_row: dict[str, Any] | None = self.games.get(game_pk or 0)
            if game_row:
                url = reverse("twitch:game_detail", args=[game_row["twitch_id"]])
            name = (
                f"{self.value('organization_id', org_pk).text} owns "
                f"{self.value('game_id', game_pk).text}"
            )

        return name or event_model.rsplit(".", 1)[-1], url


def _diff_rows(
    event: dict[str, Any],
    resolver: HistoryResolver,
) -> list[dict[str, Any]]:
    diff: dict[str, Any] = event.get("pgh_diff") or {}
    rows: list[dict[str, Any]] = []
    for field, change in diff.items():
        old_value: _Value = resolver.value(field, change[0] if change else None)
        new_value: _Value = resolver.value(field, change[1] if change else None)
        rows.append({
            "field": FIELD_LABELS.get(
                field,
                field.replace("_id", "").replace("_", " ").title(),
            ),
            "old": old_value,
            "new": new_value,
        })
    return rows


def _snapshot_rows(
    event: dict[str, Any],
    resolver: HistoryResolver,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data: dict[str, Any] = event.get("pgh_data") or {}
    diff: dict[str, Any] = event.get("pgh_diff") or {}
    label: str = str(event.get("pgh_label") or "")
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []

    for field, new_raw in sorted(data.items()):
        old_raw: Any = diff.get(field, [data.get(field), new_raw])[0] if diff else None
        old_value: _Value = resolver.value(field, old_raw)
        new_value: _Value = resolver.value(field, new_raw)
        before.append({
            "field": FIELD_LABELS.get(
                field,
                field.replace("_id", "").replace("_", " ").title(),
            ),
            "value": old_value,
            "changed": field in diff,
        })
        after.append({
            "field": FIELD_LABELS.get(
                field,
                field.replace("_id", "").replace("_", " ").title(),
            ),
            "value": new_value,
            "changed": field in diff,
        })

    if label == "created" or (label == "updated" and not diff):
        before = []
    elif label == "deleted":
        after = []
    return before, after


SOURCE_URLS: dict[str, str] = {
    "TwitchDropsMiner": "https://github.com/DevilXD/TwitchDropsMiner",
    "SunkwiBOT/twitch-drops-api": "https://github.com/SunkwiBOT/twitch-drops-api",
}


def _source_info(
    event: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, str, str]:
    """Return the data source, source URL, and replay/live mode.

    Args:
        event: Raw aggregate event row.
        context: Event context metadata.

    Returns:
        Tuple of source label, source URL, and mode.
    """
    data: dict[str, Any] = event.get("pgh_data") or {}
    source: str = str(context.get("data_source") or data.get("data_source") or "")
    mode: str = "replay" if context.get("mode") == "history" else "live"
    if not source:
        source = mode
    return source, SOURCE_URLS.get(source, ""), mode


def build_event_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw event rows into template-friendly history rows.

    Args:
        events: Aggregate event rows from ``history_queryset``.

    Returns:
        List of presentation dictionaries.
    """
    event_list: list[dict[str, Any]] = list(events)
    resolver: HistoryResolver = HistoryResolver(event_list)
    rows: list[dict[str, Any]] = []

    for event in event_list:
        event_model: str = str(event.get("pgh_model") or "")
        kind: str = _model_kind(event_model)
        label: str = str(event.get("pgh_label") or "")
        context: dict[str, Any] = event.get("pgh_context") or {}
        object_name, object_url = resolver.object_name_and_url(event)
        diff_rows: list[dict[str, Any]] = _diff_rows(event, resolver)
        source, source_url, mode = _source_info(event, context)

        if kind in {"channel_access", "game_owner"}:
            summary: str = LABEL_LABELS.get(label, label)
        elif label == "created":
            summary = "Created"
        elif label == "deleted":
            summary = "Deleted"
        elif not diff_rows:
            summary = "First observed"
        else:
            summary = f"{len(diff_rows)} field(s) changed"

        rows.append({
            "slug": event.get("pgh_slug"),
            "created_at": event.get("pgh_created_at"),
            "model": _model_label(event_model),
            "kind": kind,
            "theme": KIND_THEMES.get(kind, "secondary"),
            "label": LABEL_LABELS.get(label, label),
            "label_key": label,
            "object_name": object_name,
            "object_url": object_url,
            "summary": summary,
            "diff": diff_rows,
            "source": source,
            "source_url": source_url,
            "mode": mode,
            "source_file": context.get("source_file", ""),
        })

    return rows


def build_event_detail(event: dict[str, Any]) -> dict[str, Any]:
    """Build the before/after presentation for a single event.

    Args:
        event: One aggregate event row.

    Returns:
        Template-friendly event detail dictionary.
    """
    resolver: HistoryResolver = HistoryResolver([event])
    before, after = _snapshot_rows(event, resolver)
    event_model: str = str(event.get("pgh_model") or "")
    kind: str = _model_kind(event_model)
    label: str = str(event.get("pgh_label") or "")
    context: dict[str, Any] = event.get("pgh_context") or {}
    object_name, object_url = resolver.object_name_and_url(event)
    source, source_url, mode = _source_info(event, context)
    return {
        "slug": event.get("pgh_slug"),
        "created_at": event.get("pgh_created_at"),
        "model": _model_label(event_model),
        "kind": kind,
        "theme": KIND_THEMES.get(kind, "secondary"),
        "label": LABEL_LABELS.get(label, label),
        "label_key": label,
        "object_name": object_name,
        "object_url": object_url,
        "before": before,
        "after": after,
        "source": source,
        "source_url": source_url,
        "mode": mode,
        "source_file": context.get("source_file", ""),
        "raw": event.get("pgh_data") or {},
        "raw_json": json.dumps(
            event.get("pgh_data") or {},
            indent=2,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        ),
    }
