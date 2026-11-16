import math
import re
from datetime import datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from app import config
from app.helpers import minutes_to_hhmm
from app.models import (
    TV,
    AlbumTracker,
    Anime,
    ArtistTracker,
    BoardGame,
    Book,
    CollectionEntry,
    Comic,
    ComicIssue,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Music,
    Podcast,
    PodcastEpisode,
    PodcastShowTracker,
    Season,
    Sources,
    Status,
    Video,
)

CHOICE_PAIR_LENGTH = 2


def get_form_class(media_type):
    """Return the form class for the media type."""
    class_name = media_type.capitalize() + "Form"
    return globals().get(class_name, None)


class CustomDurationField(forms.CharField):
    """Custom form field for duration input that accepts multiple time formats."""

    _UNIT_DURATION_PATTERN = re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>hours?|hrs?|hr|h|minutes?|mins?|min)"
    )

    def _parse_hours_minutes(self, value):
        """Parse and return total minutes from various time formats.

        Supported formats:
        - Plain number (hours only): "5"
        - Plain float number (hours and minutes): "1.5"
        - HH:MM: "5:30"
        - Nh Nmin: "5h 30min"
        - NhNmin: "5h30min"
        - Nmin: "30min"
        - Nh: "5h"
        - N minutes: "111 minutes"
        - Decimal hours: "4.3 hours"
        """
        normalized_value = value.strip().lower()

        if re.fullmatch(r"\d+(?:\.\d+)?", normalized_value):
            converted_to_float = float(normalized_value)
            if math.isfinite(converted_to_float) and converted_to_float >= 0:
                return int(converted_to_float * 60)

        if normalized_value.isdigit():  # hours only
            return int(normalized_value) * 60

        hh_mm_minutes = self._parse_hh_mm_duration(normalized_value)
        if hh_mm_minutes is not None:
            return hh_mm_minutes

        unit_minutes = self._parse_unit_duration(normalized_value)
        if unit_minutes is not None:
            return unit_minutes

        msg = _("Invalid time format")
        raise ValueError(msg)

    def _parse_hh_mm_duration(self, value):
        """Parse hh:mm input and return total minutes."""
        if ":" not in value:
            return None

        chunks = value.split(":")
        expected_chunk_count = 2
        if len(chunks) != expected_chunk_count:
            msg = _("Invalid time format")
            raise ValueError(msg)

        hours_str, minutes_str = chunks
        if not (hours_str.isdigit() and minutes_str.isdigit()):
            msg = _("Invalid time format")
            raise ValueError(msg)

        hours = int(hours_str)
        minutes = int(minutes_str)
        self._validate_minutes(minutes)
        return hours * 60 + minutes

    def _parse_unit_duration(self, value):
        """Parse unit-based duration strings and return total minutes."""
        matches = list(self._UNIT_DURATION_PATTERN.finditer(value))
        if not matches:
            return None

        remainder = self._UNIT_DURATION_PATTERN.sub("", value)
        if remainder.strip():
            msg = _("Invalid time format")
            raise ValueError(msg)

        total_minutes = Decimal(0)
        has_hours_token = any(
            match.group("unit").startswith(("h", "hr")) for match in matches
        )

        for match in matches:
            raw_value = match.group("value")
            try:
                amount = Decimal(raw_value)
            except InvalidOperation as e:
                msg = _("Invalid time format")
                raise ValueError(msg) from e

            unit = match.group("unit")
            if unit.startswith(("h", "hr")):
                total_minutes += amount * 60
            else:
                if has_hours_token:
                    self._validate_minutes(int(amount))
                total_minutes += amount

        return int(total_minutes)

    def _validate_minutes(self, minutes):
        """Validate that minutes are within acceptable range."""
        max_min = 59
        if not (0 <= minutes <= max_min):
            msg = _("Minutes must be between 0 and %(maximum)s.") % {"maximum": max_min}
            raise forms.ValidationError(msg)

    def clean(self, value):
        """Validate and convert the time string to total minutes."""
        cleaned_value = super().clean(value)
        if not cleaned_value:
            return 0

        try:
            return self._parse_hours_minutes(cleaned_value)
        except ValueError as e:
            msg = _(
                "Invalid time played format. Please use hh:mm, [n]h [n]min, "
                "[n]h[n]min, [n] minutes, or [n.n] hours."
            )
            raise forms.ValidationError(msg) from e


