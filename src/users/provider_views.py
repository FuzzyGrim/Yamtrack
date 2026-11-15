"""Provider settings navigation using the existing forms and action handlers."""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from app.log_safety import redact_secrets
from users import metadata_views, provider_forms, provider_guides, provider_ui, views
from users.provider_catalog import (
    ACCOUNT_IMPORTS,
    CAPABILITIES,
    MEDIA_CHOICES,
    catalog,
    provider_state,
)
from users.templatetags.user_tags import user_datetime_format


@require_GET
def providers(request):
    """Search all supported providers by capability and library type."""
    query = request.GET.get("q", "").strip()
    directory_filters = ("connection", *CAPABILITIES)
    capability = request.GET.get("capability", "")
    if capability not in directory_filters:
        capability = ""
    media = request.GET.get("media", "")
    if media not in dict(MEDIA_CHOICES):
        media = ""
    items = catalog()
    if query:
        items = [
            item
            for item in items
            if query.casefold()
            in (
                item["label"] + " " + item["description"] + " " + item["card_description"]
            ).casefold()
        ]
    if capability in directory_filters:
        items = [item for item in items if item.get(capability)]
    if media:
        items = [item for item in items if media in item["media_types"]]
    for item in items:
        item["state"] = provider_state(item, request.user)
        item["pinned"] = item["slug"] in (request.user.pinned_settings_providers or [])
    items.sort(key=lambda item: not item["pinned"])
    context = {
        "providers": items,
        "query": query,
        "capability": capability,
        "media": media,
        "capability_options": [{"key": "", "label": "All Capabilities"}]
        + [{"key": key, "label": key.title()} for key in directory_filters],
        "media_options": [{"key": "", "label": "All Types"}]
        + [{"key": key, "label": label} for key, label in MEDIA_CHOICES],
        "capability_label": capability.title() if capability else "All Capabilities",
        "media_label": dict(MEDIA_CHOICES).get(media, "All Types"),
    }
    context["provider_directory"] = provider_ui.directory_page(context, request)
    if request.GET.get("format") == "json":
        response = JsonResponse(context["provider_directory"])
        response["Cache-Control"] = "private, no-store"
        return response
    template = (
        "users/components/provider_results.html"
        if request.headers.get("HX-Target") == "provider-results"
        else "users/providers.html"
    )
    return render(request, template, context)


@login_required
@require_POST
def pin_provider(request):
    """Set one user's provider pin, independently of watch-service favorites."""
    slug = request.POST.get("provider", "")
    desired = request.POST.get("pinned", "")
    if slug not in {item["slug"] for item in catalog()} or desired not in {"0", "1"}:
        return HttpResponse(status=400)
    if request.user.is_demo:
        return HttpResponse(status=403)
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        pinned = list(user.pinned_settings_providers or [])
        if desired == "1" and slug not in pinned:
            pinned.append(slug)
        elif desired == "0" and slug in pinned:
            pinned.remove(slug)
        user.pinned_settings_providers = pinned
        user.save(update_fields=["pinned_settings_providers"])
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"pinned": desired == "1"})
    from app.helpers import safe_return_url

    return redirect(safe_return_url(request, request.POST.get("next", ""), reverse("providers")))


MAX_PINNED_LISTS = 200


def clean_sidebar_pins(data):
    """Return only well-formed sidebar pins from untrusted ``data``."""
    from app.sidebar_model import PIN_KEYS

    data = data if isinstance(data, dict) else {}
    nav = [key for key in dict.fromkeys(data.get("nav") or []) if key in PIN_KEYS] if isinstance(data.get("nav"), list) else []
    lists, seen = [], set()
    for item in data.get("lists") if isinstance(data.get("lists"), list) else []:
        if not isinstance(item, dict):
            continue
        ident, name, url = (item.get(key) for key in ("id", "name", "url"))
        if (
            not all(isinstance(value, str) for value in (ident, name, url))
            or ident in seen
            or not url.startswith("/")
            or url.startswith("//")
            or len(lists) >= MAX_PINNED_LISTS
        ):
            continue
        seen.add(ident)
        lists.append({"id": ident[:64], "name": name[:255], "url": url[:512]})
    return {"nav": nav, "lists": lists}


