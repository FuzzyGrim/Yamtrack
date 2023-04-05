from django_unicorn.components import UnicornView
from app.models import Season


class ProgressView(UnicornView):
    progress = 0

    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)
        self.media = kwargs.get("media")
        if hasattr(self.media, "season_number"):
            self.season_number = self.media.season_number
            self.progress = self.media.season_progress
        else:
            self.progress = self.media.progress

    # number is 1 or -1
    def update(self, number):
        self.progress += number
        if hasattr(self, 'season_number'):
            season = Season.objects.get(
                media_id=self.media.id,
                number=self.season_number,
            )
            season.progress = self.progress
            season.save()

        self.media.progress += number
        self.media.save()