class ManualItemForm(forms.ModelForm):
    """Form for adding items to the database."""

    parent_tv = forms.ModelChoiceField(
        required=False,
        queryset=TV.objects.none(),
        empty_label=_("Select"),
        label=_("Parent TV Show"),
    )

    parent_season = forms.ModelChoiceField(
        required=False,
        queryset=Season.objects.none(),
        empty_label=_("Select"),
        label=_("Parent Season"),
    )
    rss_feed_url = forms.URLField(
        required=False,
        label=_("RSS feed URL"),
        help_text=_("Optional. Import the show's episodes from this feed."),
    )

    class Meta:
        """Bind form to model."""

        model = Item
        labels = {
            "season_number": _("Season number"),
            "episode_number": _("Episode number"),
        }
        fields = [
            "media_type",
            "title",
            "image",
            "season_number",
            "episode_number",
            "synopsis",
        ]
        widgets = {
            "synopsis": forms.Textarea(
                attrs={"placeholder": _("Add a description..."), "rows": "5"},
            ),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields["parent_tv"].queryset = TV.objects.filter(
                user=self.user,
                item__source=Sources.MANUAL.value,
                item__media_type=MediaTypes.TV.value,
            )
            self.fields["parent_season"].queryset = Season.objects.filter(
                user=self.user,
                item__source=Sources.MANUAL.value,
                item__media_type=MediaTypes.SEASON.value,
            )
        self.fields["image"].required = False
        self.fields["title"].required = False

    def clean(self):
        """Validate the form."""
        cleaned_data = super().clean()
        image = cleaned_data.get("image")
        media_type = cleaned_data.get("media_type")

        if not image:
            cleaned_data["image"] = settings.IMG_NONE

        # Title not required for season/episode
        if media_type in [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]:
            if media_type == MediaTypes.SEASON.value:
                parent = cleaned_data.get("parent_tv")
                if not parent:
                    self.add_error(
                        "parent_tv",
                        _("Parent TV show is required for seasons"),
                    )
                    return cleaned_data
                cleaned_data["title"] = parent.item.title
                cleaned_data["episode_number"] = None
            else:  # episode
                parent = cleaned_data.get("parent_season")
                if not parent:
                    self.add_error(
                        "parent_season",
                        _("Parent season is required for episodes"),
                    )
                    return cleaned_data
                cleaned_data["title"] = parent.item.title
                cleaned_data["season_number"] = parent.item.season_number
        else:
            # For standalone media, title is required
            if not cleaned_data.get("title"):
                self.add_error("title", _("Title is required for this media type"))
            cleaned_data["season_number"] = None
            cleaned_data["episode_number"] = None

        if media_type != MediaTypes.PODCAST.value:
            cleaned_data["rss_feed_url"] = ""

        return cleaned_data

    def save(self, commit=True):
        """Save the form and handle manual media ID generation."""
        instance = super().save(commit=False)
        instance.source = Sources.MANUAL.value

        if instance.media_type == MediaTypes.SEASON.value:
            parent_tv = self.cleaned_data["parent_tv"]
            instance.media_id = parent_tv.item.media_id
        elif instance.media_type == MediaTypes.EPISODE.value:
            parent_season = self.cleaned_data["parent_season"]
            instance.media_id = parent_season.item.media_id
            instance.season_number = parent_season.item.season_number
        else:
            instance.media_id = Item.generate_manual_id()

        if commit:
            instance.save()
        return instance


def episode_duration_input(seconds):
    """Return a duration as the ``mm:ss`` / ``h:mm:ss`` text the form accepts."""
    if not seconds:
        return ""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class PodcastDurationField(forms.CharField):
    """Accept minutes, ``mm:ss`` or ``h:mm:ss`` and store seconds."""

    _MINUTES = re.compile(r"\d+")
    _MINUTES_UNIT = re.compile(r"(\d+)\s*(?:m|min|mins|minute|minutes)")
    _SECONDS_UNIT = re.compile(r"(\d+)\s*(?:s|sec|secs|second|seconds)")
    _SECONDS_PER_MINUTE = 60
    _SECONDS_PER_HOUR = 3600
    _SHORT_PARTS = 2
    _LONG_PARTS = 3

    def clean(self, value):
        """Convert the submitted duration to seconds."""
        value = (super().clean(value) or "").strip().lower()
        if not value:
            return None

        seconds_per_minute = self._SECONDS_PER_MINUTE

        if self._MINUTES.fullmatch(value):
            return int(value) * seconds_per_minute

        if match := self._MINUTES_UNIT.fullmatch(value):
            return int(match.group(1)) * seconds_per_minute

        if match := self._SECONDS_UNIT.fullmatch(value):
            return int(match.group(1))

        parts = value.split(":")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            numbers = []
        if len(numbers) == self._SHORT_PARTS and all(
            number >= 0 for number in numbers
        ):
            minutes, secs = numbers
            if secs < seconds_per_minute:
                return minutes * seconds_per_minute + secs
        if len(numbers) == self._LONG_PARTS and all(number >= 0 for number in numbers):
            hours, minutes, secs = numbers
            if minutes < seconds_per_minute and secs < seconds_per_minute:
                return (
                    hours * self._SECONDS_PER_HOUR
                    + minutes * seconds_per_minute
                    + secs
                )

        raise forms.ValidationError(
            _("Enter the duration as minutes, mm:ss or h:mm:ss."),
        )


class PodcastEpisodeForm(forms.ModelForm):
    """Create or edit a manually managed podcast episode."""

    duration = PodcastDurationField(
        required=False,
        label=_("Duration"),
        help_text=_("Minutes, mm:ss or h:mm:ss. Leave blank if unknown."),
    )
    field_order = [
        "title",
        "description",
        "episode_number",
        "season_number",
        "published",
        "duration",
        "audio_url",
        "website_url",
        "image",
    ]

    class Meta:
        """Bind the form to the episode model."""

        model = PodcastEpisode
        fields = [
            "title",
            "description",
            "episode_number",
            "season_number",
            "published",
            "duration",
            "audio_url",
            "website_url",
            "image",
        ]
        labels = {
            "title": _("Episode title"),
            "description": _("Description"),
            "episode_number": _("Episode number"),
            "season_number": _("Season number"),
            "published": _("Publication date"),
            "audio_url": _("Audio URL"),
            "website_url": _("Website URL"),
            "image": _("Artwork URL"),
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": "4"}),
            "published": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, **kwargs):
        """Style the fallback inputs and show the stored duration."""
        super().__init__(*args, **kwargs)
        input_class = (
            "w-full p-2 bg-[var(--color-surface-strong)] rounded-md "
            "text-[var(--color-text)] border border-gray-600 "
            "focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", input_class)
        if not self.is_bound and self.instance and self.instance.pk:
            self.initial["duration"] = episode_duration_input(self.instance.duration)


class RatingScaleFormMixin:
    """Apply user rating scale preferences to score fields."""

    def __init__(self, *args, **kwargs):
        """Store the extra keyword arguments this form needs."""
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self._apply_rating_scale()

    def _apply_rating_scale(self):
        if not self.user or "score" not in self.fields:
            return
        if not self.user.ratings_enabled:
            # Dropping the field keeps the stored score untouched on save.
            del self.fields["score"]
            return
        scale_max = self.user.rating_scale_max
        self.fields["score"].widget.attrs.update(
            {
                "min": 0,
                "max": scale_max,
                "step": 0.1,
                "placeholder": f"0-{scale_max}",
            },
        )
        if (
            not self.is_bound
            and self.instance
            and getattr(self.instance, "score", None) is not None
        ):
            self.initial["score"] = self.user.scale_score_for_display(
                self.instance.score
            )

    def clean_score(self):
        """Validate the score field."""
        score = self.cleaned_data.get("score")
        if score is None or not self.user:
            return score
        return self.user.scale_score_for_storage(score)


class MediaForm(RatingScaleFormMixin, forms.ModelForm):
    """Base form for all media types."""

    instance_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    media_type = forms.CharField(widget=forms.HiddenInput(), required=True)
    identity_media_type = forms.CharField(widget=forms.HiddenInput(), required=False)
    library_media_type = forms.CharField(widget=forms.HiddenInput(), required=False)
    source = forms.CharField(widget=forms.HiddenInput(), required=True)
    media_id = forms.CharField(widget=forms.HiddenInput(), required=True)
    image_url = forms.URLField(
        required=False,
        label=_("Image URL"),
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://example.com/poster.jpg",
            },
        ),
    )

    class Meta:
        """Define fields and input types."""

        fields = [
            "score",
            "progress",
            "status",
            "start_date",
            "end_date",
            "notes",
            "entry_source",
        ]
        widgets = {
            "score": forms.NumberInput(
                attrs={"min": 0, "max": 10, "step": 0.1, "placeholder": "0-10"},
            ),
            "progress": forms.NumberInput(attrs={"min": 0}),
            "start_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"placeholder": _("Add any notes or comments..."), "rows": "5"},
            ),
            "entry_source": forms.TextInput(
                attrs={
                    "placeholder": _("e.g. Plex, Jellyfin, Theatre..."),
                    # A plain text input's native Enter-to-submit can bypass
                    # htmx's own submit handling (issue: Enter here fell back
                    # to a raw form POST/navigation instead of the AJAX
                    # save). Route Enter through the same requestSubmit()
                    # path a real click on the submit button uses.
                    "@keydown.enter.prevent": "$el.closest('form').requestSubmit()",
                },
            ),
        }
        labels = {
            "entry_source": _("Source"),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        # Safely pop max_progress and user if they're passed (some form subclasses use them, others don't)
        kwargs.pop("max_progress", None)
        super().__init__(*args, **kwargs)
        # Make date fields optional to allow submission without dates
        if "start_date" in self.fields:
            self.fields["start_date"].required = False
            # Explicitly remove required attribute from widget to prevent HTML5 validation
            self.fields["start_date"].widget.attrs.pop("required", None)
        if "end_date" in self.fields:
            self.fields["end_date"].required = False
            # Explicitly remove required attribute from widget to prevent HTML5 validation
            self.fields["end_date"].widget.attrs.pop("required", None)

        # status is nullable: blank means the user holds the media (usually an
        # imported rating) without tracking it. Label it like the list filter
        # instead of Django's default "---------".
        if "status" in self.fields:
            self.fields["status"].choices = [
                ("", _("No Status")),
                *[
                    (value, _(label))
                    for value, label in self.fields["status"].choices
                    if value
                ],
            ]

        if self.instance and getattr(self.instance, "item", None):
            current_image = self.instance.item.image
            if current_image and current_image != settings.IMG_NONE:
                self.initial.setdefault("image_url", current_image)
        # Audiobookshelf and Plex covers are Floppy's own proxy paths, which the
        # URL field rejects. Prefilling one made every save of that item fail,
        # with the error hidden on the Metadata tab (#1316). Blank means
        # "keep the current image".
        if not str(self.initial.get("image_url") or "").startswith(
            ("http://", "https://"),
        ):
            self.initial.pop("image_url", None)

    def clean_image_url(self):
        """Normalize optional image URL input."""
        return (self.cleaned_data.get("image_url") or "").strip()


class ReadingForm(MediaForm):
    """Shared base for books, comics and manga: adds the time spent reading."""

    reading_time = CustomDurationField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("hh:mm or 111 minutes")}),
        label=_("Reading time"),
    )

    class Meta(MediaForm.Meta):
        """Put reading time right after progress."""

        fields = [
            "score",
            "progress",
            "reading_time",
            "status",
            "start_date",
            "end_date",
            "notes",
            "entry_source",
        ]

    def __init__(self, *args, **kwargs):
        """Drop reading time for audiobooks, whose progress already is the time."""
        super().__init__(*args, **kwargs)
        item = getattr(self.instance, "item", None)
        if item is not None and item.format == "audiobook":
            del self.fields["reading_time"]
        elif self.instance.reading_time:
            # The field reads bare numbers as hours, so never show raw minutes.
            self.initial["reading_time"] = minutes_to_hhmm(self.instance.reading_time)


