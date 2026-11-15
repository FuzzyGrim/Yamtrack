"""Provider navigation for capabilities exposed in the settings pages.

Keep credential metadata in the existing registry; this module describes the
other settings surfaces and their existing deep links.
"""

from app.providers import credentials

# Values are the import modal slug and the media tags used by that modal.
IMPORTS = {
    "plex": ("Plex", "screen music"),
    "radarr": ("Radarr", "screen"),
    "sonarr": ("Sonarr", "screen"),
    "stremio": ("Stremio", "screen"),
    "audiobookshelf": ("Audiobookshelf", "books podcasts"),
    "kavita": ("Kavita", "reading"),
    "komga": ("Komga", "reading"),
    "storyteller": ("Storyteller", "reading"),
    "koreader": ("KOReader", "reading"),
    "mylar": ("Mylar3", "reading"),
    "kapowarr": ("Kapowarr", "reading"),
    "pocketcasts": ("Pocket Casts", "podcasts"),
    "gpodder": ("GPodder", "podcasts"),
    "lastfm": ("Last.fm", "music"),
    "koito": ("Koito", "music"),
    "trakt": ("Trakt", "screen"),
    "mdblist": ("MDBList", "screen"),
    "simkl": ("SIMKL", "screen anime_manga"),
    "mal": ("MyAnimeList", "anime_manga"),
    "anilist": ("AniList", "anime_manga"),
    "kitsu": ("Kitsu", "anime_manga"),
    "mangabaka": ("MangaBaka", "anime_manga"),
    "yamtrack_native": (
        "Yamtrack CSV",
        "screen anime_manga reading games podcasts music",
    ),
    "clz": ("CLZ", "screen reading games"),
    "hltb": ("HowLongToBeat", "games"),
    "playnite": ("Playnite", "games"),
    "grouvee": ("Grouvee", "games"),
    "steam": ("Steam", "games"),
    "xbox": ("Xbox", "games"),
    "psn": ("PlayStation", "games"),
    "imdb": ("IMDb", "screen"),
    "tvtime": ("TV Time", "screen"),
    "wetrakr": ("WeTrakr", "screen"),
    "goodreads": ("Goodreads", "reading"),
    "hardcover": ("Hardcover", "reading"),
    "storygraph": ("StoryGraph", "reading"),
    "youtube": ("YouTube", "screen"),
}

TRACKING = {
    "jellyfin": ("Jellyfin", "screen music"),
    "plex": ("Plex", "screen music"),
    "emby": ("Emby", "screen"),
    "kodi": ("Kodi", "screen"),
    "stremio": ("Stremio", "screen"),
    "seerr": ("Seerr", "screen"),
    "nuvio": ("Nuvio", "screen"),
    "navidrome": ("Navidrome", "music"),
}


# These import actions require a user-supplied export and cannot recur.
FILE_ONLY_IMPORTS = frozenset({
    "clz", "yamtrack_native", "hltb", "playnite", "grouvee", "imdb", "goodreads",
    "storygraph", "youtube", "wetrakr", "tvtime",
})
ONE_TIME_IMPORTS = FILE_ONLY_IMPORTS | {"mangabaka"}


