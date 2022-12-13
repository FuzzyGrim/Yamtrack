from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from app.forms import UserUpdateForm

def home(request):
    """Home page"""
    if ("content" and "query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )

    return render(request, "app/home.html")


def search(request, content, query):
    """Search page"""
    if ("content" and "query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )
    
    context = {
        "query_list": api.search(content, query)
    }

    return render(request, "app/search.html", context)


def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Account created for {username}! You can now log in')
            return redirect('login')
    else:
        form = UserCreationForm()
    return render(request, 'app/register.html', {'form': form})


@login_required
def profile(request):
    if request.method == 'POST':
        user_form = UserUpdateForm(request.POST, instance=request.user)
        password_form = PasswordChangeForm(request.user, request.POST)
        if user_form.is_valid() and password_form.is_valid():
            user_form.save()
            password = password_form.save()
            update_session_auth_hash(request, password)
            messages.success(request, f'Your account has been updated!')
            return redirect('profile')
        else:
            messages.info(request, 'Please correct the error below.')
    else:
        user_form = UserUpdateForm(instance=request.user)
        password_form = PasswordChangeForm(request.user)

    context = {
        'user_form': user_form,
        'password_form': password_form
    }

    return render(request, 'app/profile.html', context)