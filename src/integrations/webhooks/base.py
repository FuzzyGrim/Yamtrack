import logging

from django.core.cache import cache
from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status

logger = logging.getLogger(__name__)


class BaseWebhookProcessor:
    """Base class for webhook processors."""

    MEDIA_TYPE_MAPPING = {
        "Episode": MediaTypes.TV.value,
        "Movie": MediaTypes.MOVIE.value,
    }

    def process_payload(self, payload, user):
        """Process webhook payload."""
        raise NotImplementedError

    def _is_supported_event(self, event_type):
        """Check if event type is supported."""
        raise NotImplementedError

    def _is_played(self, payload):
        """Check if media is marked as played."""
        raise NotImplementedError

    def _extract_external_ids(self, payload):
        """Extract external IDs from payload."""
        raise NotImplementedError

    def _get_media_type(self, payload):
        """Get media type from payload."""
        raise NotImplementedError

    def _get_media_title(self, payload):
        """Get media title from payload."""
        raise NotImplementedError

    def _process_media(self, payload, user, ids):
        """Route processing based on media type."""
        media_type = self._get_media_type(payload)
        if not media_type:
            logger.debug("Ignoring unsupported media type")
            return

        title = self._get_media_title(payload)
        logger.info("Received webhook for %s: %s", media_type, title)

        if media_type == MediaTypes.TV.value:
            self._process_tv(payload, user, ids)
        elif media_type == MediaTypes.MOVIE.value:
            self._process_movie(payload, user, ids)

    def _process_tv(self, payload, user, ids):
        media_id, season_number, episode_number = self._find_tv_media_id(ids)
        if not media_id:
            logger.warning("No matching TMDB ID found for TV show")
            return

        tvdb_id = app.providers.tmdb.tv_with_seasons(media_id, [season_number])[
            "tvdb_id"
        ]

        if not tvdb_id:
            logger.warning("No TVDB ID found for TMDB ID: %s", media_id)
            return

        if user.anime_enabled:
            mapping_data = self._fetch_mapping_data()
            mal_id, episode_offset = self._get_mal_id_from_tvdb(
                mapping_data,
                int(tvdb_id),
                season_number,
                episode_number,
            )
            if mal_id:
                logger.info(
                    "Detected anime episode via MAL ID: %s, Episode: %d",
                    mal_id,
                    episode_offset,
                )
                self._handle_anime(mal_id, episode_offset, payload, user)
                return

        logger.info(
            "Detected TV episode via TMDB ID: %s, Season: %d, Episode: %d",
            media_id,
            season_number,
            episode_number,
        )
        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)

    def _process_movie(self, payload, user, ids):
        tmdb_id = ids["tmdb_id"]
        imdb_id = ids["imdb_id"]

        # Try to detect anime first if user has anime enabled
        if user.anime_enabled:
            mapping_data = self._fetch_mapping_data()
            mal_id = None
            source = None

            if tmdb_id:
                mal_id = self._get_mal_id_from_tmdb_movie(mapping_data, tmdb_id)
                source = "TMDB"

            if not mal_id and imdb_id:
                mal_id = self._get_mal_id_from_imdb(mapping_data, imdb_id)
                source = "IMDB"

            if mal_id:
                logger.info(
                    "Detected anime movie with MAL ID: %s (via %s)",
                    mal_id,
                    source,
                )
                self._handle_anime(mal_id, 1, payload, user)
                return

        # Handle as regular movie
        if tmdb_id:
            logger.info("Detected movie via TMDB ID: %s", tmdb_id)
            self._handle_movie(tmdb_id, payload, user)
        elif imdb_id:
            logger.debug("No TMDB ID found, looking up via IMDB ID: %s", imdb_id)
            response = app.providers.tmdb.find(imdb_id, "imdb_id")

            if response.get("movie_results"):
                media_id = response["movie_results"][0]["id"]
                logger.info("Found matching TMDB ID: %s", media_id)
                self._handle_movie(media_id, payload, user)
            else:
                logger.warning(
                    "No matching TMDB ID found for IMDB ID: %s",
                    imdb_id,
                )
        else:
            logger.warning("No TMDB or IMDB ID found for movie, skipping processing")

    def _find_tv_media_id(self, ids):
        """Find TV media ID from external IDs."""
        for ext_id, ext_type in [
            (ids["imdb_id"], "imdb_id"),
            (ids["tvdb_id"], "tvdb_id"),
        ]:
            if ext_id:
                response = app.providers.tmdb.find(ext_id, ext_type)
                if response.get("tv_episode_results"):
                    result = response["tv_episode_results"][0]
                    return (
                        result.get("show_id"),
                        result.get("season_number"),
                        result.get("episode_number"),
                    )
        return None, None, None

    def _fetch_mapping_data(self):
        """Fetch anime mapping data with caching."""
        data = cache.get("anime_mapping_data")
        if data is None:
            url = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/refs/heads/master/anime_ids.json"
            data = app.providers.services.api_request("GITHUB", "GET", url)
            cache.set("anime_mapping_data", data)
        return data

    def _get_mal_id_from_tvdb(
        self,
        mapping_data,
        tvdb_id,
        season_number,
        episode_number,
    ):
        matching_entries = [
            entry
            for entry in mapping_data.values()
            if entry.get("tvdb_id") == tvdb_id
            and entry.get("tvdb_season") == season_number
            and "mal_id" in entry
        ]

        if not matching_entries:
            return None, None

        matching_entries.sort(key=lambda x: x.get("tvdb_epoffset", 0))
        for i, entry in enumerate(matching_entries):
            current_offset = entry.get("tvdb_epoffset", 0)
            next_offset = (
                matching_entries[i + 1].get("tvdb_epoffset", float("inf"))
                if i < len(matching_entries) - 1
                else float("inf")
            )

            if current_offset < episode_number <= next_offset:
                mal_id = self._parse_mal_id(entry["mal_id"])
                return mal_id, episode_number - current_offset

        return None, None

    def _get_mal_id_from_tmdb_movie(self, mapping_data, tmdb_movie_id):
        """Find MAL ID from TMDB movie mapping."""
        for entry in mapping_data.values():
            if entry.get("tmdb_movie_id") == tmdb_movie_id and "mal_id" in entry:
                return self._parse_mal_id(entry["mal_id"])
        return None

    def _get_mal_id_from_imdb(self, mapping_data, imdb_id):
        """Find MAL ID from IMDB ID mapping."""
        for entry in mapping_data.values():
            if entry.get("imdb_id") == imdb_id and "mal_id" in entry:
                return self._parse_mal_id(entry["mal_id"])
        return None

    def _parse_mal_id(self, mal_id):
        """Parse MAL ID from potentially comma-separated string.

        mal_id: Either a single ID (int) or comma-separated string of IDs
        """
        if isinstance(mal_id, str) and "," in mal_id:
            return mal_id.split(",")[0].strip()
        return mal_id

    def _extract_position_and_runtime(self, payload):
        """Extract playback position and runtime from payload.

        Returns tuple of (position_ticks, runtime_ticks) or (None, None) if not found.
        Must be implemented by subclasses to handle different payload structures.
        """
        raise NotImplementedError

    def _calculate_progress_percent(self, position_ticks, runtime_ticks):
        """Calculate progress percentage from position and runtime ticks.

        Args:
            position_ticks: Current playback position in ticks
            runtime_ticks: Total runtime in ticks

        Returns:
            Integer percentage (0-100) or None if calculation not possible
        """
        if not position_ticks or not runtime_ticks or runtime_ticks == 0:
            return None

        percent = min(100, round((position_ticks / runtime_ticks) * 100))
        return int(percent)

    def _handle_movie(self, media_id, payload, user):
        """Handle movie playback event."""
        movie_metadata = app.providers.tmdb.movie(media_id)
        movie_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": movie_metadata["title"],
                "image": movie_metadata["image"],
            },
        )

        movie_instances = app.models.Movie.objects.filter(item=movie_item, user=user)
        current_instance = movie_instances.first()
        movie_played = self._is_played(payload)

        # Extract progress percentage from payload
        position_ticks, runtime_ticks = self._extract_position_and_runtime(payload)
        progress_percent = self._calculate_progress_percent(position_ticks, runtime_ticks)

        # If explicitly played/scrobbled, mark as complete (100%)
        if movie_played:
            progress_percent = 100

        now = timezone.now().replace(second=0, microsecond=0)

        if current_instance and current_instance.status != Status.COMPLETED.value:
            # Update progress if we have a percentage value
            if progress_percent is not None:
                current_instance.progress = progress_percent

            # Mark as completed if >= 95% or explicitly played
            if movie_played or (progress_percent is not None and progress_percent >= 95):
                current_instance.end_date = now
                current_instance.status = Status.COMPLETED.value
                if progress_percent is not None:
                    current_instance.progress = 100

            elif current_instance.status != Status.IN_PROGRESS.value:
                current_instance.start_date = now
                current_instance.status = Status.IN_PROGRESS.value

            if current_instance.tracker.changed():
                current_instance.save()
                logger.info(
                    "Updated existing movie instance to status: %s, progress: %s%%",
                    current_instance.status,
                    current_instance.progress,
                )
            else:
                logger.debug(
                    "No changes detected for existing movie instance: %s",
                    current_instance.item,
                )
        else:
            # Create new instance
            is_completed = movie_played or (progress_percent is not None and progress_percent >= 95)
            initial_progress = 100 if is_completed else (progress_percent if progress_percent is not None else 0)

            app.models.Movie.objects.create(
                item=movie_item,
                user=user,
                progress=initial_progress,
                status=Status.COMPLETED.value if is_completed else Status.IN_PROGRESS.value,
                start_date=now if not is_completed else None,
                end_date=now if is_completed else None,
            )
            logger.info(
                "Created new movie instance with status: %s, progress: %s%%",
                Status.COMPLETED.value if is_completed else Status.IN_PROGRESS.value,
                initial_progress,
            )

    def _ensure_tv_instance(self, media_id, tv_metadata, user):
        """Ensure TV item and instance exist, creating/updating as needed.
        
        Returns:
            tv_instance: The TV instance for the user
        """
        tv_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            defaults={
                "title": tv_metadata["title"],
                "image": tv_metadata["image"],
            },
        )

        tv_instance, tv_created = app.models.TV.objects.get_or_create(
            item=tv_item,
            user=user,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        if tv_created:
            logger.info("Created new TV instance: %s", tv_metadata["title"])
        elif tv_instance.status != Status.IN_PROGRESS.value:
            tv_instance.status = Status.IN_PROGRESS.value
            tv_instance.save()
            logger.info(
                "Updated TV instance status to %s: %s",
                Status.IN_PROGRESS.value,
                tv_metadata["title"],
            )
        
        return tv_instance

    def _ensure_season_instance(self, media_id, season_number, tv_metadata, season_metadata, tv_instance, user):
        """Ensure season item and instance exist, creating/updating as needed.
        
        Returns:
            season_instance: The Season instance for the user
        """
        season_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_metadata["title"],
                "image": season_metadata["image"],
            },
        )

        season_instance, season_created = app.models.Season.objects.get_or_create(
            item=season_item,
            user=user,
            related_tv=tv_instance,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        if season_created:
            logger.info(
                "Created new season instance: %s S%02d",
                tv_metadata["title"],
                season_number,
            )
        elif season_instance.status != Status.IN_PROGRESS.value:
            season_instance.status = Status.IN_PROGRESS.value
            season_instance.save()
            logger.info(
                "Updated season instance status to %s: %s S%02d",
                Status.IN_PROGRESS.value,
                tv_metadata["title"],
                season_number,
            )
        
        return season_instance

    def _should_update_episode(self, latest_episode, episode_played, now):
        """Determine if episode should be updated based on duplicate detection logic.
        
        Returns:
            bool: True if episode should be updated, False otherwise
        """
        if not latest_episode or not latest_episode.end_date:
            return True

        # Episode already marked as complete
        if not episode_played:
            # Episode complete, don't update progress
            return False

        # Only update if we got a new play event (to handle duplicates)
        time_diff = abs((now - latest_episode.end_date).total_seconds())
        threshold = 5
        if time_diff < threshold:
            logger.debug(
                "Skipping duplicate episode record (time difference: %d seconds)",
                time_diff,
            )
            return False

        return True

    def _update_or_create_episode(
        self,
        episode_item,
        season_instance,
        latest_episode,
        progress_percent,
        episode_played,
        is_completed,
        now,
        tv_title,
        season_number,
        episode_number,
    ):
        """Update existing episode or create new one based on current state."""
        if latest_episode and not latest_episode.end_date:
            # Update existing incomplete episode record
            if progress_percent is not None:
                latest_episode.progress = progress_percent

            if is_completed:
                latest_episode.end_date = now
                latest_episode.progress = 100

            latest_episode.save()
            logger.info(
                "Updated episode progress: %s S%02dE%02d, progress: %s%%, completed: %s",
                tv_title,
                season_number,
                episode_number,
                latest_episode.progress,
                is_completed,
            )
        else:
            # Create new episode record
            app.models.Episode.objects.create(
                item=episode_item,
                related_season=season_instance,
                progress=progress_percent if progress_percent is not None else 0,
                end_date=now if is_completed else None,
            )
            logger.info(
                "Created episode record: %s S%02dE%02d, progress: %s%%, completed: %s",
                tv_title,
                season_number,
                episode_number,
                progress_percent if progress_percent is not None else 0,
                is_completed,
            )

    def _handle_tv_episode(
        self,
        media_id,
        season_number,
        episode_number,
        payload,
        user,
    ):
        """Handle TV episode playback event."""
        tv_metadata = app.providers.tmdb.tv_with_seasons(media_id, [season_number])
        season_metadata = tv_metadata[f"season/{season_number}"]

        tv_instance = self._ensure_tv_instance(media_id, tv_metadata, user)
        season_instance = self._ensure_season_instance(
            media_id, season_number, tv_metadata, season_metadata, tv_instance, user
        )
        episode_item = season_instance.get_episode_item(episode_number, season_metadata)

        # Extract progress percentage from payload
        position_ticks, runtime_ticks = self._extract_position_and_runtime(payload)
        progress_percent = self._calculate_progress_percent(position_ticks, runtime_ticks)

        now = timezone.now().replace(second=0, microsecond=0)
        episode_played = self._is_played(payload)

        # If explicitly played/scrobbled, mark as complete (100%)
        if episode_played:
            progress_percent = 100

        # Get latest episode record for duplicate detection
        latest_episode = (
            app.models.Episode.objects.filter(
                item=episode_item,
                related_season=season_instance,
            )
            .order_by("-created_at")
            .first()
        )

        should_update = self._should_update_episode(latest_episode, episode_played, now)

        if should_update:
            is_completed = episode_played or (progress_percent is not None and progress_percent >= 95)
            self._update_or_create_episode(
                episode_item,
                season_instance,
                latest_episode,
                progress_percent,
                episode_played,
                is_completed,
                now,
                tv_metadata["title"],
                season_number,
                episode_number,
            )
        else:
            logger.debug(
                "Episode not updated: %s S%02dE%02d",
                tv_metadata["title"],
                season_number,
                episode_number,
            )

    def _handle_anime(self, media_id, episode_number, payload, user):
        """Handle anime playback event."""
        anime_metadata = app.providers.mal.anime(media_id)
        anime_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            defaults={
                "title": anime_metadata["title"],
                "image": anime_metadata["image"],
            },
        )

        anime_instances = app.models.Anime.objects.filter(item=anime_item, user=user)
        current_instance = anime_instances.first()

        # Extract progress percentage from payload
        position_ticks, runtime_ticks = self._extract_position_and_runtime(payload)
        progress_percent = self._calculate_progress_percent(position_ticks, runtime_ticks)

        episode_played = self._is_played(payload)

        # If explicitly played/scrobbled, mark current episode as complete (100%)
        if episode_played:
            progress_percent = 100

        if not episode_played:
            episode_number = max(0, episode_number - 1)

        now = timezone.now().replace(second=0, microsecond=0)
        is_completed = episode_number == anime_metadata["max_progress"]
        status = Status.COMPLETED.value if is_completed else Status.IN_PROGRESS.value

        if current_instance and current_instance.status != Status.COMPLETED.value:
            current_instance.progress = episode_number

            # Update current episode progress percentage
            if progress_percent is not None:
                current_instance.current_episode_progress = progress_percent
            elif episode_played:
                current_instance.current_episode_progress = 100

            if is_completed:
                current_instance.end_date = now
                current_instance.status = status
                current_instance.current_episode_progress = 100

            elif current_instance.status != Status.IN_PROGRESS.value:
                current_instance.start_date = now
                current_instance.status = status

            if current_instance.tracker.changed():
                current_instance.save()
                logger.info(
                    "Updated existing anime instance to status: %s with progress %d (episode), current episode: %s%%",
                    current_instance.status,
                    episode_number,
                    current_instance.current_episode_progress,
                )
            else:
                logger.debug(
                    "No changes detected for existing anime instance: %s",
                    current_instance.item,
                )
        else:
            initial_episode_progress = progress_percent if progress_percent is not None else (100 if episode_played else None)

            app.models.Anime.objects.create(
                item=anime_item,
                user=user,
                progress=episode_number,
                current_episode_progress=initial_episode_progress,
                status=status,
                start_date=now if not is_completed else None,
                end_date=now if is_completed else None,
            )
            logger.info(
                "Created new anime instance with status: %s, progress %d (episode), current episode: %s%%",
                status,
                episode_number,
                initial_episode_progress,
            )
