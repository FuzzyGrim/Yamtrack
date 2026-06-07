import math

from django import forms
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.validators import FileExtensionValidator, URLValidator
from django.db.models import Q

from app import config
from app.models import (
    PERCENT_COMPLETE,
    TV,
    Anime,
    BoardGame,
    Book,
    BookProgressUnits,
    Comic,
    Episode,
    Experience,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Tag,
)

url_validator = URLValidator()
ALLOWED_MANUAL_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def validate_manual_item_uploaded_image(uploaded_file):
    """Validate manual item image content type and size."""
    if uploaded_file.size > settings.MANUAL_ITEM_IMAGE_MAX_SIZE:
        max_mb = settings.MANUAL_ITEM_IMAGE_MAX_SIZE // (1024 * 1024)
        msg = f"Image file must be {max_mb} MB or smaller."
        raise forms.ValidationError(msg)

    content_type = getattr(uploaded_file, "content_type", "")
    if content_type and content_type not in ALLOWED_MANUAL_IMAGE_CONTENT_TYPES:
        msg = "Upload a JPG, PNG, or WebP image."
        raise forms.ValidationError(msg)


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

    uploaded_image = forms.ImageField(
        required=False,
        label="Upload local image",
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"]),
            validate_manual_item_uploaded_image,
        ],
    )

    class Meta:
        """Bind form to model."""

        model = Item
        fields = [
            "media_type",
            "title",
            "image",
            "uploaded_image",
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

    uploaded_image = forms.ImageField(
        required=False,
        label="Replace local image",
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"]),
            validate_manual_item_uploaded_image,
        ],
    )
    clear_uploaded_image = forms.BooleanField(
        required=False,
        label="Remove local image",
    )
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

    def __init__(self, *args, **kwargs):
        """Initialize form and keep submitted custom links for processing."""
        self.custom_link_entries = kwargs.pop("custom_link_entries", None)
        self.tag_names = kwargs.pop("tag_names", None)
        super().__init__(*args, **kwargs)
        item = getattr(self.instance, "item", None)
        if not item or item.source != Sources.MANUAL.value:
            self.fields.pop("uploaded_image", None)
            self.fields.pop("clear_uploaded_image", None)

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
        cleaned_data["tag_names"] = self.clean_tag_names()
        return cleaned_data

    def clean_tag_names(self):
        """Validate submitted tag names and deduplicate case-insensitively."""
        if self.tag_names is None:
            return None

        cleaned = []
        seen_normalized = set()
        for raw_name in self.tag_names:
            name = (raw_name or "").strip()
            if not name:
                continue
            normalized = name.casefold()
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            cleaned.append({"name": name[:100], "normalized_name": normalized})
        return cleaned

    def _get_tv_season_sync_targets(self, instance):
        """Return TV/season sibling targets for tag synchronization."""
        media_type = instance.item.media_type
        if media_type not in [MediaTypes.TV.value, MediaTypes.SEASON.value]:
            return None

        shared_item_filters = {
            "item__media_id": instance.item.media_id,
            "item__source": instance.item.source,
            "user": instance.user,
        }
        tv_targets = TV.objects.filter(**shared_item_filters)
        season_targets = Season.objects.filter(**shared_item_filters)
        return {
            "tv_ids": list(tv_targets.values_list("id", flat=True)),
            "season_ids": list(season_targets.values_list("id", flat=True)),
        }

    def save(self, commit=True):  # noqa: FBT002
        """Save media and synchronize custom links when submitted."""
        instance = super().save(commit=commit)
        item = getattr(instance, "item", None)
        if commit and item and item.source == Sources.MANUAL.value:
            if self.cleaned_data.get("clear_uploaded_image") and item.uploaded_image:
                item.uploaded_image.delete(save=False)
                item.uploaded_image = None
            if uploaded_image := self.cleaned_data.get("uploaded_image"):
                if item.uploaded_image:
                    item.uploaded_image.delete(save=False)
                item.uploaded_image = uploaded_image
            image_changed = self.cleaned_data.get(
                "clear_uploaded_image",
            ) or self.cleaned_data.get("uploaded_image")
            if image_changed:
                item.save(update_fields=["uploaded_image"])

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

        submitted_tags = self.cleaned_data.get("tag_names")
        if commit and instance.pk and submitted_tags is not None:
            sync_targets = self._get_tv_season_sync_targets(instance)
            if sync_targets:
                target_content_filters = Q()
                if sync_targets["tv_ids"]:
                    target_content_filters |= Q(
                        content_type=ContentType.objects.get_for_model(TV),
                        object_id__in=sync_targets["tv_ids"],
                    )
                if sync_targets["season_ids"]:
                    target_content_filters |= Q(
                        content_type=ContentType.objects.get_for_model(Season),
                        object_id__in=sync_targets["season_ids"],
                    )

                if target_content_filters:
                    instance.tagged_media.model.objects.filter(
                        user=instance.user,
                    ).filter(target_content_filters).delete()
            else:
                instance.tagged_media.filter(user=instance.user).delete()

            tag_ids = []
            for entry in submitted_tags:
                tag, _ = Tag.objects.get_or_create(
                    user=instance.user,
                    normalized_name=entry["normalized_name"],
                    defaults={"name": entry["name"]},
                )
                tag_ids.append(tag.id)

            if tag_ids:
                tagged_media_to_create = []
                if sync_targets:
                    tv_content_type = ContentType.objects.get_for_model(TV)
                    season_content_type = ContentType.objects.get_for_model(Season)
                    for tag_id in tag_ids:
                        for tv_id in sync_targets["tv_ids"]:
                            tagged_media_to_create.append(
                                instance.tagged_media.model(
                                    user=instance.user,
                                    tag_id=tag_id,
                                    content_type=tv_content_type,
                                    object_id=tv_id,
                                )
                            )
                        for season_id in sync_targets["season_ids"]:
                            tagged_media_to_create.append(
                                instance.tagged_media.model(
                                    user=instance.user,
                                    tag_id=tag_id,
                                    content_type=season_content_type,
                                    object_id=season_id,
                                )
                            )
                else:
                    tagged_media_to_create = [
                        instance.tagged_media.model(
                            user=instance.user,
                            tag_id=tag_id,
                            content_object=instance,
                        )
                        for tag_id in tag_ids
                    ]

                instance.tagged_media.model.objects.bulk_create(tagged_media_to_create)
            Tag.objects.filter(user=instance.user, tagged_media__isnull=True).delete()
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
        fields = [
            "score",
            "progress",
            "progress_unit",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]
        labels = {
            "progress_unit": "Progress Unit",
        }
        widgets = {
            **MediaForm.Meta.widgets,
            "progress_unit": forms.Select(),
        }

    def clean_progress(self):
        """Clamp percent progress to a valid range."""
        progress = self.cleaned_data["progress"]
        progress_unit = self.data.get(
            self.add_prefix("progress_unit"),
            self.initial.get("progress_unit") or self.instance.progress_unit,
        )
        if progress_unit == BookProgressUnits.PERCENT.value:
            return min(progress, PERCENT_COMPLETE)
        return progress


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


class ExperienceForm(MediaForm):
    """Form for experiences."""

    location = forms.CharField(required=False, max_length=255)

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Experience
        fields = [
            "score",
            "status",
            "start_date",
            "end_date",
            "notes",
            "location",
        ]


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
