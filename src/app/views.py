from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.middleware import csrf
from crispy_forms.utils import render_crispy_form

from app.utils import helpers, search, metadata, form_handlers
from app.utils.imports import anilist, mal, tmdb
from app.models import TV, Season, Episode, Anime, Manga
from app.forms import (
    UserLoginForm,
    UserRegisterForm,
    UserUpdateForm,
    PasswordChangeForm,
)

from datetime import date
import logging
from itertools import chain

logger = logging.getLogger(__name__)


@login_required
def home(request):
    watching = {}

    seasons = Season.objects.filter(user_id=request.user, status="Watching")
    if seasons.exists():
        watching["season"] = seasons

    animes = Anime.objects.filter(user_id=request.user, status="Watching")
    if animes.exists():
        watching["anime"] = animes

    mangas = Manga.objects.filter(user_id=request.user, status="Watching")
    if mangas.exists():
        watching["manga"] = mangas

    context = {
        "watching": watching,
        "page": "home",
    }
    return render(request, "app/home.html", context)


@login_required
def media_list(request, media_type):
    media_mapping = helpers.media_type_mapper(media_type)
    if request.method == "POST":
        media_id = request.POST.get("media_id")
        form_media_type = request.POST.get("media_type")
        media_metadata = metadata.get_media_metadata(media_type, media_id)

        form_handlers.media_form_handler(
            request,
            media_id,
            media_metadata["title"],
            media_metadata["image"],
            form_media_type,
        )
        return redirect("medialist", media_type=media_type)

    if media_type == "tv":
        # show both tv and seasons in the same list
        tv_list = TV.objects.filter(user_id=request.user)
        season_list = Season.objects.filter(user_id=request.user)

        media_list = sorted(
            chain(tv_list, season_list),
            key=lambda item: item.score if item.score is not None else float("-inf"),
            reverse=True,
        )
    else:
        media_list = media_mapping["model"].objects.filter(user_id=request.user)

    return render(
        request,
        media_mapping["list_layout"],
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
            "page": f"{media_type}s",
        },
    )