class MangaForm(ReadingForm):
    """Form for manga."""

    class Meta(ReadingForm.Meta):
        """Bind form to model."""

        model = Manga
        labels = {
            "progress": _("Progress (Chapters)"),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        kwargs.pop("max_progress", None)
        super().__init__(*args, **kwargs)

        # Adjust progress field for percentage mode
        if self.user and self.user.book_comic_manga_progress_percentage:
            self.fields["progress"].label = _("Progress (%)")
            self.fields["progress"].widget.attrs.update(
                {"min": 0, "max": 100, "step": 0.1, "placeholder": "%"}
            )


class AnimeForm(MediaForm):
    """Form for anime."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Anime


class MovieForm(MediaForm):
    """Form for movies."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Movie
        fields = [
            "score",
            "status",
            "start_date",
            "end_date",
            "notes",
            "entry_source",
        ]


class GameForm(MediaForm):
    """Form for games."""

    progress = CustomDurationField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("hh:mm or 111 minutes")}),
        label=_("Progress (Time Played)"),
    )
    start_date_cleared = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Game

    def clean(self):
        """Backfill a missing start date from the saved end date and progress."""
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        progress = cleaned_data.get("progress")
        start_date_cleared = cleaned_data.get("start_date_cleared") == "1"

        if (
            not start_date
            and not start_date_cleared
            and not self.instance.start_date
            and isinstance(end_date, datetime)
            and progress
            and progress > 0
        ):
            cleaned_data["start_date"] = end_date - timedelta(minutes=progress)

        return cleaned_data


