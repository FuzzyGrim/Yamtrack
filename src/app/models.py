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
from model_utils import FieldTracker

from app.providers import services, tmdb

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
    repeats = models.PositiveIntegerField(default=0)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["-score"]
        unique_together = ["media_id", "user"]

    def __str__(self):
        """Return the title of the media."""
        return f"{self.title}"

    def save(self, *args, **kwargs):
        """Save the media instance."""
        if "status" in self.tracker.changed():
            self.process_status()

        if "progress" in self.tracker.changed():
            self.process_progress()

        super().save(*args, **kwargs)

    def process_status(self):
        """Update fields depending on the status of the media."""
        if self.status == "Completed":
            if not self.end_date:
                self.end_date = datetime.datetime.now(tz=settings.TZ).date()

            media_type = self.__class__.__name__.lower()
            total_episodes = services.get_media_metadata(media_type, self.media_id)[
                "max_progress"
            ]

            if total_episodes != "Unknown":
                self.progress = total_episodes

        elif self.status == "In progress" and not self.start_date:
            self.start_date = datetime.datetime.now(tz=settings.TZ).date()

    def process_progress(self):
        """Update fields depending on the progress of the media."""
        media_type = self.__class__.__name__.lower()

        if self.progress < 0:
            self.progress = 0
        else:
            total_episodes = services.get_media_metadata(media_type, self.media_id)[
                "max_progress"
            ]

            if total_episodes != "Unknown":
                if self.progress > total_episodes:
                    self.progress = total_episodes

                if self.progress == total_episodes:
                    self.status = "Completed"

                    if not self.end_date:
                        self.end_date = datetime.datetime.now(tz=settings.TZ).date()

    def increase_progress(self):
        """Increase the progress of the media by one."""
        self.progress += 1
        # need extra fields because if completed,
        # the save method changes status and end_date
        self.save(update_fields=["progress", "status", "end_date"])
        logger.info("Watched %s E%s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by one."""
        self.progress -= 1
        self.save(update_fields=["progress"])
        logger.info("Unwatched %s E%s", self, self.progress + 1)


class TV(Media):
    """Model for TV shows."""

    tracker = FieldTracker()

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if (
            "status" in self.tracker.changed()
            and self.status == "Completed"
            and self.progress < tmdb.tv(self.media_id)["max_progress"]
        ):
            self.completed()

    @property
    def progress(self):
        """Return the user's episodes watched for the TV show."""
        return sum(season.progress for season in self.seasons.all())

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        return min(
            (season.start_date for season in self.seasons.all()),
            default=datetime.date(datetime.MINYEAR, 1, 1),
        )

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        return max(
            (season.end_date for season in self.seasons.all()),
            default=datetime.date(datetime.MINYEAR, 1, 1),
        )

    def completed(self):
        """Create remaining seasons and episodes for a TV show."""
        seasons_to_update = []
        episodes_to_create = []

        tv_metadata = tmdb.tv(self.media_id)
        season_numbers = range(1, tv_metadata["details"]["number_of_seasons"] + 1)
        tv_seasons_metadata = tmdb.tv_with_seasons(self.media_id, season_numbers)
        for season_number in season_numbers:
            season_metadata = tv_seasons_metadata[f"season/{season_number}"]
            try:
                season_instance = Season.objects.get(
                    media_id=self.media_id,
                    user=self.user,
                    season_number=season_number,
                )

                if season_instance.status != "Completed":
                    season_instance.status = "Completed"
                    seasons_to_update.append(season_instance)

            except Season.DoesNotExist:
                season_instance = Season(
                    media_id=self.media_id,
                    title=tv_metadata["title"],
                    image=season_metadata["image"],
                    score=None,
                    status="Completed",
                    notes="",
                    season_number=season_number,
                    related_tv=self,
                    user=self.user,
                )
                Season.save_base(season_instance)
            episodes_to_create.extend(
                season_instance.get_remaining_eps(season_metadata),
            )

        Season.objects.bulk_update(seasons_to_update, ["status"])
        Episode.objects.bulk_create(episodes_to_create)


class Season(Media):
    """Model for seasons of TV shows."""

    related_tv = models.ForeignKey(
        TV,
        on_delete=models.CASCADE,
        related_name="seasons",
    )
    season_number = models.PositiveIntegerField()

    tracker = FieldTracker()

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        unique_together = ["related_tv", "season_number"]

    def __str__(self):
        """Return the title of the media and season number."""
        return f"{self.title} S{self.season_number}"

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        # if related_tv is not set
        if self.related_tv_id is None:
            self.related_tv = self.get_tv()

        super(Media, self).save(*args, **kwargs)

        if "status" in self.tracker.changed() and self.status == "Completed":
            season_metadata = tmdb.season(self.media_id, self.season_number)
            Episode.objects.bulk_create(
                self.get_remaining_eps(season_metadata),
            )

    @property
    def progress(self):
        """Return the user's episodes watched for the season."""
        return self.episodes.count()

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        return min(
            (episode.watch_date for episode in self.episodes.all()),
            default=datetime.date(datetime.MINYEAR, 1, 1),
        )

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        return max(
            (episode.watch_date for episode in self.episodes.all()),
            default=datetime.date(datetime.MINYEAR, 1, 1),
        )

    def increase_progress(self):
        """Increase the progress of the season by one."""
        season_metadata = tmdb.season(self.media_id, self.season_number)

        progress = self.progress

        if progress == 0:
            next_episode = season_metadata["episodes"][0]["episode_number"]
        elif progress < len(season_metadata["episodes"]):
            next_episode = season_metadata["episodes"][progress]["episode_number"]

        try:
            Episode.objects.create(
                related_season=self,
                episode_number=next_episode,
                watch_date=datetime.datetime.now(tz=settings.TZ).date(),
            )
            logger.info("Watched %sE%s", self, next_episode)

        # next_episode not defined,
        # happens when another request completes the season
        except UnboundLocalError:
            logger.warning("No episodes to watch, %s is already completed", self)

    def decrease_progress(self):
        """Decrease the progress of the season by one."""
        try:
            last_watched = Episode.objects.filter(
                related_season=self,
            ).latest("episode_number")

            last_watched_number = last_watched.episode_number

            last_watched.delete()

            logger.info("Unwatched %sE%s", self, last_watched_number)
        except Episode.DoesNotExist:
            logger.warning("No episodes to unwatch in %s", self)

    def get_tv(self):
        """Get related TV instance for a season and create it if it doesn't exist."""
        try:
            tv = TV.objects.get(media_id=self.media_id, user=self.user)
        except TV.DoesNotExist:
            tv_metadata = tmdb.tv(self.media_id)

            # creating tv with multiple seasons from a completed season
            if (
                self.status == "Completed"
                and tv_metadata["details"]["number_of_seasons"] > 1
            ):
                status = "In progress"
            else:
                status = self.status

            tv = TV(
                media_id=self.media_id,
                title=tv_metadata["title"],
                image=tv_metadata["image"],
                score=None,
                status=status,
                notes="",
                user=self.user,
            )

            # save_base to avoid custom save method
            TV.save_base(tv)
            logger.info("%s did not exist, it was created successfully.", tv)

        return tv

    def get_remaining_eps(self, season_metadata):
        """Return episodes needed to complete a season."""
        max_episode_number = Episode.objects.filter(related_season=self).aggregate(
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
                related_season=self,
                episode_number=episode["episode_number"],
                watch_date=datetime.datetime.now(tz=settings.TZ).date(),
            )
            episodes_to_create.append(episode_db)

        return episodes_to_create


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

    def __str__(self):
        """Return the season and episode number."""
        return f"{self.related_season}E{self.episode_number}"

    def save(self, *args, **kwargs):
        """Save the episode instance."""
        if self._state.adding:
            super().save(*args, **kwargs)
            season_metadata = tmdb.season(
                self.related_season.media_id,
                self.related_season.season_number,
            )

            if self.related_season.progress == len(season_metadata["episodes"]):
                self.related_season.status = "Completed"
                # save_base to avoid custom save method
                self.related_season.save_base(update_fields=["status"])
        else:
            super().save(*args, **kwargs)


class Manga(Media):
    """Model for manga."""

    tracker = FieldTracker()


class Anime(Media):
    """Model for anime."""

    tracker = FieldTracker()


class Movie(Media):
    """Model for movies."""

    tracker = FieldTracker()


class Game(Media):
    """Model for games."""

    tracker = FieldTracker()

    def increase_progress(self):
        """Increase the progress of the media by 30 minutes."""
        self.progress += 30
        self.save(update_fields=["progress"])
        logger.info("Watched %s E%s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by 30 minutes."""
        self.progress -= 30
        self.save(update_fields=["progress"])
        logger.info("Unwatched %s E%s", self, self.progress + 1)
