from django_unicorn.components import UnicornView
from app.models import Media
import logging

logger = logging.getLogger(__name__)


class MediaFormView(UnicornView):
    status = "Completed"
    score = None
    progress = 0
    start_date = None
    end_date = None
    in_db = False

    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)
        self.media = kwargs.get("media")

    def open_form(self):
        media_id = self.media["id"]
        media_type = self.media["media_type"]

        try:
            media = Media.objects.get(
                    media_id=media_id,
                    media_type=media_type,
                    user=self.request.user.id,
            )

            logger.info(f"Media {media_id} found in database, loading data...")

            self.status = media.status
            self.score = media.score
            self.progress = media.progress
            self.start_date = media.start_date
            self.end_date = media.end_date
            self.in_db = True

        except Media.DoesNotExist:
            logger.info(f"Media {media_id} not found in database, creating new entry...")
