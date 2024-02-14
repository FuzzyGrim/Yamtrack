import datetime
import logging

from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import Max
from django.http import HttpRequest
from model_utils import FieldTracker

from app.utils import metadata

logger = logging.getLogger(__name__)


class Media(models.Model):
    """Abstract model for all media types."""

    media_id = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    image = models.URLField()
    score = models.DecimalField(
        null=True,
        blank=True,
        max_digits=3,
        decimal_places=1,
        validators=[
            DecimalValidator(3, 1),
            MinValueValidator(0),
            MaxValueValidator(10),
        ],
    )
    progress = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=12,
        default="Completed",
        choices=[
            ("Completed", "Completed"),
            ("In progress", "In progress"),
            ("Planning", "Planning"),
            ("Paused", "Paused"),
            ("Dropped", "Dropped"),
        ],
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    notes = models.TextField(blank=True)

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["-score"]
        unique_together = ["media_id", "user"]

    def __str__(self: "Media") -> str:
        """Return the title of the media."""

        return f"{self.title}"

    def save(self: "Media", *args: list, **kwargs: dict) -> None:
        """Save the media instance."""

        media_type = self.__class__.__name__.lower()

        if "status" in self.tracker.changed():
            if self.status == "Completed":
                if not self.end_date:
                    self.end_date = datetime.datetime.now(tz=settings.TZ).date()

                self.progress = metadata.get_media_metadata(media_type, self.media_id)[
                    "num_episodes"
                ]

            elif self.status == "In progress" and not self.start_date:
                self.start_date = datetime.datetime.now(tz=settings.TZ).date()

        if "progress" in self.tracker.changed():
            max_episodes = metadata.get_media_metadata(media_type, self.media_id)[
                "num_episodes"
            ]

            if self.progress > max_episodes:
                self.progress = max_episodes

            if self.progress == max_episodes:
                self.status = "Completed"

                if not self.end_date:
                    self.end_date = datetime.datetime.now(tz=settings.TZ).date()

        super().save(*args, **kwargs)

    def increase_progress(self: "Media") -> None:
        """Increase the progress of the media by one."""
        self.progress += 1
        self.save()
        logger.info("Watched %s E%s", self, self.progress)

    def decrease_progress(self: "Media") -> None:
        """Decrease the progress of the media by one."""
        self.progress -= 1
        self.save()
        logger.info("Unwatched %s E%s", self, self.progress)


class TV(Media):
    """Model for TV shows."""

    tracker = FieldTracker()

    @property
    def progress(self: "Season") -> int:
        """Return the user's episodes watched for the TV show."""
        return sum(season.progress for season in self.seasons.all())

    @property
    def start_date(self: "TV") -> datetime.date:
        """Return the date of the first episode watched."""
        return min(season.start_date for season in self.seasons.all())

    @property
    def end_date(self: "TV") -> datetime.date:
        """Return the date of the last episode watched."""
        return max(season.end_date for season in self.seasons.all())

    @tracker  # postpone field reset until after the save
    def save(self: "Media", *args: list, **kwargs: dict) -> None:
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if (
            "status" in self.tracker.changed()
            and self.status == "Completed"
            and self.progress < metadata.tv(self.media_id)["num_episodes"]
        ):
            tv_complete(self, metadata.tv(self.media_id))


class Season(Media):
    """Model for seasons of TV shows."""

    related_tv = models.ForeignKey(
        TV,
        on_delete=models.CASCADE,
        related_name="seasons",
    )
    season_number = models.PositiveIntegerField()

    tracker = FieldTracker()

    @property
    def progress(self: "Season") -> int:
        """Return the user's episodes watched for the season."""
        return self.episodes.count()

    @property
    def start_date(self: "Season") -> datetime.date:
        """Return the date of the first episode watched."""
        try:
            start = min(episode.watch_date for episode in self.episodes.all())
        except ValueError:  # no episodes watched
            start = datetime.date(datetime.MINYEAR, 1, 1)
        return start

    @property
    def end_date(self: "Season") -> datetime.date:
        """Return the date of the last episode watched."""
        try:
            end = max(episode.watch_date for episode in self.episodes.all())
        except ValueError:
            end = datetime.date(datetime.MINYEAR, 1, 1)
        return end

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        unique_together = ["related_tv", "season_number"]

    def __str__(self: "Season") -> str:
        """Return the title of the media and season number."""
        return f"{self.title} S{self.season_number}"

    @tracker  # postpone field reset until after the save
    def save(self: "Media", *args: list, **kwargs: dict) -> None:
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if "status" in self.tracker.changed() and self.status == "Completed":
            season_metadata = metadata.season(self.media_id, self.season_number)
            Episode.objects.bulk_create(
                get_remaining_eps(self, season_metadata),
            )

    def increase_progress(self: "Season") -> None:
        """Increase the progress of the season by one."""

        last_watched = (
            Episode.objects.filter(
                related_season=self,
            )
            .order_by("-episode_number")
            .first()
            .episode_number
        )

        # get next episode number as there could be gaps in the episode numbers
        season_metadata = metadata.season(self.media_id, self.season_number)
        found_current = False
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == last_watched:
                found_current = True
            elif found_current:
                episode_number = episode["episode_number"]
                break

        Episode.objects.create(
            related_season=self,
            episode_number=episode_number,
            watch_date=datetime.datetime.now(tz=settings.TZ).date(),
        )
        logger.info("Watched %sE%s", self, episode_number)

    def decrease_progress(self: "Season") -> None:
        """Decrease the progress of the season by one."""
        last_watched = (
            Episode.objects.filter(
                related_season=self,
            )
            .order_by("-episode_number")
            .first()
            .episode_number
        )

        Episode.objects.filter(
            related_season=self,
            episode_number=last_watched,
        ).delete()

        logger.info("Unwatched %sE%s", self, last_watched)


