import logging

import apprise
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_celery_beat.models import PeriodicTask

from app.models import Item, MediaTypes
from app.providers import tmdb
from users.forms import NotificationSettingsForm, PasswordChangeForm, UserUpdateForm
from users.models import (
    WATCH_PROVIDER_REGION_UNSET,
    DateFormatChoices,
    QuickWatchDateChoices,
    TimeFormatChoices,
    User,
    WeekStartDayChoices,
)

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def account(request):
    """Update the user's account and import/export data."""
    user_form = UserUpdateForm(instance=request.user)
    password_form = PasswordChangeForm(user=request.user)

    if request.method == "POST":
        # Handle username update
        if "username" in request.POST:
            user_form = UserUpdateForm(
                request.POST, request.FILES, instance=request.user,
            )

            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Your profile has been updated!")
                logger.info(
                    "Successful profile change for user: %s",
                    request.user.username,
                )
                return redirect("account")
            logger.warning(
                "Failed profile change for user: %s - %s",
                request.user.username,
                list(user_form.errors.keys()),
            )

        # Handle password update
        elif any(
            key in request.POST
            for key in ["old_password", "new_password1", "new_password2"]
        ):
            password_form = PasswordChangeForm(user=request.user, data=request.POST)

            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(
                    request,
                    user,
                )
                messages.success(request, "Your password has been updated!")
                logger.info(
                    "Successful password change for user: %s",
                    request.user.username,
                )
                return redirect("account")
            logger.warning(
                "Failed password change for user: %s - %s",
                request.user.username,
                list(password_form.errors.keys()),
            )

    context = {
        "user_form": user_form,
        "password_form": password_form,
    }

    return render(request, "users/account.html", context)


@require_http_methods(["GET", "POST"])
def notifications(request):
    """Render the notifications settings page."""
    if request.method == "POST":
        form = NotificationSettingsForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Notification settings updated successfully!")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, f"{error}")

        return redirect("notifications")

    form = NotificationSettingsForm(instance=request.user)

    return render(
        request,
        "users/notifications.html",
        {
            "form": form,
        },
    )


@require_GET
def search_items(request):
    """Search for items to exclude from notifications."""
    query = request.GET.get("q", "").strip()

    if not query or len(query) <= 1:
        return render(
            request,
            "users/components/search_results.html",
        )

    # Search for items that match the query
    items = (
        Item.objects.filter(
            Q(title__icontains=query),
        )
        .exclude(
            id__in=request.user.notification_excluded_items.values_list(
                "id",
                flat=True,
            ),
        )
        .distinct()[:10]
    )

    return render(
        request,
        "users/components/search_results.html",
        {"items": items, "query": query},
    )


@require_POST
def exclude_item(request):
    """Exclude an item from notifications."""
    item_id = request.POST["item_id"]
    item = get_object_or_404(Item, id=item_id)
    request.user.notification_excluded_items.add(item)

    # Return the updated excluded items list
    excluded_items = request.user.notification_excluded_items.all()

    return render(
        request,
        "users/components/excluded_items.html",
        {"excluded_items": excluded_items},
    )


@require_POST
def include_item(request):
    """Remove an item from the exclusion list."""
    item_id = request.POST["item_id"]
    item = get_object_or_404(Item, id=item_id)
    request.user.notification_excluded_items.remove(item)

    # Return the updated excluded items list
    excluded_items = request.user.notification_excluded_items.all()

    return render(
        request,
        "users/components/excluded_items.html",
        {"excluded_items": excluded_items},
    )


@require_GET
def test_notification(request):
    """Send a test notification to the user."""
    try:
        # Create Apprise instance
        apobj = apprise.Apprise()

        # Add all notification URLs
        notification_urls = [
            url.strip()
            for url in request.user.notification_urls.splitlines()
            if url.strip()
        ]
        if not notification_urls:
            messages.error(request, "No notification URLs configured.")
            return redirect("notifications")

        for url in notification_urls:
            apobj.add(url)

        # Send test notification
        result = apobj.notify(
            title="YamTrack Test Notification",
            body=(
                "This is a test notification from YamTrack. "
                "If you're seeing this, your notifications are working correctly!"
            ),
        )

        if result:
            messages.success(request, "Test notification sent successfully!")
        else:
            messages.error(request, "Failed to send test notification.")
    except Exception:
        logger.exception("Error sending notification")

    return redirect("notifications")