# Persisted setup is distinct from file input and from an enabled operation.
ACCOUNT_IMPORTS = frozenset(
    {
        "plex",
        "radarr",
        "sonarr",
        "stremio",
        "audiobookshelf",
        "kavita",
        "komga",
        "storyteller",
        "koreader",
        "mylar",
        "kapowarr",
        "pocketcasts",
        "gpodder",
        "lastfm",
        "koito",
        "mdblist",
        "mal",
        "xbox",
        "psn",
    }
)
TAB_ORDER = (
    "overview",
    "connection",
    "metadata",
    "import",
    "tracking",
    "sync",
    "activity",
)
CAPABILITIES = ("metadata", "import", "tracking", "sync")
# Public access is independent of optional account credentials. Keep its scope
# explicit so a public feature never implies every provider API is public.
PUBLIC_ACCESS = {
    "steam": {
        "description": "Game artwork uses a public API and does not require an API key.",
        "capabilities": ("metadata",),
    },
}
MEDIA_CHOICES = (
    ("movie", "Movies"),
    ("tv", "TV Shows"),
    ("anime", "Anime"),
    ("manga", "Manga"),
    ("game", "Games"),
    ("boardgame", "Board Games"),
    ("book", "Books"),
    ("comic", "Comics"),
    ("music", "Music"),
    ("podcast", "Podcasts"),
    ("video", "Videos"),
)
IMPORT_MEDIA = {
    "plex": "movie tv music book",
    "radarr": "movie",
    "sonarr": "tv",
    "stremio": "movie tv",
    "audiobookshelf": "book podcast",
    "kavita": "book comic manga",
    "komga": "book comic",
    "storyteller": "book",
    "koreader": "book",
    "mylar": "comic",
    "kapowarr": "comic",
    "pocketcasts": "podcast",
    "gpodder": "podcast",
    "lastfm": "music",
    "koito": "music",
    "trakt": "movie tv",
    "mdblist": "movie tv",
    "simkl": "movie tv anime",
    "mal": "anime manga",
    "anilist": "anime manga",
    "kitsu": "anime manga",
    "mangabaka": "manga",
    "clz": "movie book comic game",
    "hltb": "game",
    "playnite": "game",
    "grouvee": "game",
    "steam": "game",
    "xbox": "game",
    "psn": "game",
    "imdb": "movie tv",
    "tvtime": "movie tv",
    "wetrakr": "movie tv",
    "goodreads": "book",
    "hardcover": "book",
    "storygraph": "book",
    "youtube": "video",
    "yamtrack_native": "movie tv anime manga game boardgame book comic music podcast video",
}
TRACKING_MEDIA = {
    "plex": "movie tv anime music book",
    "jellyfin": "movie tv anime music",
    "emby": "movie tv anime",
    "kodi": "movie tv anime",
    "stremio": "movie tv anime",
    "seerr": "movie tv",
    "nuvio": "movie tv",
    "navidrome": "music",
}
IMPORT_DESCRIPTIONS = {
    "plex": "Import watch and listen history from Plex.",
    "radarr": "Import owned movies from Radarr.",
    "sonarr": "Import owned TV shows from Sonarr.",
    "stremio": "Import watched movies and episodes from Stremio.",
    "audiobookshelf": "Import audiobook progress from Audiobookshelf.",
    "storyteller": "Import reading progress from Storyteller.",
    "koreader": "Sync book reading progress from a KOReader sync server.",
    "mylar": "Import owned comics from Mylar3.",
    "kapowarr": "Import owned comics from Kapowarr.",
    "pocketcasts": "Import podcast progress from Pocket Casts.",
    "gpodder": "Import podcast history from GPodder.",
    "lastfm": "Import listening history from Last.fm.",
    "koito": "Import listening history from a self-hosted Koito server.",
    "trakt": "Import movies and TV shows from Trakt.",
    "mdblist": "Import watch history and ratings from MDBList.",
    "simkl": "Import movies, TV shows and anime from Simkl.",
    "mal": "Import anime and manga from MyAnimeList.",
    "anilist": "Import anime and manga from AniList.",
    "kitsu": "Import anime and manga from Kitsu.",
    "mangabaka": "Import manga from a MangaBaka library.",
    "yamtrack": "Import a backup from Floppy.",
    "yamtrack_native": "Import a CSV export from Yamtrack.",
    "clz": "Import a CLZ (Collectorz) CSV or XML export.",
    "hltb": "Import games from HowLongToBeat.",
    "playnite": "Import a Playnite library CSV backup.",
    "grouvee": "Import games from Grouvee.",
    "steam": "Import games from Steam.",
    "xbox": "Sync played games and hours from Xbox.",
    "psn": "Sync played games and hours from PlayStation.",
    "imdb": "Import ratings from IMDB.",
    "tvtime": "Import watch history from a TV Time data export.",
    "wetrakr": "Import history, ratings, notes and lists from a WeTrakr data export.",
    "goodreads": "Import a backup from Goodreads.",
    "hardcover": "Import an export from Hardcover.",
    "storygraph": "Import an export from StoryGraph.",
    "youtube": "Import watch history from Google Takeout.",
    "kavita": "Sync book, comic and manga reading progress from Kavita.",
    "komga": "Sync book and comic reading progress from Komga.",
}