class BoardgameForm(MediaForm):
    """Form for board games."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = BoardGame
        labels = {
            "progress": _("Progress (Plays)"),
        }


class BookForm(ReadingForm):
    """Form for books."""

    class Meta(ReadingForm.Meta):
        """Bind form to model."""

        model = Book
        labels = {
            "progress": _("Progress (Pages)"),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        kwargs.pop("max_progress", None)
        super().__init__(*args, **kwargs)

        # Adjust progress field for percentage mode
        if self.user and self.user.book_comic_manga_progress_percentage:
            self.fields["progress"].label = _("Progress (%)")
            self.fields["progress"].widget.attrs.update(
                {"min": 0, "max": 100, "step": 0.1, "placeholder": "%"}
            )


class ComicForm(ReadingForm):
    """Form for comics."""

    class Meta(ReadingForm.Meta):
        """Bind form to model."""

        model = Comic
        labels = {
            "progress": _("Progress (Issues)"),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        kwargs.pop("max_progress", None)
        super().__init__(*args, **kwargs)

        # Adjust progress field for percentage mode
        if self.user and self.user.book_comic_manga_progress_percentage:
            self.fields["progress"].label = _("Progress (%)")
            self.fields["progress"].widget.attrs.update(
                {"min": 0, "max": 100, "step": 0.1, "placeholder": "%"}
            )


class ComicissueForm(MediaForm):
    """Form for individual comic issues."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = ComicIssue


