from __future__ import annotations

from django.contrib import admin

from twitch.models import DropBenefit, DropBenefitEdge, DropCampaign, Game, Organization, TimeBasedDrop


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    """Admin configuration for Game model."""

    list_display = ("id", "display_name", "slug")
    search_fields = ("id", "display_name", "slug")
    readonly_fields = ("added_at", "updated_at")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    """Admin configuration for Organization model."""

    list_display = ("id", "name")
    search_fields = ("id", "name")
    readonly_fields = ("added_at", "updated_at")


class TimeBasedDropInline(admin.TabularInline):
    """Inline admin for TimeBasedDrop model."""

    model = TimeBasedDrop
    extra = 0


@admin.register(DropCampaign)
class DropCampaignAdmin(admin.ModelAdmin):
    """Admin configuration for DropCampaign model."""

    list_display = ("id", "name", "game", "start_at", "end_at", "is_active")
    list_filter = ("game",)
    search_fields = ("id", "name", "description")
    inlines = [TimeBasedDropInline]
    readonly_fields = ("added_at", "updated_at")


class DropBenefitEdgeInline(admin.TabularInline):
    """Inline admin for DropBenefitEdge model."""

    model = DropBenefitEdge
    extra = 0


@admin.register(TimeBasedDrop)
class TimeBasedDropAdmin(admin.ModelAdmin):
    """Admin configuration for TimeBasedDrop model."""

    list_display = (
        "id",
        "name",
        "campaign",
        "required_minutes_watched",
        "required_subs",
        "start_at",
        "end_at",
    )
    list_filter = ("campaign__game", "campaign")
    readonly_fields = ("added_at", "updated_at")

    search_fields = ("id", "name")
    inlines = [DropBenefitEdgeInline]


@admin.register(DropBenefit)
class DropBenefitAdmin(admin.ModelAdmin):
    """Admin configuration for DropBenefit model."""

    list_display = (
        "id",
        "name",
        "distribution_type",
        "entitlement_limit",
        "created_at",
    )
    list_filter = ("distribution_type",)
    search_fields = ("id", "name")
    readonly_fields = ("added_at", "updated_at")
