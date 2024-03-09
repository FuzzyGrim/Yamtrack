import logging

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from users import services
from users.forms import (
    PasswordChangeForm,
    UserLoginForm,
    UserRegisterForm,
    UserUpdateForm,
)

logger = logging.getLogger(__name__)


def register(request: HttpRequest) -> HttpResponse:
    """Register a new user."""

    form = UserRegisterForm(request.POST or None)

    if form.is_valid():
        form.save()
        messages.success(request, "Your account has been created, you can now log in!")
        logger.info(
            "New user registered: %s at %s",
            form.cleaned_data.get("username"),
            services.get_client_ip(request),
        )
        return redirect("login")

    return render(request, "users/register.html", {"form": form})


class CustomLoginView(LoginView):
    """Custom login view with logging."""

    form_class = UserLoginForm
    template_name = "users/login.html"

    def form_valid(self: "CustomLoginView", form: UserLoginForm) -> HttpResponse:
        """Log the user in and set the session expiry."""

        logger.info(
            "User logged in as: %s at %s",
            self.request.POST["username"],
            services.get_client_ip(self.request),
        )
        return super().form_valid(form)

    def form_invalid(self: "CustomLoginView", form: UserLoginForm) -> HttpResponse:
        """Log the failed login attempt."""

        logger.error(
            "Failed login attempt for: %s at %s",
            self.request.POST["username"],
            services.get_client_ip(self.request),
        )
        return super().form_invalid(form)


def profile(request: HttpRequest) -> HttpResponse:
    """Update the user's profile and import/export data."""

    user_form = UserUpdateForm(instance=request.user)
    password_form = PasswordChangeForm(request.user)

    if request.method == "POST":
        if "username" in request.POST:
            user_form = UserUpdateForm(request.POST, instance=request.user)

            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Your username has been updated!")
                logger.info("Successful username change to %s", request.user.username)
            else:
                logger.error(
                    "Failed username change for %s: %s",
                    request.user.username,
                    user_form.errors.as_data(),
                )

        elif "new_password1" in request.POST:
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                password = password_form.save()
                update_session_auth_hash(request, password)
                messages.success(request, "Your password has been updated!")
                logger.info(
                    "Successful password change for: %s",
                    request.user.username,
                )
            else:
                logger.error(
                    "Failed password change for %s: %s",
                    request.user.username,
                    password_form.errors.as_data(),
                )

        else:
            messages.error(request, "There was an error with your request")

    context = {
        "user_form": user_form,
        "password_form": password_form,
    }

    return render(request, "users/profile.html", context)