class TvForm(MediaForm):
    """Form for TV shows."""

    end_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"})
        if settings.TRACK_TIME
        else forms.DateInput(attrs={"type": "date"}),
    )

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = TV
        fields = ["score", "status", "notes", "entry_source"]


class SeasonForm(MediaForm):
    """Form for seasons."""

    season_number = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    end_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"})
        if settings.TRACK_TIME
        else forms.DateInput(attrs={"type": "date"}),
    )

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Season
        fields = [
            "score",
            "status",
            "notes",
            "entry_source",
        ]


class EpisodeForm(RatingScaleFormMixin, forms.ModelForm):
    """Form for episodes."""

    instance_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    media_type = forms.CharField(widget=forms.HiddenInput(), required=False)
    identity_media_type = forms.CharField(widget=forms.HiddenInput(), required=False)
    library_media_type = forms.CharField(widget=forms.HiddenInput(), required=False)
    source = forms.CharField(widget=forms.HiddenInput(), required=False)
    media_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    season_number = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    episode_number = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    watch_operation_id = forms.UUIDField(widget=forms.HiddenInput(), required=False)

    class Meta:
        """Bind form to model."""

        model = Episode
        fields = ("score", "status", "start_date", "end_date", "notes", "entry_source")
        widgets = {
            "score": forms.NumberInput(
                attrs={"min": 0, "max": 10, "step": 0.1, "placeholder": "0-10"},
            ),
            "start_date": forms.DateTimeInput(attrs={"type": "datetime-local"})
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"placeholder": _("Add any notes or comments..."), "rows": "5"},
            ),
            "entry_source": forms.TextInput(
                attrs={
                    "placeholder": _("e.g. Plex, Jellyfin, Theatre..."),
                    "@keydown.enter.prevent": "$el.closest('form').requestSubmit()",
                },
            ),
        }
        labels = {
            "entry_source": _("Source"),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)

        if not self.is_bound and self.instance.pk is None:
            self.initial["watch_operation_id"] = uuid4()

        if settings.TRACK_TIME:
            self.fields["start_date"].widget = forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            self.fields["end_date"].widget = forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
        else:
            self.fields["start_date"].widget = forms.DateInput(
                attrs={"type": "date"},
            )
            self.fields["end_date"].widget = forms.DateInput(
                attrs={"type": "date"},
            )

        self.fields["start_date"].required = False
        self.fields["end_date"].required = False
        self.fields["status"].required = False

    def clean_status(self):
        """Keep legacy episode-save clients equivalent to adding a watch."""
        return self.cleaned_data.get("status") or Status.COMPLETED.value

    def clean(self):
        """Complete an open play when the edit gives it a watch date.

        The track modal edits the latest play, so marking an episode watched
        while a paused check-in left it open resubmits its In Progress status.
        """
        cleaned_data = super().clean()
        if (
            self.instance.pk
            and self.instance.status == Status.IN_PROGRESS.value
            and self.instance.end_date is None
            and cleaned_data.get("status") == Status.IN_PROGRESS.value
            and cleaned_data.get("end_date")
        ):
            cleaned_data["status"] = Status.COMPLETED.value
        return cleaned_data


