from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row
from django import forms

from .models import TV, Anime, Episode, Game, Manga, Movie, Season


def get_form_class(media_type):
    """Return the form class for the media type."""
    form_classes = {
        "manga": MangaForm,
        "anime": AnimeForm,
        "movie": MovieForm,
        "tv": TVForm,
        "season": SeasonForm,
        "episode": EpisodeForm,
        "game": GameForm,
    }

    return form_classes[media_type]


class CustomDurationField(forms.CharField):
    """Custom form field for duration input."""

    def clean(self, value):
        """Validate the time string."""
        cleaned_value = super().clean(value)
        msg = "Invalid time format. Please use hh:mm or [n]h [n]min format."
        if not cleaned_value:
            return 0
        try:
            if ":" in cleaned_value:  # hh:mm format
                hours, minutes = map(int, cleaned_value.split(":"))
            elif " " in cleaned_value:  # [n]h [n]min format
                hours, minutes = cleaned_value.split(" ")
                hours = int(hours.strip("h"))
                minutes = int(minutes.strip("min"))
            elif "min" in cleaned_value:  # [n]min format
                hours = 0
                minutes = int(cleaned_value.strip("min"))
            else:
                raise forms.ValidationError(msg)
        # error parsing the time string
        except ValueError as error:
            raise forms.ValidationError(msg) from error
        else:
            max_min = 59
            if not (0 <= minutes <= max_min):
                msg = f"Minutes must be between 0 and {max_min}."
                raise forms.ValidationError(msg)
            return hours * 60 + minutes


class MediaForm(forms.ModelForm):
    """Base form for all media types."""

    media_type = forms.CharField(
        max_length=20,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            "media_type",
            Row(
                Column("score", css_class="form-group col-md-6 pe-md-1"),
                Column("progress", css_class="form-group col-md-6 ps-md-1"),
                css_class="form-row",
            ),
            Row(
                Column("status", css_class="form-group col-md-6 pe-md-1"),
                Column("revisits", css_class="form-group col-md-6 ps-md-1"),
                css_class="form-row",
            ),
            Row(
                Column("start_date", css_class="form-group col-md-6 pe-md-1"),
                Column("end_date", css_class="form-group col-md-6 ps-md-1"),
                css_class="form-row",
            ),
            "notes",
        )

    class Meta:
        """Define fields and input types."""

        fields = [
            "media_id",
            "media_type",
            "score",
            "progress",
            "status",
            "revisits",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "media_id": forms.HiddenInput(),
            "score": forms.NumberInput(attrs={"min": 0, "max": 10, "step": 0.1}),
            "progress": forms.NumberInput(attrs={"min": 0}),
            "revisits": forms.NumberInput(attrs={"min": 0}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "revisits": "Rewatches",
        }


class MangaForm(MediaForm):
    """Form for manga."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Manga
        labels = {
            "revisits": "Rereads",
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


class TVForm(MediaForm):
    """Form for TV shows."""

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            "media_type",
            "score",
            "status",
            "notes",
        )

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = TV
        fields = ["media_id", "media_type", "score", "status", "notes"]


class SeasonForm(MediaForm):
    """Form for seasons."""

    media_id = forms.IntegerField(widget=forms.HiddenInput())

    season_number = forms.IntegerField(
        min_value=0,
        step_size=1,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            "media_type",
            "season_number",
            "score",
            "status",
            "notes",
        )

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Season
        fields = ["media_id", "media_type", "season_number", "score", "status", "notes"]


class EpisodeForm(forms.ModelForm):
    """Form for episodes."""

    class Meta:
        """Bind form to model."""

        model = Episode
        fields = ("episode_number", "watch_date")


class GameForm(MediaForm):
    """Form for manga."""

    progress = CustomDurationField(
        required=False,
        label="Progress (hh:mm)",
        widget=forms.TextInput(attrs={"placeholder": "hh:mm"}),
    )

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Game
        labels = {
            "revisits": "Replays",
        }


class FilterForm(forms.Form):
    """Form for filtering media on media list view."""

    status = forms.ChoiceField(
        choices=[
            # left side in lower case for better looking url when filtering
            ("all", "All"),
            ("completed", "Completed"),
            ("in progress", "In progress"),
            ("planning", "Planning"),
            ("paused", "Paused"),
            ("dropped", "Dropped"),
        ],
    )

    sort = forms.ChoiceField(
        choices=[
            ("score", "Score"),
            ("title", "Title"),
            ("progress", "Progress"),
            ("revisits", "Revisits"),
            ("start_date", "Start Date"),
            ("end_date", "End Date"),
        ],
    )

    layout = forms.ChoiceField(
        choices=[
            ("grid", "Grid"),
            ("table", "Table"),
        ],
    )

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        layout = kwargs.pop("layout")

        super().__init__(*args, **kwargs)

        self.fields["layout"].initial = layout