@require_http_methods(["GET", "POST"])
def preferences(request):
    """Render the preferences settings page."""
    media_types = MediaTypes.values
    media_types.remove(MediaTypes.EPISODE.value)
    watch_provider_regions = tmdb.watch_provider_regions()

    if request.method == "GET":
        return render(
            request,
            "users/preferences.html",
            {
                "media_types": media_types,
                "quick_watch_date_choices": QuickWatchDateChoices.choices,
                "date_format_choices": DateFormatChoices.choices,
                "time_format_choices": TimeFormatChoices.choices,
                "week_start_day_choices": WeekStartDayChoices.choices,
                "watch_provider_choices": watch_provider_regions,
            },
        )

    # Prevent demo users from updating preferences
    if request.user.is_demo:
        messages.error(request, "This section is view-only for demo accounts.")
        return redirect("preferences")

    # Process form submission
    request.user.clickable_media_cards = "clickable_media_cards" in request.POST
    request.user.obfuscate_unseen_episodes = "obfuscate_unseen_episodes" in request.POST
    request.user.quick_watch_date = request.POST.get(
        "quick_watch_date",
        QuickWatchDateChoices.CURRENT_DATE,
    )
    request.user.progress_bar = "progress_bar" in request.POST
    request.user.hide_completed_recommendations = (
        "hide_completed_recommendations" in request.POST
    )
    request.user.hide_zero_rating = "hide_zero_rating" in request.POST
    request.user.date_format = request.POST.get(
        "date_format",
        DateFormatChoices.ISO,
    )
    request.user.time_format = request.POST.get(
        "time_format",
        TimeFormatChoices.HOUR_24,
    )
    week_start_day = request.POST.get("week_start_day")
    if week_start_day in WeekStartDayChoices.values:
        request.user.week_start_day = week_start_day
    media_types_checked = request.POST.getlist("media_types_checkboxes")

    provider_region = request.POST.get("watch_provider_region", "")
    if provider_region in [region[0] for region in watch_provider_regions]:
        request.user.watch_provider_region = provider_region
    else:
        request.user.watch_provider_region = WATCH_PROVIDER_REGION_UNSET

    # Update user preferences for each media type
    for media_type in media_types:
        setattr(
            request.user,
            f"{media_type}_enabled",
            media_type in media_types_checked,
        )

    # Save changes and redirect
    request.user.save()
    messages.success(request, "Settings updated.")

    return redirect("preferences")


@require_GET
def integrations(request):
    """Render the integrations settings page."""
    return render(request, "users/integrations.html")


@require_GET
def import_data(request):
    """Render the import data settings page."""
    import_tasks = request.user.get_import_tasks()
    return render(request, "users/import_data.html", {"import_tasks": import_tasks})


@require_GET
def export_data(request):
    """Render the export data settings page."""
    return render(request, "users/export_data.html")


@require_GET
def advanced(request):
    """Render the advanced settings page."""
    return render(request, "users/advanced.html")


@require_GET
def about(request):
    """Render the about page."""
    return render(request, "users/about.html", {"version": settings.VERSION})


@require_POST
def delete_import_schedule(request):
    """Delete an import schedule."""
    task_name = request.POST.get("task_name")
    try:
        task = PeriodicTask.objects.get(
            name=task_name,
            kwargs__contains=f'"user_id": {request.user.id}',
        )
        task.delete()
        messages.success(request, "Import schedule deleted.")
    except PeriodicTask.DoesNotExist:
        messages.error(request, "Import schedule not found.")
    return redirect("import_data")


@require_POST
def regenerate_token(request):
    """Regenerate the token for the user."""
    while True:
        try:
            request.user.regenerate_token()
            messages.success(request, "Token regenerated successfully.")
            break
        except IntegrityError:
            continue
    return redirect("integrations")


@require_POST
def update_plex_usernames(request):
    """Update the Plex usernames for the user."""
    usernames = request.POST.get("plex_usernames", "")

    username_list = [u.strip() for u in usernames.split(",") if u.strip()]

    seen = set()
    deduplicated_usernames = [
        u for u in username_list if not (u in seen or seen.add(u))
    ]

    # Reconstruct with comma-space separation
    cleaned_usernames = ", ".join(deduplicated_usernames)

    if cleaned_usernames != request.user.plex_usernames:
        request.user.plex_usernames = cleaned_usernames
        request.user.save(update_fields=["plex_usernames"])
        messages.success(request, "Plex usernames updated successfully")

    return redirect("integrations")


