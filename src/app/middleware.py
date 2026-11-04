from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.shortcuts import render
from django.utils import translation

from app.providers import services


class AutoLoginMiddleware:
    """Middleware to auto-login with a specific user."""

    def __init__(self, get_response):
        """Initialize the middleware."""
        self.get_response = get_response

    def __call__(self, request):
        """Handle authorization request."""
        auto_login_username = settings.YAMTRACK_AUTO_LOGIN_USERNAME
        if auto_login_username and not request.user.is_authenticated:
            user_model = get_user_model()
            try:
                user = user_model.objects.get(username=auto_login_username)
                if user.is_active:
                    login(
                        request,
                        user,
                        backend="django.contrib.auth.backends.ModelBackend",
                    )
            except user_model.DoesNotExist:
                pass

        return self.get_response(request)


class UserLanguageMiddleware:
    """Middleware to activate the language saved on the user's account.

    Runs after LocaleMiddleware, so authenticated users get their saved
    language instead of the one from the cookie or the Accept-Language header.
    """

    def __init__(self, get_response):
        """Initialize the middleware."""
        self.get_response = get_response

    def __call__(self, request):
        """Activate the user's language for the request."""
        if request.user.is_authenticated:
            translation.activate(request.user.language)
            request.LANGUAGE_CODE = translation.get_language()

        return self.get_response(request)


class ProviderAPIErrorMiddleware:
    """Middleware to handle ProviderAPIError exceptions."""

    def __init__(self, get_response):
        """Initialize the middleware with the get_response callable."""
        self.get_response = get_response

    def __call__(self, request):
        """Process the request and handle exceptions."""
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Handle exceptions raised during request processing."""
        if isinstance(exception, services.ProviderAPIError):
            return render(
                request,
                "500.html",
                {
                    "error_message": str(exception),
                    "provider": exception.provider,
                },
                status=500,
            )
        return None
