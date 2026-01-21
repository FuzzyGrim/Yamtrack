from django.urls import reverse
from django.utils.http import urlencode

from app.models import MediaTypes, Sources, Status

# --- Color Constants ---
COLORS = {
    "emerald": {"text": "text-emerald-400", "hex": "#10b981"},
    "purple": {"text": "text-purple-400", "hex": "#a855f7"},
    "indigo": {"text": "text-indigo-400", "hex": "#6366f1"},
    "orange": {"text": "text-orange-400", "hex": "#f97316"},
    "blue": {"text": "text-blue-400", "hex": "#3b82f6"},
    "red": {"text": "text-red-400", "hex": "#ef4444"},
    "yellow": {"text": "text-yellow-400", "hex": "#eab308"},
    "fuchsia": {"text": "text-fuchsia-400", "hex": "#d946ef"},
    "cyan": {"text": "text-cyan-400", "hex": "#06b6d4"},
}

# --- Central Configuration Dictionary ---
MEDIA_TYPE_CONFIG = {
    MediaTypes.TV.value: {
        "sources": [Sources.TMDB],
        "default_source": Sources.TMDB,
        "sample_query": "Breaking Bad",
        "unicode_icon": "📺",
        "verb": ("watch", "watched"),
        "text_color": COLORS["emerald"]["text"],
        "stats_color": COLORS["emerald"]["hex"],
        "svg_icon": """
            <rect width="20" height="15" x="2" y="7" rx="2" ry="2"/>
            <polyline points="17 2 12 7 7 2"/>""",
    },
    MediaTypes.SEASON.value: {
        "sources": [Sources.TMDB],
        "default_source": Sources.TMDB,
        "unicode_icon": "📺",
        "verb": ("watch", "watched"),
        "text_color": COLORS["purple"]["text"],
        "stats_color": COLORS["purple"]["hex"],
        "svg_icon": """
            <path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0
            1.83l8.58 3.91 a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/>
            <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/>
            <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>""",
        "unit": ("E", "Episode"),
    },
    MediaTypes.EPISODE.value: {
        "sources": [Sources.TMDB],
        "default_source": Sources.TMDB,
        "unicode_icon": "📺",
        "verb": ("watch", "watched"),
        "text_color": COLORS["indigo"]["text"],
        "stats_color": COLORS["indigo"]["hex"],
        "svg_icon": """<polygon points="6 3 20 12 6 21 6 3"/>""",
    },
    MediaTypes.MOVIE.value: {
        "sources": [Sources.TMDB],
        "default_source": Sources.TMDB,
        "sample_query": "The Shawshank Redemption",
        "unicode_icon": "🎬",
        "verb": ("watch", "watched"),
        "text_color": COLORS["orange"]["text"],
        "stats_color": COLORS["orange"]["hex"],
        "svg_icon": """
            <rect width="18" height="18" x="3" y="3" rx="2"/>
            <path d="M7 3v18"/>
            <path d="M3 7.5h4"/>
            <path d="M3 12h18"/>
            <path d="M3 16.5h4"/>
            <path d="M17 3v18"/>
            <path d="M17 7.5h4"/>
            <path d="M17 16.5h4"/>""",
        "date_key": "release_date",
    },
    MediaTypes.ANIME.value: {
        "sources": [Sources.MAL],
        "default_source": Sources.MAL,
        "sample_query": "Perfect Blue",
        "unicode_icon": "🎭",
        "verb": ("watch", "watched"),
        "text_color": COLORS["blue"]["text"],
        "stats_color": COLORS["blue"]["hex"],
        "svg_icon": """
            <circle cx="12" cy="12" r="10"/>
            <polygon points="10 8 16 12 10 16 10 8"/>""",
        "unit": ("E", "Episode"),
        "date_key": "end_date",
    },
    MediaTypes.MANGA.value: {
        "sources": [Sources.MAL, Sources.MANGAUPDATES],
        "default_source": Sources.MAL,
        "sample_query": "Berserk",
        "unicode_icon": "📚",
        "verb": ("read", "read"),
        "text_color": COLORS["red"]["text"],
        "stats_color": COLORS["red"]["hex"],
        "svg_icon": """
            <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2
            0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>
            <path d="M14 2v4a2 2 0 0 0 2 2h4"/>
            <path d="M10 9H8"/>
            <path d="M16 13H8"/>
            <path d="M16 17H8"/>""",
        "date_key": "end_date",
        "unit": ("#", "Chapter"),
    },
    MediaTypes.GAME.value: {
        "sources": [Sources.IGDB],
        "default_source": Sources.IGDB,
        "sample_query": "Half-Life",
        "unicode_icon": "🎮",
        "verb": ("play", "played"),
        "text_color": COLORS["yellow"]["text"],
        "stats_color": COLORS["yellow"]["hex"],
        "svg_icon": """
            <line x1="6" x2="10" y1="11" y2="11"/>
            <line x1="8" x2="8" y1="9" y2="13"/>
            <line x1="15" x2="15.01" y1="12" y2="12"/>
            <line x1="18" x2="18.01" y1="10" y2="10"/>
            <path d="M17.32 5H6.68a4 4 0 0 0-3.978
            3.59c-.006.052-.01.101-.017.152C2.6049.416
            2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5
            2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2
            2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0
            3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4
            4 0 0 0 17.32 5z"/>""",
        "date_key": "release_date",
    },
    MediaTypes.BOOK.value: {
        "sources": [Sources.HARDCOVER, Sources.OPENLIBRARY],
        "default_source": Sources.HARDCOVER,
        "sample_query": "The Great Gatsby",
        "unicode_icon": "📖",
        "verb": ("read", "read"),
        "text_color": COLORS["fuchsia"]["text"],
        "stats_color": COLORS["fuchsia"]["hex"],
        "svg_icon": """
            <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5
            2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>""",
        "date_key": "publish_date",
        "unit": ("P", "Page"),
    },
    MediaTypes.COMIC.value: {
        "sources": [Sources.COMICVINE],
        "default_source": Sources.COMICVINE,
        "sample_query": "Batman",
        "unicode_icon": "📕",
        "verb": ("read", "read"),
        "text_color": COLORS["cyan"]["text"],
        "stats_color": COLORS["cyan"]["hex"],
        "svg_icon": """
            <rect width="8" height="18" x="3" y="3" rx="1"/>
            <path d="M7 3v18"/>
            <path d="M20.4 18.9c.2.5-.1 1.1-.6 1.3l-1.9.7c-.5.2-1.1-.1-1.3-.6L11.1
            5.1c-.2-.5.1-1.1.6-1.3l1.9-.7c.5-.2 1.1.1 1.3.6Z"/>""",
        "unit": ("#", "Issue"),
    },
}

