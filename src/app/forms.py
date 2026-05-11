import math

from django import forms
from django.core.validators import URLValidator
from django.conf import settings

from app import config
from app.models import (
    TV,
    Anime,
    BoardGame,
    Book,
    Comic,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
)

url_validator = URLValidator()


def get_form_class(media_type):
    """Return the form class for the media type."""
    class_name = media_type.capitalize() + "Form"
    return globals().get(class_name, None)


class CustomDurationField(forms.CharField):
    """Custom form field for duration input that accepts multiple time formats."""

    def _parse_hours_minutes(self, value):
        """Parse hours and minutes from various time formats.

        Supported formats:
        - Plain number (hours only): "5"
        - Plain float number (hours and minutes): "1.5"
        - HH:MM: "5:30"
        - Nh Nmin: "5h 30min"
        - NhNmin: "5h30min"
        - Nmin: "30min"
        - Nh: "5h"
        """
        if value.isdigit() or "." in value:  # e.g. "5" or "3.5" for 3h 30min
            converted_to_float = float(value)
            if math.isfinite(converted_to_float) and converted_to_float >= 0:
                frac, hours = math.modf(converted_to_float)
                return int(hours), int(frac * 60)

        if ":" in value:  # hh:mm format
            hours, minutes = value.split(":")
            return int(hours), int(minutes)

        if " " in value:  # [n]h [n]min format
            hours, minutes = value.split(" ")
            return int(hours.strip("h")), int(minutes.strip("min"))

        if "h" in value and "min" in value:  # [n]h[n]min format
            hours, minutes = value.split("h")
            return int(hours), int(minutes.strip("min"))

        if "min" in value:  # [n]min format
            return 0, int(value.strip("min"))

        if "h" in value:  # [n]h format
            return int(value.strip("h")), 0

        msg = "Invalid time format"
        raise ValueError(msg)

    def _validate_minutes(self, minutes):
        """Validate that minutes are within acceptable range."""
        max_min = 59
        if not (0 <= minutes <= max_min):
            msg = f"Minutes must be between 0 and {max_min}."
            raise forms.ValidationError(msg)

    def clean(self, value):
        """Validate and convert the time string to total minutes."""
        cleaned_value = super().clean(value)
        if not cleaned_value:
            return 0

        try:
            hours, minutes = self._parse_hours_minutes(cleaned_value)
            self._validate_minutes(minutes)
            return hours * 60 + minutes
        except ValueError as e:
            msg = "Invalid time format. Provide duration in hours (e.g., '5', '1.5'), hours and minutes (e.g., '5:30', '5h 30min'), or just minutes (e.g., '30min')."  # noqa: E501
            raise forms.ValidationError(msg) from e


class ManualItemForm(forms.ModelForm):
    """Form for adding items to the database."""

    parent_tv = forms.ModelChoiceField(
        required=False,
        queryset=TV.objects.none(),
        empty_label="Select",
        label="Parent TV Show",
    )

    parent_season = forms.ModelChoiceField(
        required=False,
        queryset=Season.objects.none(),
        empty_label="Select",
        label="Parent Season",
    )

    class Meta:
        """Bind form to model."""

        model = Item
        fields = [
            "media_type",
            "title",
            "image",
            "season_number",
            "episode_number",
        ]

    def __init__(self, *args, **kwargs):
        """Initialize form and keep submitted custom links for processing."""
        self.custom_link_entries = kwargs.pop("custom_link_entries", None)
        super().__init__(*args, **kwargs)

    def clean_custom_link_entries(self):
        """Validate custom link pairs passed from request.POST.getlist."""
        if self.custom_link_entries is None:
            return None

        cleaned = []
        for entry in self.custom_link_entries:
            label = (entry.get("label") or "").strip()
            url = (entry.get("url") or "").strip()
            if not label and not url:
                continue
            if not label or not url:
                raise forms.ValidationError("Each link requires both a label and URL.")
            if len(url) > 500:
                raise forms.ValidationError(f"URL is too long for '{label}'.")
            try:
                url_validator(url)
            except forms.ValidationError as e:
                raise forms.ValidationError(f"Invalid URL for '{label}'.") from e
            cleaned.append({"label": label[:100], "url": url})
        return cleaned

    def clean(self):
        """Run base clean and validate custom links payload."""
        cleaned_data = super().clean()
        cleaned_data["custom_link_entries"] = self.clean_custom_link_entries()
        return cleaned_data

    def save(self, commit=True):  # noqa: FBT002
        """Save media and synchronize custom links when submitted."""
        instance = super().save(commit=commit)
        submitted_links = self.cleaned_data.get("custom_link_entries")
        if commit and instance.pk and submitted_links is not None:
            instance.custom_links.filter(user=instance.user).delete()
            instance.custom_links.model.objects.bulk_create(
                [
                    instance.custom_links.model(
                        user=instance.user,
                        label=entry["label"],
                        url=entry["url"],
                        content_object=instance,
                    )
                    for entry in submitted_links
                ]
            )
        return instance

class MangaForm(MediaForm):
    """Form for manga."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Manga
        labels = {
            "progress": (
                f"Progress ({config.get_unit(MediaTypes.MANGA.value, short=False)}s)"
            ),
        }


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
        ]


class GameForm(MediaForm):
    """Form for games."""

    progress = CustomDurationField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "hh:mm"}),
        label="Progress (Time Played)",
    )

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Game


class BookForm(MediaForm):
    """Form for books."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Book
        labels = {
            "progress": (
                f"Progress ({config.get_unit(MediaTypes.BOOK.value, short=False)}s)"
            ),
        }


class ComicForm(MediaForm):
    """Form for comics."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Comic
        labels = {
            "progress": (
                f"Progress ({config.get_unit(MediaTypes.COMIC.value, short=False)}s)"
            ),
        }


class BoardgameForm(MediaForm):
    """Form for board games."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = BoardGame
        labels = {
            "progress": (
                "Progress "
                f"({config.get_unit(MediaTypes.BOARDGAME.value, short=False)}s)"
            ),
        }


class TvForm(MediaForm):
    """Form for TV shows."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = TV
        fields = ["score", "status", "notes"]


class SeasonForm(MediaForm):
    """Form for seasons."""

    season_number = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Season
        fields = [
            "score",
            "status",
            "notes",
        ]


class EpisodeForm(forms.ModelForm):
    """Form for episodes."""

    class Meta:
        """Bind form to model."""

        model = Episode
        fields = ("end_date",)
        widgets = {
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)

        if settings.TRACK_TIME:
            self.fields["end_date"].widget = forms.DateTimeInput(
                attrs={"type": "datetime-local"},
            )
        else:
            self.fields["end_date"].widget = forms.DateInput(
                attrs={"type": "date"},
            )
