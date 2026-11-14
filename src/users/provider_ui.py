"""Safe view models for the shared provider shell, Overview and owned React tabs."""

from datetime import timedelta

from celery import states
from django.db.models import Q
from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

from app import config
from app.icon_specs import file_icon
from app.log_safety import redact_secrets
from app.models import Status
from app.providers import credentials
from app.services import metadata_resolution
from app.templatetags.app_tags import media_type_readable, media_type_readable_plural
from integrations.models import ImportRun
from users import helpers, views
from users.provider_catalog import FILE_ONLY_IMPORTS, ONE_TIME_IMPORTS, activity_sources
from users.templatetags.user_tags import (
    SOURCES_CONFIG,
    translate_import_summary,
    user_datetime_format,
)

ACTIVITY_PAGE_SIZE = 50
MAX_ACTIVITY_PAGES = 1000


FILE_IMPORTS = {
    "yamtrack": ("import_yamtrack", "yamtrack_csv", ".csv", "Import a Floppy backup (media + lists)", "Select CSV File"),
    "yamtrack_native": ("import_yamtrack", "yamtrack_csv", ".csv", "Import a CSV exported from Yamtrack (media + lists)", "Select CSV File"),
    "hltb": ("import_hltb", "hltb_csv", ".csv", "Import games", "Select CSV File"),
    "playnite": ("import_playnite", "playnite_csv", ".csv", "Import a Playnite library backup", "Select CSV File"),
    "grouvee": ("import_grouvee", "grouvee_json", ".json,.zip", "Import games", "Select File"),
    "imdb": ("import_imdb", "imdb_csv", ".csv", "Import movies and TV shows from your ratings", "Select CSV File"),
    "goodreads": ("import_goodreads", "goodreads_csv", ".csv", "Import from Goodreads backup", "Select CSV File"),
    "youtube": ("import_youtube_takeout", "youtube_takeout", ".json,.html,.zip",
                "Export YouTube history from Google Takeout. Upload watch-history.json, watch-history.html, or the zip.", "Select File", {"fields": ("mode",)}),
    "wetrakr": ("import_wetrakr", "wetrakr_export", ".zip,.csv",
                "Upload the .zip from a WeTrakr data export, or its tracklog, ratings, notes and lists .csv files. Importing lists replaces any lists previously imported from WeTrakr.",
                "Select File", {"fields": ("mode",), "multiple": True}),
    "hardcover": ("import_hardcover", "hardcover_csv", ".csv", "Import from Hardcover export (Account -> Export Data)", "Select CSV File", {
        "auto": False, "submit": "Save",
        "text_fields": (("hardcover_api_key", "Hardcover API Key", "password", "Optional: also settable in Providers > Hardcover > Connection", False),)}),
    "mdblist": ("import_mdblist", "", "", "Import watched history, watchlist, ratings, dropped shows and collection. Lists sync separately from the Lists page.",
                "", {"auto": False, "submit": "Import", "files": (),
                     "text_fields": (("api_key", "MDBList API key", "password", "MDBList API key", True),)}),
    "tvtime": ("import_tvtime", "", "", "Import from a TV Time GDPR data export (Settings -> Export My Data). Select the shows file, the movies file, or both.",
               "Select CSV File", {"auto": False, "submit": "Import", "files": (
                   ("tvtime_shows_csv", "Shows CSV (tracking-prod-records.csv)"), ("tvtime_movies_csv", "Movies CSV (tracking-prod-records-v2.csv)"))}),
    "storygraph": ("import_storygraph", "storygraph_csv", ".csv", "Import from StoryGraph export (Profile -> Manage Account -> Export StoryGraph Library)", "Select CSV File"),
}
CSV_FORMAT_DOCS = "https://github.com/dannyvfilms/Floppy/wiki/6.-Admin-and-Operations#csv-import-floppy--yamtrack-format"


def file_import(request, slug, preferences, context=None):
    """Shared single-file import presentation; the file is the only upload."""
    action, field, accept, description, label, *rest = FILE_IMPORTS[slug]
    options = rest[0] if rest else {}
    model = {
        "action": reverse(action), "csrf": get_token(request), "next": request.get_full_path(),
        "values": preferences["values"], "description": _(description), "selectLabel": _(label),
        "fileField": field, "accept": accept, "mediaTypes": [],
        "postFields": [{"name": name, "key": name} for name in options.get("fields", ("frequency", "time", "mode"))],
        "multiple": options.get("multiple", False), "autoSubmit": options.get("auto", True),
        "submitLabel": _(options.get("submit", "Import")),
        "textFields": [{"name": name, "label": _(label), "type": kind, "placeholder": _(hint), "required": required}
                      for name, label, kind, hint, required in options.get("text_fields", ())],
        **({"files": [{"field": name, "label": _(label), "accept": ".csv", "selectLabel": _(label_text)}
                      for (name, label), label_text in zip(options["files"], ("Select CSV File",) * len(options["files"]), strict=True)]} if "files" in options else {}),
        "note": "",
        "blockedLabel": _("This import mode is not supported by this provider."),
        "oneTime": {"label": _("One-time"), "title": _("File uploads can't be scheduled. This file imports once, now.")},
    }
    if slug == "mdblist":
        account = (context or {}).get("mdblist_account")
        if account and (account.is_connected() if callable(account.is_connected) else account.is_connected):
            model["textFields"] = []
            model["note"] = _("Using your connected MDBList account.")
    if slug in {"yamtrack", "yamtrack_native"}:
        model["docs"] = {"url": CSV_FORMAT_DOCS, "label": _("View CSV format documentation")}
    return model


