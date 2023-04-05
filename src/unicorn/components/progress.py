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

    # number is 1 or -1
    def update(self, number):

        self.progress += number
        media = Media.objects.get(
            user=self.user_id, media_id=self.media_id, api=self.api
        )
        if hasattr(self, 'season_number'):
            season = Season.objects.get(
                media_id=media.id,
                number=self.season_number,
            )
            season.progress = self.progress
            season.save()

        media.progress += number
        media.save()
