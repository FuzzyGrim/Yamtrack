from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.conf import settings

from app.utils import database, helpers, search, metadata
from app.utils.imports import anilist, mal, tmdb
from app.models import Media, Season, Episode
from app.forms import (
    UserLoginForm,
    UserRegisterForm,
    UserUpdateForm,
    PasswordChangeForm,
)

import logging

logger = logging.getLogger(__name__)


@login_required
def home(request):
    if request.method == "POST":
        database.media_form_handler(request)
        return redirect("home")

    media_list = (
        Media.objects.filter(user_id=request.user, status__in=["Watching"])
        .order_by("media_type", "title")
        .prefetch_related("seasons")
    )

    # Create a dictionary to group the results by media_type and status
    media_dict = {}
    for media in media_list:
        key = f"{media.media_type}_{media.status}"
        if key not in media_dict:
            if media.media_type == "tv":
                list_title = "TV in Progress"
            else:
                list_title = f"{media.media_type.capitalize()} in Progress"
            media_dict[key] = {
                "list_title": list_title,
                "media_list": [],
            }

        # template will show the season that is being watched
        if media.seasons.exists():
            for season in media.seasons.all():
                if season.status == "Watching":
                    media.season_number = season.number
                    media.progress = season.progress
                    media.image = season.image

        media_dict[key]["media_list"].append(media)

    context = {
        "media_dict": media_dict,
        "page": "home",
    }
    return render(request, "app/home.html", context)


@login_required
def media_list(request, media_type, status=None):
    if request.method == "POST":
        database.media_form_handler(request)

        if status:
            return redirect("medialist", media_type=media_type, status=status)
        else:
            return redirect("medialist", media_type=media_type)

    if status:
        media_list = Media.objects.filter(
            user_id=request.user, media_type=media_type, status=status.capitalize()
        )
    else:
        media_list = Media.objects.filter(user_id=request.user, media_type=media_type)

    return render(
        request,
        "app/medialist.html",
        {
            "media_type": media_type,
            "media_list": media_list,
            "statuses": [
                "All",
                "Completed",
                "Watching",
                "Paused",
                "Dropped",
                "Planning",
            ],
            "page": f"{media_type}s {status.capitalize()}" if status else f"{media_type}s"
        },
    )


@login_required
def media_search(request):
    media_type = request.GET.get("media_type")
    query = request.GET.get("q")

    if media_type and query:
        # update user default search type
        request.user.last_search_type = media_type
        request.user.save()

        if request.method == "POST":
            database.media_form_handler(request)
            return redirect("/search?media_type=" + media_type + "&q=" + query)

        if media_type == "anime" or media_type == "manga":
            query_list = search.mal(media_type, query)
        elif media_type == "tv" or media_type == "movie":
            query_list = search.tmdb(media_type, query)

        context = {
            "query_list": query_list,
            "page": "search",
        }

    else:
        context = {
            "page": "search",
        }

    return render(request, "app/search.html", context)


@login_required
def media_details(request, media_type, media_id, title):
    if request.method == "POST":
        database.media_form_handler(request)
        return redirect("media_details", media_type, media_id, title)

    media = metadata.get_media_metadata(media_type, media_id)

    related_data_list = [
        {"name": "Related Animes", "data": media.get("related_anime")},
        {"name": "Related Mangas", "data": media.get("related_manga")},
        {"name": "Recommendations", "data": media.get("recommendations")},
    ]

    context = {
        "media": media,
        "seasons": media.get("seasons"),
        "related_data_list": related_data_list,
        "page": title,
    }
    return render(request, "app/media_details.html", context)


@login_required
def season_details(request, media_id, title, season_number):
    if request.method == "POST":
        database.media_form_handler(request)
        return redirect("season_details", media_id, title, season_number)

    tv = metadata.tv(media_id)
    season = metadata.season(media_id, season_number)

    episodes_db = Episode.objects.filter(
        season__parent__media_id=media_id,
        season__parent__media_type="tv",
        season__parent__user=request.user,
        season__number=season_number,
    ).values("number", "watched")

    # Convert the QuerySet to a dictionary for easier lookup
    episodes_dict = {episode["number"]: episode["watched"] for episode in episodes_db}

    # Set the watched status for each episode and default to False
    for episode in season["episodes"]:
        episode["watched"] = episodes_dict.get(episode["episode_number"], False)

    context = {
        "media_id": media_id,
        "media_title": tv["title"],
        "season": season,
        "page": f"{tv['title']} - Season {season_number}",
    }
    return render(request, "app/season_details.html", context)


def register(request):
    form = UserRegisterForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        form.save()
        messages.success(request, "Your account has been created, you can now log in!")
        logger.info(
            f"New user registered: {form.cleaned_data.get('username')} at {helpers.get_client_ip(request)}"
        )
        return redirect("login")
    return render(request, "app/register.html", {"form": form, "page": "register"})


