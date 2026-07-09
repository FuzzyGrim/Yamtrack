import logging
from collections import defaultdict

import requests

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from legendary.api.egs import EPCAPI

logger = logging.getLogger(__name__)


def get_auth_url(_request=None):
    """Generate the Epic Games OAuth authorization URL.

    The user visits this URL and logs in. After authorizing, Epic
    redirects to an Epic-hosted page containing the auth code.
    The user copies that full URL and pastes it into Yamtrack.

    Returns:
        str: The Epic login URL.
    """
    login_url = "https://www.epicgames.com/id/login?redirectUrl="
    redirect_url = (
        "https://www.epicgames.com/id/api/redirect?"
        "clientId=34a02cf8f4414e29b15921876da36f9a&responseType=code"
    )
    return login_url + requests.utils.quote(redirect_url)


def handle_oauth_callback(authorization_code, redirect_uri=None):
    """Exchange an authorization code for an access + refresh token pair.

    Args:
        authorization_code (str): The OAuth authorization code from Epic.
        redirect_uri (str, optional): The redirect URI. Not strictly required
            for Epic since the public client uses a fixed redirect.

    Returns:
        dict: With 'refresh_token' (encrypted) and 'username'.
    """
    if not authorization_code:
        msg = "No authorization code received from Epic Games."
        raise MediaImportError(msg)

    try:
        api = EPCAPI()
        session_data = api.start_session(authorization_code=authorization_code)
    except Exception as e:
        msg = f"Failed to complete Epic Games authentication: {e!s}"
        raise MediaImportError(msg) from e

    refresh_token = session_data.get("refresh_token")
    if not refresh_token:
        msg = "No refresh token returned from Epic Games. Auth may have failed."
        raise MediaImportError(msg)

    username = session_data.get("displayName") or session_data.get(
        "account_id", "unknown"
    )

    return {
        "refresh_token": helpers.encrypt(refresh_token),
        "username": username,
    }


def get_access_token(encrypted_refresh_token):
    """Use an encrypted refresh token to obtain a fresh EPCAPI session.

    Also updates the stored refresh token since Epic tokens rotate.

    Args:
        encrypted_refresh_token (str): Encrypted Epic refresh token.

    Returns:
        EPCAPI: Authenticated API session.
    """
    decrypted_token = helpers.decrypt(encrypted_refresh_token)

    try:
        api = EPCAPI()
        api.start_session(refresh_token=decrypted_token)
    except Exception as e:
        msg = f"Failed to refresh Epic Games token: {e!s}"
        raise MediaImportError(msg) from e

    new_refresh_token = api.user.get("refresh_token") if api.user else None
    if new_refresh_token and new_refresh_token != decrypted_token:
        update_stored_refresh_token(
            encrypted_refresh_token, helpers.encrypt(new_refresh_token)
        )

    return api


def update_stored_refresh_token(old_encrypted, new_encrypted):
    """Update the refresh token stored in any periodic import tasks."""
    from django_celery_beat.models import PeriodicTask

    periodic_task = PeriodicTask.objects.filter(
        task="Import from Epic Games",
        kwargs__contains=f'"token": "{old_encrypted}"',
    ).first()

    if periodic_task:
        periodic_task.kwargs = periodic_task.kwargs.replace(
            old_encrypted,
            new_encrypted,
        )
        periodic_task.save()
        logger.info("Updated Epic refresh token in periodic task")


def importer(encrypted_token, user, mode):
    """Import Epic Games library for the given user.

    Args:
        encrypted_token (str): Encrypted Epic refresh token.
        user: Django user object.
        mode (str): 'new' or 'overwrite'.

    Returns:
        tuple: (imported_counts dict, warnings string)
    """
    epic_importer = EpicImporter(encrypted_token, user, mode)
    return epic_importer.import_data()


