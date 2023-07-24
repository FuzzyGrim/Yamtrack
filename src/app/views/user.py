from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required

from app.utils import helpers
from app.forms import (
    UserLoginForm,
    UserRegisterForm,
    UserUpdateForm,
    PasswordChangeForm,
)

import logging


logger = logging.getLogger(__name__)


def register(request):
    form = UserRegisterForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Your account has been created, you can now log in!")
        logger.info(
            f"New user registered: {form.cleaned_data.get('username')} at {helpers.get_client_ip(request)}"
        )
        return redirect("login")
    return render(request, "app/register.html", {"form": form})


class CustomLoginView(LoginView):
    form_class = UserLoginForm
    template_name = "app/login.html"

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
            else:
                logger.error(
                    f"Failed username change for {request.user.username}: {user_form.errors.as_data()}"
                )

        elif "new_password1" in request.POST:
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                password = password_form.save()
                update_session_auth_hash(request, password)
                messages.success(request, "Your password has been updated!")
                logger.info(f"Successful password change for: {request.user.username}")
            else:
                logger.error(
                    f"Failed password change for {request.user.username}: {password_form.errors.as_data()}"
                )

        else:
            messages.error(request, "There was an error with your request")

    context = {
        "user_form": user_form,
        "password_form": password_form,
    }

    return render(request, "app/profile.html", context)