def import_preferences(request, slug, context=None):
    """Shared import controls and saved preferences, with no provider secrets."""
    context = context or {}
    fixed = {"radarr", "sonarr", "mylar", "kapowarr", "stremio", "audiobookshelf", "kavita", "komga",
             "storyteller", "pocketcasts", "gpodder", "lastfm", "koito", "psn"}
    fixed_values = None
    if slug in fixed:
        if slug in {"kavita", "komga"}:
            account = context.get(f"{slug}_account")
            frequency = _("Every %(minutes)s minutes") % {"minutes": account.sync_interval_minutes} if account else _("Choose when connecting")
        elif slug in {"audiobookshelf", "lastfm"}:
            frequency = _("Every %(minutes)s minutes") % {"minutes": context.get(f"{slug}_poll_interval", 15)}
        elif slug == "koito":
            frequency = _("Every 15 minutes")
        elif slug == "psn":
            frequency = _("Every 3 hours")
        else:
            frequency = _("Every 2 hours")
        mode = _("Sync reading progress") if slug in {"kavita", "komga", "audiobookshelf"} else (
            _("Sync owned collection") if slug in {"radarr", "sonarr", "mylar", "kapowarr"} else _("Only sync new items")
        )
        explanation = _("Pocket Casts uses a fixed schedule to estimate podcast completion time and imports new items automatically.") if slug == "pocketcasts" else (
            _("The interval is chosen in Connection. This integration manages its own reading progress sync.") if slug in {"kavita", "komga"} else
            _("This integration manages its own schedule and import behavior.")
        )
        fixed_values = {"mode": mode, "frequency": frequency, "explanation": explanation}
    user = request.user
    labels = {text: _(text) for text in (
        "Import preferences", "Editable", "Read-only", "Import Mode", "History", "Watchlist", "Collection",
        "Override mode", "New Items Only", "Overwrite Existing", "Import Frequency", "One Time Import", "Every Day", "Every 2 Days",
        "Import Time", "Watchlist status", "Watchlist tag", "No tag", "Saving…", "Saved", "Could not save preferences. Try again.",
        "Retry", "One-time import", "Files are imported once when uploaded.", "Import frequency help", "Override mode help",
        "New items leaves existing entries unchanged. Overwrite replaces matching imported entries.",
        "Collection and wishlist", "Import mode help", "CLZ imports owned copies and wishlist entries. It does not import reading or viewing history.",
        "Wishlist items", "Tag for wishlist items", "Optional tag name", 'Create tag "{name}"',
    )}
    tags = list(user.tags.order_by("name").values("id", "name"))
    tag_ids = {tag["id"] for tag in tags}
    return {
        "values": {"frequency": user.import_frequency, "time": str(user.import_time)[:5], "mode": user.import_mode,
                   "watchlistStatus": user.watchlist_import_status, "watchlistTag": str(user.watchlist_import_tag_id) if user.watchlist_import_tag_id in tag_ids else "",
                   "clzWishlistStatus": user.clz_wishlist_status or Status.PLANNING.value, "clzWishlistTag": user.clz_wishlist_tag},
        "statuses": [{"value": value, "label": str(label)} for value, label in Status.choices],
        "tagNames": [tag["name"] for tag in tags],
        "tags": [{"value": "", "label": _("No tag")}] + [{"value": str(tag["id"]), "label": tag["name"]} for tag in tags],
        "fixed": fixed_values, "profile": "clz" if slug == "clz" else "", "fixedFrequency": slug in ONE_TIME_IMPORTS,
        "fixedFrequencyHelp": _("Files are imported once when uploaded; automatic imports are unavailable.") if slug in FILE_ONLY_IMPORTS else
            _("MangaBaka imports require a personal token and cannot run automatically.") if slug == "mangabaka" else "",
        "editable": not user.is_demo, "saveUrl": reverse("save_import_settings"), "csrf": get_token(request), "labels": labels,
    }


def device_login(code, url, interval, expires_at=None):
    """Describe a pending device-code login so the page resumes it after a reload."""
    return {"kind": "device", "userCode": code, "verificationUrl": url, "openUrl": url, "interval": int(interval or 5),
            "expiresAt": expires_at.isoformat() if expires_at else None}


def account_connection(request, *, connected, saved, attention, fields, messages, follow_up, connect_url, disconnect_url,
                       instances=None, description="", summary=None, device=None, link_slug="", link_label=""):
    """Describe an account form without serializing saved URLs or credentials.

    ``instances`` lists connections that can coexist (one row per saved server);
    their addresses are shown as labels and no keys are serialized.
    """
    return {
        "connected": connected, "saved": saved, "attention": attention, "fields": fields, "messages": messages, "followUp": follow_up,
        "connectUrl": connect_url, "disconnectUrl": disconnect_url, "instances": instances, "description": description,
        "summary": summary, "device": device,
        # Providers that sign in with a device code share the app's link flow
        # (ProviderLinkCard) instead of a plain connect form.
        "link": {"label": link_label, **{key: reverse(f"api_provider_connect_{key}", kwargs={"slug": link_slug}) for key in ("start", "poll", "cancel")}}
            if link_slug else None,
        "csrf": get_token(request), "next": request.get_full_path(),
        "labels": {text: _(text) for text in (
            "Connection settings", "Account connection", "Connected", "Not connected", "Needs attention",
            "Connect", "Reconnect", "Disconnect", "Add Instance", "Disconnected", "Connections",
        )},
    }


