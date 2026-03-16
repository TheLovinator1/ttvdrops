from typing import TYPE_CHECKING

from django.test import TestCase
from django.urls import reverse

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse


class YouTubeIndexViewTest(TestCase):
    """Tests for the YouTube drops channels index page."""

    def test_index_returns_200(self) -> None:
        """The YouTube index page should return HTTP 200."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(reverse("youtube:index"))
        assert response.status_code == 200

    def test_index_displays_known_channels(self) -> None:
        """The page should include key known channels from the partner list."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(reverse("youtube:index"))
        content: str = response.content.decode()

        assert "YouTube channels with rewards." in content
        assert "Call of Duty" in content
        assert "PlayOverwatch" in content
        assert "Hearthstone" in content
        assert "Fortnite" in content
        assert "Riot Games" in content
        assert "Ubisoft" in content

    def test_index_includes_partner_urls(self) -> None:
        """The page should render partner channel links from the source list."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(reverse("youtube:index"))
        content: str = response.content.decode()

        assert "https://www.youtube.com/channel/UCbLIqv9Puhyp9_ZjVtfOy7w" in content
        assert "https://www.youtube.com/user/epicfortnite" in content
        assert "https://www.youtube.com/lolesports" in content

    def test_index_groups_partners_alphabetically(self) -> None:
        """Partner sections should render grouped and in alphabetical order."""
        response: _MonkeyPatchedWSGIResponse = self.client.get(reverse("youtube:index"))
        content: str = response.content.decode()

        activision_cell: str = "<td>Activision (Call of Duty)</td>"
        blizzard_cell: str = "<td>Battle.net / Blizzard</td>"

        assert activision_cell in content
        assert blizzard_cell in content
        assert content.index(activision_cell) < content.index(
            blizzard_cell,
        )
