from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, authenticate, login as auth_login
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm, AuthenticationForm
from django.contrib.auth.decorators import login_required, user_passes_test
from app.forms import UserUpdateForm
from app.models import Media
from app.utils import api


def home(request):
    """Home page"""
    if ("query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )

    return render(request, "app/home.html")


def search(request, content, query):
    """Search page"""
    if "query" in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )
    # elif ("season" and "number" and "title" and "description" and "image" and "year" and "media_id") in request.POST:

    elif "score" in request.POST:
        if request.user.is_authenticated:
            add_media(request)
        else:
            messages.error(request, "Please log in to add media to your list.")

        return redirect("/search/" + content + "/" + query + "/")

    
    query_list = api.search(content, query)


    context = {"query_list": query_list}

    return render(request, "app/search.html", context)


@login_required
def add_media(request):
    if "year" not in request.POST:
        year = 0
    else:
        year = request.POST["year"]

    if "season" in request.POST:
        seasons = {request.POST["season"] : request.POST["score"]}
        Media.objects.create(
            media_id=request.POST["media_id"],
            title=request.POST["title"],
            description=request.POST["description"],
            image=request.POST["image"],
            year=year,
            media_type=request.POST["media_type"],
            seasons=seasons,
            user=request.user,
        )
    else:
        Media.objects.create(
            media_id=request.POST["media_id"],
            title=request.POST["title"],
            description=request.POST["description"],
            image=request.POST["image"],
            year=year,
            media_type=request.POST["media_type"],
            ind_score=request.POST["score"],
            user=request.user,
        )


def register(request):
    if ("query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )
    elif "username" in request.POST:
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get("username")
            messages.success(
                request, f"Account created for {username}! You can now log in"
            )
            return redirect("login")
    else:
        form = UserCreationForm()
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
            messages.success(
                request, f"Logged in as {user}!"
            )
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