@login_required
@require_POST
def save_sidebar_pins(request):
    """Store the user's sidebar pins so every browser shows the same sidebar."""
    if request.user.is_demo:
        return HttpResponse(status=403)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponse(status=400)
    pins = clean_sidebar_pins(data)
    request.user.sidebar_pins = pins
    request.user.save(update_fields=["sidebar_pins"])
    return JsonResponse(pins)


INSTANCE_CONNECTIONS = {
    "radarr": ("Radarr", "Sync owned movies from Radarr every 2 hours and reconcile quality metadata with Plex.",
               "Connect %(name)s using your server URL and API key.", "Connect another %(name)s instance.",
               "https://radarr.local:7878", "Radarr API key"),
    "sonarr": ("Sonarr", "Sync owned TV collection from Sonarr every 2 hours and reconcile ownership with Plex.",
               "Connect %(name)s using your server URL and API key.", "Connect another %(name)s instance.",
               "https://sonarr.local:8989", "Sonarr API key"),
    "mylar": ("Mylar3", "Sync the comic issues Mylar3 has downloaded every 2 hours and mark them as owned in your collection.",
              "Connect %(name)s using your server URL and API key (Mylar3 Settings, Web Interface, API).", "Connect another %(name)s instance.",
              "https://mylar.local:8090", "Mylar3 API key"),
    "kapowarr": ("Kapowarr", "Sync the comic issues Kapowarr has downloaded every 2 hours and mark them as owned in your collection.",
                 "Connect %(name)s using your server URL and API key (Kapowarr Settings, General, Security).", "Connect another %(name)s instance.",
                 "https://kapowarr.local:5656", "Kapowarr API key"),
}


SYNC_INTERVALS = (("5", "5 minutes"), ("15", "15 minutes"), ("30", "30 minutes"), ("60", "60 minutes"))

# slug: (button label, fields as (name, label, input type, placeholder, required[, select options])).
ACCOUNT_CONNECTIONS = {
    "pocketcasts": ("Connect Pocket Casts", (("email", "Email", "email", "Your Pocket Casts email", True),
                                              ("password", "Password", "password", "Your Pocket Casts password", True))),
    "koito": ("Connect Koito", (("base_url", "Koito Server URL", "url", "https://koito.example.com", True),
                                ("api_key", "API Key", "password", "Koito API key", True))),
    "lastfm": ("Connect Last.fm", (("lastfm_username", "Last.fm Username", "text", "Your Last.fm username", True),)),
    "psn": ("Connect PlayStation", (("npsso", "NPSSO token", "password", "NPSSO token", False),)),
    "storyteller": ("Connect Storyteller", (("server_url", "Server URL", "url", "https://storyteller.example.com", True),)),
    "kavita": ("Connect Kavita", (("base_url", "Server URL", "url", "https://kavita.example.com", True),
                                  ("api_key", "API Key", "password", "Kavita API key", True),
                                  ("sync_interval_minutes", "Sync every", "select", "", True, SYNC_INTERVALS))),
    "komga": ("Connect Komga", (("base_url", "Server URL", "url", "https://komga.example.com", True),
                                ("api_key", "API Key", "password", "Komga API key", True),
                                ("sync_interval_minutes", "Sync every", "select", "", True, SYNC_INTERVALS))),
    "xbox": ("Connect Xbox", (("api_key", "OpenXBL API key", "password", "OpenXBL API key", False),)),
    "stremio": ("Connect Stremio", (("email", "Email", "email", "Stremio email", False),
                                    ("password", "Password", "password", "Stremio password", False),
                                    ("auth_key", "Auth key (optional)", "password", "Stremio auth key", False))),
}


def _flag(value):
    """Model flags are properties on some accounts and methods on others."""
    return value() if callable(value) else value