DESCRIPTIONS = {
    "playnite": "Import a Playnite library CSV backup.",
    "plex": "Import watch and listen history, receive playback events, and share webhook intake.",
    "jellyfin": "Import server history, receive playback events, and send watched state to your server.",
    "emby": "Receive playback events from your Emby server.",
    "kodi": "Receive playback events from Kodi.",
    "stremio": "Import watched state and track playback through the Floppy addon.",
    "seerr": "Receive request and approval events, with optional server request actions.",
    "nuvio": "Connect Nuvio playback tracking to Floppy's API.",
    "navidrome": "Receive listening scrobbles through the ListenBrainz-compatible API.",
    "musicbrainz": "Primary artist, album and track metadata for music.",
    "openlibrary": "Book search, editions and cover metadata.",
    "mangaupdates": "Manga catalog metadata from MangaUpdates.",
    "youtube": "Video metadata and watch-history file imports.",
}

# Short, action-oriented summaries for the provider directory cards.
CARD_DESCRIPTIONS = {
    "anilist": "Import anime and manga lists.",
    "audiobookshelf": "Import audiobook progress.",
    "bgg": "Find metadata for board games.",
    "clz": "Import CLZ collection files.",
    "comicvine": "Find metadata for comics and issues.",
    "emby": "Track playback from your media server.",
    "goodreads": "Import reading history from a file.",
    "googlebooks": "Find book metadata and cover art.",
    "gpodder": "Import podcast listening history.",
    "gcd": "Find metadata for comics and issues.",
    "grouvee": "Import a game library from a file.",
    "hardcover": "Find books and import reads.",
    "hltb": "Import game history from a file.",
    "igdb": "Find game metadata and artwork.",
    "imdb": "Import your movie and TV ratings.",
    "jellyfin": "Import and track your plays.",
    "kapowarr": "Import comics from your collection.",
    "kavita": "Import your reading progress.",
    "kitsu": "Import anime and manga lists.",
    "kodi": "Track playback from your media player.",
    "koito": "Import music listening history.",
    "komga": "Import your reading progress.",
    "koreader": "Import your reading progress.",
    "lastfm": "Import listens and music data.",
    "libretro": "Find artwork for retro games.",
    "mangabaka": "Find manga and import your library.",
    "mangaupdates": "Find metadata for manga titles.",
    "mdblist": "Import your watch history and ratings.",
    "musicbrainz": "Find artist and album metadata.",
    "mal": "Import and sync anime and manga.",
    "mylar": "Import comics from your collection.",
    "navidrome": "Track your music listening.",
    "nuvio": "Playback tracking is not yet supported.",
    "openlibrary": "Find book metadata and cover art.",
    "opencritic": "Find critic scores for your games.",
    "playnite": "Import a game library from a file.",
    "psn": "Import your games and played hours.",
    "plex": "Import and track your plays.",
    "pocketcasts": "Import podcast progress.",
    "radarr": "Import movies from your library.",
    "screenscraper": "Find artwork for retro games.",
    "seerr": "Track requests and approvals.",
    "simkl": "Import your movies, shows and anime.",
    "sonarr": "Import TV shows from your library.",
    "steam": "Import your games and find artwork.",
    "steamgriddb": "Find game logos and backdrops.",
    "storygraph": "Import reading history from a file.",
    "storyteller": "Import your reading progress.",
    "stremio": "Import and track watched titles.",
    "thegamesdb": "Find logos and artwork for games.",
    "tvdb": "Find TV and anime metadata.",
    "tmdb": "Find metadata and sync ratings.",
    "trakt": "Import history and find trends.",
    "tvtime": "Import your watch history from a file.",
    "wetrakr": "Import and sync your activity.",
    "xbox": "Import your games and played hours.",
    "yamtrack_native": "Import a library from CSV.",
    "youtube": "Find videos and import history.",
}