def directory_page(context, request):
    """Expose only directory presentation and filter state, never account data."""
    return {
        "url": reverse("providers"), "setupUrl": reverse("onboarding_restart"),
        "pinUrl": reverse("pin_settings_provider"), "csrf": get_token(request), "canPin": not request.user.is_demo,
        "icon": file_icon("database"), "searchIcon": file_icon("magnifying-glass"),
        "filterIcon": file_icon("funnel"),
        "filters": {"q": context["query"], "capability": context["capability"], "media": context["media"]},
        "capabilities": [{"value": row["key"], "label": _(row["label"])} for row in context["capability_options"]],
        "media": [{"value": row["key"], "label": _(row["label"])} for row in context["media_options"]],
        "items": [{
            "slug": item["slug"], "title": item["label"], "description": item["card_description"],
            "logo": SOURCES_CONFIG.get(item.get("logo_slug") or item["slug"], {}).get("logo"),
            "url": reverse("provider_detail", args=[item["slug"]]) +
                ("?" + urlencode({"tab": context["capability"]}) if context["capability"] else ""),
            "attention": _(item["state"]["summary"]) if item["state"]["attention"] else "",
            "pinned": item["pinned"],
            "pinLabel": _("Pin %(name)s") % {"name": item["label"]},
            "unpinLabel": _("Unpin %(name)s") % {"name": item["label"]},
            "status": {
                "label": _(item["state"]["connection"]),
                "tone": item["state"]["connection_tone"],
            } if context["capability"] == "connection" else None,
            "openLabel": _("Open %(name)s") % {"name": item["label"]},
        } for item in context["providers"]],
        "labels": {text: _(text) for text in (
            "Providers", "Run guided setup", "Find services and their available settings.",
            "Search providers", "Search providers...", "Filter by capability", "Filter by media type",
            "No providers match these filters.", "Could not load providers. Try again.", "Retry",
            "Could not update the pinned provider. Try again.",
        )},
    }


def _schedules(user):
    """Read enabled AniList schedules without exposing their stored OAuth tokens."""
    candidates = PeriodicTask.objects.filter(
        task="Import from AniList", enabled=True, one_off=False,
        kwargs__regex=rf"[\"']user_id[\"']:\s*{user.pk}\s*[,}}]",
    ).select_related("crontab", "interval")
    return [task for task in candidates if helpers.task_kwargs_belong_to_user(task.kwargs, user.pk)]


def _latest_import(user):
    """Include old imports and newly queued tasks, rather than only recent results."""
    run = ImportRun.objects.filter(user=user, source="anilist").only(
        "id", "task_id", "status", "started_at", "finished_at", "created_count", "updated_count",
    ).order_by("-started_at", "-id").first()
    candidates = TaskResult.objects.filter(task_name="Import from AniList",
        task_kwargs__regex=rf"[\"']user_id[\"']:\s*{user.pk}\s*[,}}]",
    ).exclude(
        Q(status=states.PENDING) & Q(date_created__lt=timezone.now() - timedelta(minutes=30)),
    ).only("task_id", "task_kwargs", "status", "date_created").order_by("-date_created", "-id")
    task = next((row for row in candidates.iterator() if helpers.task_kwargs_belong_to_user(row.task_kwargs, user.pk)), None)
    if task and (not run or (task.task_id != run.task_id and task.date_created > run.started_at)):
        return {"status": _status(task.status), "date": user_datetime_format(task.date_created, user)}
    if run:
        return {"status": _status(run.status), "date": user_datetime_format(run.started_at, user)}
    return None


def _status(value):
    """Localize the existing task/run vocabulary at the server boundary."""
    return {
        "SUCCESS": _("Success"), "STARTED": _("Started"), "PENDING": _("Pending"),
        "FAILURE": _("Failed"), "REVOKED": _("Cancelled"), "running": _("Running"),
        "completed": _("Completed"), "failed": _("Failed"), "cancelled": _("Cancelled"),
    }.get(value, value)


def anilist_overview(user):
    """Use resolved credential tiers, enabled schedules and persisted import activity."""
    configured = credentials.is_configured("anilist", user)
    sources = {credentials.source_of("anilist", field.name, user) for field in credentials.REGISTRY["anilist"].fields}
    source = "none"
    if configured:
        source = "personal" if credentials.SOURCE_USER in sources else "instance"
    schedules = list(_schedules(user))
    next_runs = [info["next_run"] for task in schedules if (info := helpers.get_next_run_info(task))]
    return {
        "connection": source,
        "instanceFallback": source == "personal" and bool(sources - {credentials.SOURCE_USER}),
        "scheduleCount": len(schedules),
        "scheduleSummary": ngettext("%(count)s enabled schedule", "%(count)s enabled schedules", len(schedules)) % {"count": len(schedules)},
        "nextImport": user_datetime_format(min(next_runs), user) if next_runs else "",
        "latestImport": _latest_import(user),
    }


def _credential_line(user, slug):
    """Say whose API credentials are in use, or nothing when users cannot manage them."""
    spec = credentials.REGISTRY.get(slug)
    if not spec or spec.bundled_only:
        return ""
    sources = {credentials.source_of(slug, field.name, user) for field in spec.fields if field.required}
    if credentials.SOURCE_USER in sources:
        return _("Personal credentials active")
    return _("Bundled credentials active") if sources == {credentials.SOURCE_DEFAULT} else _("Instance credentials active")


