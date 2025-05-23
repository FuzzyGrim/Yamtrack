import logging

import apprise
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_celery_beat.models import PeriodicTask

from app.models import Item, MediaTypes
from users.forms import (
    NotificationSettingsForm,
    PasswordChangeForm,
    UserUpdateForm,
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
            user_form = UserUpdateForm(request.POST, instance=request.user)

            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Your username has been updated!")
                logger.info(
                    "Successful username change for user: %s",
                    request.user.username,
                )
                return redirect("account")
            logger.warning(
                "Failed username change for user: %s - %s",
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
def sidebar(request):
    """Render the sidebar settings page."""
    media_types = MediaTypes.values
    media_types.remove(MediaTypes.EPISODE.value)

    if request.method == "GET":
        return render(request, "users/sidebar.html", {"media_types": media_types})

    # Prevent demo users from updating preferences
    if request.user.is_demo:
        messages.error(request, "This section is view-only for demo accounts.")
        return redirect("sidebar")

    # Process form submission
    request.user.hide_from_search = "hide_disabled" in request.POST
    media_types_checked = request.POST.getlist("media_types_checkboxes")

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

    return redirect("sidebar")


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

    # input validation
    # if there is any error in input: messages.error(request, "Message")

    if usernames != request.user.plex_usernames:
        request.user.plex_usernames = usernames
        request.user.save(update_fields=["plex_usernames"])
        messages.success(request, "Plex usernames updated successfully")

    return redirect("integrations")