class BulkEpisodeTrackForm(forms.Form):
    """Form for bulk tracking episode plays across a TV/anime range."""

    WRITE_MODE_ADD = "add"
    WRITE_MODE_REPLACE = "replace"
    DISTRIBUTION_MODE_EVEN = "even"
    DISTRIBUTION_MODE_AIR_DATE = "air_date"

    WRITE_MODE_CHOICES = (
        (WRITE_MODE_ADD, _("Add additional plays")),
        (WRITE_MODE_REPLACE, _("Replace all plays")),
    )
    DISTRIBUTION_MODE_CHOICES = (
        (DISTRIBUTION_MODE_AIR_DATE, _("Target air date")),
        (DISTRIBUTION_MODE_EVEN, _("Even distribution")),
    )

    media_id = forms.CharField(widget=forms.HiddenInput(), required=True)
    source = forms.CharField(widget=forms.HiddenInput(), required=True)
    media_type = forms.CharField(widget=forms.HiddenInput(), required=True)
    identity_media_type = forms.CharField(widget=forms.HiddenInput(), required=False)
    library_media_type = forms.CharField(widget=forms.HiddenInput(), required=False)
    instance_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    return_url = forms.CharField(widget=forms.HiddenInput(), required=False)
    context_kind = forms.CharField(widget=forms.HiddenInput(), required=False)
    context_id = forms.CharField(widget=forms.HiddenInput(), required=False)

    first_season_number = forms.TypedChoiceField(
        label=_("First season"),
        coerce=int,
        choices=(),
    )
    first_episode_number = forms.TypedChoiceField(
        label=_("First episode"),
        coerce=int,
        choices=(),
    )
    last_season_number = forms.TypedChoiceField(
        label=_("Last season"),
        coerce=int,
        choices=(),
    )
    last_episode_number = forms.TypedChoiceField(
        label=_("Last episode"),
        coerce=int,
        choices=(),
    )
    write_mode = forms.ChoiceField(
        label=_("Play handling"),
        choices=WRITE_MODE_CHOICES,
        initial=WRITE_MODE_ADD,
    )
    distribution_mode = forms.ChoiceField(
        label=_("Distribution"),
        choices=DISTRIBUTION_MODE_CHOICES,
        initial=DISTRIBUTION_MODE_AIR_DATE,
    )
    if settings.TRACK_TIME:
        start_date = forms.DateTimeField(
            required=False,
            widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"}),
        )
        end_date = forms.DateTimeField(
            required=False,
            widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"}),
        )
    else:
        start_date = forms.DateField(
            required=False,
            widget=forms.DateInput(attrs={"type": "date"}),
        )
        end_date = forms.DateField(
            required=False,
            widget=forms.DateInput(attrs={"type": "date"}),
        )

    def __init__(self, *args, **kwargs):
        """Initialize dynamic selector choices from the episode domain."""
        self.domain = kwargs.pop("domain", None) or {}
        super().__init__(*args, **kwargs)

        distribution_target_label = (
            self.domain.get("distribution_target_label") or "air date"
        )
        self.fields["distribution_mode"].choices = (
            (
                self.DISTRIBUTION_MODE_AIR_DATE,
                f"Target {distribution_target_label}",
            ),
            (
                self.DISTRIBUTION_MODE_EVEN,
                _("Even distribution"),
            ),
        )

        seasons = self.domain.get("seasons", [])
        season_choices = [
            (season["season_number"], season["season_title"]) for season in seasons
        ]
        self.fields["first_season_number"].choices = season_choices
        self.fields["last_season_number"].choices = season_choices

        for field_name in ("start_date", "end_date"):
            if field_name in self.fields:
                self.fields[field_name].required = False
                self.fields[field_name].widget.attrs.pop("required", None)

        default_first = self.domain.get("default_first") or {}
        default_last = self.domain.get("default_last") or {}
        if not self.is_bound:
            self.initial.setdefault(
                "first_season_number",
                default_first.get("season_number"),
            )
            self.initial.setdefault(
                "first_episode_number",
                default_first.get("episode_number"),
            )
            self.initial.setdefault(
                "last_season_number",
                default_last.get("season_number"),
            )
            self.initial.setdefault(
                "last_episode_number",
                default_last.get("episode_number"),
            )

        self._bind_episode_choices(
            "first_episode_number",
            "first_season_number",
            default_first.get("season_number"),
        )
        self._bind_episode_choices(
            "last_episode_number",
            "last_season_number",
            default_last.get("season_number"),
        )

    def _season_value(self, field_name, fallback_season_number):
        """Return the selected season number for a dependent episode dropdown."""
        if self.is_bound:
            raw_value = self.data.get(self.add_prefix(field_name))
        else:
            raw_value = self.initial.get(field_name, fallback_season_number)

        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return fallback_season_number

    def _bind_episode_choices(
        self,
        episode_field_name,
        season_field_name,
        fallback_season_number,
    ):
        """Populate episode choices for the selected season."""
        season_number = self._season_value(season_field_name, fallback_season_number)
        season_episode_map = self.domain.get("season_episode_map", {})
        episode_choices = [
            (
                episode["episode_number"],
                f"E{episode['episode_number']} - {episode['episode_title']}",
            )
            for episode in season_episode_map.get(season_number, [])
        ]
        self.fields[episode_field_name].choices = episode_choices

    def _normalize_datetime_value(self, value):
        """Convert date-only values into aware datetimes for downstream services."""
        if value in (None, ""):
            return None

        if hasattr(value, "hour"):
            if timezone.is_naive(value):
                return timezone.make_aware(
                    value,
                    timezone.get_current_timezone(),
                )
            return value

        combined = datetime.combine(value, datetime_time.min)
        return timezone.make_aware(
            combined,
            timezone.get_current_timezone(),
        )

    def clean_start_date(self):
        """Normalize start dates for both date and datetime inputs."""
        return self._normalize_datetime_value(self.cleaned_data.get("start_date"))

    def clean_end_date(self):
        """Normalize end dates for both date and datetime inputs."""
        return self._normalize_datetime_value(self.cleaned_data.get("end_date"))

    def clean(self):
        """Validate the selected episode range against the resolved domain."""
        cleaned_data = super().clean()
        episode_lookup = self.domain.get("episode_lookup", {})
        selection_noun = self.domain.get("selection_noun") or "episode"
        selection_noun_plural = (
            self.domain.get("selection_noun_plural") or f"{selection_noun}s"
        )
        distribution_target_label = (
            self.domain.get("distribution_target_label") or "air date"
        )
        missing_target_date_fallback_distribution = self.domain.get(
            "missing_target_date_fallback_distribution",
        )

        first_key = (
            cleaned_data.get("first_season_number"),
            cleaned_data.get("first_episode_number"),
        )
        last_key = (
            cleaned_data.get("last_season_number"),
            cleaned_data.get("last_episode_number"),
        )
        first_episode = episode_lookup.get(first_key)
        last_episode = episode_lookup.get(last_key)

        if first_episode is None:
            self.add_error(
                "first_episode_number",
                f"Select a {selection_noun} that exists in the available range.",
            )
        if last_episode is None:
            self.add_error(
                "last_episode_number",
                f"Select a {selection_noun} that exists in the available range.",
            )
        if self.errors:
            return cleaned_data

        if first_episode["order"] > last_episode["order"]:
            self.add_error(
                "last_episode_number",
                f"The last {selection_noun} must come after or match the first {selection_noun}.",
            )
            return cleaned_data

        selected_episodes = [
            episode
            for episode in self.domain.get("episodes", [])
            if first_episode["order"] <= episode["order"] <= last_episode["order"]
        ]
        cleaned_data["selected_domain_episodes"] = selected_episodes

        distribution_mode = cleaned_data.get("distribution_mode")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if not start_date:
            self.add_error("start_date", _("Start date is required."))
        if not end_date:
            self.add_error("end_date", _("End date is required."))
        if start_date and end_date and start_date > end_date:
            self.add_error(
                "end_date", _("End date must be on or after the start date.")
            )

        if distribution_mode == self.DISTRIBUTION_MODE_AIR_DATE:
            missing_air_dates = [
                episode for episode in selected_episodes if not episode.get("air_date")
            ]
            if missing_air_dates:
                if (
                    missing_target_date_fallback_distribution
                    == self.DISTRIBUTION_MODE_EVEN
                ):
                    cleaned_data["distribution_mode"] = self.DISTRIBUTION_MODE_EVEN
                    cleaned_data["missing_target_date_fallback_applied"] = True
                else:
                    self.add_error(
                        "distribution_mode",
                        "One or more selected "
                        f"{selection_noun_plural} are missing {distribution_target_label}s. "
                        "Use even distribution instead.",
                    )

        return cleaned_data