def _connection_card(user, provider, overview, base):
    """Describe the Connection card; a linked account and API credentials are different things."""
    slug = provider["slug"]
    state = provider["state"]
    tooltip = ""
    detail = ""
    if state["account_linkable"]:
        # The card answers "is my account linked?". Credentials only support that
        # and appear as a second line, never as the headline.
        linked = state["account_saved"] and state["connection_tone"] != "warning"
        subtitle = _({
            "Needs attention": "Account needs attention",
            "Awaiting authorization": "Waiting for you to approve",
            "Webhook events received": "Receiving events",
        }.get(state["connection"], "Account linked" if state["account_saved"] else "Account not linked"))
        active = linked or state["connection"] == "Webhook events received"
        tone = "warning" if state["connection_tone"] == "warning" else "info" if active else "neutral"
        if state["credentials_available"]:
            detail = _credential_line(user, slug)
    else:
        active = state["connection_tone"] == "success"
        subtitle = _(state["connection"])
        tone = "warning" if state["connection_tone"] == "warning" else "info" if active else "neutral"
        if state["credentials_available"] and credentials.REGISTRY.get(slug):
            subtitle = _credential_line(user, slug) or subtitle
            sources = {credentials.source_of(slug, field.name, user) for field in credentials.REGISTRY[slug].fields if field.required}
            if credentials.SOURCE_USER in sources and sources - {credentials.SOURCE_USER}:
                tooltip = _("Uses instance fallback for fields without a personal value.")
        if overview is not None:
            active = overview["connection"] != "none" or state["connection"] == "Public API active"
            subtitle = _("Personal credentials active") if overview["connection"] == "personal" else (
                _("Instance credentials active") if overview["connection"] == "instance" else
                _("Public API active") if active else _("No credentials active")
            )
            if not active:
                tooltip = _("Public username imports do not require provider credentials.")
            tone = "warning" if state["connection_tone"] == "warning" else "info" if active else "neutral"
    return {"key": "connection", "title": _("Connection"), "url": f"{base}?tab=connection", "tone": tone,
            "badge": _("Active") if active else _("Inactive"), "subtitle": subtitle, "detail": detail, "tooltip": tooltip}


def overview_cards(user, provider, overview=None):
    """Describe supported tabs once; every provider uses the same card renderer."""
    slug = provider["slug"]
    base = reverse("provider_detail", args=[slug])
    cards = []
    if provider.get("connection"):
        cards.append(_connection_card(user, provider, overview, base))
    latest = None
    if provider.get("import"):
        if overview is not None:
            count, summary, next_run, latest = (overview[key] for key in ("scheduleCount", "scheduleSummary", "nextImport", "latestImport"))
        else:
            sources = activity_sources(slug)
            activity = user.get_import_tasks(sources=sources, result_limit=1, refresh_results=False)
            schedules = [row for row in activity["schedules"] if not row["task"].one_off]
            count = len(schedules)
            summary = ngettext("%(count)s enabled schedule", "%(count)s enabled schedules", count) % {"count": count}
            next_runs = [row["next_run"] for row in schedules if row["next_run"]]
            next_run = user_datetime_format(min(next_runs), user) if next_runs else ""
            run = ImportRun.objects.filter(user=user, source__in=sources).only("status", "started_at").order_by("-started_at", "-pk").first()
            task = activity["results"][0] if activity["results"] else None
            if task and (not run or (task["date"] and task["date"] > run.started_at)):
                latest = {"status": _status(task["status"]), "date": user_datetime_format(task["date"], user)}
            elif run:
                latest = {"status": _status(run.status), "date": user_datetime_format(run.started_at, user)}
            if not count and provider["state"]["capabilities"].get("import") == "Scheduled import enabled":
                count, summary = 1, _("Scheduled import enabled")
        one_time = slug in ONE_TIME_IMPORTS
        cards.append({"key": "import", "title": _("Automatic import"), "url": f"{base}?tab=import",
                      "tone": "info" if count and not one_time else "neutral",
                      "badge": _("File only") if slug in FILE_ONLY_IMPORTS else _("One-time") if one_time else _("Active") if count else _("Inactive"),
                      "subtitle": _("Automatic imports are unavailable for file uploads") if slug in FILE_ONLY_IMPORTS else
                          _("Automatic imports are unavailable for this provider") if one_time else summary if count else _("No automatic imports configured"),
                      "detail": _("Next import") + ": " + next_run if next_run and not one_time else "", "tooltip": ""})
    if provider.get("activity") and provider.get("import"):
        cards.append({"key": "activity", "title": _("Last active import"), "url": f"{base}?tab=activity", "tone": "neutral", "badge": "",
                      "subtitle": latest["status"] if latest else _("No imports yet"), "detail": latest["date"] if latest else "", "tooltip": ""})
    for tab in provider["tabs"]:
        if tab in {"overview", "connection", "import"} or (tab == "activity" and provider.get("import")):
            continue
        if tab == "metadata":
            status = metadata_status(user, provider)
            cards.append({"key": tab, "title": _("Metadata"), "url": f"{base}?tab=metadata",
                          "tone": "info" if status["active"] else "neutral", "badge": _("Active") if status["active"] else _("Inactive"),
                          "subtitle": status["summary"], "detail": "", "tooltip": ""})
        else:
            cards.append({"key": tab, "title": _(tab.title()), "url": f"{base}?tab={tab}", "tone": "neutral", "badge": "",
                          "subtitle": _(provider["state"]["capabilities"].get(tab, "Available")), "detail": "", "tooltip": ""})
    tab_order = {tab: index for index, tab in enumerate(provider["tabs"])}
    cards.sort(key=lambda card: tab_order[card["key"]])
    return cards


def metadata_status(user, provider):
    """Distinguish selected primary sources from supplemental and unused sources."""
    enabled = metadata_resolution.provider_is_enabled(provider["slug"], user)
    if "metadata" in (provider.get("public_access") or {}).get("capabilities", ()):
        enabled = True
    elif provider["slug"] == "screenscraper":
        from app.game_artwork.screenscraper import developer_credentials

        enabled = bool(developer_credentials())
    if provider.get("metadata_role") == "imported":
        return {"active": True, "state": "imported", "summary": _("Metadata for imported items")}
    primary = any(provider["slug"] in {str(source.value) for source in config.get_sources(media) or []}
                  and metadata_resolution.metadata_default_source(user, media) == provider["slug"]
                  for media in provider["media_types"])
    if not enabled:
        return {"active": False, "state": "unconfigured", "summary": _("Instance artwork access is not configured") if provider["slug"] == "screenscraper" else _("Provider credentials required")}
    if primary:
        return {"active": True, "state": "active", "summary": _("Primary metadata source active")}
    if provider.get("metadata_role") == "supplemental":
        return {"active": True, "state": "active", "summary": _("Supplemental metadata active")}
    return {"active": False, "state": "not_selected", "summary": _("Another provider is selected")}


