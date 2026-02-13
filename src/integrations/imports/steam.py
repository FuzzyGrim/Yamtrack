import logging
import time
from collections import defaultdict

import requests
from django.conf import settings

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from app.providers.igdb import ExternalGameSource, external_game
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

STEAM_API_BASE_URL = "https://api.steampowered.com"


def importer(steam_id, user, mode):
    """Import the user's games from Steam."""
    steam_importer = SteamImporter(steam_id, user, mode)
    return steam_importer.import_data()


class SteamImporter:
    """Class to handle importing user game data from Steam."""

    def __init__(self, steam_id, user, mode):
        """Initialize the importer with user details and mode.

        Args:
            steam_id (str): Steam user ID (64-bit SteamID) to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.steam_id = steam_id
        self.user = user
        self.mode = mode
        self.warnings = []
        self.api_key = getattr(settings, "STEAM_API_KEY", None)

        if not self.api_key:
            msg = "Steam API key not configured in environment variables"
            raise MediaImportError(msg)

        self.existing_media = helpers.get_existing_media(user)

        self.to_delete = defaultdict(lambda: defaultdict(set))

        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized Steam importer for Steam ID %s with mode %s",
            steam_id,
            mode,
        )

    def import_data(self):
        """Import user's Steam game library."""
        owned_games = self._get_owned_games()

        if not owned_games:
            logger.info("No games found for Steam user %s", self.steam_id)
            return {}, ""

        for game_data in owned_games:
            self._process_game(game_data)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        logger.info(
            "Steam import completed for user %s: %s",
            self.user.username,
            imported_counts,
        )

        return imported_counts, "\n".join(self.warnings) if self.warnings else ""

    def _get_owned_games(self):
        """Fetch owned games from Steam API with retry logic for rate limiting."""
        url = f"{STEAM_API_BASE_URL}/IPlayerService/GetOwnedGames/v0001/"
        params = {
            "key": self.api_key,
            "steamid": self.steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        }

        max_retries = 3
        base_delay = 15

        for attempt in range(max_retries):
            try:
                response = services.api_request("STEAM", "GET", url, params=params)

                if "response" not in response:
                    msg = "Invalid response from Steam API"
                    raise MediaImportError(msg)

                if "games" not in response["response"]:
                    # User might have private profile or no games
                    logger.warning(
                        "No games found in Steam response for user %s",
                        self.steam_id,
                    )
                    return []

                games = response["response"]["games"]
                logger.info(
                    "Found %d games for Steam user %s",
                    len(games),
                    self.steam_id,
                )
                return games  # noqa: TRY300

            except requests.HTTPError as e:
                if e.response.status_code == requests.codes.too_many_requests:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            "Steam API rate limited (429). "
                            "Retrying in %d seconds (attempt %d/%d)",
                            delay,
                            attempt + 1,
                            max_retries,
                        )
                        time.sleep(delay)
                        continue
                    msg = "Steam API rate limit exceeded. Please try again later."
                    raise MediaImportError(msg) from e
                if e.response.status_code == requests.codes.forbidden:
                    msg = "Steam profile is private or invalid"
                    raise MediaImportError(msg) from e
                if e.response.status_code == requests.codes.bad_request:
                    msg = "Bad request to Steam API. Please check the Steam ID."
                    raise MediaImportError(msg) from e
                if e.response.status_code == requests.codes.unauthorized:
                    msg = "Invalid Steam API key"
                    raise MediaImportError(msg) from e
                msg = f"Steam API error: {e.response.status_code}"
                raise MediaImportError(msg) from e

        msg = "Steam API request failed after all retries"
        raise MediaImportUnexpectedError(msg)

    def _process_game(self, game_data):
        """Process a single game from Steam API response."""
        appid = str(game_data["appid"])
        name = game_data.get("name", f"Unknown Game {appid}")
        playtime_forever = game_data.get("playtime_forever", 0)  # in minutes
        playtime_2weeks = game_data.get("playtime_2weeks", 0)  # in minutes

        try:
            # Try to match with IGDB
            igdb_game = self._match_with_igdb(name, appid)

            if not igdb_game:
                # Skip games that can't be matched to IGDB
                logger.debug(
                    "Skipping Steam game %s (appid: %s) - no IGDB match found",
                    name,
                    appid,
                )
                self.warnings.append(
                    f"{name} ({appid}): Couldn't find a match in {Sources.IGDB.label}",
                )
                return

            if not helpers.should_process_media(
                self.existing_media,
                self.to_delete,
                MediaTypes.GAME.value,
                Sources.IGDB.value,
                str(igdb_game["media_id"]),
                self.mode,
            ):
                return

            # Use IGDB data if found
            item, _ = app.models.Item.objects.get_or_create(
                media_id=str(igdb_game["media_id"]),
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
                defaults={
                    "title": igdb_game["title"],
                    "image": igdb_game["image"],
                },
            )

            # Determine status based on playtime
            status = self._determine_game_status(playtime_forever, playtime_2weeks)

            # Create game object
            game = app.models.Game(
                item=item,
                user=self.user,
                status=status,
                score=None,
                progress=playtime_forever,
                notes="Imported from Steam",
                start_date=None,
                end_date=None,
            )

            self.bulk_media[MediaTypes.GAME.value].append(game)

        except (ValueError, KeyError, TypeError) as e:
            logger.warning("Failed to process Steam game %s (%s): %s", name, appid, e)
            self.warnings.append(f"{name} ({appid}): {e!s}")

    def _determine_game_status(self, playtime_forever, playtime_2weeks):
        """Determine game status based on Steam playtime data.

        Args:
            playtime_forever (int): Total playtime in minutes
            playtime_2weeks (int): Playtime in last 2 weeks in minutes

        Returns:
            str: Status value from Status choices
        """
        # Games with no playtime are considered "Planning"
        if playtime_forever == 0:
            return Status.PLANNING.value

        # Games played in the last 2 weeks are "In Progress"
        if playtime_2weeks > 0:
            return Status.IN_PROGRESS.value

        # Games with total playtime but no recent activity are "On Hold"
        return Status.PAUSED.value

    def _match_with_igdb(self, game_name, steam_appid):
        """Try to match Steam game with IGDB using External Game endpoint."""
        try:
            # Try to find IGDB game by Steam App ID using external_game endpoint

            igdb_game_id = external_game(steam_appid, ExternalGameSource.STEAM)

            if igdb_game_id:
                # Get the game details using the IGDB ID
                game_details = services.get_media_metadata(
                    MediaTypes.GAME.value,
                    str(igdb_game_id),
                    Sources.IGDB.value,
                )

                if game_details:
                    logger.debug(
                        "Matched Steam game %s (appid: %s) with IGDB ID %s "
                        "via external_game",
                        game_name,
                        steam_appid,
                        igdb_game_id,
                    )
                    return {
                        "media_id": igdb_game_id,
                        "source": Sources.IGDB.value,
                        "media_type": MediaTypes.GAME.value,
                        "title": game_details.get("title", game_name),
                        "image": game_details["image"],
                    }

        except (ValueError, KeyError, TypeError) as e:
            logger.debug(
                "Failed to match Steam game %s (appid: %s) with IGDB "
                "via external_game: %s",
                game_name,
                steam_appid,
                e,
            )

        return None
