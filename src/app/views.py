from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, authenticate, login as auth_login
from django.contrib.auth.forms import PasswordChangeForm, AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.conf import settings
from django.template.loader import render_to_string


from app.models import Media, Season
from app.forms import UserRegisterForm, UserUpdateForm
from app.utils import database, interactions, imports


@login_required
def home(request):
    if request.method == "POST":
        if "delete" in request.POST:
            metadata = request.session.get("metadata")
            Media.objects.get(
                media_id=metadata["id"],
                user=request.user,
                api=metadata["api"],
            ).delete()
        elif "status" in request.POST:
            database.edit_media(request)

        return redirect("home")

    # get all media with tracked seasons of user
    queryset = Media.objects.filter(user_id=request.user).prefetch_related("seasons")
    data = {
        "tv": {"media": [], "statuses": {}},
        "movie": {"media": [], "statuses": {}},
        "anime": {"media": [], "statuses": {}},
        "manga": {"media": [], "statuses": {}},
    }
    for media in queryset:
        media_type = media.media_type
        data[media_type]["media"].append(media)
        status = (media.status).lower()
        if status not in data[media_type]["statuses"]:
            data[media_type]["statuses"][status] = []
        data[media_type]["statuses"][status].append(media)
    return render(request, "app/home.html", {"data": data})


@login_required
def search(request):
    api = request.GET.get("api")
    query = request.GET.get("q")
    request.session['last_selected_api'] = api
    if request.method == "POST":
        metadata = request.session.get("metadata")
        if "delete" in request.POST:
            Media.objects.get(
                media_id=metadata["id"],
                user=request.user,
                api=metadata["api"],
            ).delete()
        elif "status" in request.POST:
            if Media.objects.filter(
                media_id=metadata["id"],
                user=request.user,
                api=metadata["api"],
            ).exists():
                database.edit_media(request)
            else:
                database.add_media(request)

        return redirect("/search?api=" + api + "&q=" + query)

    query_list = interactions.search(api, query)
    context = {"query_list": query_list}
    return render(request, "app/search.html", context)


def register(request):
    form = UserRegisterForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        form.save()
        return redirect("login")
    return render(request, "app/register.html", {"form": form})


def login(request):
    form = AuthenticationForm()
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST["username"],
            password=request.POST["password"],
        )
        if user is not None:
            auth_login(request, user)
            return redirect_after_login(request)
        else:
            messages.error(
                request,
                "Please enter a correct username and password. Note that both fields may be case-sensitive.",
            )
    return render(request, "app/login.html", {"form": form})


@login_required
def profile(request):
    user_form = UserUpdateForm(request.POST or None, instance=request.user)
    password_form = PasswordChangeForm(request.user, request.POST or None)
    
    if request.method == 'POST':
        if user_form.is_valid():
            user_form.save()
            messages.success(request, "Your account has been updated!")

        elif password_form.is_valid():
            password = password_form.save()
            update_session_auth_hash(request, password)
            messages.success(request, "Your password has been updated!")

        elif request.POST.get("mal"):
            if imports.import_myanimelist(request.POST.get("mal"), request.user):
                messages.success(request, "Your MyAnimeList has been imported!")
            else:
                messages.error(request, "User not found")

        elif request.FILES.get("tmdb"):
            if imports.import_tmdb(request.FILES.get("tmdb"), request.user):
                messages.success(request, "Your TMDB list has been imported!")
            else:
                messages.error(
                    request,
                    'Error importing your list, make sure it\'s a CSV file containing the word "ratings" or "watchlist" in the name'
                )

        elif request.POST.get("anilist"):
            error = imports.import_anilist(request.POST.get("anilist"), request.user)
            if error == "":
                messages.success(request, "Your AniList has been imported!")
            elif error == "User not found":
                messages.error(request, "User not found")
            else:
                title = "Couldn't find a matching MAL ID for: \n"
                messages.error(request, title + error)
        return redirect("profile")

    context = {"user_form": user_form, "password_form": password_form}
    return render(request, "app/profile.html", context)



def edit(request, media_type, media_id):

    if media_type in ["anime", "manga"]:
        media = interactions.mal_edit(request, media_type, media_id)
    elif media_type in ["movie", "tv"]:
        media = interactions.tmdb_edit(request, media_type, media_id)

    response = media["response"]
    database = media["database"]
    
    # Save the metadata in the session to be used when form is submitted
    request.session["metadata"] = response

    data = {"title": response.get("title", None), "year": response.get("year", None),
            "media_type": response.get("media_type", None), "original_type" : response.get("original_type", None)}
    
    media['response']['media_type'] = media_type
    data["html"] = render_to_string("app/edit.html", {"media": media}, request=request)

    if "seasons" in response:
        data["seasons"] = response["seasons"]
    
    if "database" in media:
        data["in_db"] = True
        data["media_seasons"] = list(Season.objects.filter(media=database).values("number", "score", "status", "progress", "start_date", "end_date"))
        data["score"] = database.score
        data["status"] = database.status
        data["progress"] = database.progress
        data["start_date"] = database.start_date
        data["end_date"] = database.end_date
    else:
        data["in_db"] = False

    return JsonResponse(data)


def redirect_after_login(request):
    next = request.GET.get("next", None)
    if next is None:
        return redirect(settings.LOGIN_REDIRECT_URL)
    elif not url_has_allowed_host_and_scheme(
            url=next,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return redirect(settings.LOGIN_REDIRECT_URL)
    else:
        return redirect(next)