class UpdatedLoginView(LoginView):
    form_class = UserLoginForm
    template_name = "app/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page"] = "login"
        return context

    def form_valid(self, form):
        remember_me = form.cleaned_data["remember_me"]
        if remember_me:
            self.request.session.set_expiry(2592000)  # 30 days
            self.request.session.modified = True

        logger.info(
            f"User logged in as: {self.request.POST['username']} at {helpers.get_client_ip(self.request)}"
        )
        return super(UpdatedLoginView, self).form_valid(form)

    def form_invalid(self, form):
        messages.error(
            self.request,
            "Please enter a correct username and password. Note that both fields are case-sensitive.",
        )
        logger.error(
            f"Failed login attempt for: {self.request.POST['username']} at {helpers.get_client_ip(self.request)}"
        )
        return super(UpdatedLoginView, self).form_invalid(form)


@login_required
def profile(request):
    user_form = UserUpdateForm(instance=request.user)
    password_form = PasswordChangeForm(request.user)

    if request.method == "POST":
        if "username" in request.POST:
            # it's here because UserUpdateForm updates request.user.username
            old_username = request.user.username
            user_form = UserUpdateForm(request.POST, instance=request.user)

            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Your username has been updated!")
                logger.info(
                    f"Successful username change from {old_username} to {request.user.username}"
                )

        elif "new_password1" in request.POST:
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                password = password_form.save()
                update_session_auth_hash(request, password)
                messages.success(request, "Your password has been updated!")
                logger.info(f"Successful password change for: {request.user.username}")

        elif "mal" in request.POST:
            if mal.import_myanimelist(request.POST["mal"], request.user):
                messages.success(request, "Your MyAnimeList has been imported!")
            else:
                messages.error(
                    request, f"User {request.POST['mal']} not found in MyAnimeList."
                )

        elif "tmdb" in request.POST:
            auth_url = tmdb.auth_url()
            return redirect(f"{auth_url}?redirect_to={request.build_absolute_uri()}")

        elif "anilist" in request.POST:
            error = anilist.import_anilist(request.POST["anilist"], request.user)

            if not error:
                messages.success(request, "Your AniList has been imported!")
            elif error == "User not found":
                messages.error(
                    request, f"User {request.POST['anilist']} not found in Anilist."
                )
            else:
                title = "Couldn't find a matching MAL ID for: \n"
                messages.error(request, title + error)

        else:
            messages.error(request, "There was an error with your request")

    # After TMDB authentication
    if "request_token" in request.GET:
        if request.GET["approved"]:
            tmdb.import_tmdb(request.user, request.GET["request_token"])
            messages.success(request, "Your TMDB has been imported!")
            # To avoid resubmitting by clearing get parameters
            return redirect("profile")

    context = {
        "user_form": user_form,
        "password_form": password_form,
        "page": "profile",
    }
    return render(request, "app/profile.html", context)


def modal_data(request):
    media_type = request.GET.get("media_type")
    media_id = request.GET.get("media_id")

    media_filter = Media.objects.filter(
        media_type=media_type, media_id=media_id, user=request.user.id
    ).values("id", "score", "status", "progress", "start_date", "end_date", "notes")

    if media_filter:
        media = media_filter[0]
    else:
        return JsonResponse({})

    # if selected media is a season, return season stats
    season_number = request.GET.get("season_number")
    if season_number is None:
        return JsonResponse(media)
    else:
        season = Season.objects.filter(
            parent_id=media["id"], number=season_number
        ).values("score", "status", "progress", "start_date", "end_date", "notes")
        if season:
            return JsonResponse(season[0])
        else:
            return JsonResponse({})


@login_required
def progress_edit(request):
    media_type = request.POST.get("media_type")
    media_id = request.POST.get("media_id")

    media_metadata = metadata.get_media_metadata(media_type, media_id)

    max_progress = media_metadata.get("num_episodes", 1)

    media = Media.objects.get(
        media_type=media_type, media_id=media_id, user=request.user.id
    )

    operation = request.POST.get("operation")

    media.progress, media.status = database.update_progress_status(
        operation, media.progress, max_progress, media.status
    )
    media.save()

    season_number = request.POST.get("season_number")
    if season_number is not None:
        season_number = int(season_number)

        selected_season_metadata = metadata.get_season_metadata(
            season_number, media_metadata.get("seasons")
        )

        if "episode_count" in selected_season_metadata:
            max_progress = selected_season_metadata["episode_count"]

        season = Season.objects.get(parent_id=media.id, number=season_number)

        season.progress, season.status = database.update_progress_status(
            operation, season.progress, max_progress, season.status
        )
        season.save()

        response = {"progress": season.progress}

    response = {"progress": media.progress}

    response["min"] = response["progress"] == 0
    response["max"] = response["progress"] == max_progress

    return JsonResponse(response)


def redirect_after_login(request):
    next = request.GET.get("next", None)
    if next is None:
        return redirect(settings.LOGIN_REDIRECT_URL)
    elif not url_has_allowed_host_and_scheme(
        url=next,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(settings.LOGIN_REDIRECT_URL)
    else:
        return redirect(next)


def error_view(request, exception=None, status_code=None):
    return render(
        request,
        "app/error.html",
        {"status_code": status_code},
        status=status_code,
    )