@require_POST
def update_jellyfin_webhook_events(request):
    """Update optional Jellyfin webhook event handling for the user."""
    request.user.jellyfin_mark_played_enabled = (
        "jellyfin_mark_played_enabled" in request.POST
    )
    request.user.jellyfin_mark_unplayed_enabled = (
        "jellyfin_mark_unplayed_enabled" in request.POST
    )
    request.user.save(
        update_fields=[
            "jellyfin_mark_played_enabled",
            "jellyfin_mark_unplayed_enabled",
        ],
    )
    messages.success(request, "Jellyfin webhook settings updated successfully")

    return redirect("integrations")


@require_POST
def clear_search_cache(request):
    """Clear all cached search entries."""
    deleted = cache.delete_pattern("search_*")

    messages.success(
        request,
        f"Successfully cleared {deleted} search entr{pluralize(deleted, 'y,ies')}",
    )
    logger.info(
        "Successfully cleared %s search entries",
        deleted,
    )

    return redirect("advanced")


@login_not_required
@require_GET
def public_profile(request, username):
    """Display a user's public profile page."""
    target_user = get_object_or_404(User, username=username)

    is_owner = request.user.is_authenticated and request.user == target_user
    is_admin = request.user.is_authenticated and request.user.is_staff

    if target_user.profile_private and not is_owner and not is_admin:
        messages.error(request, "This profile is private.")
        return redirect("home")

    from app.models import BasicMedia, MediaTypes as MT
    from app.statistics import get_user_media, get_media_type_distribution
    from lists.models import CustomList

    private_item_ids = list(CustomList.objects.get_private_item_ids(target_user)) if not is_owner else []

    media_types = target_user.get_enabled_media_types()
    user_media, media_count = get_user_media(target_user, None, None)

    if private_item_ids:
        for media_type_key, queryset in user_media.items():
            user_media[media_type_key] = queryset.exclude(item_id__in=private_item_ids)
            media_count[media_type_key] = user_media[media_type_key].count()
        media_count["total"] = sum(
            v for k, v in media_count.items() if k != "total"
        )

    recently_watched = []
    currently_watching = []
    for mt in media_types:
        if mt == MT.TV.value:
            continue
        from django.apps import apps as django_apps
        model_class = django_apps.get_model("app", mt)
        qs = model_class.objects.filter(
            user=target_user,
        ).select_related("item").order_by("-created_at")

        if private_item_ids:
            qs = qs.exclude(item_id__in=private_item_ids)

        for item in qs[:10]:
            if item.status == "In progress":
                currently_watching.append({"media": item, "media_type": mt})
            if item.end_date or item.created_at:
                recently_watched.append({"media": item, "media_type": mt})

    recently_watched.sort(
        key=lambda x: x["media"].end_date or x["media"].created_at,
        reverse=True,
    )
    recently_watched = recently_watched[:12]

    public_lists = CustomList.objects.get_public_lists_for_user(target_user)[:6]
    list_count = CustomList.objects.filter(owner=target_user, is_public=True).count()

    total_movies = media_count.get("movie", 0)
    total_tv = media_count.get("tv", 0)
    total_seasons = media_count.get("season", 0)

    context = {
        "target_user": target_user,
        "is_owner": is_owner,
        "recently_watched": recently_watched,
        "currently_watching": currently_watching[:10],
        "public_lists": public_lists,
        "list_count": list_count,
        "total_movies": total_movies,
        "total_tv": total_tv,
        "total_seasons": total_seasons,
        "media_count": media_count,
        "media_types": media_types,
    }

    return render(request, "users/public_profile.html", context)


@require_GET
def user_directory(request):
    """Display all users with public profiles."""
    from django.core.paginator import Paginator
    from lists.models import CustomList

    search_query = request.GET.get("q", "")
    sort_by = request.GET.get("sort", "username")
    page = request.GET.get("page", 1)

    users = User.objects.filter(profile_private=False, is_active=True)

    if search_query:
        users = users.filter(username__icontains=search_query)

    if sort_by == "newest":
        users = users.order_by("-date_joined")
    else:
        users = users.order_by("username")

    paginator = Paginator(users, 20)
    users_page = paginator.get_page(page)

    for user in users_page:
        user.list_count = CustomList.objects.filter(
            owner=user, is_public=True,
        ).count()

    context = {
        "users_page": users_page,
        "search_query": search_query,
        "current_sort": sort_by,
    }

    return render(request, "users/user_directory.html", context)
