import os

from django.contrib.auth import get_user_model, login
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from app.providers import services


class AutoLoginMiddleware(MiddlewareMixin):
    """Middleware to auto-login with a specific user."""

    def process_request(self, request):
        """Handle authorization request."""
        auto_login_username = os.environ.get("YAMTRACK_AUTO_LOGIN_USERNAME")
        if not auto_login_username or request.user.is_authenticated:
            return
        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=auto_login_username)
            user.backend = "django.contrib.auth.backends.ModelBackend"
            login(request, user)
        except user_model.DoesNotExist:
            pass


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
