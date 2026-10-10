import logging

from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status

logger = logging.getLogger(__name__)


class AnimeWebhookMixin:
    """Anime-specific webhook processing."""

    def _handle_anime(self, media_id, episode_number, payload, user):
        """Handle anime playback event."""
        if not self._is_played(payload):
            episode_number = max(0, episode_number - 1)

        if self._is_unplayed(payload):
            current_instance = self._get_current_instance(
                app.models.Anime,
                media_id,
                Sources.MAL.value,
                MediaTypes.ANIME.value,
                user,
            )
            self._mark_anime_unplayed(current_instance, episode_number)
            return

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

        current_instance = self._get_current_instance(
            app.models.Anime,
            media_id,
            Sources.MAL.value,
            MediaTypes.ANIME.value,
            user,
        )

        now = timezone.now().replace(second=0, microsecond=0)
        is_completed = episode_number == anime_metadata["max_progress"]
        status = Status.COMPLETED.value if is_completed else Status.IN_PROGRESS.value

        if current_instance and current_instance.status != Status.COMPLETED.value:
            current_instance.progress = episode_number

            if is_completed:
                current_instance.end_date = now
                current_instance.status = status

            elif current_instance.status != Status.IN_PROGRESS.value:
                current_instance.start_date = now
                current_instance.status = status

            if current_instance.tracker.changed():
                current_instance.save()
                logger.info(
                    "Updated existing anime instance to status: %s with progress %d",
                    current_instance.status,
                    episode_number,
                )
            else:
                logger.debug(
                    "No changes detected for existing anime instance: %s",
                    current_instance.item,
                )
        else:
            app.models.Anime.objects.create(
                item=anime_item,
                user=user,
                progress=episode_number,
                status=status,
                start_date=now if not is_completed else None,
                end_date=now if is_completed else None,
            )
            logger.info(
                "Created new anime instance with status: %s and progress %d",
                status,
                episode_number,
            )

    def _mark_anime_unplayed(self, current_instance, episode_number):
        """Update an existing anime instance for an unplayed event."""
        if not current_instance:
            logger.debug("Anime marked as unplayed but no instance exists")
            return

        current_instance.progress = episode_number
        current_instance.status = Status.IN_PROGRESS.value
        current_instance.end_date = None

        if current_instance.tracker.changed():
            current_instance.save()
            logger.info(
                "Marked existing anime instance as unplayed with progress %d",
                episode_number,
            )
        else:
            logger.debug(
                "No changes detected for unplayed anime instance: %s",
                current_instance.item,
            )
