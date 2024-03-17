from django.contrib.auth.decorators import login_required
from django.urls import resolve
from django.utils.deprecation import MiddlewareMixin

LOGIN_EXEMPT_ROUTES = ("login", "register")

class LoginRequiredMiddleware(MiddlewareMixin):
    """Middleware that requires a user to be authenticated to view any page.

    Exemptions to this requirement can optionally be specified
    in settings by setting a tuple of routes to ignore
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Process the request and view."""
        current_route_name = resolve(request.path_info).url_name

        if request.user.is_authenticated:
            return None

        if current_route_name in LOGIN_EXEMPT_ROUTES:
            return None

        return login_required(view_func)(request, *view_args, **view_kwargs)
