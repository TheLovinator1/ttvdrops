from io import StringIO
from unittest.mock import patch

import httpx
import pytest
from django.core.management import CommandError
from django.core.management import call_command

from twitch.models import ChatBadge
from twitch.models import ChatBadgeSet
from twitch.schemas import GlobalChatBadgesResponse

pytestmark: pytest.MarkDecorator = pytest.mark.django_db


def _build_response(title: str = "VIP") -> GlobalChatBadgesResponse:
    """Build a valid GlobalChatBadgesResponse payload for tests.

    Returns:
        A validated Twitch global chat badges response object.
    """
    return GlobalChatBadgesResponse.model_validate({
        "data": [
            {
                "set_id": "vip",
                "versions": [
                    {
                        "id": "1",
                        "image_url_1x": "https://example.com/vip-1x.png",
                        "image_url_2x": "https://example.com/vip-2x.png",
                        "image_url_4x": "https://example.com/vip-4x.png",
                        "title": title,
                        "description": "VIP Badge",
                        "click_action": "visit_url",
                        "click_url": "https://help.twitch.tv",
                    },
                ],
            },
        ],
    })


def test_raises_when_client_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Command should fail when client ID is not provided."""
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TWITCH_ACCESS_TOKEN", raising=False)

    with pytest.raises(CommandError, match="Twitch Client ID is required"):
        call_command("import_chat_badges", stdout=StringIO())


def test_raises_when_token_and_secret_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Command should fail when no token and no client secret are available."""
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TWITCH_ACCESS_TOKEN", raising=False)

    with pytest.raises(CommandError, match="Either --access-token or --client-secret"):
        call_command(
            "import_chat_badges",
            "--client-id",
            "client-id",
            stdout=StringIO(),
        )


@pytest.mark.django_db
def test_import_creates_then_updates_existing_badge() -> None:
    """Running import twice should update existing badges rather than duplicate them."""
    with patch(
        "twitch.management.commands.import_chat_badges.Command._fetch_global_chat_badges",
        side_effect=[_build_response("VIP"), _build_response("VIP Updated")],
    ) as fetch_mock:
        call_command(
            "import_chat_badges",
            "--client-id",
            "client-id",
            "--access-token",
            "access-token",
            stdout=StringIO(),
        )
        call_command(
            "import_chat_badges",
            "--client-id",
            "client-id",
            "--access-token",
            "access-token",
            stdout=StringIO(),
        )

    assert fetch_mock.call_count == 2
    assert ChatBadgeSet.objects.count() == 1
    assert ChatBadge.objects.count() == 1

    badge_set = ChatBadgeSet.objects.get(set_id="vip")
    badge = ChatBadge.objects.get(badge_set=badge_set, badge_id="1")
    assert badge.title == "VIP Updated"
    assert badge.click_action == "visit_url"
    assert badge.click_url == "https://help.twitch.tv"


@pytest.mark.django_db
def test_uses_client_credentials_when_access_token_missing() -> None:
    """Command should obtain token from Twitch when no access token is provided."""
    with (
        patch(
            "twitch.management.commands.import_chat_badges.Command._get_app_access_token",
            return_value="generated-token",
        ) as token_mock,
        patch(
            "twitch.management.commands.import_chat_badges.Command._fetch_global_chat_badges",
            return_value=GlobalChatBadgesResponse.model_validate({"data": []}),
        ) as fetch_mock,
    ):
        call_command(
            "import_chat_badges",
            "--client-id",
            "client-id",
            "--client-secret",
            "client-secret",
            stdout=StringIO(),
        )

    token_mock.assert_called_once_with("client-id", "client-secret")
    fetch_mock.assert_called_once_with(
        client_id="client-id",
        access_token="generated-token",
    )


def test_wraps_http_errors_from_badges_fetch() -> None:
    """Command should convert HTTP client errors to CommandError."""
    with (
        patch(
            "twitch.management.commands.import_chat_badges.Command._fetch_global_chat_badges",
            side_effect=httpx.HTTPError("boom"),
        ),
        pytest.raises(
            CommandError,
            match="Failed to fetch chat badges from Twitch API",
        ),
    ):
        call_command(
            "import_chat_badges",
            "--client-id",
            "client-id",
            "--access-token",
            "access-token",
            stdout=StringIO(),
        )
