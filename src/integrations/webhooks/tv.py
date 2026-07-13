import logging

from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status
from app.providers import tvdb as tvdb_provider

from . import anime_mappings

logger = logging.getLogger(__name__)


class TVWebhookMixin:
    """TV-specific webhook processing."""

    def _process_tv(self, payload, user, ids):
        anidb_id = ids.get("anidb_id")
        if user.anime_enabled and anidb_id:
            mapping_data = anime_mappings.fetch_mapping_data()
            episode_number = self._get_episode_number(payload)
            mal_id = None
            mal_episode_number = None

            if not episode_number:
                logger.warning(
                    "No episode number found for AniDB ID: %s",
                    anidb_id,
                )
            else:
                mal_id, mal_episode_number = anime_mappings.get_mal_id_from_anidb(
                    mapping_data,
                    anidb_id,
                    episode_number,
                )

            if episode_number and not mal_id:
                logger.info(
                    "AniDB ID %s not found in mapping, "
                    "falling through to TV processing",
                    anidb_id,
                )
            elif episode_number:
                logger.info(
                    "Detected anime via AniDB ID: %s. Matching MAL ID: %s, Episode: %d",
                    anidb_id,
                    mal_id,
                    mal_episode_number,
                )
                self._handle_anime(
                    mal_id,
                    mal_episode_number,
                    payload,
                    user,
                )
                return

        tvdb_episode_id = ids.get("tvdb_id")
        if tvdb_episode_id:
            tvdb_episode = tvdb_provider.episode(int(tvdb_episode_id))
            if not tvdb_episode:
                logger.warning(
                    "No TVDB episode metadata found for TVDB episode ID: %s",
                    tvdb_episode_id,
                )
            elif self._process_tvdb_episode(
                tvdb_episode, tvdb_episode_id, payload, user
            ):
                return
        else:
            logger.debug("No TVDB episode ID found for TV episode")

        imdb_id = ids.get("imdb_id")
        if imdb_id and self._process_imdb_episode(imdb_id, payload, user):
            return

        if imdb_id:
            logger.warning("No matching TMDB ID found for IMDB ID: %s", imdb_id)
        else:
            logger.warning("No TVDB or IMDB ID found for TV episode")

    def _process_tvdb_episode(self, tvdb_episode, tvdb_episode_id, payload, user):
        """Process a TV episode using TVDB metadata."""
        if user.anime_enabled:
            mapping_data = anime_mappings.fetch_mapping_data()
            mal_id, episode_offset = anime_mappings.get_mal_id_from_tvdb(
                mapping_data,
                tvdb_episode["series_id"],
                tvdb_episode["season_number"],
                tvdb_episode["episode_number"],
            )
            if mal_id:
                logger.info(
                    "Detected anime episode via MAL ID: %s, Episode: %d",
                    mal_id,
                    episode_offset,
                )
                self._handle_anime(mal_id, episode_offset, payload, user)
                return True

        media_id, season_number, episode_number = self._find_tv_media_id(
            tvdb_episode_id,
            "tvdb_id",
        )
        if not media_id:
            media_id = tvdb_provider.series_tmdb_id(tvdb_episode["series_id"])
            season_number = tvdb_episode["season_number"]
            episode_number = tvdb_episode["episode_number"]

        if not media_id:
            logger.warning(
                "No matching TMDB ID found for TVDB episode ID: %s", tvdb_episode_id
            )
            return False

        logger.info(
            "Detected TV episode via TMDB ID: %s, Season: %d, Episode: %d",
            media_id,
            season_number,
            episode_number,
        )
        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)
        return True

    def _process_imdb_episode(self, imdb_id, payload, user):
        """Process a TV episode using IMDB as a TVDB fallback."""
        media_id, season_number, episode_number = self._find_tv_media_id(
            imdb_id,
            "imdb_id",
        )
        if not media_id:
            return False

        logger.info(
            "Detected TV episode via IMDB ID: %s, TMDB ID: %s, Season: %d, Episode: %d",
            imdb_id,
            media_id,
            season_number,
            episode_number,
        )
        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)
        return True

    def _find_tv_media_id(self, external_id, external_source):
        """Find TMDB TV episode metadata from an external ID."""
        if external_id:
            response = app.providers.tmdb.find(external_id, external_source)
            if response.get("tv_episode_results"):
                result = response["tv_episode_results"][0]
                return (
                    result.get("show_id"),
                    result.get("season_number"),
                    result.get("episode_number"),
                )
        return None, None, None

    def _handle_tv_episode(
        self,
        media_id,
        season_number,
        episode_number,
        payload,
        user,
    ):
        """Handle TV episode playback event."""
        if self._is_unplayed(payload):
            self._delete_tv_episode(media_id, season_number, episode_number, user)
            return

        tv_metadata = app.providers.tmdb.tv_with_seasons(media_id, [season_number])
        season_metadata = tv_metadata[f"season/{season_number}"]

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

        episode_item = season_instance.get_episode_item(episode_number, season_metadata)

        if self._is_played(payload):
            now = timezone.now().replace(second=0, microsecond=0)
            latest_episode = (
                app.models.Episode.objects.filter(
                    item=episode_item,
                    related_season=season_instance,
                )
                .order_by("-end_date")
                .first()
            )

            should_create = True
            # check for duplicate episode records,
            # sometimes webhooks are triggered multiple times #689
            if latest_episode and latest_episode.end_date:
                time_diff = abs((now - latest_episode.end_date).total_seconds())
                threshold = 5
                if time_diff < threshold:
                    should_create = False
                    logger.debug(
                        "Skipping duplicate episode record "
                        "(time difference: %d seconds): %s S%02dE%02d",
                        time_diff,
                        tv_metadata["title"],
                        season_number,
                        episode_number,
                    )

            if should_create:
                app.models.Episode.objects.create(
                    item=episode_item,
                    related_season=season_instance,
                    end_date=now,
                )
                logger.info(
                    "Marked episode as played: %s S%02dE%02d",
                    tv_metadata["title"],
                    season_number,
                    episode_number,
                )
        else:
            logger.debug(
                "Episode not marked as played: %s S%02dE%02d",
                tv_metadata["title"],
                season_number,
                episode_number,
            )

    def _delete_tv_episode(self, media_id, season_number, episode_number, user):
        """Delete the latest tracked episode instance for an unplayed event."""
        episode = (
            app.models.Episode.objects.filter(
                related_season__user=user,
                item__media_id=media_id,
                item__source=Sources.TMDB.value,
                item__media_type=MediaTypes.EPISODE.value,
                item__season_number=season_number,
                item__episode_number=episode_number,
            )
            .order_by("-end_date", "-created_at")
            .first()
        )

        if not episode:
            logger.debug(
                "Episode marked as unplayed but no instance exists: %s S%02dE%02d",
                media_id,
                season_number,
                episode_number,
            )
            return

        episode.delete()
        logger.info(
            "Marked episode as unplayed: %s S%02dE%02d",
            media_id,
            season_number,
            episode_number,
        )
