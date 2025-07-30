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
        try:
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

        except MediaImportError:
            raise
        except requests.RequestException as e:
            logger.exception("Network error during Steam import")
            msg = "Network error occurred"
            raise MediaImportUnexpectedError(msg) from e
        except Exception as e:
            logger.exception("Unexpected error during Steam import")
            msg = "An unexpected error occurred"
            raise MediaImportUnexpectedError(msg) from e

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
                        "No games found in Steam response for user %s", self.steam_id,
                    )
                    return []

                games = response["response"]["games"]
                logger.info(
                    "Found %d games for Steam user %s", len(games), self.steam_id,
                )
                return games  # noqa: TRY300

            except requests.HTTPError as e:
                # Define HTTP status codes as constants for better readability
                http_too_many_requests = 429
                http_forbidden = 403
                http_internal_server_error = 500

                if e.response.status_code == http_too_many_requests:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "Steam API rate limited (429). "
                            "Retrying in %d seconds (attempt %d/%d)",
                            delay, attempt + 1, max_retries,
                        )
                        time.sleep(delay)
                        continue
                    msg = "Steam API rate limit exceeded. Please try again later."
                    raise MediaImportError(msg) from e
                if e.response.status_code == http_forbidden:
                    msg = "Steam profile is private or invalid"
                    raise MediaImportError(msg) from e
                if e.response.status_code == http_internal_server_error:
                    msg = "Steam API returned an internal error"
                    raise MediaImportError(msg) from e
                msg = f"Steam API error: {e.response.status_code}"
                raise MediaImportError(msg) from e
            except requests.RequestException as e:
                logger.exception("Request error when fetching Steam games")
                msg = "Failed to connect to Steam API"
                raise MediaImportError(msg) from e

        msg = "Steam API request failed after all retries"
        raise MediaImportError(msg)

    def _process_game(self, game_data):
        """Process a single game from Steam API response."""
        appid = str(game_data["appid"])
        name = game_data.get("name", f"Unknown Game {appid}")
        playtime_forever = game_data.get("playtime_forever", 0)  # in minutes
        playtime_2weeks = game_data.get("playtime_2weeks", 0)  # in minutes

        img_icon_url = game_data.get("img_icon_url", "")
        image_url = ""
        if img_icon_url:
            image_url = f"http://media.steampowered.com/steamcommunity/public/images/apps/{appid}/{img_icon_url}.jpg"

        try:
            # Try to match with IGDB
            igdb_game = self._match_with_igdb(name, appid)

            if igdb_game:
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
                        "title": igdb_game.get("title", name),
                        "image": igdb_game.get("image", image_url),
                    },
                )
            else:
                manual_media_id = f"steam_{appid}"
                if not helpers.should_process_media(
                    self.existing_media,
                    self.to_delete,
                    MediaTypes.GAME.value,
                    Sources.MANUAL.value,
                    manual_media_id,
                    self.mode,
                ):
                    return

                # Create manual entry if no IGDB match
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=manual_media_id,
                    source=Sources.MANUAL.value,
                    media_type=MediaTypes.GAME.value,
                    defaults={
                        "title": name,
                        "image": image_url,
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
                notes=(
                    f"Imported from Steam. Total playtime: "
                    f"{playtime_forever // 60}h {playtime_forever % 60}m"
                ),
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
                        "image": game_details.get("image", ""),
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