@require_GET
def provider_detail(request, slug):
    """Render one provider with only its supported capability tabs."""
    provider = next((item for item in catalog() if item["slug"] == slug), None)
    if provider is None:
        raise Http404
    provider["state"] = provider_state(provider, request.user)
    tab = request.GET.get("tab", "overview")
    if tab not in provider["tabs"]:
        raise Http404
    if slug == "anilist" and request.GET.get("format") == "json":
        if tab != "activity":
            raise Http404
        try:
            page = int(request.GET.get("page", "1"))
        except ValueError as error:
            raise Http404 from error
        if not 1 <= page <= provider_ui.MAX_ACTIVITY_PAGES:
            raise Http404
        response = JsonResponse(provider_ui.anilist_activity(request.user, page))
        response["Cache-Control"] = "private, no-store"
        return response
    context = {
        "provider": provider,
        "provider_slug": slug,
        "provider_tab": tab,
        "inline_import": tab == "import",
        "activity_source": slug,
        "provider_navigation": True,
        "public_access": {"description": _(provider["public_access"]["description"])} if provider.get("public_access") else None,
        "capability_rows": [
            {"key": key, "label": key.title(), "state": state}
            for key, state in provider["state"]["capabilities"].items()
        ],
    }
    if tab == "metadata" or (tab == "connection" and provider.get("credential")):
        related_media = provider.get("metadata_related_media", ())
        if provider.get("metadata_role") == "supplemental":
            related_media = provider["media_types"]
        context.update(metadata_views.metadata_context(request.user, slug, related_media))
        if tab == "connection" and slug != "anilist" and context.get("metadata_provider"):
            context["credentials_model"] = provider_ui.provider_credentials(request, context)
    if (
        tab == "import"
        or (tab == "connection" and slug in ACCOUNT_IMPORTS and slug != "mal")
    ) and provider.get("import_slug"):
        if slug == "anilist":
            context["plex_sections_json"] = "[]"
        else:
            context.update(views.import_data_context(request))
        context["import_slug"] = provider["import_slug"]
    if (tab == "activity" and slug != "anilist") or (
        provider.get("integration")
        and tab in {"connection", "import", "tracking", "sync"}
    ):
        context.update(
            views.integrations_context(
                request, slug, refresh_plex=tab not in {"import", "activity"}
            )
        )
    if tab == "connection" and slug == "audiobookshelf":
        account = context.get("audiobookshelf_account")
        context["account_connection"] = provider_ui.account_connection(
            request, connected=bool(account and account.is_connected), saved=bool(account),
            attention=bool(account and account.connection_broken),
            fields=[{"name": "base_url", "label": _("Base URL"), "type": "url", "placeholder": _("Server URL")},
                    {"name": "api_token", "label": _("API token"), "type": "password", "placeholder": _("API token")}],
            messages={"disconnected": _("Connect your account to enable imports."),
                      "connected": _("Your account is connected and imports run automatically."),
                      "attention": _("Reconnect to restore automatic imports.")},
            follow_up={"title": _("Automatic import"),
                       "description": _("Connecting starts an initial import and recurring sync."),
                       "label": _("View activity"),
                       "url": reverse("provider_detail", args=["audiobookshelf"]) + "?tab=activity"},
            connect_url=reverse("audiobookshelf_connect"), disconnect_url=reverse("audiobookshelf_disconnect"),
        )
    if tab == "connection" and slug in INSTANCE_CONNECTIONS:
        label, intro, first, another, url_hint, key_label = INSTANCE_CONNECTIONS[slug]
        instances = context.get(f"{slug}_instances", [])
        context["account_connection"] = provider_ui.account_connection(
            request, connected=any(_flag(row.is_connected) for row in instances), saved=bool(instances),
            attention=any(row.last_error_message for row in instances),
            fields=[{"name": "name", "label": _("Label (optional)"), "type": "text", "placeholder": _("Label (optional)"), "required": False},
                    {"name": "base_url", "label": _("Server URL"), "type": "url", "placeholder": url_hint},
                    {"name": "api_key", "label": key_label, "type": "password", "placeholder": key_label}],
            messages={"disconnected": _(first) % {"name": label}, "connected": _(another) % {"name": label},
                      "attention": _(another) % {"name": label}},
            follow_up=None, connect_url=reverse(f"{slug}_connect"), disconnect_url=reverse(f"{slug}_disconnect"),
            description=_(intro),
            instances=[{"id": row.pk, "name": row.display_name, "connected": bool(_flag(row.is_connected)), "url": row.base_url,
                        "error": redact_secrets(row.last_error_message or "")} for row in instances],
        )
        context["account_connection"]["submitLabel"] = _("Add Instance") if instances else _("Connect %(name)s") % {"name": label}
    if tab == "connection" and slug in ACCOUNT_CONNECTIONS:
        button, rows = ACCOUNT_CONNECTIONS[slug]
        account = context.get(f"{slug}_account")
        context["account_connection"] = provider_ui.account_connection(
            request, connected=bool(account and _flag(account.is_connected)), saved=bool(account),
            attention=bool(account and getattr(account, "connection_broken", False)),
            fields=[{"name": row[0], "label": _(row[1]), "type": row[2], "placeholder": _(row[3]) if row[3] else "", "required": row[4],
                     **({"options": [{"value": value, "label": _(text)} for value, text in row[5:][0]], "value": "15"} if row[5:] else {})}
                    for row in rows],
            summary={"url": getattr(account, "base_url", "") or getattr(account, "server_url", ""),
                     "lastSync": user_datetime_format(account.last_sync_at, request.user) if getattr(account, "last_sync_at", None) else "",
                     "error": redact_secrets(getattr(account, "last_error_message", "") or "")} if account else None,
            messages={"disconnected": _("Connect your account to enable imports."),
                      "connected": _("Your account is connected and imports run automatically."),
                      "attention": _("Reconnect to restore automatic imports.")},
            follow_up={"title": _("Automatic import"), "description": _("Connecting starts an initial import and recurring sync."),
                       "label": _("View activity"), "url": reverse("provider_detail", args=[slug]) + "?tab=activity"},
            connect_url=reverse(f"{slug}_connect"), disconnect_url=reverse(f"{slug}_disconnect"),
        )
        context["account_connection"]["submitLabel"] = _(button)
    if tab == "connection" and slug == "plex":
        account = context.get("plex_account")
        connected = bool(account and account.plex_token)
        badges = []
        notes = {}
        if account:
            badges.append({
                "label": _("Watchlist sync on") if account.watchlist_sync_enabled else _("Watchlist sync off"),
                "tone": "info" if account.watchlist_sync_enabled else "neutral",
                "tooltip": _("Sync the connected Plex account's watchlist into Planning every 15 minutes. Plex usernames still control webhook filtering."),
            })
            if account.watchlist_last_synced_at:
                notes["lastSync"] = _("Last watchlist sync: %(when)s") % {"when": user_datetime_format(account.watchlist_last_synced_at, request.user)}
        context["account_connection"] = provider_ui.account_connection(
            request, connected=connected, saved=bool(account),
            attention=bool(account and getattr(account, "connection_broken", False)),
            fields=[], messages={
                "disconnected": _("Connect your Plex account to import watch history, load your libraries and sync your watchlist."),
                "connected": _("Your Plex account is connected."),
                "attention": _("Plex rejected the saved sign-in. Reconnect to restore library loading and imports.")},
            follow_up=None, connect_url=reverse("plex_connect"), disconnect_url=reverse("plex_disconnect"),
            summary={"url": "", "lastSync": notes.get("lastSync", ""),
                     "error": redact_secrets(account.watchlist_last_error or "") if account else ""} if account else None,
        )
        context["account_connection"]["badges"] = badges
        context["account_connection"]["title"] = _("Plex account")
        context["account_connection"]["submitLabel"] = _("Reconnect Plex") if account else _("Connect Plex")
    if tab == "connection" and slug == "jellyfin":
        account = context.get("jellyfin_account")
        connected = bool(account and _flag(account.is_connected))
        context["account_connection"] = provider_ui.account_connection(
            request, connected=connected, saved=bool(account), attention=bool(account and account.connection_broken),
            fields=[{"name": "base_url", "label": _("Server URL"), "type": "url", "placeholder": "https://jellyfin.local:8096",
                     "value": account.base_url if account else ""},
                    {"name": "api_key", "label": _("API key"), "type": "password", "placeholder": _("API key")},
                    {"name": "username", "label": _("Username"), "type": "text", "required": False,
                     "placeholder": _("Only needed for server-admin keys"), "value": account.jellyfin_username if account else ""}],
            messages={"disconnected": _("Connect Jellyfin using your server URL and API key. If the key doesn't resolve a user automatically, also provide the exact Jellyfin username."),
                      "connected": _("Your Jellyfin server is connected."),
                      "attention": _("Jellyfin rejected the API key. Floppy re-checks it on every scheduled run; reconnect if the key was changed or revoked.")},
            follow_up=None, connect_url=reverse("jellyfin_connect"), disconnect_url=reverse("jellyfin_disconnect"),
            summary={"url": " • ".join(filter(None, [account.base_url, account.jellyfin_username])),
                     "lastSync": _("Last sync: %(when)s") % {"when": user_datetime_format(account.last_sync_at, request.user)} if account.last_sync_at else "",
                     "error": redact_secrets(account.last_error_message or "")} if account else None,
        )
        context["account_connection"]["title"] = _("Jellyfin account")
        context["account_connection"]["editable"] = connected
        context["account_connection"]["submitLabel"] = _("Update connection") if account else _("Connect Jellyfin")
        context["account_connection"]["labels"]["Edit connection"] = _("Edit connection")
        context["account_connection"]["labels"]["Cancel"] = _("Cancel")
    if tab == "connection" and slug == "mdblist":
        context["file_import"] = provider_ui.file_import(request, slug, provider_ui.import_preferences(request, slug, context), context)
    if tab == "connection" and slug == "koreader":
        account = context.get("koreader_account")
        connected = bool(account and _flag(account.is_connected))
        context["account_connection"] = provider_ui.account_connection(
            request, connected=connected, saved=bool(account), attention=bool(account and getattr(account, "last_error_message", "")),
            fields=[], messages={
                "disconnected": _("Connect your self-hosted KOReader sync server. Enable Send document metadata in KOReader for automatic matching on supported servers."),
                "connected": _("Sync reading progress using your import schedule."),
                "attention": _("Reconnect to restore automatic imports.")},
            follow_up=None, connect_url="", disconnect_url=reverse("koreader_disconnect"),
            summary={"url": account.server_url, "lastSync": user_datetime_format(account.last_sync_at, request.user) if account.last_sync_at else "",
                     "error": redact_secrets(account.last_error_message or "")} if account else None,
        )
    if tab == "connection" and slug == "gpodder":
        account = context.get("gpodder_account")
        context["account_connection"] = provider_ui.account_connection(
            request, connected=bool(account and _flag(account.is_connected)), saved=bool(account),
            attention=bool(account and not _flag(account.is_connected)),
            fields=[{"name": "server_url", "label": _("Server URL"), "type": "url", "placeholder": _("Server URL")},
                    {"name": "username", "label": _("Username"), "type": "text", "placeholder": _("Your GPodder username")},
                    {"name": "password", "label": _("Password / App Password"), "type": "password", "placeholder": _("Your password or app password")},
                    {"name": "device_filter", "label": _("Optional Device Filter"), "type": "text", "placeholder": _("antenna-pod-device"), "required": False}],
            messages={"disconnected": _("Recurring podcast history sync for GPodder, AntennaPod, and self-hosted compatible servers every 2 hours."),
                      "connected": _("Recurring podcast history sync for GPodder, AntennaPod, and self-hosted compatible servers every 2 hours."),
                      "attention": _("Reconnect to restore automatic imports.")},
            follow_up={"title": _("Automatic import"), "description": _("Connecting starts recurring history sync."),
                       "label": _("View activity"), "url": reverse("provider_detail", args=["gpodder"]) + "?tab=activity"},
            connect_url=reverse("gpodder_connect"), disconnect_url=reverse("gpodder_disconnect"),
        )
        context["account_connection"]["submitLabel"] = _("Connect GPodder")
    if tab in {"connection", "tracking"} and slug in {"emby", "kodi", "navidrome", "nuvio"}:
        context["setup_guides"], context["guide_copy"] = provider_guides.setup_guides(request, slug, context)
    if (tab == "connection" and slug in {"jellyfin", "seerr", "plex"}) or (tab in {"connection", "import", "tracking"} and slug == "stremio"):
        context["setup_guides"], context["guide_copy"] = provider_guides.setup_guides(request, slug, context)
    if slug in {"plex", "jellyfin", "seerr", "koreader", "hardcover"}:
        context["provider_forms"] = provider_forms.build(request, slug, tab, context)
    if tab == "import" and slug != "anilist":
        context["import_preferences"] = provider_ui.import_preferences(request, slug, context)
        if slug in provider_ui.FILE_IMPORTS:
            context["file_import"] = provider_ui.file_import(request, slug, context["import_preferences"], context)
        if slug == "clz":
            preferences = context["import_preferences"]
            context["clz_import"] = {
                "action": reverse("import_clz"), "csrf": get_token(request), "next": request.get_full_path(),
                "values": preferences["values"], "website": "https://clz.com/",
                "intro": _("Import a CSV or XML export from"), "sourceLabel": _("CLZ (formerly Collectorz.com)"),
                "description": _("Columns Floppy has no built-in home for become custom collection fields."),
                "mediaField": "clz_media_type", "fileField": "clz_export", "accept": ".csv,.txt,.xml",
                "fieldLabel": _("Import as"), "selectLabel": _("Select CLZ Export"),
                "allowedModes": ["new", "overwrite"], "defaultMode": "new", "frequency": "once",
                "postFields": [{"name": "mode", "key": "mode"}, {"name": "time", "key": "time"},
                               {"name": "clz_wishlist_status", "key": "clzWishlistStatus"},
                               {"name": "clz_wishlist_tag", "key": "clzWishlistTag"}],
                "mediaTypes": [{"value": value, "label": _(label)} for value, label in (
                    ("", "Detect from the export's columns"), ("comicissue", "Comics"), ("manga", "Manga"),
                    ("book", "Books"), ("movie", "Movies"), ("game", "Games"),
                )],
                "labels": {text: _(text) for text in (
                    "Import as", "Select CLZ Export", "Import a CSV or XML export from", "CLZ (formerly Collectorz.com)",
                    "Columns Floppy has no built-in home for become custom collection fields.",
                )},
            }
        if slug == "audiobookshelf":
            account = context.get("audiobookshelf_account")
            connected = bool(account and account.is_connected)
            context["account_import_action"] = {
                "description": _("Import the latest audiobook progress from your connected account.") if connected else
                    _("Connect this provider before starting an import."),
                "label": _("Sync now") if connected else _("Open Connection"),
                "action": reverse("import_audiobookshelf") if connected else "",
                "href": "" if connected else reverse("provider_detail", args=[slug]) + "?tab=connection",
                "csrf": get_token(request), "next": request.get_full_path(),
            }
    if slug in {"mal", "tmdb", "wetrakr"} and tab in {"connection", "sync", "activity"}:
        context.update(views.mal_export_context(request, slug))
    if slug == "mal" and tab == "connection":
        account = context.get("mal_account")
        connected = bool(account and _flag(account.is_connected))
        messages = {
            "connected": _("Connected as %(name)s.") % {"name": (account.mal_username if account else "") or _("MyAnimeList user")},
            "attention": _("MyAnimeList authorization needs attention."),
            "disconnected": _("This authorizes outgoing writes and enables automatic status, progress and score updates. Adding API credentials alone does not enable sync.")
            if context.get("mal_sync_configured") else _("Add a MyAnimeList Client ID and Client secret to enable account authorization."),
        }
        context["account_connection"] = provider_ui.account_connection(
            request, connected=connected, saved=bool(account), attention=bool(account and account.connection_broken),
            fields=[], messages=messages, follow_up=None,
            connect_url=reverse("mal_oauth") if context.get("mal_sync_configured") else "", disconnect_url=reverse("mal_disconnect"),
            summary={"url": "", "lastSync": "", "error": redact_secrets(account.last_error_message or "")} if account and account.connection_broken else None,
        )
        context["account_connection"]["title"] = _("MyAnimeList account")
        context["account_connection"]["submitLabel"] = _("Authorize and enable MyAnimeList sync")
    if slug == "wetrakr" and tab == "connection":
        account = (context.get("wetrakr_sync") or {}).get("account")
        pending = bool(account and account.is_pending)
        connected = bool(account and account.is_connected)
        attention = bool(account and account.connection_broken and not pending)
        context["account_connection"] = provider_ui.account_connection(
            request, connected=connected, saved=bool(account) and not pending, attention=attention,
            fields=[], follow_up=None, connect_url=reverse("wetrakr_connect"), disconnect_url=reverse("wetrakr_disconnect"),
            link_slug="wetrakr", link_label=provider["label"],
            messages={
                "connected": _("Your WeTrakr account is linked. Choose what Floppy sends on the Sync tab."),
                "attention": _("WeTrakr rejected your login. Reconnect to continue."),
                "disconnected": _("Link your WeTrakr account to send your plays, statuses and ratings."),
            },
            summary={"url": "", "lastSync": "", "error": redact_secrets(account.last_error_message or "")} if attention and account.last_error_message else None,
            device=provider_ui.device_login(account.user_code, account.verification_url, account.device_interval, account.device_expires_at) if pending else None,
        )
        context["account_connection"]["title"] = _("WeTrakr account")
        context["account_connection"]["submitLabel"] = _("Connect WeTrakr")
    context["provider_page"] = provider_ui.provider_page(request, provider, tab, credential_context=context)
    if getattr(request, "provider_context_only", False):
        # The native JSON API reuses this view model instead of rebuilding it.
        return context
    return render(request, "users/provider_detail.html", context)