@login_required
def media_list_status(request, media_type, status):
    media_mapping = helpers.media_type_mapper(media_type)

    if request.method == "POST":
        media_id = request.POST.get("media_id")
        form_media_type = request.POST.get("media_type")
        media_metadata = metadata.get_media_metadata(media_type, media_id)

        form_handlers.media_form_handler(
            request,
            media_id,
            media_metadata["title"],
            media_metadata["image"],
            form_media_type,
        )

        return redirect("medialist", media_type=media_type, status=status)

    if media_type == "tv":
        # as tv doesn't have a status field, only filter seasons
        media_list = Season.objects.filter(
            user_id=request.user, status=status.capitalize()
        )
    else:
        media_list = media_mapping["model"].objects.filter(
            user_id=request.user, status=status.capitalize()
        )

    return render(
        request,
        media_mapping["list_layout"],
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
            "page": f"{media_type}s {status.capitalize()}",
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
            media_id = request.POST.get("media_id")
            media_metadata = metadata.get_media_metadata(media_type, media_id)

            form_handlers.media_form_handler(
                request,
                media_id,
                media_metadata["title"],
                media_metadata["image"],
                media_type,
            )
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
    media_metadata = metadata.get_media_metadata(media_type, media_id)

    if request.method == "POST":
        form_media_type = request.POST.get("media_type")

        form_handlers.media_form_handler(
            request,
            media_id,
            media_metadata["title"],
            media_metadata["image"],
            form_media_type,
        )

        return redirect("media_details", media_type, media_id, title)

    related_data_list = [
        {"name": "Related Animes", "data": media_metadata.get("related_anime")},
        {"name": "Related Mangas", "data": media_metadata.get("related_manga")},
        {"name": "Recommendations", "data": media_metadata.get("recommendations")},
    ]

    context = {
        "media": media_metadata,
        "seasons": media_metadata.get("seasons"),
        "related_data_list": related_data_list,
        "page": title,
    }
    return render(request, "app/media_details.html", context)


@login_required
def season_details(request, media_id, title, season_number):
    season_metadata = metadata.season(media_id, season_number)
    tv_metadata = metadata.tv(media_id)

    if request.method == "POST":
        form_handlers.media_form_handler(
            request,
            media_id,
            tv_metadata["title"],
            season_metadata["image"],
            "season",
            season_metadata,
            season_number,
        )

        return redirect("season_details", media_id, title, season_number)

    # returns tuple of watched episodes in database
    watched_episodes = set(
        Episode.objects.filter(
            related_season__media_id=media_id,
            related_season__season_number=season_number,
            related_season__user=request.user,
        ).values_list("episode_number", flat=True)
    )
    for episode in season_metadata["episodes"]:
        episode["watched"] = episode["episode_number"] in watched_episodes

    # set previous and next season numbers
    min_season = tv_metadata["seasons"][0]["season_number"]
    max_season = tv_metadata["seasons"][-1]["season_number"]
    if season_number > min_season:
        previous_season = season_number - 1
    else:
        previous_season = None
    if season_number < max_season:
        next_season = season_number + 1
    else:
        next_season = None

    context = {
        "media_id": media_id,
        "media_title": tv_metadata["title"],
        "season": season_metadata,
        "previous_season": previous_season,
        "next_season": next_season,
        "page": f"{tv_metadata['title']} Season {season_number}",
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


class CustomLoginView(LoginView):
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
        return super(CustomLoginView, self).form_valid(form)

    def form_invalid(self, form):
        messages.error(
            self.request,
            "Please enter a correct username and password. Note that both fields are case-sensitive.",
        )
        logger.error(
            f"Failed login attempt for: {self.request.POST['username']} at {helpers.get_client_ip(self.request)}"
        )
        return super(CustomLoginView, self).form_invalid(form)


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
    media_mapping = helpers.media_type_mapper(media_type)

    if media_type == "season":
        season_number = request.GET.get("season_number")
        # set up filters to retrieve the appropriate media object
        filters = {
            "media_id": media_id,
            "season_number": season_number,
            "user": request.user,
        }
        initial_data = {
            "media_id": media_id,
            "media_type": media_type,
            "season_number": season_number,
        }
        form_id = f"form-{media_type}_{media_id}_{season_number}"
    else:
        filters = {"media_id": media_id, "user": request.user}
        initial_data = {"media_id": media_id, "media_type": media_type}
        form_id = f"form-{media_type}_{media_id}"

    try:
        # try to retrieve the media object using the filters
        media = media_mapping["model"].objects.get(**filters)
        form = media_mapping["form"](instance=media, initial=initial_data)
        allow_delete = True
    except media_mapping["model"].DoesNotExist:
        form = media_mapping["form"](initial=initial_data)
        allow_delete = False

    # render form as HTML
    form_html = render_crispy_form(
        form, context={"csrf_token": csrf.get_token(request)}
    )
    # set the form's ID
    form_html = form_html.replace("<form", f'<form id="{form_id}"')

    return JsonResponse({"html": form_html, "allow_delete": allow_delete})


@login_required
def progress_edit(request):
    media_type = request.POST.get("media_type")
    media_id = request.POST.get("media_id")
    operation = request.POST.get("operation")

    if media_type == "season":
        season_number = request.POST.get("season_number")
        # season_number = int(season_number)
        season_metadata = metadata.season(media_id, season_number)
        max_progress = len(season_metadata["episodes"])

        season = Season.objects.get(media_id=media_id, season_number=season_number)

        # save episode progress
        if operation == "increase":
            # next episode = current progress + 1, but 0-indexed so -1
            episode_number = season_metadata["episodes"][season.progress][
                "episode_number"
            ]
            Episode.objects.create(
                related_season=season, episode_number=episode_number, watch_date=date.today()
            )
        elif operation == "decrease":
            episode_number = season_metadata["episodes"][season.progress - 1][
                "episode_number"
            ]
            Episode.objects.get(
                related_season=season, episode_number=episode_number
            ).delete()

        # change status to completed if progress is max
        if season.progress == max_progress:
            season.status = "Completed"
            season.save()

        response = {"progress": season.progress}

    else:
        media_mapping = helpers.media_type_mapper(media_type)
        media_metadata = metadata.get_media_metadata(media_type, media_id)

        max_progress = media_metadata.get("num_episodes", 1)

        media = media_mapping["model"].objects.get(
            media_id=media_id, user=request.user.id
        )
        if operation == "increase":
            media.progress += 1
        elif operation == "decrease":
            media.progress -= 1

        # before saving, if progress is max, set status to completed
        if media.progress == max_progress:
            media.status = "Completed"
        media.save()
        response = {"progress": media.progress}

    response["min"] = response["progress"] == 0
    response["max"] = response["progress"] == max_progress

    return JsonResponse(response)


def error_view(request, exception=None, status_code=None):
    return render(
        request,
        "app/error.html",
        {"status_code": status_code},
        status=status_code,
    )