def metadata_contributions(provider, status, sources=()):
    """Describe the outputs Floppy actually consumes, not a provider's full API."""
    outputs = {
        "audiobookshelf": {"details", "covers"},
        "igdb": {"search", "details", "covers", "backgrounds", "gallery", "scores", "trailers"},
        "steamgriddb": {"logos", "backgrounds", "gallery"},
        "thegamesdb": {"logos", "backgrounds", "gallery"},
        "screenscraper": {"logos", "backgrounds", "gallery"},
        "steam": {"logos", "backgrounds"},
        "libretro": {"logos", "backgrounds"},
        "opencritic": {"scores"},
        "trakt": {"ratings", "popularity"},
        "lastfm": {"biographies"},
    }.get(provider["slug"], {"search", "details", "covers"} if provider.get("metadata_role") == "primary" else {"additional"})
    labels = {
        "search": _("Search results"), "details": _("Title details"), "covers": _("Cover images"),
        "logos": _("Title logos"), "backgrounds": _("Background images"),
        "gallery": _("Extra gallery images"), "scores": _("Critic scores"),
        "trailers": _("Trailers"), "additional": _("Additional metadata"),
        "ratings": _("Community ratings"), "popularity": _("Popularity scores"), "biographies": _("Artist biographies"),
    }
    notes = {}
    if provider["slug"] == "steam":
        notes = {key: _("For games linked to Steam") for key in outputs}
    elif provider["slug"] == "screenscraper":
        notes = {key: _("For retro games") for key in outputs}
    elif provider["slug"] == "libretro":
        notes = {"logos": _("For retro games, when a logo is available"), "backgrounds": _("Title screens or screenshots for retro games")}
    shown = outputs | ({"search", "details", "covers", "logos", "backgrounds"} if "game" in provider["media_types"] else set())
    if provider.get("metadata_role") == "imported":
        shown.add("search")
    selected = list(dict.fromkeys(source["currentLabel"] for source in sources if source["currentLabel"]))
    supplied_by = _("From %(providers)s") % {"providers": ", ".join(selected)} if selected else ""
    return [{
        "key": key, "label": label, "note": notes.get(key, ""),
        "status": supplied_by if supplied_by and ((status["state"] == "not_selected" and key in outputs) or
                    (status["state"] == "imported" and key == "search")) else
            _("Not provided") if key not in outputs else _("Active") if status["active"] else
            _("Not selected") if status["state"] == "not_selected" else _("Needs setup"),
        "tone": "neutral" if key not in outputs or status["state"] == "not_selected" else "success" if status["active"] else "warning",
    } for key, label in labels.items() if key in shown]


def metadata_page(user, provider, context):
    """Inline source/default controls; never serialize credentials or proxy values."""
    entries = []
    for entry in context["provider_summary"]:
        media = entry["media_type"]
        existing = entry["existing_count"]
        entries.append({
            "key": media, "title": str(media_type_readable_plural(media)), "current": entry["current_provider"],
            "currentLabel": str(entry["current_label"]), "editable": entry["configurable"] and not user.is_demo,
            "action": reverse("set_media_type_provider", args=[media]) if entry["configurable"] else "",
            "choices": [{"value": row["value"], "label": str(row["label"]), "configured": row["configured"],
                         "connectionUrl": context["metadata_source_connections"].get(row["value"], ""),
                         "imported": provider.get("metadata_role") == "imported" and row["value"] == provider["slug"]} for row in entry["choices"]],
            "animeMode": user.anime_library_mode if media == "anime" else None,
            "animeModes": [{"value": value, "label": str(label)} for value, label in context["anime_library_mode_choices"]],
            "existingCount": existing,
            "moveAction": reverse("convert_tv_library" if media == "tv" else "convert_anime_library") if existing and media in {"tv", "anime"} else "",
        })
    status = metadata_status(user, provider)
    return {
        "status": status, "sources": entries, "contributions": metadata_contributions(provider, status, entries),
        "editable": any(entry["editable"] for entry in entries),
        "supplemental": provider.get("metadata_role") == "supplemental",
        "imported": provider.get("metadata_role") == "imported",
        "proxy": {"configured": context["tmdb_proxy_configured"], "action": reverse("update_tmdb_proxy")} if provider["slug"] == "tmdb" and user.is_superuser else None,
    }


