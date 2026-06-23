from django_celery_results.models import TaskResult

from integrations import tasks

TASKS_BY_SOURCE = {
    "trakt": tasks.import_trakt,
    "mal": tasks.import_mal,
    "anilist": tasks.import_anilist,
    "kitsu": tasks.import_kitsu,
    "steam": tasks.import_steam,
    "yamtrack": tasks.import_yamtrack,
    "hltb": tasks.import_hltb,
    "imdb": tasks.import_imdb,
    "goodreads": tasks.import_goodreads,
    "letterboxd": tasks.import_letterboxd,
}


def queue_import(source, user, data, files=None):
    """Queue a supported once-only import."""
    task = TASKS_BY_SOURCE[source]
    mode = data["mode"]
    username = data.get("username")
    if source == "letterboxd":
        uploaded_file = data.get("file") or (files.get("file") if files else None)
        result = task.delay(file=uploaded_file.read(), user_id=user.id, mode=mode)
    elif source in {"yamtrack", "hltb", "imdb", "goodreads"}:
        uploaded_file = data.get("file") or (files.get("file") if files else None)
        result = task.delay(file=uploaded_file, user_id=user.id, mode=mode)
    elif source == "trakt" or source == "anilist":
        result = task.delay(user_id=user.id, mode=mode, username=username)
    else:
        result = task.delay(username=username, user_id=user.id, mode=mode)
    return {"task_id": result.id, "status": "queued"}


def task_status(task_id, user):
    """Return task status if it belongs to the current user."""
    task = TaskResult.objects.filter(
        task_id=task_id,
        task_kwargs__contains=f"'user_id': {user.id},",
    ).first()
    if task is None:
        return None
    return {
        "task_id": task.task_id,
        "task_name": task.task_name,
        "status": task.status,
        "date_created": task.date_created,
        "date_done": task.date_done,
        "result": task.result,
    }