class Episode(models.Model):
    """Model for episodes of a season."""

    related_season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    episode_number = models.PositiveIntegerField()
    watch_date = models.DateField(null=True, blank=True)

    class Meta:
        """Limit the uniqueness of episodes.

        Only one episode per season can have the same episode number.
        """

        unique_together = ["related_season", "episode_number"]

    def __str__(self: "Episode") -> str:
        """Return the season and episode number."""
        return f"{self.related_season}E{self.episode_number}"

    def save(self: "Episode", *args: list, **kwargs: dict) -> None:
        """Save the episode instance."""
        super().save(*args, **kwargs)

        season_metadata = metadata.season(
            self.related_season.media_id,
            self.related_season.season_number,
        )
        if self.related_season.progress == len(season_metadata["episodes"]):
            self.related_season.status = "Completed"
            # save_base to avoid custom save method
            self.related_season.save_base(update_fields=["status"])


class Manga(Media):
    """Model for manga."""

    tracker = FieldTracker()


class Anime(Media):
    """Model for anime."""

    tracker = FieldTracker()


class Movie(Media):
    """Model for movies."""

    tracker = FieldTracker()


################################
# METHODS FOR MODEL MANAGEMENT #
################################


def get_or_create_tv(request: HttpRequest, media_id: int) -> TV:
    """Get related TV instance for a season or create it if it doesn't exist."""
    try:
        tv = TV.objects.get(media_id=media_id, user=request.user)
    except TV.DoesNotExist:
        tv_metadata = metadata.tv(media_id)

        # default to in progress for when handling episode form
        status = request.POST.get("status", "In progress")
        # creating tv with multiple seasons from a completed season
        if status == "Completed" and tv_metadata["season_number"] > 1:
            status = "In progress"

        tv = TV(
            media_id=media_id,
            title=tv_metadata["title"],
            image=tv_metadata["image"],
            score=None,
            status=status,
            notes="",
            user=request.user,
        )

        # save_base to avoid custom save method
        TV.save_base(tv)
        logger.info("%s did not exist, it was created successfully.", tv)

    return tv


def tv_complete(
    tv: TV,
    tv_metadata: dict,
) -> None:
    """Create remaining seasons and episodes for a TV show."""

    seasons_to_update = []
    seasons_to_create = []
    episodes_to_create = []
    for season_number in range(1, tv_metadata["number_of_seasons"] + 1):
        season_metadata = metadata.season(tv.media_id, season_number)
        try:
            season_instance = Season.objects.get(
                media_id=tv.media_id,
                user=tv.user,
                season_number=season_number,
            )

            if season_instance.status != "Completed":
                season_instance.status = "Completed"
                seasons_to_update.append(season_instance)

        except Season.DoesNotExist:
            season_instance = Season(
                media_id=tv.media_id,
                title=tv_metadata["title"],
                image=season_metadata["image"],
                score=None,
                status="Completed",
                notes="",
                season_number=season_number,
                related_tv=tv,
                user=tv.user,
            )
            seasons_to_create.append(season_instance)
        episodes_to_create.extend(
            get_remaining_eps(season_instance, season_metadata),
        )

    Season.objects.bulk_update(seasons_to_update, ["status"])
    Season.objects.bulk_create(seasons_to_create)
    Episode.objects.bulk_create(episodes_to_create)


def get_remaining_eps(season: Season, season_metadata: dict) -> None:
    """Return remaining episodes to complete for a season."""

    max_episode_number = Episode.objects.filter(related_season=season).aggregate(
        max_episode_number=Max("episode_number"),
    )["max_episode_number"]

    if max_episode_number is None:
        max_episode_number = 0

    episodes_to_create = []

    # Create Episode objects for the remaining episodes
    for episode in reversed(season_metadata["episodes"]):
        if episode["episode_number"] <= max_episode_number:
            break

        episode_db = Episode(
            related_season=season,
            episode_number=episode["episode_number"],
            watch_date=datetime.datetime.now(tz=settings.TZ).date(),
        )
        episodes_to_create.append(episode_db)

    return episodes_to_create