def anilist_activity(user, page=1, *, all_sources=False, activity=None):
    """Serialize existing activity controls with owned rows and bounded history pages."""
    sources = None if all_sources else {"anilist"}
    if activity is None:
        activity = user.get_import_tasks(sources=sources, result_limit=ACTIVITY_PAGE_SIZE + 1,
                                        result_offset=(page - 1) * ACTIVITY_PAGE_SIZE)
    runs_query = ImportRun.objects.filter(user=user)
    if sources is not None:
        runs_query = runs_query.filter(source__in=sources)
    runs = list(runs_query.defer(
        "prepared_state", "terminal_error",
    ).order_by("-started_at", "-id")[:10])
    active_tasks = TaskResult.objects.filter(status__in=[states.PENDING, states.STARTED],
        task_kwargs__regex=rf"[\"']user_id[\"']:\s*{user.pk}\s*[,}}]",
    ).exclude(Q(status=states.PENDING) & Q(date_created__lt=timezone.now() - timedelta(minutes=30)))
    active_tasks = active_tasks.filter(Q(task_name__startswith="Import ") | Q(task_name="integrations.tasks.import_goodreads")) if all_sources else active_tasks.filter(task_name="Import from AniList")
    active = any(row["status"] in {states.PENDING, states.STARTED} for row in activity["results"]) or runs_query.filter(status="running").exists() or any(
        helpers.task_kwargs_belong_to_user(kwargs, user.pk) for kwargs in active_tasks.values_list("task_kwargs", flat=True).iterator()
    )
    return {
        "page": page,
        "hasMore": len(activity["results"]) > ACTIVITY_PAGE_SIZE and page < MAX_ACTIVITY_PAGES,
        "active": active,
        "schedules": [{
            "source": row["source"], "sourceLabel": SOURCES_CONFIG.get(row["source"], {}).get("name", row["source"]),
            "sourceLogo": SOURCES_CONFIG.get(row["source"], {}).get("logo", ""),
            "id": row["task"].pk, "name": row["task"].name, "schedule": _(row["schedule"]),
            "username": row["username"], "mode": _(row["mode"]),
            "lastRun": user_datetime_format(row["last_run"], user),
            "nextRun": user_datetime_format(row["next_run"], user),
            "action": reverse("delete_import_schedule"),
        } for row in activity["schedules"]],
        "results": [{
            "source": row["source"], "sourceLabel": SOURCES_CONFIG.get(row["source"], {}).get("name", row["source"]),
            "sourceLogo": SOURCES_CONFIG.get(row["source"], {}).get("logo", ""),
            "id": row["task_id"], "status": row["status"], "statusLabel": _status(row["status"]),
            "date": user_datetime_format(row["date"], user),
            "summary": redact_secrets(str(translate_import_summary(row["summary"]) or "")),
            "errors": redact_secrets(str(row["errors"] or "")),
            "current": row.get("progress_current"), "total": row.get("progress_total"),
            "progressLabel": row.get("progress_label"), "percent": row.get("progress_percent"),
            "cancelAction": reverse("cancel_pending_import", args=[row["task_id"]]) if row["status"] == states.PENDING else "",
        } for row in activity["results"][:ACTIVITY_PAGE_SIZE]],
        "runs": [{
            "source": run.source, "sourceLabel": SOURCES_CONFIG.get(run.source, {}).get("name", run.source),
            "sourceLogo": SOURCES_CONFIG.get(run.source, {}).get("logo", ""),
            "id": run.pk, "status": run.status, "statusLabel": _status(run.status),
            "date": user_datetime_format(run.started_at, user), "created": run.created_count,
            "updated": run.updated_count, "skipped": run.skipped_count, "failed": run.failed_count,
            "remaining": run.remaining_estimate,
            "action": reverse("cancel_import_run" if run.status == "running" else "rollback_import_run", args=[run.pk]),
        } for run in runs],
        "cleanup": [{
            "source": row["source"], "sourceLabel": SOURCES_CONFIG.get(row["source"], {}).get("name", row["source"]),
            "sourceLogo": SOURCES_CONFIG.get(row["source"], {}).get("logo", ""),
            "confirm": (_("Permanently delete all %(media)s items imported from this source?") % {"media": row["media_type"]}
                + (" " + _("Artists and albums left with no tracks are removed from your library too.") if row["media_type"] == "music" else "")
                + " " + _("This cannot be undone.")) if all_sources else "",
            "mediaType": row["media_type"], "mediaLabel": str(media_type_readable(row["media_type"])), "count": row["count"],
            "action": reverse("bulk_delete_by_import_source", args=[row["media_type"], row["source"]]),
        } for row in views._import_source_media_summary(user, sources=sources)],
    }


CREDENTIAL_LABELS = (
    "Provider credentials", "Get a key from {name}", "Get a key", "Instance credentials", "Public API", "Editable", "Read-only",
    "Read-only: explain instance credential access", "These credentials are shared across the instance.", "(optional)",
    "Environment", "Bundled default", "Stored", "Not set", "Leave a field blank to keep the current fallback.", "Save",
    "Remove stored credentials", "Personal credentials", "Your key may avoid shared instance or bundled rate limits.", "Your",
    "Save personal key", "Make this the default provider for", "Close", "Instance credential access",
)
CREDENTIAL_ACCESS_LABELS = (
    "Only superusers can change instance-wide credentials. Values set in the environment are read-only here and take precedence over stored credentials.",
    "You are not a superuser", "Instance-wide keys can only be changed by a superuser. You can still add your own personal key.",
    "Ask an administrator, or promote your own account", "This instance has no superuser yet",
    "Run this on the host running the container:", "Run this where Floppy is installed:",
    "Then sign out and back in. Add --list to see accounts first.",
)