class MusicTrackNotesForm(forms.ModelForm):
    """Edit a song's notes independently of its listens."""

    class Meta:
        """Bind only the existing per-user notes field."""

        model = Music
        fields = ("notes",)
        widgets = MediaForm.Meta.widgets


class MusicForm(MediaForm):
    """Form for music tracks."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Music
        labels = {
            "progress": _("Progress (Plays)"),
        }


class PodcastForm(MediaForm):
    """Form for podcast episodes."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Podcast
        labels = {
            "progress": _("Progress (Minutes)"),
        }


class VideoForm(MediaForm):
    """Form for videos."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Video
        labels = {
            "progress": _("Progress (Seconds)"),
        }


class ArtistTrackerForm(RatingScaleFormMixin, forms.ModelForm):
    """Form for tracking artists - mirrors MediaForm but without progress."""

    artist_id = forms.IntegerField(widget=forms.HiddenInput(), required=True)

    class Meta:
        """Define fields and input types."""

        model = ArtistTracker
        fields = [
            "score",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "score": forms.NumberInput(
                attrs={"min": 0, "max": 10, "step": 0.1, "placeholder": "0-10"},
            ),
            "start_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"placeholder": _("Add any notes or comments..."), "rows": "5"},
            ),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)
        # Make date fields optional
        if "start_date" in self.fields:
            self.fields["start_date"].required = False
            self.fields["start_date"].widget.attrs.pop("required", None)
        if "end_date" in self.fields:
            self.fields["end_date"].required = False
            self.fields["end_date"].widget.attrs.pop("required", None)


class PodcastShowTrackerForm(RatingScaleFormMixin, forms.ModelForm):
    """Form for tracking podcast shows - mirrors ArtistTrackerForm."""

    show_id = forms.IntegerField(widget=forms.HiddenInput(), required=True)

    class Meta:
        """Define fields and input types."""

        model = PodcastShowTracker
        fields = [
            "score",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "score": forms.NumberInput(
                attrs={"min": 0, "max": 10, "step": 0.1, "placeholder": "0-10"},
            ),
            "start_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"placeholder": _("Add any notes or comments..."), "rows": "5"},
            ),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)
        # Make date fields optional
        if "start_date" in self.fields:
            self.fields["start_date"].required = False
            self.fields["start_date"].widget.attrs.pop("required", None)
        if "end_date" in self.fields:
            self.fields["end_date"].required = False
            self.fields["end_date"].widget.attrs.pop("required", None)


class AlbumTrackerForm(RatingScaleFormMixin, forms.ModelForm):
    """Form for tracking albums - mirrors MediaForm but without progress."""

    album_id = forms.IntegerField(widget=forms.HiddenInput(), required=True)

    class Meta:
        """Define fields and input types."""

        model = AlbumTracker
        fields = [
            "score",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "score": forms.NumberInput(
                attrs={"min": 0, "max": 10, "step": 0.1, "placeholder": "0-10"},
            ),
            "start_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"placeholder": _("Add any notes or comments..."), "rows": "5"},
            ),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)
        # Make date fields optional
        if "start_date" in self.fields:
            self.fields["start_date"].required = False
            self.fields["start_date"].widget.attrs.pop("required", None)
        if "end_date" in self.fields:
            self.fields["end_date"].required = False
            self.fields["end_date"].widget.attrs.pop("required", None)


class CollectionEntryForm(forms.ModelForm):
    """Form for adding/editing collection entries."""

    class Meta:
        """Model and field configuration."""

        model = CollectionEntry
        fields = [
            "item",
            "media_type",
            "resolution",
            "hdr",
            "is_3d",
            "audio_codec",
            "audio_channels",
            "bitrate",
            "purchase_price",
            "purchase_location",
        ]
        widgets = {
            "item": forms.HiddenInput(),
            "media_type": forms.TextInput(
                attrs={"placeholder": "Bluray, DVD, Digital"},
            ),
            "resolution": forms.TextInput(attrs={"placeholder": "1080p, 4k"}),
            "hdr": forms.TextInput(attrs={"placeholder": "HDR10, Dolby Vision"}),
            "is_3d": forms.CheckboxInput(),
            "audio_codec": forms.TextInput(
                attrs={"placeholder": "DTS, TrueHD, Atmos"},
            ),
            "audio_channels": forms.TextInput(attrs={"placeholder": "5.1, 7.1.2"}),
            "bitrate": forms.NumberInput(attrs={"placeholder": "128, 320, 1411"}),
            "purchase_price": forms.NumberInput(
                attrs={"placeholder": "9.99", "step": "0.01", "min": "0"},
            ),
            "purchase_location": forms.TextInput(
                attrs={"placeholder": "Steam, NAS, Home, Storage"},
            ),
        }

    def __init__(self, *args, **kwargs):
        """Store the extra keyword arguments this form needs."""
        self.user = kwargs.pop("user", None)
        collection_media_type = kwargs.pop("collection_media_type", None)
        collection_choices_override = (
            kwargs.pop("collection_choices_override", None) or {}
        )
        super().__init__(*args, **kwargs)
        if settings.TRACK_TIME:
            collected_widget = forms.DateTimeInput(
                attrs={"type": "datetime-local", "step": "1"},
            )
        else:
            collected_widget = forms.DateInput(attrs={"type": "date"})

        self.fields["collected_at"] = forms.DateTimeField(
            required=False,
            label=_("Collected At"),
            widget=collected_widget,
        )
        if self.instance and self.instance.pk and self.instance.collected_at:
            collected_at = self.instance.collected_at
            if timezone.is_aware(collected_at):
                collected_at = timezone.localtime(collected_at)
            self.fields["collected_at"].initial = collected_at

        config_entry = config.get_collection_field_config(collection_media_type)
        self.collection_fields = config_entry.get("fields", [])
        if self.collection_fields:
            self.order_fields(self.collection_fields)

        labels = config_entry.get("labels", {})
        for field_name, label in labels.items():
            if field_name in self.fields:
                self.fields[field_name].label = label

        choices_by_field = config_entry.get("choices", {})
        if collection_choices_override:
            choices_by_field = {**choices_by_field, **collection_choices_override}
        for field_name, choices in choices_by_field.items():
            if field_name not in self.fields:
                continue
            normalized = []
            for option in choices:
                if (
                    isinstance(option, (tuple, list))
                    and len(option) == CHOICE_PAIR_LENGTH
                ):
                    normalized.append((option[0], option[1]))
                else:
                    normalized.append((option, option))
            current_value = getattr(
                self.instance, field_name, None
            ) or self.initial.get(field_name)
            existing_values = {str(value) for value, _ in normalized}
            if current_value and str(current_value) not in existing_values:
                normalized.append((current_value, current_value))
            submitted_value = self.data.get(field_name)
            if submitted_value and str(submitted_value) not in existing_values:
                normalized.append((submitted_value, submitted_value))
            choices_list = [("", _("Select")), *normalized]
            self.fields[field_name].widget = forms.Select(choices=choices_list)
            self.fields[field_name].required = False
