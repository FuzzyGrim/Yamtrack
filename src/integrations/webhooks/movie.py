import logging

from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status

from . import anime_mappings

logger = logging.getLogger(__name__)


class MovieWebhookMixin:
    """Movie-specific webhook processing."""

    def _process_movie(self, payload, user, ids):
        tmdb_id = ids["tmdb_id"]
        imdb_id = ids["imdb_id"]

        if user.anime_enabled:
            mapping_data = anime_mappings.fetch_mapping_data()
            mal_id = None
            source = None

            if tmdb_id:
                mal_id = anime_mappings.get_mal_id_from_tmdb_movie(
                    mapping_data,
                    tmdb_id,
                )
                source = "TMDB"

            if not mal_id and imdb_id:
                mal_id = anime_mappings.get_mal_id_from_imdb(mapping_data, imdb_id)
                source = "IMDB"

            if mal_id:
                logger.info(
                    "Detected anime movie with MAL ID: %s (via %s)",
                    mal_id,
                    source,
                )
                self._handle_anime(mal_id, 1, payload, user)
                return

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

    def _handle_movie(self, media_id, payload, user):
        """Handle movie playback event."""
        if self._is_unplayed(payload):
            current_instance = self._get_current_instance(
                app.models.Movie,
                media_id,
                Sources.TMDB.value,
                MediaTypes.MOVIE.value,
                user,
            )
            self._delete_media_instance(current_instance, "movie")
            return

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

        current_instance = self._get_current_instance(
            app.models.Movie,
            media_id,
            Sources.TMDB.value,
            MediaTypes.MOVIE.value,
            user,
        )
        movie_played = self._is_played(payload)

        progress = 1 if movie_played else 0
        now = timezone.now().replace(second=0, microsecond=0)

        if current_instance and current_instance.status != Status.COMPLETED.value:
            current_instance.progress = progress

            if movie_played:
                current_instance.end_date = now
                current_instance.status = Status.COMPLETED.value

            elif current_instance.status != Status.IN_PROGRESS.value:
                current_instance.start_date = now
                current_instance.status = Status.IN_PROGRESS.value

            if current_instance.tracker.changed():
                current_instance.save()
                logger.info(
                    "Updated existing movie instance to status: %s",
                    current_instance.status,
                )
            else:
                logger.debug(
                    "No changes detected for existing movie instance: %s",
                    current_instance.item,
                )
        else:
            app.models.Movie.objects.create(
                item=movie_item,
                user=user,
                progress=progress,
                status=Status.COMPLETED.value
                if movie_played
                else Status.IN_PROGRESS.value,
                start_date=now if not movie_played else None,
                end_date=now if movie_played else None,
            )
            logger.info(
                "Created new movie instance with status: %s",
                Status.COMPLETED.value if movie_played else Status.IN_PROGRESS.value,
            )
