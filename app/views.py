from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, authenticate, login as auth_login
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
            edit_media(request)
            return redirect("home")

    elif "delete" in request.POST:
        Media.objects.get(media_id=request.POST["delete"], user=request.user).delete()
        return redirect("home")

    elif request.user.is_authenticated:
        queryset = Media.objects.filter(user_id=request.user)
        movies = []
        movies_status = {"completed": [], "planning": [], "watching": [], "paused": [], "dropped": []}
        tv = []
        tv_status = {"completed": [], "planning": [], "watching": [], "paused": [], "dropped": []}

        for media in queryset:
            if media.media_type == "movie":
                movies.append(media)
                movies_status[(media.status).lower()].append(media)
            elif media.media_type == "tv":
                tv.append(media)
                tv_status[(media.status).lower()].append(media)

        return render(request, "app/home.html", {"media": queryset,"movies": movies, "movies_status": movies_status, "tv": tv, "tv_status": tv_status})
        
    return render(request, "app/home.html")


def search(request, content, query):
    """Search page"""
    if "query" in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )

    elif "score" in request.POST:
        if request.user.is_authenticated:
            if Media.objects.filter(media_id=request.POST["media_id"], user=request.user).exists():
                edit_media(request)
            else:
                add_media(request)
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


def add_media(request):
    if request.POST["score"] == "":
        score = None
    else:
        score = float(request.POST["score"])

    if "season" in request.POST:
        Media.objects.create(
            media_id=request.POST["media_id"],
            title=request.POST["title"],
            image=request.POST["image"],
            media_type=request.POST["media_type"],
            seasons={request.POST["season"] : score},
            ind_score=score,
            user=request.user,
            status=request.POST["status"],
            num_seasons=request.POST["num_seasons"],
        )
    else:
        Media.objects.create(
            media_id=request.POST["media_id"],
            title=request.POST["title"],
            image=request.POST["image"],
            media_type=request.POST["media_type"],
            ind_score=score,
            user=request.user,
            status=request.POST["status"],
        )

def edit_media(request):
    if request.POST["score"] == "":
        score = None
    else:
        score = float(request.POST["score"])

    media = Media.objects.get(media_id=request.POST["media_id"], user=request.user)
    if "season" in request.POST:
        # if media didn't have seasons before, add the previous score as season 1
        if not media.seasons:
            media.seasons["1"] = media.ind_score
        media.seasons[request.POST["season"]] = score

        dict(sorted(media.seasons.items()))

        # calculate the average score and add it to the ind_score field
        total = 0
        valued_seasons = 0
        for season in media.seasons:
            if media.seasons[season] is not None:
                total += media.seasons[season]
                valued_seasons += 1
        media.ind_score = round(total / valued_seasons, 1)

    else:
        media.ind_score = score

    media.status = request.POST["status"]

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
        user = authenticate(request, username=request.POST['username'], password=request.POST['password'])
        if user is not None:
            auth_login(request, user)
            return redirect("home")
        else:
            messages.error(request, "Please enter a correct username and password. Note that both fields may be case-sensitive.")
    else:
        form = AuthenticationForm()
    
    return render(request, "app/login.html", {"form": form})
        

@login_required
def profile(request):
    if request.method == "POST":
        user_form = UserUpdateForm(request.POST, instance=request.user)
        password_form = PasswordChangeForm(request.user, request.POST)
        if user_form.is_valid() and password_form.is_valid():
            user_form.save()
            password = password_form.save()
            update_session_auth_hash(request, password)
            messages.success(request, f"Your account has been updated!")
            return redirect("profile")

    else:
        user_form = UserUpdateForm(instance=request.user)
        password_form = PasswordChangeForm(request.user)

    context = {"user_form": user_form, "password_form": password_form}

    return render(request, "app/profile.html", context)