class EpicImporter:
    """Import games from an Epic Games Store library into Yamtrack."""

    def __init__(self, encrypted_token, user, mode):
        self.encrypted_token = encrypted_token
        self.user = user
        self.mode = mode
        self.warnings = []
        self.existing_media = helpers.get_existing_media(user)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)
        self.bulk_media_updates = defaultdict(list)

    def import_data(self):
        """Main import flow: auth, fetch library, match games, persist."""
        try:
            api = get_access_token(self.encrypted_token)
        except MediaImportError as e:
            logger.error("Epic auth failed: %s", e)
            return {}, str(e)

        library_items = self._get_library_items(api)

        if not library_items:
            logger.info("No Epic library items found for user %s", self.user)
            return {}, ""

        for item in library_items:
            self._process_library_item(api, item)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)
        helpers.bulk_update_media(
            self.bulk_media_updates,
            {MediaTypes.GAME.value: ["progress", "status"]},
            self.user,
        )

        created_games = len(self.bulk_media[MediaTypes.GAME.value])
        updated_games = len(self.bulk_media_updates[MediaTypes.GAME.value])
        imported_counts = {}
        if created_games or updated_games:
            imported_counts[MediaTypes.GAME.value] = created_games + updated_games

        logger.info(
            "Epic import completed for user %s: %d created, %d updated",
            self.user.username,
            created_games,
            updated_games,
        )
        return imported_counts, "\n".join(self.warnings) if self.warnings else ""

    def _get_library_items(self, api):
        """Fetch library items from Epic Games Store."""
        try:
            items = api.get_library_items(include_metadata=True)
            logger.info("Fetched %d library items from Epic", len(items))
            return items
        except Exception as e:
            msg = f"Failed to fetch Epic library: {e!s}"
            logger.error(msg)
            raise MediaImportError(msg) from e

    def _process_library_item(self, api, item):
        """Process a single library item from Epic.

        Each item from get_library_items() has:
            namespace: e.g. "61bc780f42f84fe29e6dfee957ab82de"
            catalogItemId: e.g. "6e7e8e5c9bcc4352bec6bb2fa5134ad2"
            appName: e.g. "Peony" (internal code name, not the real title)

        The real game title requires an additional catalog API call.
        """
        namespace = item.get("namespace", "")
        catalog_item_id = item.get("catalogItemId", "")
        app_name = item.get("appName", "")

        # Skip DLC and add-ons — only import base games
        if self._is_non_game_entitlement(item, api, namespace, catalog_item_id):
            return

        # Fetch real title from Epic catalog API
        try:
            game_info = api.get_game_info(namespace, catalog_item_id)
        except Exception as e:
            logger.debug(
                "Epic catalog lookup failed for %s (%s:%s): %s",
                app_name, namespace, catalog_item_id, e,
            )
            self.warnings.append(
                f"{app_name}: Couldn't look up game info in Epic catalog",
            )
            return

        if not game_info:
            logger.debug(
                "No catalog info for Epic item %s (%s:%s)",
                app_name, namespace, catalog_item_id,
            )
            return

        title = game_info.get("title") or app_name

        try:
            igdb_game = self._match_with_igdb(title)

            if not igdb_game:
                logger.debug(
                    "Skipping Epic game '%s' (%s:%s) - no IGDB match found",
                    title, namespace, catalog_item_id,
                )
                self.warnings.append(
                    f"{title}: Couldn't find a match in {Sources.IGDB.label}",
                )
                return

            media_id = str(igdb_game["media_id"])
            existing_game = self.existing_media[MediaTypes.GAME.value][
                Sources.IGDB.value
            ].get(media_id)

            if existing_game and self.mode == "overwrite":
                self._queue_existing_game_update(existing_game)
                return

            if not helpers.should_process_media(
                self.existing_media,
                self.to_delete,
                MediaTypes.GAME.value,
                Sources.IGDB.value,
                media_id,
                self.mode,
            ):
                return

            item_obj, _ = app.models.Item.objects.get_or_create(
                media_id=str(igdb_game["media_id"]),
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
                defaults={
                    "title": igdb_game["title"],
                    "image": igdb_game["image"],
                },
            )

            game = app.models.Game(
                item=item_obj,
                user=self.user,
                status=Status.PLANNING.value,
                score=None,
                progress=0,
                notes="Imported from Epic Games Store",
                start_date=None,
                end_date=None,
            )

            self.bulk_media[MediaTypes.GAME.value].append(game)

        except services.ProviderAPIError as e:
            msg = str(e).lower()
            is_not_found = "game with id" in msg and "not found" in msg
            if not is_not_found:
                raise

            logger.debug(
                "Skipping Epic game '%s' (%s:%s) - IGDB not found: %s",
                title, namespace, catalog_item_id, e,
            )
            self.warnings.append(
                f"{title}: Couldn't find a match in {Sources.IGDB.label}",
            )

        except (ValueError, KeyError, TypeError) as e:
            logger.warning(
                "Failed to process Epic game '%s' (%s:%s): %s",
                title, namespace, catalog_item_id, e,
            )
            self.warnings.append(f"{title}: {e!s}")

    @staticmethod
    def _is_non_game_entitlement(item, api, namespace, catalog_item_id):
        """Check if this entitlement is a base game or DLC/non-game content.

        Skips DLC, add-ons, soundtracks, engine downloads, etc.
        Uses Epic's catalog categories to determine what's a game vs DLC.
        """
        sandbox_type = item.get("sandboxType", "")
        record_type = item.get("recordType", "")

        # Epic library items use recordType="APPLICATION" for base games.
        # Only skip items explicitly marked as DLC via record type.
        if record_type == "DLC":
            return True

        # Try catalog info for better filtering
        try:
            info = api.get_game_info(namespace, catalog_item_id)
            if info:
                categories = info.get("categories", [])
                cat_names = [c.get("path", "") for c in categories] if isinstance(categories, list) else []
                cat_str = " ".join(cat_names).lower()

                # Skip DLC, add-ons, soundtracks, editors, engine, etc.
                skip_patterns = [
                    "addons", "digitalextras", "dlc", "soundtrack",
                    "engine", "editor", "software",
                ]
                for pattern in skip_patterns:
                    if pattern in cat_str:
                        return True

                # Only import items with "games" or "apps/games" category
                if not any("games" in c or "applications" in c for c in cat_names):
                    return True

        except Exception:
            pass  # If catalog lookup fails, err on the side of importing

        return False

    # Edition/pack/bundle suffixes to strip progressively when title search fails.
    # Ordered from longest/most-specific to shortest so we strip as little as possible.
    _EDITION_SUFFIXES = [
        " - Game of the Year Edition",
        " - Game of the Year Edition Pack",
        " Game of the Year Edition",
        " - Complete Edition",
        " Complete Edition",
        " - Definitive Edition",
        " Definitive Edition",
        " - Standard Edition",
        " Standard Edition",
        " - Gold Edition Pack",
        " Gold Edition Pack",
        " - Gold Edition",
        " Gold Edition",
        " - Deluxe Edition",
        " Deluxe Edition",
        " - Ultimate Edition",
        " Ultimate Edition",
        " - Collector's Edition",
        " Collector's Edition",
        " - Premium Edition",
        " Premium Edition",
        " Jotunn Edition",
        " - Jotunn Edition",
        " Trials of Fear Edition",
        " - Trials of Fear Edition",
        " Celebration Edition",
        " - Celebration Edition",
        " Starter Access",
        " Next Stop",
        " (Beta)",
        " (Test branch)",
    ]

    @staticmethod
    def _strip_special_chars(title):
        """Remove trademark/registered/copyright symbols from a title."""
        import re  # noqa: PLC0415

        return re.sub(r"[®™©]+", "", title).strip()

    def _search_igdb(self, clean_title):
        """Run a single IGDB title search, returning the best result or None."""
        from app.providers.igdb import search as igdb_search  # noqa: PLC0415

        if not clean_title:
            return None

        try:
            result_data = igdb_search(clean_title, 1)
        except Exception as e:
            logger.debug("IGDB search failed for '%s': %s", clean_title, e)
            return None

        if not result_data:
            return None

        results_list = result_data.get("results", [])
        if not results_list:
            return None

        best = results_list[0]
        logger.debug(
            "Matched '%s' with IGDB ID %s ('%s')",
            clean_title, best.get("media_id"), best.get("title"),
        )
        return best

    def _is_internal_identifier(self, title):
        """Check if a title is an internal Epic identifier (not a real game name).

        Identifiers have no spaces and contain underscores or mixed camelCase.
        """
        if not title:
            return True
        # Has spaces → real title
        if " " in title.strip():
            return False
        # Contains underscore or digit+uppercase boundary → looks like an ID
        import re  # noqa: PLC0415

        if "_" in title:
            return True
        if re.search(r"[a-z][A-Z]", title):
            return True
        return False

    def _match_with_igdb(self, title):
        """Search IGDB for a game by its title, with progressive fallbacks.

        Since Epic's internal IDs don't match IGDB's Epic UUIDs,
        we search by title instead. Handles special characters and
        edition suffixes.

        Args:
            title (str): The real game title from Epic's catalog API.

        Returns:
            dict or None: IGDB game data with media_id, title, image.
        """
        if not title:
            return None

        # Step 0: Skip internal identifiers that are clearly not game titles
        if self._is_internal_identifier(title):
            logger.debug("Skipping internal identifier '%s'", title)
            return None

        # Step 1: Strip special characters (®™©)
        clean = self._strip_special_chars(title)

        # Step 2: Try the basic cleaned title
        best = self._search_igdb(clean)
        if best:
            return {
                "media_id": best.get("media_id"),
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "title": best.get("title", title),
                "image": best.get("image"),
            }

        # Step 3: Progressively strip edition/pack suffixes and retry
        for suffix in self._EDITION_SUFFIXES:
            stripped = clean
            if stripped.endswith(suffix):
                candidate = stripped[: -len(suffix)].strip()
                # Clean up trailing punctuation left after stripping edition suffix
                # e.g. "Dandara:" → "Dandara", "Pillars of Eternity -" → "Pillars of Eternity"
                candidate = candidate.rstrip(":,;- ").strip()
                if not candidate:
                    continue
                best = self._search_igdb(candidate)
                if best:
                    return {
                        "media_id": best.get("media_id"),
                        "source": Sources.IGDB.value,
                        "media_type": MediaTypes.GAME.value,
                        "title": best.get("title", title),
                        "image": best.get("image"),
                    }

        logger.debug("No IGDB match found for title '%s'", title)
        return None

    def _queue_existing_game_update(self, game):
        """Queue an update for a game that already exists in Yamtrack."""
        changed = False

        if game.status in [
            Status.PLANNING.value,
            Status.IN_PROGRESS.value,
            Status.PAUSED.value,
        ]:
            game.status = Status.PLANNING.value
            changed = True

        if changed:
            self.bulk_media_updates[MediaTypes.GAME.value].append(game)
            logger.debug("Queued Epic update for existing game %s", game)
