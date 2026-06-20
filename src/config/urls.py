"""Yamtrack base URL Configuration.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/stable/topics/http/urls/

"""

from allauth.account import views as allauth_account_views
from allauth.socialaccount import views as allauth_social_account_views
from allauth.urls import build_provider_urlpatterns
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path, register_converter
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from redis.asyncio import Redis as RedisClient

from app.converters import MediaTypeChecker, SourceChecker

try:
    from health_check.views import HealthCheckView
    SUPPORTS_CONFIGURED_HEALTH_CHECKS = True
except ImportError:
    from health_check.views import MainView as HealthCheckView
    SUPPORTS_CONFIGURED_HEALTH_CHECKS = False

# Register custom URL path converters used across included apps
register_converter(SourceChecker, "source")
register_converter(MediaTypeChecker, "media_type")

urlpatterns = [
    path("api/v1/", include("api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="api-schema"),
        name="api-docs",
    ),
    path("", include("app.urls")),
    path("", include("integrations.urls")),
    path("", include("users.urls")),
    path("", include("lists.urls")),
    path("", include("events.urls")),
    path("select2/", include("django_select2.urls")),
]

if SUPPORTS_CONFIGURED_HEALTH_CHECKS:
    health_check_view = HealthCheckView.as_view(
        checks=[
            "health_check.Cache",
            "health_check.Database",
            "health_check.contrib.celery.Ping",
            (
                "health_check.contrib.redis.Redis",
                {
                    "client_factory": lambda: RedisClient.from_url(
                        settings.REDIS_URL,
                    ),
                },
            ),
        ],
    )
else:
    health_check_view = HealthCheckView.as_view()

urlpatterns.append(path("health/", login_not_required(health_check_view)))

# Build the accounts URLs
account_patterns = [
    # see allauth/account/urls.py
    # login, logout, signup, account_inactive
    path("login/", allauth_account_views.login, name="account_login"),
    path("logout/", allauth_account_views.logout, name="account_logout"),
    path("signup/", allauth_account_views.signup, name="account_signup"),
    path(
        "account_inactive/",
        allauth_account_views.account_inactive,
        name="account_inactive",
    ),
    # social account base urls, see allauth/socialaccount/urls.py
    path(
        "3rdparty/",
        include(
            [
                path(
                    "login/cancelled/",
                    allauth_social_account_views.login_cancelled,
                    name="socialaccount_login_cancelled",
                ),
                path(
                    "login/error/",
                    allauth_social_account_views.login_error,
                    name="socialaccount_login_error",
                ),
                path(
                    "signup/",
                    allauth_social_account_views.signup,
                    name="socialaccount_signup",
                ),
                path(
                    "",
                    allauth_social_account_views.connections,
                    name="socialaccount_connections",
                ),
            ],
        ),
    ),
    *build_provider_urlpatterns(),
]

# Add the accounts URLs to the main urlpatterns
urlpatterns.append(path("accounts/", include(account_patterns)))

if settings.ADMIN_ENABLED:
    urlpatterns.append(path("admin/", admin.site.urls))

# Add debug toolbar if in DEBUG mode
if settings.DEBUG:
    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))

# Serve media and static files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else None)