def catalog():
    """Return declared capabilities without inferring them from credentials."""
    from app.config import MEDIA_TYPE_CONFIG
    from app.models import Sources

    result = {}
    for slug, (label, _tags) in IMPORTS.items():
        result[slug] = {
            "slug": slug,
            "label": label,
            "media_types": set(IMPORT_MEDIA[slug].split()),
            "import": True,
            "import_slug": "myanimelist" if slug == "mal" else slug,
            "connection": slug in ACCOUNT_IMPORTS,
        }
    for slug, (label, _tags) in TRACKING.items():
        item = result.setdefault(
            slug, {"slug": slug, "label": label, "media_types": set()}
        )
        item["media_types"].update(TRACKING_MEDIA[slug].split())
        item.update(tracking=True, connection=True, integration=True)
    for media_type, config in MEDIA_TYPE_CONFIG.items():
        if media_type in {"season", "episode", "comicissue"}:
            continue
        for source in config["sources"]:
            if source == Sources.MANUAL:
                continue
            slug = source.value
            item = result.setdefault(
                slug, {"slug": slug, "label": source.label, "media_types": set()}
            )
            item["media_types"].add(media_type)
            item.update(metadata=True, metadata_role="primary")
    for slug, spec in credentials.REGISTRY.items():
        item = result.setdefault(
            slug, {"slug": slug, "label": spec.label, "media_types": set()}
        )
        item.update(
            label=spec.label,
            description=spec.description,
            credential=True,
            connection=True,
            logo_slug=spec.logo_slug or slug,
        )
        if spec.group == credentials.GROUP_METADATA or slug in {"lastfm", "trakt"}:
            item["metadata"] = True
            item.setdefault("metadata_role", "supplemental")
            if not item["media_types"]:
                item["media_types"].update(
                    {"game"} if "games" in spec.media_tags else set()
                )
    result["jellyfin"]["import"] = True
    for slug in ("jellyfin", "emby", "kodi"):
        result[slug]["sync"] = True
    for slug in ("mal", "tmdb", "wetrakr"):
        result[slug]["sync"] = True
    # Audiobookshelf supplies metadata for imported items, rather than acting as
    # the selectable search/default provider for books or podcasts.
    result["audiobookshelf"].update(metadata_role="imported", metadata_related_media={"book"})
    result["steam"].update(metadata=True, metadata_role="supplemental")
    result["trakt"]["media_types"].add("anime")
    result["libretro"] = {
        "slug": "libretro",
        "label": "Libretro",
        "media_types": {"game"},
        "description": "Supplemental retro game artwork from the Libretro database.",
        "metadata": True,
        "metadata_role": "supplemental",
    }
    result["nuvio"].update(
        tracking=False,
        available=False,
        description="A Floppy tracker has been proposed for Nuvio TV; no released Nuvio version supports it yet.",
    )
    for item in result.values():
        slug = item["slug"]
        item["public_access"] = PUBLIC_ACCESS.get(slug)
        item.setdefault(
            "description",
            DESCRIPTIONS.get(
                slug,
                IMPORT_DESCRIPTIONS.get(
                    slug, f"Import your library and history from {item['label']}."
                ),
            ),
        )
        if slug in DESCRIPTIONS and item.get("available", True):
            item["description"] = DESCRIPTIONS[slug]
        item["card_description"] = CARD_DESCRIPTIONS.get(slug, item["description"])
        item["activity"] = bool(
            item.get("import")
            or item.get("sync")
            or slug in {"plex", "jellyfin", "stremio"}
        )
        item["capabilities"] = [key for key in CAPABILITIES if item.get(key)]
        item["tabs"] = [key for key in TAB_ORDER if key == "overview" or item.get(key)]
        item["media_labels"] = [
            label for key, label in MEDIA_CHOICES if key in item["media_types"]
        ]
    return sorted(result.values(), key=lambda item: item["label"].casefold())


def activity_sources(slug):
    """Map a canonical provider to the existing import-history vocabulary."""
    return {
        "mal": {"myanimelist", "mal"},
        "jellyfin": {"jellyfin", "jellyfin_playback_reporting"},
        "yamtrack_native": {"yamtrack", "yamtrack_native"},
    }.get(slug, {slug})


_ACCOUNT_PROVIDERS = frozenset(
    {
        "plex",
        "jellyfin",
        "emby",
        "kodi",
        "stremio",
        "audiobookshelf",
        "kavita",
        "komga",
        "storyteller",
        "koreader",
        "pocketcasts",
        "gpodder",
        "lastfm",
        "koito",
        "mdblist",
        "mal",
        "xbox",
        "psn",
        "tmdb",
        "wetrakr",
    }
)
_SERVER_PROVIDERS = frozenset({"radarr", "sonarr", "mylar", "kapowarr"})