def provider_credentials(request, context):
    """Serialize one provider's credential form; stored values and previews stay server-side."""
    provider = context["metadata_provider"]
    slug = provider["slug"]
    user = request.user

    def fields(rows, personal=False):
        return [{
            "name": field["name"], "label": _(field["label"]), "secret": field["secret"], "required": field["required"],
            "placeholder": _(field["placeholder"]) if field["placeholder"] else "",
            "disabled": False if personal else field["locked"] or not context["can_edit_instance"],
            "environment": field["locked"], "setting": field["setting"], "stored": field["has_instance_value"],
            "usingDefault": field["using_default"], "fallback": field["has_fallback_value"],
        } for field in rows]

    editable = context["can_edit_instance"] and not provider["locked"]
    can_clear = context["can_edit_instance"] and provider["has_instance_value"]
    model = {
        "slug": slug, "label": provider["label"], "csrf": get_token(request), "next": request.get_full_path(),
        "editable": editable, "canClear": can_clear,
        "getKeyUrl": provider["docs_url"], "setupHint": _(provider["setup_hint"]) if provider["setup_hint"] else "",
        "publicAccess": bool(context.get("public_access")),
        "fields": fields(provider["fields"]),
        "personalFields": fields(provider["personal_fields"], personal=True) if provider["user_scope"] else None,
        "saveUrl": reverse("save_provider_credential", args=[slug]),
        "clearUrl": reverse("clear_provider_credential", args=[slug]),
        "personalUrl": reverse("save_personal_credential", args=[slug]),
        "defaults": [{"mediaType": media, "action": reverse("set_media_type_provider", args=[media]),
                      "label": _("Set as default for %(type)s") % {"type": media_type_readable_plural(media)}}
                     for media in provider["settable_media_types"]],
        "superuser": user.is_superuser, "promoteCommand": context.get("promote_command", ""),
        "hasSuperuser": context.get("instance_has_superuser", True),
        "inContainer": context.get("in_container", False),
        "labels": {text: _(text) for text in CREDENTIAL_LABELS if text != "Remove stored credentials" or can_clear},
    }
    model["labels"].update({text: _(text) for text in CREDENTIAL_ACCESS_LABELS if not editable})
    if not editable and user.is_superuser:
        model["labels"].pop("You are not a superuser", None)
    return model


def provider_page(request, provider, tab, credential_context=None):
    """Build safe browser props. Saved credential values/previews are never serialized."""
    user = request.user
    base = reverse("provider_detail", args=[provider["slug"]])
    react_content = tab in {"overview", "metadata"} or provider["slug"] == "anilist"
    page = {
        "provider": {"label": provider["label"], "description": provider["description"],
                     "logo": SOURCES_CONFIG.get(provider.get("logo_slug") or provider["slug"], {}).get("logo"), "media": provider["media_labels"]},
        "contentOwner": "react" if react_content else "server",
        "tab": tab,
        "tabs": [{"key": key, "label": _(key.title()), "url": f"{base}?tab={key}"} for key in provider["tabs"]],
        "urls": {key: f"{base}?tab={key}" for key in provider["tabs"]},
        "activityUrl": f"{base}?tab=activity&format=json",
        "csrf": get_token(request), "next": request.get_full_path(),
        "labels": _labels() if provider["slug"] == "anilist" else {
            "Media types": _("Media types"), "Provider sections": _("Provider sections"),
            "Continue Guided Setup": _("Continue Guided Setup"),
        },
    }
    if request.GET.get("onboarding"):
        page["onboardingUrl"] = reverse("onboarding_service_setup" if request.GET.get("setup_flow") == "import" else "onboarding_integration_setup")
    if tab == "overview":
        overview = anilist_overview(user) if provider["slug"] == "anilist" else None
        if overview is not None:
            page["overview"] = overview
        page["overviewCards"] = overview_cards(user, provider, overview)
    elif tab == "metadata":
        page["metadata"] = metadata_page(user, provider, credential_context)
        page["labels"].update({text: _(text) for text in (
            "Metadata settings", "Metadata sources", "Provider settings", "Library sources", "Editable", "Read-only", "Active", "Ready", "Not configured",
            "Provider for new titles", "Anime Library", "Save", "Configured", "Not configured", "TMDB Proxy", "Proxy URL",
            "Supplemental metadata", "Library metadata",
            "Each library uses one primary source. Supplemental providers keep their separate roles.",
            "Used for new titles. Existing entries keep their provider until you move them.",
            "This library uses its supported source directly; the default cannot be changed.",
            "Default for new titles", "Imported items", "Available", "Default", "Configure", "Close",
            "Choose where new titles come from. Existing titles keep their original provider.",
            "The default cannot be changed. Imported titles keep their original source.",
            "This provider supplies metadata for its imported items; library defaults apply to new searches.",
            "This provider adds metadata alongside the primary source; it does not replace it.",
            "This provider has no additional settings.", "Inactive",
            "Existing entries on another provider or format", "Moving preserves history when supported; entries that cannot be moved safely are left unchanged.",
            "Converting rebuilds entries. Moving to MyAnimeList drops per-episode history; unsafe conversions are left unchanged.",
            "Move existing shows", "Convert existing anime", "Route TMDB requests through an HTTP or SOCKS5 proxy. Leave blank and save to remove it.",
        )})
    elif not react_content:
        return page
    elif tab == "connection":
        context = credential_context
        provider = context["metadata_provider"]
        page["credentials"] = provider_credentials(request, context)
    elif tab == "import":
        configured = credentials.is_configured("anilist", user)
        preferences = import_preferences(request, "anilist")
        page["labels"].update(preferences["labels"])
        page["imports"] = {
            **preferences, "configured": configured,
            "publicUrl": reverse("import_anilist_public"), "privateUrl": reverse("import_anilist_oauth") if configured else "",
        }
    return page


