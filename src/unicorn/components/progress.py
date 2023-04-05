from django_unicorn.components import UnicornView
from app.models import Media, Season


class ProgressView(UnicornView):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)
        self.media_id = kwargs.get("media_id")
        self.api = kwargs.get("api")
        self.progress = kwargs.get("progress")
        self.user_id = kwargs.get("user_id")
        if "season_number" in kwargs:
            self.season_number = kwargs.get("season_number")

    def add(self):
        self.progress += 1
        media = Media.objects.get(
            user=self.user_id, media_id=self.media_id, api=self.api
        )
        if self.season_number:
            season = Season.objects.get(
                media_id=media.id,
                number=self.season_number,
            )
            season.progress = self.progress
            season.save()

        media.progress += 1
        media.save()

    def subtract(self):
        self.progress -= 1
        media = Media.objects.get(
            user=self.user_id, media_id=self.media_id, api=self.api
        )
        if self.season_number:
            season = Season.objects.get(
                media_id=media.id,
                number=self.season_number,
            )
            season.progress = self.progress
            season.save()

        media.progress -= 1
        media.save()