def provider_state(provider, user):
    """Describe saved setup and opt-ins without probing a remote service.

    Saved accounts and available API credentials are deliberately distinct from
    enabled operations. None of these labels promises that a service is healthy.
    """
    slug = provider["slug"]
    spec = credentials.REGISTRY.get(slug)
    credentials_available = bool(
        provider.get("credential") and credentials.is_configured(slug, user)
    )
    account = (
        getattr(user, f"{slug}_account", None) if slug in _ACCOUNT_PROVIDERS else None
    )
    servers = (
        list(getattr(user, f"{slug}_instances").all())
        if slug in _SERVER_PROVIDERS
        else []
    )
    account_saved = bool(account or servers)
    attention = bool(
        getattr(account, "connection_broken", False)
        or any(server.connection_broken for server in servers)
    )
    awaiting_authorization = bool(
        slug == "wetrakr"
        and account
        and account.device_code
        and not account.refresh_token
    )
    if slug == "seerr":
        account_saved = bool(
            user.seerr_url and user.seerr_api_key and user.seerr_user_id
        )
    if provider.get("available") is False:
        connection = "Not yet supported"
    elif attention:
        connection = "Needs attention"
    elif awaiting_authorization:
        connection = "Awaiting authorization"
    elif slug == "plex" and user.plex_webhook_last_received_at:
        connection = "Webhook events received"
    elif account_saved:
        connection = "Account saved"
    elif credentials_available and not (spec and spec.bundled_only):
        sources = {credentials.source_of(slug, field.name, user) for field in spec.fields if field.required} if spec else set()
        connection = "Bundled credentials available" if sources == {credentials.SOURCE_DEFAULT} else "Credentials available"
    elif provider.get("public_access"):
        connection = "Public API active"
    elif provider.get("connection"):
        connection = "Needs setup"
    else:
        connection = "No connection required"
    capabilities = dict.fromkeys(provider["capabilities"], "Available")
    if provider.get("import") and account_saved:
        capabilities["import"] = "Account saved"
    if slug == "jellyfin" and account:
        capabilities["import"] = (
            "Scheduled import enabled"
            if account.pull_history_enabled
            else "Scheduled import disabled"
        )
        capabilities["sync"] = (
            "Push options enabled"
            if account.push_watched_enabled or account.push_unwatched_enabled
            else "Push options disabled"
        )
    if slug == "seerr" and provider.get("tracking"):
        capabilities["tracking"] = "Enabled" if user.jellyseerr_enabled else "Disabled"
    if slug == "plex" and user.plex_webhook_last_received_at:
        capabilities["tracking"] = "Events received"
    if slug == "mal" and provider.get("sync"):
        capabilities["sync"] = (
            ("Enabled" if account.sync_enabled else "Disabled")
            if account
            else "Needs setup"
        )
    if slug in {"tmdb", "wetrakr"} and provider.get("sync"):
        bindings = user.sync_bindings.filter(
            client_kind=slug, status="active", kill_switch=False
        )
        enabled = any(
            "outbound" in (binding.approved_directions or [])
            and bool(binding.approved_capabilities)
            for binding in bindings
        )
        capabilities["sync"] = (
            ("Enabled" if enabled else "Not enabled")
            if account_saved and not awaiting_authorization
            else "Needs setup"
        )
    return {
        "credentials_available": credentials_available,
        # An account a person links is separate from the API credentials Floppy
        # holds. Providers with one show "linked" and credentials separately.
        "account_linkable": slug in _ACCOUNT_PROVIDERS | _SERVER_PROVIDERS or slug == "seerr",
        "account_saved": account_saved,
        "attention": attention,
        "connection": connection,
        "connection_tone": "warning" if connection in {"Needs attention", "Awaiting authorization"} else
            "success" if connection in {"Account saved", "Credentials available", "Bundled credentials available", "Webhook events received", "Public API active"} else "neutral",
        "summary": connection if provider.get("connection") else "Available",
        "capabilities": capabilities,
    }
