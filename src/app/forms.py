from django import forms
from django.contrib.auth.forms import (
    UserCreationForm,
    PasswordChangeForm,
    AuthenticationForm,
)
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column
from .models import TV, Season, Manga, Anime, Movie, User
from app.utils import metadata

import datetime


class UserLoginForm(AuthenticationForm):
    """
    Subclass of Django ``AuthenticationForm`` which adds a remember me
    checkbox.
    """

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_username",
                "placeholder": "Username",
            }
        ),
        label="Username",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_password1",
                "placeholder": "Password",
            }
        ),
        label="Password",
    )

    remember_me = forms.BooleanField(
        label="Remember Me",
        initial=False,
        required=False,
        widget=forms.CheckboxInput(
            attrs={"class": "checkboxinput form-check-input", "id": "id_remember_me"}
        ),
    )


class UserRegisterForm(UserCreationForm):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_username",
                "placeholder": "Username",
            }
        ),
        label="Username",
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_password1",
                "placeholder": "Password",
            }
        ),
        label="Password",
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "textinput textInput form-control",
                "required": True,
                "id": "id_password2",
                "placeholder": "Password confirmation",
            }
        ),
        label="Password confirmation",
    )

    class Meta:
        model = User
        fields = ["username", "password1", "password2"]


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username"]


# remove autofocus from password change form
class PasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)


class MediaForm(forms.ModelForm):
    media_type = forms.CharField(
        max_length=20,
        widget=forms.HiddenInput(),
    )

    def clean(self):
        cleaned_data = super().clean()
        media_id = cleaned_data.get("media_id")
        media_type = cleaned_data.get("media_type")
        progress = cleaned_data.get("progress")
        status = cleaned_data.get("status")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        # if status is changed or media is being added
        if "status" in self.changed_data or self.instance.pk is None:
            if status == "Completed":
                if not end_date:
                    cleaned_data["end_date"] = datetime.date.today()

                if isinstance(self, AnimeForm) or isinstance(self, MangaForm):
                    cleaned_data["progress"] = metadata.anime_manga(
                        media_type, media_id
                    )["num_episodes"]

            elif status == "Watching" and not start_date:
                cleaned_data["start_date"] = datetime.date.today()

        if "progress" in self.changed_data:
            total_episodes = metadata.get_media_metadata(media_type, media_id)[
                "num_episodes"
            ]

            # limit progress to total_episodes
            if progress > total_episodes:
                cleaned_data["progress"] = total_episodes

            # If progress == total_episodes and status not explicitly changed
            if progress == total_episodes and "status" not in self.changed_data:
                cleaned_data["status"] = "Completed"
                cleaned_data["end_date"] = datetime.date.today()

        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            "media_type",
            Row(
                Column("score", css_class="form-group col-md-6 pe-1"),
                Column("progress", css_class="form-group col-md-6 ps-1"),
                css_class="form-row",
            ),
            "status",
            Row(
                Column("start_date", css_class="form-group col-md-6 pe-1"),
                Column("end_date", css_class="form-group col-md-6 ps-1"),
                css_class="form-row",
            ),
            "notes",
        )

    class Meta:
        fields = [
            "media_id",
            "media_type",
            "score",
            "progress",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "media_id": forms.HiddenInput(),
            "score": forms.NumberInput(attrs={"min": 0, "max": 10, "step": 0.1}),
            "progress": forms.NumberInput(attrs={"min": 0}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


class MangaForm(MediaForm):
    class Meta(MediaForm.Meta):
        model = Manga


class AnimeForm(MediaForm):
    class Meta(MediaForm.Meta):
        model = Anime


class MovieForm(MediaForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        # movies don"t have progress, score will fill whole row
        self.helper.layout = Layout(
            "media_id",
            "media_type",
            "score",
            "status",
            "end_date",
            "notes",
        )

    class Meta(MediaForm.Meta):
        model = Movie
        exclude = ("progress", "start_date")


class TVForm(MediaForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            "media_id",
            "media_type",
            "score",
            "notes",
        )

    class Meta(MediaForm.Meta):
        model = TV
        exclude = ("progress", "status", "start_date", "end_date")


class SeasonForm(MediaForm):
    season_number = forms.IntegerField(
        min_value=0,
        step_size=1,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
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
        model = Season
        exclude = ("progress", "start_date", "end_date")