# --- Status Configuration ---
STATUS_CONFIG = {
    Status.COMPLETED.value: {
        "text_color": COLORS["emerald"]["text"],
        "stats_color": COLORS["emerald"]["hex"],
    },
    Status.IN_PROGRESS.value: {
        "text_color": COLORS["indigo"]["text"],
        "stats_color": COLORS["indigo"]["hex"],
    },
    Status.PAUSED.value: {
        "text_color": COLORS["orange"]["text"],
        "stats_color": COLORS["orange"]["hex"],
    },
    Status.PLANNING.value: {
        "text_color": COLORS["blue"]["text"],
        "stats_color": COLORS["blue"]["hex"],
    },
    Status.DROPPED.value: {
        "text_color": COLORS["red"]["text"],
        "stats_color": COLORS["red"]["hex"],
    },
}


def get_config(media_type):
    """Get the full config dictionary for a media type."""
    return MEDIA_TYPE_CONFIG.get(media_type)


def get_property(media_type, prop_name):
    """Get a specific property for a media type."""
    config = get_config(media_type)
    try:
        return config[prop_name]
    except KeyError:
        msg = f"Property '{prop_name}' not found for media type '{media_type}'."
        raise KeyError(msg) from None


def get_sources(media_type):
    """Get the list of sources for a media type."""
    return get_property(media_type, "sources")


def get_default_source_name(media_type):
    """Get the human-readable default source name."""
    return get_property(media_type, "default_source")


def get_sample_query(media_type):
    """Get the sample search query."""
    return get_property(media_type, "sample_query")


def get_sample_search_url(media_type):
    """Get the full sample search URL."""
    if media_type == MediaTypes.SEASON.value:
        media_type = MediaTypes.TV.value

    query = get_sample_query(media_type)

    base_url = reverse("search")
    query_params = {"media_type": media_type, "q": query}
    return f"{base_url}?{urlencode(query_params)}"


def get_unicode_icon(media_type):
    """Get the unicode icon."""
    return get_property(media_type, "unicode_icon")


def get_verb(media_type, past_tense):
    """Get the verb (present or past tense)."""
    verbs = get_property(media_type, "verb")
    return verbs[1] if past_tense else verbs[0]


def get_text_color(media_type):
    """Get the text color class."""
    return get_property(media_type, "text_color")


def get_stats_color(media_type):
    """Get the stats color."""
    return get_property(media_type, "stats_color")


def get_svg_icon(media_type):
    """Get the SVG path data."""
    return get_property(media_type, "svg_icon")


def get_date_key(media_type):
    """Get the primary date key used for fetching release/start dates."""
    return get_property(media_type, "date_key")


def get_unit(media_type, short):
    """Get the unit of measurement (e.g., episode, chapter)."""
    unit = get_property(media_type, "unit")
    return unit[0] if short else unit[1] if unit else None


def get_status_config(status):
    """Get the full config dictionary for a status."""
    return STATUS_CONFIG.get(status)


def get_status_property(status, prop_name):
    """Get a specific property for a status."""
    config = get_status_config(status)
    if config is None:
        msg = f"Status '{status}' not found in configuration."
        raise KeyError(msg)
    try:
        return config[prop_name]
    except KeyError:
        msg = f"Property '{prop_name}' not found for status '{status}'."
        raise KeyError(msg) from None


def get_status_text_color(status):
    """Get the text color class for a status."""
    return get_status_property(status, "text_color")


def get_status_stats_color(status):
    """Get the stats color for a status."""
    return get_status_property(status, "stats_color")