def _labels():
    """Mark presentation text for translation before it reaches shared components."""
    return {
        "Connection": _("Connection"),
        "Automatic import": _("Automatic import"),
        "Last active import": _("Last active import"),
        "Active": _("Active"),
        "Inactive": _("Inactive"),
        "Personal credentials active": _("Personal credentials active"),
        "Instance credentials active": _("Instance credentials active"),
        "No credentials active": _("No credentials active"),
        "Uses instance fallback for fields without a personal value.": _("Uses instance fallback for fields without a personal value."),
        "Public username imports do not require provider credentials.": _("Public username imports do not require provider credentials."),
        "No automatic imports configured": _("No automatic imports configured"),
        "Next import": _("Next import"),
        "No imports yet": _("No imports yet"),
        "Provider credentials": _("Provider credentials"),
        "Get a key from AniList": _("Get a key from AniList"),
        "Instance credentials": _("Instance credentials"),
        "Personal credentials": _("Personal credentials"),
        "Editable": _("Editable"),
        "Read-only": _("Read-only"),
        "Public API": _("Public API"),
        "Read-only: explain instance credential access": _("Read-only: explain instance credential access"),
        "Instance credential access": _("Instance credential access"),
        "Close": _("Close"),
        "These credentials are shared across the instance.": _("These credentials are shared across the instance."),
        "Environment": _("Environment"),
        "Stored": _("Stored"),
        "Not set": _("Not set"),
        "Save": _("Save"),
        "Remove stored credentials": _("Remove stored credentials"),
        "Your": _("Your"),
        "Save personal key": _("Save personal key"),
        "Your key may avoid shared instance or bundled rate limits.": _("Your key may avoid shared instance or bundled rate limits."),
        "Leave a field blank to keep the current fallback.": _("Leave a field blank to keep the current fallback."),
        "Saved: enter a new value to replace": _("Saved: enter a new value to replace"),
        "Only superusers can change instance-wide credentials. Values set in the environment are read-only here and take precedence over stored credentials.": _("Only superusers can change instance-wide credentials. Values set in the environment are read-only here and take precedence over stored credentials."),
        "You are not a superuser": _("You are not a superuser"),
        "Ask an administrator, or promote your own account": _("Ask an administrator, or promote your own account"),
        "This instance has no superuser yet": _("This instance has no superuser yet"),
        "Instance-wide keys can only be changed by a superuser. You can still add your own personal key.": _("Instance-wide keys can only be changed by a superuser. You can still add your own personal key."),
        "Run this on the host running the container:": _("Run this on the host running the container:"),
        "Run this where Floppy is installed:": _("Run this where Floppy is installed:"),
        "Then sign out and back in. Add --list to see accounts first.": _("Then sign out and back in. Add --list to see accounts first."),
        "Import settings": _("Import settings"),
        "Import preferences": _("Import preferences"),
        "Import options": _("Import options"),
        "Import Frequency": _("Import Frequency"),
        "Import Time": _("Import Time"),
        "Import Mode": _("Import Mode"),
        "One Time Import": _("One Time Import"),
        "Every Day": _("Every Day"),
        "Every 2 Days": _("Every 2 Days"),
        "Only Sync New Items": _("Only Sync New Items"),
        "Sync New Items and Overwrite Existing": _("Sync New Items and Overwrite Existing"),
        "Import Watchlist Data Only": _("Import Watchlist Data Only"),
        "Update Collection Metadata Only": _("Update Collection Metadata Only"),
        "Saving…": _("Saving…"),
        "Saved": _("Saved"),
        "Could not save preferences. Try again.": _("Could not save preferences. Try again."),
        "Retry": _("Retry"),
        "Import anime and manga from your AniList profile.": _("Import anime and manga from your AniList profile."),
        "Import private profile (requires authentication)": _("Import private profile (requires authentication)"),
        "Public": _("Public"),
        "Private": _("Private"),
        "Public username": _("Public username"),
        "Public AniList username": _("Public AniList username"),
        "Import": _("Import"),
        "Connect with AniList & Import": _("Connect with AniList & Import"),
        "Private imports require provider credentials.": _("Private imports require provider credentials."),
        "Configure Connection": _("Configure Connection"),
        "This import mode is not supported by AniList.": _("This import mode is not supported by AniList."),
        "Continue Guided Setup": _("Continue Guided Setup"),
        "Active Periodic Imports": _("Active Periodic Imports"),
        "Import History": _("Import History"),
        "Recent Import Runs": _("Recent Import Runs"),
        "Counts and undo options for supported imports.": _("Counts and undo options for supported imports."),
        "Imported Media by Source": _("Imported Media by Source"),
        "Loading import activity…": _("Loading import activity…"),
        "Could not load import activity.": _("Could not load import activity."),
        "No periodic imports configured": _("No periodic imports configured"),
        "Showing import activity from the last 7 days.": _("Showing import activity from the last 7 days."),
        "No imports in the last 7 days": _("No imports in the last 7 days"),
        "Username": _("Username"),
        "Mode": _("Mode"),
        "Last Import": _("Last Import"),
        "Next Import": _("Next Import"),
        "Remove schedule": _("Remove schedule"),
        "Show details": _("Show details"),
        "Hide details": _("Hide details"),
        "Show traceback": _("Show traceback"),
        "Hide traceback": _("Hide traceback"),
        "Copy": _("Copy"),
        "Copied!": _("Copied!"),
        "Cancel import": _("Cancel import"),
        "Cancel this import?": _("Cancel this import?"),
        "Undo this import": _("Undo this import"),
        "Remove the items this import created? This cannot be undone.": _("Remove the items this import created? This cannot be undone."),
        "Created": _("Created"),
        "Updated": _("Updated"),
        "Skipped": _("Skipped"),
        "Failed": _("Failed"),
        "Remaining": _("Remaining"),
        "Delete all": _("Delete all"),
        "Previous": _("Previous"),
        "Next": _("Next"),
        "Permanently delete all items of this media type imported from AniList? This cannot be undone.": _("Permanently delete all items of this media type imported from AniList? This cannot be undone."),
        "Permanently delete everything of a media type imported from this source.": _("Permanently delete everything of a media type imported from this source."),
        "Could not copy details.": _("Could not copy details."),
        "Media types": _("Media types"),
        "Provider sections": _("Provider sections"),
    }
