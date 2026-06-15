import math

from django import forms
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
    DiaryEntry,
)


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
                        "Parent TV show is required for seasons",
                    )
                    return cleaned_data
                cleaned_data["title"] = parent.item.title
                cleaned_data["episode_number"] = None
            else:  # episode
                parent = cleaned_data.get("parent_season")
                if not parent:
                    self.add_error(
                        "parent_season",
                        "Parent season is required for episodes",
                    )
                    return cleaned_data
                cleaned_data["title"] = parent.item.title
                cleaned_data["season_number"] = parent.item.season_number
        else:
            # For standalone media, title is required
            if not cleaned_data.get("title"):
                self.add_error("title", "Title is required for this media type")
            cleaned_data["season_number"] = None
            cleaned_data["episode_number"] = None

        return cleaned_data

    def save(self, commit=True):  # noqa: FBT002
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


class MediaForm(forms.ModelForm):
    """Base form for all media types."""

    instance_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    media_type = forms.CharField(widget=forms.HiddenInput(), required=True)
    source = forms.CharField(widget=forms.HiddenInput(), required=True)
    media_id = forms.CharField(widget=forms.HiddenInput(), required=True)

    class Meta:
        """Define fields and input types."""

        fields = [
            "score",
            "progress",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "score": forms.NumberInput(
                attrs={"min": 0, "max": 10, "step": 0.1, "placeholder": "0-10"},
            ),
            "progress": forms.NumberInput(attrs={"min": 0}),
            "start_date": forms.DateTimeInput(attrs={"type": "datetime-local"})
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateTimeInput(attrs={"type": "datetime-local"})
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"placeholder": "Add any notes or comments...", "rows": "5"},
            ),
        }


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

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Game
        fields = [
            "score",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]


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


class DiaryEntryForm(forms.ModelForm):
    """Form for creating and editing diary entries."""
    
    tags = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "eg. netflix",
                "id": "tags-input",
            }
        ),
        help_text="Press Tab to complete, Enter to create. Separate multiple tags with commas."
    )
    
    class Meta:
        """Bind form to model."""
        model = DiaryEntry
        fields = ["consumed_at", "rating", "review", "tags"]
        widgets = {
            "consumed_at": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "form-control",
                }
            ) if settings.TRACK_TIME else forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                }
            ),
            "rating": forms.NumberInput(
                attrs={
                    "min": 0,
                    "max": 10,
                    "step": 0.5,
                    "class": "form-control",
                    "placeholder": "0-10",
                }
            ),
            "review": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 5,
                    "placeholder": "Write your thoughts...",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        self.user = kwargs.pop("user", None)
        self.item = kwargs.pop("item", None)
        super().__init__(*args, **kwargs)
        
        if not settings.TRACK_TIME and "consumed_at" in self.initial:
            # If not tracking time, only show the date part
            consumed_at = self.initial["consumed_at"]
            if consumed_at and hasattr(consumed_at, 'date'):
                # Only convert if it's a datetime object
                from django.utils import timezone
                if isinstance(consumed_at, timezone.datetime):
                    self.initial["consumed_at"] = consumed_at.date()
        
        # Initialize tags field with existing tags
        if self.instance and self.instance.pk:
            existing_tags = self.instance.tags.all()
            self.initial["tags"] = ", ".join([tag.name for tag in existing_tags])

    def clean_tags(self):
        """Clean and validate tags."""
        tags_data = self.cleaned_data.get('tags', '')
        if not tags_data:
            return []
        
        # Split by comma and clean each tag
        tag_names = [tag.strip().lower() for tag in tags_data.split(',') if tag.strip()]
        
        # Remove duplicates while preserving order
        seen = set()
        unique_tags = []
        for tag in tag_names:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)
        
        # Validate tag length
        for tag in unique_tags:
            if len(tag) > 100:
                raise forms.ValidationError(f"Tag '{tag}' is too long. Maximum length is 100 characters.")
            if len(tag) < 1:
                raise forms.ValidationError("Tags cannot be empty.")
        
        return unique_tags

    def clean(self):
        """Validate the form data."""
        cleaned_data = super().clean()
        consumed_at = cleaned_data.get("consumed_at")
        
        if consumed_at and self.user and self.item:
            # Check if there's already an entry for this item on this day
            existing = DiaryEntry.objects.filter(
                user=self.user,
                item=self.item,
                consumed_at__date=consumed_at.date(),
            )
            
            if self.instance:
                existing = existing.exclude(pk=self.instance.pk)
                
            if existing.exists():
                self.add_error(
                    "consumed_at",
                    "You already have a diary entry for this item on this date."
                )
        
        return cleaned_data


class QuickConsumeForm(forms.Form):
    """Hidden form for marking media as consumed."""
    
    # No visible fields, just CSRF protection
    pass


class BookProgressForm(forms.Form):
    """Form for tracking book reading progress."""

    PROGRESS_TYPE_CHOICES = [
        ('pages', 'Pages'),
        ('percentage', 'Percentage'),
    ]

    progress_type = forms.ChoiceField(
        choices=PROGRESS_TYPE_CHOICES,
        widget=forms.RadioSelect,
        initial='pages'
    )
    progress_value = forms.IntegerField(
        min_value=0,
        help_text="Enter pages read or percentage (0-100)"
    )

    def clean_progress_value(self):
        """Validate progress value based on type."""
        progress_type = self.cleaned_data.get('progress_type')
        progress_value = self.cleaned_data.get('progress_value')

        if progress_type == 'percentage' and progress_value > 100:
            raise forms.ValidationError("Percentage cannot exceed 100%")

        return progress_value


class BookLogForm(forms.Form):
    """Form for logging a completed book."""
    
    score = forms.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=0,
        max_value=10,
        required=False,
        help_text="Rate the book (0-10)"
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        help_text="Optional review or notes"
    )
    end_date = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        required=False,
        help_text="When did you finish reading? (optional)"
    )


class BookStartReadingForm(forms.Form):
    """Form for starting to read a book."""
    
    start_date = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        required=False,
        help_text="When did you start reading? (optional)"
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        help_text="Optional notes"
    )
