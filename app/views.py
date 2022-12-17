from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import (
    update_session_auth_hash,
    authenticate,
    login as auth_login,
)
from django.contrib.auth.forms import PasswordChangeForm, AuthenticationForm
from django.contrib.auth.decorators import login_required
from app.forms import UserRegisterForm, UserUpdateForm
from app.models import Media
from app.utils import api

def home(request):
    """Home page"""
    if ("query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )

    elif "score" in request.POST:
        if request.user.is_authenticated:
            edit_media(
                request.POST["media_id"],
                request.POST.get("season"),
                request.POST["score"],
                request.user,
                request.POST["status"],
                request.POST.get("num_seasons"),
                request.POST["api_origin"],
            )
            return redirect("home")

    elif "delete" in request.POST:
        Media.objects.get(
            media_id=request.POST["delete"],
            user=request.user,
            api_origin=request.POST["api_origin"],
        ).delete()
        return redirect("home")

    elif request.user.is_authenticated:
        queryset = Media.objects.filter(user_id=request.user)
        movies = []
        movies_status = {
            "completed": [],
            "planning": [],
            "watching": [],
            "paused": [],
            "dropped": [],
        }
        tv = []
        tv_status = {
            "completed": [],
            "planning": [],
            "watching": [],
            "paused": [],
            "dropped": [],
        }
        anime = []
        anime_status = {
            "completed": [],
            "planning": [],
            "watching": [],
            "paused": [],
            "dropped": [],
        }
        manga = []
        manga_status = {
            "completed": [],
            "planning": [],
            "watching": [],
            "paused": [],
            "dropped": [],
        }

        for media in queryset:
            if media.api_origin == "tmdb":
                if media.media_type == "movie":
                    movies.append(media)
                    movies_status[(media.status).lower()].append(media)

                else:  # media.media_type == "tv"
                    tv.append(media)
                    tv_status[(media.status).lower()].append(media)
            else:  # mal
                if (
                    media.media_type == "anime"
                    or media.media_type == "movie"
                    or media.media_type == "special"
                    or media.media_type == "ova"
                ):
                    anime.append(media)
                    anime_status[(media.status).lower()].append(media)
                else:  # media.media_type == "manga" or media.media_type == "light_novel" or media.media_type == "one_shot"
                    manga.append(media)
                    manga_status[(media.status).lower()].append(media)

        return render(
            request,
            "app/home.html",
            {
                "media": queryset,
                "movies": movies,
                "movies_status": movies_status,
                "tv": tv,
                "tv_status": tv_status,
                "anime": anime,
                "anime_status": anime_status,
                "manga": manga,
                "manga_status": manga_status,
            },
        )

    return render(request, "app/home.html")


def search(request, content, query):
    """Search page"""
    if "query" in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )

    elif "score" in request.POST:
        if request.user.is_authenticated:
            if Media.objects.filter(
                media_id=request.POST["media_id"],
                user=request.user,
                api_origin=request.POST["api_origin"],
            ).exists():
                edit_media(
                    request.POST["media_id"],
                    request.POST.get("season"),
                    request.POST["score"],
                    request.user,
                    request.POST["status"],
                    request.POST.get("num_seasons"),
                    request.POST["api_origin"],
                )
            else:
                add_media(
                    request.POST["media_id"],
                    request.POST["title"],
                    request.POST["image"],
                    request.POST["media_type"],
                    request.POST.get("season"),
                    request.POST["score"],
                    request.user,
                    request.POST["status"],
                    request.POST.get("num_seasons"),
                    request.POST["api_origin"],
                )
        else:
            messages.error(request, "Please log in to track media to your account.")
            return redirect("login")

        return redirect("/search/" + content + "/" + query + "/")

    query_list = api.search(content, query)
    context = {"query_list": query_list}

    if content == "tmdb":
        return render(request, "app/search_tmdb.html", context)
    else:
        return render(request, "app/search_mal.html", context)


def add_media(
    media_id,
    title,
    image,
    media_type,
    season,
    score,
    user,
    status,
    num_seasons,
    api_origin,
):
    if score == "":
        score = None
    else:
        score = float(score)

    if season:
        Media.objects.create(
            media_id=media_id,
            title=title,
            image=image,
            media_type=media_type,
            seasons={season: score},
            score=score,
            user=user,
            status=status,
            num_seasons=num_seasons,
            api_origin=api_origin,
        )
    else:
        Media.objects.create(
            media_id=media_id,
            title=title,
            image=image,
            media_type=media_type,
            score=score,
            user=user,
            status=status,
            api_origin=api_origin,
        )


def edit_media(media_id, season, score, user, status, num_seasons, api_origin):
    if score == "":
        score = None
    else:
        score = float(score)

    media = Media.objects.get(
        media_id=media_id,
        user=user,
        api_origin=api_origin,
    )
    if season:
        # if media didn't have seasons before, add the previous score as season 1
        if not media.seasons:
            media.seasons["1"] = media.score
        media.seasons[season] = score
        dict(sorted(media.seasons.items()))

        # calculate the average score
        total = 0
        valued_seasons = 0
        for season in media.seasons:
            if media.seasons[season] is not None:
                total += media.seasons[season]
                valued_seasons += 1
        if valued_seasons != 0:
            media.score = round(total / valued_seasons, 1)

        media.num_seasons = num_seasons

    else:
        media.score = score

    media.status = status

    media.save()


def register(request):
    if ("query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )
    elif "username" in request.POST:
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("login")
    else:
        form = UserRegisterForm()
    return render(request, "app/register.html", {"form": form})


def login(request):
    if ("query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )

    elif "username" in request.POST:
        form = AuthenticationForm()
        user = authenticate(
            request,
            username=request.POST["username"],
            password=request.POST["password"],
        )
        if user is not None:
            auth_login(request, user)
            return redirect("home")
        else:
            messages.error(
                request,
                "Please enter a correct username and password. Note that both fields may be case-sensitive.",
            )
    else:
        form = AuthenticationForm()

    return render(request, "app/login.html", {"form": form})


@login_required
def profile(request):
    if "username" in request.POST:
        user_form = UserUpdateForm(request.POST, instance=request.user)
        password_form = PasswordChangeForm(request.user, request.POST)
        if user_form.is_valid() and password_form.is_valid():
            user_form.save()
            password = password_form.save()
            update_session_auth_hash(request, password)
            messages.success(request, f"Your account has been updated!")
            return redirect("profile")

    elif request.POST.get("mal") and request.POST.get("mal-btn"):
        if api.import_myanimelist(request.POST.get("mal"), request.user):
            messages.success(request, f"Your MyAnimeList has been imported!")
        else:
            messages.error(
                request, "User not found",
            )
        return redirect("profile")

    else:
        user_form = UserUpdateForm(instance=request.user)
        password_form = PasswordChangeForm(request.user)

    context = {"user_form": user_form, "password_form": password_form}

    return render(request, "app/profile.html", context)
