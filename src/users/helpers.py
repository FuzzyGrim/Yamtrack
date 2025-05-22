import json
import zoneinfo
from datetime import datetime

import croniter
from django.utils import timezone

import integrations


def get_client_ip(request):
    """Return the client's IP address.

    Used when logging for user registration and login.
    """
    # get the user's IP address
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR")

    # if the IP address is not available in HTTP_X_FORWARDED_FOR
    if not ip_address:
        ip_address = request.META.get("REMOTE_ADDR")

    return ip_address


def process_task_result(task):
    """Process task result based on status and format appropriately."""
    try:
        if isinstance(task.task_kwargs, str):
            kwargs = json.loads(task.task_kwargs)
        else:
            kwargs = task.task_kwargs
        mode = kwargs.get("mode", "new")  # Default to 'new' if not specified
    except (TypeError, json.JSONDecodeError, AttributeError):
        mode = "new"

    mode = "Only New Items" if mode == "new" else "Overwrite Existing"

    if task.status == "FAILURE":
        result_json = json.loads(task.result)
        if result_json["exc_type"] == "MediaImportError":
            task.summary = result_json["exc_message"][0]
            task.errors = task.traceback
        else:
            task.summary = "Unexpected error occurred while processing the task."
            task.errors = task.traceback
    elif task.status == "STARTED":
        task.summary = "This task is currently running."
        task.errors = None
    elif task.status == "SUCCESS":
        result_json = json.loads(task.result)
        # Split by the error indicator
        parts = result_json.split(integrations.tasks.ERROR_TITLE.strip())
        if len(parts) > 1:
            # We have both summary and errors
            task.summary = parts[0].strip()

            # Keep errors as a single string with newlines
            task.errors = parts[1].strip()
        else:
            # Only summary, no errors
            task.summary = result_json.strip()
            task.errors = None
    elif task.status == "PENDING":
        task.summary = "This task has been queued and is waiting to run."
        task.errors = None

    task.mode = mode  # Add mode to task object
    return task


def get_next_run_info(periodic_task):
    """Calculate next run time and frequency for a periodic task."""
    if not periodic_task.crontab:
        return None

    try:
        kwargs = json.loads(periodic_task.kwargs)
        mode = kwargs.get("mode", "new")  # Default to 'new' if not specified
    except json.JSONDecodeError:
        mode = "new"

    mode = "Only New Items" if mode == "new" else "Overwrite Existing"

    cron = periodic_task.crontab
    tz = zoneinfo.ZoneInfo(str(cron.timezone))
    now = timezone.now().astimezone(tz)

    # Create cron expression
    cron_expr = (
        f"{cron.minute} {cron.hour} {cron.day_of_month} "
        f"{cron.month_of_year} {cron.day_of_week}"
    )
    cron_iter = croniter.croniter(cron_expr, now)
    next_run = cron_iter.get_next(datetime)

    # Determine frequency
    if cron.day_of_week == "*":
        frequency = "Every Day"
    elif cron.day_of_week == "*/2":
        frequency = "Every 2 days"
    else:
        frequency = f"Cron: {cron_expr}"

    return {
        "next_run": next_run,
        "frequency": frequency,
        "mode": mode,
    }