@require_GET
def activity(request):
    """Show the same operational records used by provider Activity tabs."""
    context = views.integrations_context(request, refresh_plex=False)
    context.update(views.mal_export_context(request))
    return render(request, "users/provider_activity.html", context)


@require_GET
def data_management(request):
    """Keep file exports, restore and library repair together."""
    context = views.export_data_context(request)
    context.update(views.import_data_context(request))
    context.update(metadata_views.metadata_context(request.user))
    context.update(
        {
            "import_slug": "yamtrack",
            "deletable_media_types": views.DELETABLE_MEDIA_TYPES,
            "import_source_media": views._import_source_media_summary(request.user),
        }
    )
    return render(request, "users/data_management.html", context)


@require_GET
def access_tokens(request):
    """Manage credentials granting clients access to Floppy."""
    context = views.integrations_context(
        request, include_tokens=True, refresh_plex=False
    )
    return render(request, "users/access_tokens.html", context)


@require_GET
def legacy_settings(request, section):
    """Keep bookmarks and action redirects working during settings relocation."""
    params = request.GET.copy()
    if params.get("onboarding"):
        params["setup_flow"] = "import" if section == "import" else "tracking"
    opened = params.get("open", "")
    slug = {"myanimelist": "mal"}.get(opened, opened)
    known = {item["slug"]: item for item in catalog()}
    destination = "providers"
    args = []
    if section == "import" and opened == "yamtrack":
        destination = "data_management"
    elif section == "integrations" and not opened:
        params["capability"] = "tracking"
    elif section == "export":
        destination = "data_management"
    elif section == "metadata" and opened.startswith("media-type-"):
        media_type = opened.removeprefix("media-type-")
        candidates = [
            item
            for item in catalog()
            if item.get("metadata") and media_type in item["media_types"]
        ]
        if candidates:
            slug = candidates[0]["slug"]
            params["tab"] = "metadata"
            destination, args = "provider_detail", [slug]
    elif slug in known:
        destination, args = "provider_detail", [slug]
        params["tab"] = {
            "metadata": "connection",
            "import": "import",
            "integrations": "tracking",
            "sync": "sync",
        }[section]
        if params["tab"] not in known[slug]["tabs"]:
            params["tab"] = "overview"
    elif section == "sync":
        destination, args = "provider_detail", ["mal"]
        params["tab"] = "sync"
    else:
        params["capability"] = {"metadata": "metadata", "import": "import"}.get(
            section, ""
        )
    url = reverse(destination, args=args)
    if params:
        url += "?" + urlencode(params, doseq=True)
    return redirect(url)
