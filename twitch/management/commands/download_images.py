from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from django.core.management.base import BaseCommand
from django.db.models.query import QuerySet

from twitch.models import Game

if TYPE_CHECKING:
    from collections.abc import Generator

    from django.db.models import QuerySet


MAX_AMOUNT_GAMES_PER_REQUEST = 100
"""Maximum number of games that can be requested from Twitch API in a single request."""


class TwitchTooManyGamesError(Exception):
    """Raised when too many game IDs are requested from the Twitch API."""

    def __init__(self, max_allowed: int, actual: int) -> None:
        """Initialize the exception with details about the limit breach.

        Args:
            max_allowed (int): The maximum number of game IDs allowed per request.
            actual (int): The actual number of game IDs requested.
        """
        self.max_allowed: int = max_allowed
        self.actual: int = actual
        super().__init__(f"Requested {actual} game IDs, but the maximum allowed is {max_allowed}.")


@dataclass
class AuthResponse:
    """Represents a response from the Twitch authentication endpoint.

    {
        "access_token": "access12345token",
        "expires_in": 5587808,
        "token_type": "bearer"
    }

    The expires_in shows you the number of seconds before the access_token will expire and must be refreshed.
    """

    access_token: str
    """OAuth access token."""

    expires_in: int
    """The number of seconds before the access_token expires."""

    token_type: str
    """The type of the token. For example "bearer"."""


def download_images(twitch_client_id: str, client: httpx.Client, auth: AuthResponse, game_ids: list[int]) -> httpx.Response:
    """Downloads game data including images from the IGDB API.

    This function sends a POST request to the IGDB API to fetch game data with all available fields.
    The request is authorized using the Twitch client ID and access token.

    Args:
        twitch_client_id (str): The Twitch client ID used for authentication with IGDB API.
        client (httpx.Client): An instance of httpx.Client for making HTTP requests.
        auth (AuthResponse): The authentication response object containing the access token.
        game_ids (list[int]): A list of game IDs to fetch data for.

    Returns:
        httpx.Response: The HTTP response from the IGDB API containing game data.
    """
    url = "https://api.igdb.com/v4/games"

    body: str = "fields *; where id = (" + ",".join(str(gid) for gid in game_ids) + ");"

    response: httpx.Response = client.post(
        url=url,
        headers={"Client-ID": twitch_client_id, "Authorization": f"Bearer {auth.access_token}"},
        content=body,
        timeout=30.0,
    )
    return response


def batched_queryset(qs: QuerySet, batch_size: int = 500) -> Generator[list[Any], Any]:
    """Process a Django QuerySet in batches to avoid loading all records into memory at once.

    This function yields lists of igdb_id values from the provided QuerySet in batches
    of the specified size, allowing for efficient processing of large datasets.

    Args:
        qs (QuerySet): The Django QuerySet to process in batches
        batch_size (int, optional): The number of records to include in each batch. Defaults to 500.

    Yields:
        list[Any]: A batch of igdb_id values from the QuerySet

    Example:
        >>> games = Game.objects.all()
        >>> for batch in batched_queryset(games):
        ...     process_game_batch(batch)
    """
    start = 0
    while True:
        batch: list[Any] = list(qs.values_list("igdb_id", flat=True)[start : start + batch_size])
        if not batch:
            break
        yield batch
        start += batch_size


def get_twitch_data(twitch_client_id: str, game_ids: list[int]) -> httpx.Response:
    """Fetches game data from the Twitch API.

    Args:
        twitch_client_id (str): The Twitch client ID used for authentication.
        game_ids (list[int]): A list of game IDs to fetch data for.

    Raises:
        TwitchTooManyGamesError: If the number of game IDs exceeds the maximum allowed per request.
        ValueError: If the request parameters are invalid.
        PermissionError: If the authentication fails due to invalid credentials.

    References:
        - Twitch API Documentation: https://dev.twitch.tv/docs/api/reference#get-games

    Returns:
        httpx.Response: The HTTP response from the Twitch API containing game data.
    """
    if len(game_ids) > MAX_AMOUNT_GAMES_PER_REQUEST:
        raise TwitchTooManyGamesError(MAX_AMOUNT_GAMES_PER_REQUEST, len(game_ids))

    with httpx.Client() as client:
        response: httpx.Response = client.get(
            url="https://api.twitch.tv/helix/games",
            headers={"Client-ID": twitch_client_id},
            params=[("id", str(gid)) for gid in game_ids],
            timeout=30.0,
        )

        if response.status_code == httpx.codes.OK:
            # Successfully retrieved the specified games.
            return response

        if response.status_code == httpx.codes.BAD_REQUEST:
            # The request must specify the id or name or igdb_id query parameter.
            # The combined number of game IDs (id and igdb_id) and game names that you specify in the request must not exceed 100.
            if len(game_ids) > MAX_AMOUNT_GAMES_PER_REQUEST:
                raise TwitchTooManyGamesError(MAX_AMOUNT_GAMES_PER_REQUEST, len(game_ids))

            msg: str = f"Bad Request: Check that the request parameters are correct. Response: {response.text}"
            raise ValueError(msg)

        if response.status_code == httpx.codes.UNAUTHORIZED:
            # The Authorization header is required and must specify an app access token or user access token.
            # The access token is not valid.
            # The ID in the Client-Id header must match the client ID in the access token.
            msg = "Unauthorized: Check that the access token and client ID are correct."
            raise PermissionError(msg)

    return response


class Command(BaseCommand):
    """Populate database with data from Twitch and IGDB APIs."""

    help: str = __doc__ or ""

    def handle(self, **options) -> None:  # noqa: ARG002
        """Execute the image download process."""
        twitch_client_id: str | None = os.getenv("TWITCH_CLIENT_ID")
        twitch_client_secret: str | None = os.getenv("TWITCH_CLIENT_SECRET")
        if not twitch_client_id or not twitch_client_secret:
            self.stderr.write(self.style.ERROR("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set in environment"))
            return

        with httpx.Client() as client:
            response: httpx.Response = client.post(
                url="https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": twitch_client_id,
                    "client_secret": twitch_client_secret,
                    "grant_type": "client_credentials",
                },
            )

        auth: AuthResponse = AuthResponse(**response.json())

        self.stdout.write(self.style.SUCCESS(f"Auth response: {response.text}"))

        # Get game_ids from the database
        qs: QuerySet[Game, Game] = Game.objects.all()
        for game_ids in batched_queryset(qs, batch_size=500):
            with httpx.Client() as client:
                download_images(twitch_client_id=twitch_client_id, client=client, auth=auth, game_ids=game_ids)

            self.stdout.write(self.style.SUCCESS(f"Fetched {len(game_ids)} games"))
