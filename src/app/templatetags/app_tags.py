import calendar
import re
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse as _django_reverse
from django.utils import formats, timezone
from django.utils.dateparse import parse_date
from django.utils.html import format_html
from django.utils.translation import (
    get_language,
    get_language_info,
    ngettext,
    npgettext,
    pgettext,
)
from django.utils.translation import gettext as _
from unidecode import unidecode

from app import (
    card_surfaces,
    checkin,
    config,
    helpers,
    image_cache,
    live_playback,
    track_actions,
)
from app import entry_sources as entry_sources_module
from app.models import Item, MediaTypes, Sources, Status
from app.providers import tmdb
from app.services import metadata_resolution
from app.stats_music import COUNTRY_NAME_MAP
from users.media_type_chips import media_type_chip_preferences
from users.models import ALL_SEARCH_TYPE, HISTORY_VIEW_TYPE, TimeFormatChoices
from users.templatetags.user_tags import (
    is_basic_theme,
    user_date_format,
    user_time_format,
)


@lru_cache(maxsize=8192)
def _reverse_cached(viewname, kwargs_items):
    return _django_reverse(viewname, kwargs=dict(kwargs_items))


def reverse(viewname, kwargs=None, **rest):
    """Resolve URLs; a card grid repeats the same few thousand names."""
    if kwargs and not rest:
        try:
            return _reverse_cached(viewname, tuple(sorted(kwargs.items())))
        except TypeError:  # unhashable kwarg value
            pass
    return _django_reverse(viewname, kwargs=kwargs, **rest)


register = template.Library()
COUNTRY_CODE_LENGTH = 2
COUNTRY_CODE_BY_NAME = {
    name.casefold(): code for code, name in COUNTRY_NAME_MAP.items()
}


@register.simple_tag
def detail_promoted_facts(media_type, details):
    """Pick existing provider facts for the desktop carousel summary."""
    details = details if isinstance(details, dict) else {}
    fields_by_type = {
        MediaTypes.TV.value: (
            ("status", _("Series status")),
            ("format", _("Format")),
            ("air_dates", _("Air dates")),
            ("total_runtime", _("Total runtime")),
            ("locale", _("Languages and country")),
        ),
        MediaTypes.MOVIE.value: (
            ("format", _("Format")),
            ("release_date", _("Release date")),
            ("status", _("Release status")),
            ("runtime", _("Runtime")),
            ("certification", _("Certification")),
            ("locale", _("Languages and country")),
        ),
        MediaTypes.SEASON.value: (
            ("air_dates", _("Air dates")),
            ("episodes", _("Episodes")),
            ("total_runtime", _("Total runtime")),
        ),
        MediaTypes.GAME.value: (
            ("release_date", _("Release date")),
            ("platforms", _("Platforms")),
            ("format", _("Format")),
        ),
    }
    fields = []
    suppressed_keys = set()
    for key, default_label in fields_by_type.get(media_type, ()):
        label = default_label
        if key == "locale":
            languages = details.get("languages")
            country = details.get("country")
            if not languages and not country:
                continue
            value = {"languages": languages, "country": country}
            suppressed_keys.update(("languages", "country"))
        elif key == "air_dates":
            value = (details.get("first_air_date"), details.get("last_air_date"))
            if not any(value):
                continue
            suppressed_keys.update(("first_air_date", "last_air_date"))
        else:
            value = details.get(key)
            if value is None or value in ("", []):
                continue
            if (
                media_type == MediaTypes.TV.value
                and key == "format"
                and details.get("status")
            ):
                suppressed_keys.add(key)
                continue
            suppressed_keys.add(key)
            if media_type == MediaTypes.MOVIE.value and key == "runtime":
                suppressed_keys.add("total_runtime")
            if (
                media_type == MediaTypes.TV.value
                and key == "status"
                and details.get("format")
            ):
                label = _("%(format)s series status") % {"format": _(details["format"])}
        fields.append({"key": key, "label": label, "value": value})
    return {"fields": fields, "suppressed_keys": suppressed_keys}


@register.filter
def translate_detail_value(value):
    """Translate dynamic media-detail values while preserving their data."""
    if value is None:
        return _("Unknown")

    text = str(value)
    season_match = re.fullmatch(r"(Spring|Summer|Fall|Winter)\s+(\d{4})", text)
    if season_match:
        season, year = season_match.groups()
        return _("%(season)s %(year)s") % {"season": _(season), "year": year}

    broadcast_match = re.fullmatch(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)(\s+.+)",
        text,
    )
    if broadcast_match:
        weekday, remainder = broadcast_match.groups()
        return f"{_(weekday)}{remainder}"

    players_match = re.fullmatch(r"(\d+(?:-\d+)?)\s+players?", text, re.IGNORECASE)
    if players_match:
        return _("%(count)s players") % {"count": players_match.group(1)}

    return _(text)


@register.filter
def country_code(value):
    """Compact known country names to their ISO alpha-2 code."""
    if not value:
        return ""
    text = str(value).strip()
    if len(text) == COUNTRY_CODE_LENGTH and text.isalpha():
        return text.upper()
    return COUNTRY_CODE_BY_NAME.get(text.casefold(), text)


# Built-in source labels are stored lowercase ("plex"); these need casing that
# a plain capitalize would get wrong. Anything else with no capitals is
# capitalized, and text a user typed ("Theatre") is shown as typed.
ENTRY_SOURCE_LABELS = {
    "anilist": "AniList",
    "audiobookshelf": "Audiobookshelf",
    "gpodder": "gPodder",
    "imdb": "IMDb",
    "koreader": "KOReader",
    "lastfm": "Last.fm",
    "listenbrainz": "ListenBrainz",
    "myanimelist": "MyAnimeList",
    "simkl": "SIMKL",
    "youtube": "YouTube",
}


@register.simple_tag
def entry_source_suggestions(user):
    """Return the sources this user has used, for the Source field."""
    return entry_sources_module.entry_source_suggestions(user)


@register.filter
def entry_source_label(value):
    """Return a display label for a media entry's source."""
    text = str(value or "").strip()
    if text != text.lower():
        return text
    return ENTRY_SOURCE_LABELS.get(text) or text.replace("_", " ").capitalize()


@register.filter
def translate_language_code(value):
    """Return the localized language name for an ISO language code."""
    if not value:
        return _("Unknown")
    try:
        return get_language_info(str(value).replace("_", "-"))["name_translated"]
    except KeyError:
        return str(value)


@register.filter
def translate_activity_text(value, media_type=None):
    """Translate dynamic activity counters shared by all detail pages."""
    if not value:
        return ""

    text = str(value)
    comparison_match = re.fullmatch(r"(Up|Down) (.+)", text)
    if comparison_match:
        direction, amount = comparison_match.groups()
        message = _("Up %(value_1)s") if direction == "Up" else _("Down %(value_1)s")
        return message % {"value_1": amount}

    progress_match = re.fullmatch(r"Progress:\s*(.+)", text)
    if progress_match:
        return _("Progress: %(value_1)s") % {"value_1": progress_match.group(1)}

    activity_match = re.fullmatch(
        r"(Watched|Played|Listened|Read)\s+(once|\d+\s+times)",
        text,
        re.IGNORECASE,
    )
    if not activity_match:
        return _(text)

    verb = activity_match.group(1).title()
    amount = activity_match.group(2).lower()
    if verb == "Played" and media_type in {
        MediaTypes.GAME.value,
        MediaTypes.BOARDGAME.value,
    }:
        if amount == "once":
            return pgettext("game activity", "Played once")
        count = amount.split()[0]
        return pgettext("game activity", "Played %(value_1)s times") % {
            "value_1": count,
        }

    count = amount.split()[0]
    if verb == "Watched":
        if amount == "once":
            return _("Watched once")
        return _("Watched %(value_1)s times") % {"value_1": count}
    if verb == "Listened":
        if amount == "once":
            return _("Listened once")
        return _("Listened %(value_1)s times") % {"value_1": count}
    if verb == "Read":
        if amount == "once":
            return _("Read once")
        return _("Read %(value_1)s times") % {"value_1": count}
    if amount == "once":
        return _("Played once")
    return _("Played %(value_1)s times") % {"value_1": count}


@register.filter
def translate_history_description(value):
    """Translate dated descriptions that history_processor builds dynamically."""
    if not value:
        return ""

    text = str(value)
    dated_change = re.fullmatch(r"(Started|Finished) on\s+(.+)", text)
    if not dated_change:
        return _(text)

    action, formatted_date = dated_change.groups()
    if action == "Started":
        return _("Started on %(date)s") % {"date": formatted_date}
    return _("Finished on %(date)s") % {"date": formatted_date}


@register.simple_tag
def home_media_type_chip(user, media_type):
    """Resolve one user's validated Home media-type label appearance."""
    return media_type_chip_preferences(user, media_type)


@register.simple_tag
def watch_operation_id():
    """Return private replay identity for one rendered first-party watch action."""
    return str(uuid4())


@register.simple_tag(takes_context=True)
def absolute_app_url(context, path):
    """Return an absolute app URL for links copied into external services."""
    return helpers.build_absolute_app_url(context.get("request"), path)


@register.simple_tag
def djdt_enabled():
    """Return the djdt enabled."""
    return getattr(settings, "ENABLE_DEBUG_TOOLBAR", False)


@register.simple_tag
def javascript_catalog_url():
    """Return the translation catalog URL, versioned for long browser caching.

    The catalog is a blocking script in every page's head. Keyed by release,
    language and the compiled catalogs' mtime, the browser can keep it for a
    year and still pick up a new release, a language switch or a recompile.
    """
    language = get_language() or settings.LANGUAGE_CODE
    compiled_mtime = 0
    for locale_dir in settings.LOCALE_PATHS:
        for mo_file in Path(locale_dir).glob("*/LC_MESSAGES/djangojs.mo"):
            try:
                compiled_mtime = max(compiled_mtime, int(mo_file.stat().st_mtime))
            except OSError:
                continue
    return (
        f"{reverse('javascript-catalog')}"
        f"?v={settings.VERSION}.{compiled_mtime}&l={language}"
    )


@register.simple_tag
def get_static_file_mtime(file_path):
    """Return the last modification time of a static file for cache busting."""
    # Check STATICFILES_DIRS first (for development), then STATIC_ROOT (for production)
    for static_dir in getattr(settings, "STATICFILES_DIRS", []):
        full_path = Path(static_dir) / file_path
        try:
            mtime = int(full_path.stat().st_mtime)
        except OSError:
            continue
        else:
            return f"?{mtime}"

    # Fall back to STATIC_ROOT
    full_path = Path(settings.STATIC_ROOT) / file_path
    try:
        mtime = int(full_path.stat().st_mtime)
    except OSError:
        # If file doesn't exist or can't be accessed
        return ""
    else:
        return f"?{mtime}"


@register.filter
def dict_get(dictionary, key):
    """Look up a value in a dict by key, keyed by its string form."""
    if not dictionary:
        return None
    return dictionary.get(str(key))


@register.filter
def newline_join(values):
    """Join a list of strings with newlines, for round-tripping into a textarea."""
    return "\n".join(values or [])


@register.filter
def no_underscore(arg1):
    """Return the title case of the string."""
    return arg1.replace("_", " ")


@register.filter
def related_section_label(name):
    """Heading for a related-media section; keep acronyms like DLCs upper-case."""
    if name == "dlcs":
        return "DLCs"
    return str(name).replace("_", " ").title()


@register.filter
def image_url(value):
    """Return a safe Floppy image proxy URL when external caching is enabled."""
    return image_cache.rewrite_image_url(value)


@register.simple_tag
def episode_queue_url(item, media):
    """Return the episode page the Up Next chip opens."""
    queue = media.next_episode_queue
    if queue.get("season_number") is None:
        return ""
    is_anime = item.media_type == MediaTypes.ANIME.value
    return reverse(
        "anime_episode_details" if is_anime else "episode_details",
        kwargs={
            "source": queue["source"],
            "media_id": queue["media_id"],
            "title": slug(item.title) or slug(str(queue["media_id"])) or "item",
            "season_number": queue["season_number"],
            "episode_number": queue["episode_number"],
        },
    )


@register.simple_tag
def episode_queue_label(media):
    """Localize the Up Next chip when rendering, after the Home row cache."""
    queue = media.next_episode_queue
    if queue["released"]:
        return f"{media.episodes_left} {_('Behind')}"
    days = queue.get("days_until")
    if days is None:
        return _("Upcoming")
    if days == 0:
        return _("Today")
    return f"{days} {ngettext('day', 'days', days)}"


@register.filter
def youtube_landscape_thumbnail(value):
    """Prefer YouTube's high-resolution thumbnail for standard artwork."""
    if not isinstance(value, str):
        return value
    return re.sub(
        r"^(https://(?:i\.ytimg\.com|img\.youtube\.com)/vi/[A-Za-z0-9_-]{11}/)(?:hqdefault|mqdefault|sddefault)\.jpg$",
        r"\1maxresdefault.jpg",
        value,
    )


@register.filter
def user_tag_names(item_tags, user):
    """Return tag names from a prefetched ItemTag queryset, scoped to one user."""
    if not item_tags or user is None:
        return []
    return [
        item_tag.tag.name for item_tag in item_tags if item_tag.tag.user_id == user.id
    ]


@register.filter
def title_preserve_acronyms(value):
    """Title-case text while preserving all-uppercase acronyms."""
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if not normalized:
        return normalized

    if normalized.isupper():
        return normalized

    return normalized.title()


def _collection_entry_field(collection_entry, field_name):
    """Read a collection metadata field from models, dicts, or simple namespaces."""
    if collection_entry is None:
        return None
    if isinstance(collection_entry, dict):
        return collection_entry.get(field_name)
    return getattr(collection_entry, field_name, None)


@register.filter
def collection_quality_label(collection_entry):
    """Return a compact display label for collection quality metadata."""
    if not collection_entry:
        return ""

    resolution = (_collection_entry_field(collection_entry, "resolution") or "").strip()
    hdr = (_collection_entry_field(collection_entry, "hdr") or "").strip()
    media_type = (_collection_entry_field(collection_entry, "media_type") or "").strip()

    parts = []
    if resolution:
        parts.append(str(resolution))
    if hdr:
        parts.append(str(hdr))
    if not parts and media_type:
        parts.append(title_preserve_acronyms(str(media_type)))

    return " • ".join(parts)


@register.filter
def collection_quality_display(collection_entry, explicit_label=""):
    """Prefer an explicit quality label before falling back to collection metadata."""
    normalized_label = str(explicit_label or "").strip()
    if normalized_label:
        return normalized_label
    return collection_quality_label(collection_entry)


@register.filter
def music_artist_join_phrase(value):
    """Normalize album artist join phrases for display."""
    phrase = str(value or "")
    stripped = phrase.strip()
    if not stripped:
        return ""

    if stripped == ",":
        return ", "

    return f" {stripped} "


@register.filter
def slug(arg1):
    """Return the slug of the string.

    Sometimes slugify removes all characters from a string, so we need to
    urlencode the special characters first.
    e.g Anime: 31687

    The result must stay a single path segment, so "/" is encoded too and
    the dot segments "." and ".." are replaced (e.g. episode title "/").
    """
    cleaned = template.defaultfilters.slugify(arg1)
    if cleaned == "":
        cleaned = template.defaultfilters.slugify(
            template.defaultfilters.urlencode(unidecode(arg1), ""),
        )
        if cleaned == "":
            cleaned = template.defaultfilters.urlencode(unidecode(arg1), "")

            if cleaned == "":
                cleaned = template.defaultfilters.urlencode(arg1, "")

    if cleaned in {".", ".."}:
        cleaned = cleaned.replace(".", "2e")

    return cleaned


@register.filter
def minutes_hhmm(total_minutes):
    """Format minutes as "1h 05min", or "45min" under an hour."""
    return helpers.minutes_to_hhmm(total_minutes or 0)


@register.filter
def date_format(datetime, user):
    """Format a datetime using user's preferred date format (date only, no time).

    Args:
        datetime: The datetime object to format
        user: User object to get preferred date format
    """
    return user_date_format(datetime, user)


@register.filter
def is_future_date(value):
    """Only known dates after today represent unreleased episodes."""
    if isinstance(value, str):
        try:
            value = parse_date(value)
        except ValueError:
            return False
    if isinstance(value, datetime):
        value = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return isinstance(value, date) and value > timezone.localdate()


@register.filter
def iso_date_format(value, user):
    """Format an ISO date string (YYYY-MM-DD) using user's preferred date format.

    If value is not a valid ISO date string, returns the original value.
    """
    if not value or not user:
        return value

    parsed_value = value
    if isinstance(value, str):
        parsed_value = parse_date(value)
        if parsed_value is None:
            return value
    elif not isinstance(value, (date, datetime)):
        return value

    # user_date_format expects datetime-like values for user-specific formatting.
    if isinstance(parsed_value, date) and not isinstance(parsed_value, datetime):
        parsed_value = timezone.make_aware(
            datetime.combine(parsed_value, time.min),
            timezone.get_current_timezone(),
        )

    return user_date_format(parsed_value, user)


@register.filter
def time_format(datetime, user):
    """Format a datetime using user's preferred time format (time only, no date)."""
    return user_time_format(datetime, user)


@register.filter
def datetime_format(datetime, user):
    """Format a datetime using user's preferred formats.

    Includes time only if TRACK_TIME setting is enabled.

    Args:
        datetime: The datetime object to format
        user: User object to get preferred date/time format
    """
    if not datetime:
        return None
    formatted_date = user_date_format(datetime, user)

    if settings.TRACK_TIME:
        formatted_time = user_time_format(datetime, user)
        if formatted_time:
            return f"{formatted_date} {formatted_time}"
    return formatted_date


@register.filter
def rating_scale_max(user):
    """Return the user's rating scale max (defaults to 10)."""
    if not user:
        return 10
    try:
        return user.rating_scale_max
    except AttributeError:
        return 10


@register.filter
def watched_count(history):
    """Return how many plays in an episode history list are watches."""
    if not history:
        return 0
    from app.models import Episode

    return sum(1 for entry in history if Episode.is_watch(entry))


@register.filter
def rating_plays(episode):
    """Return the plays a row's rating reads, ignoring any open rewatch pass.

    A rating belongs to the episode rather than to one pass over it, so this
    prefers the lifetime list and falls back to whatever the payload carries
    (other media types only supply `history`).
    """
    if isinstance(episode, dict):
        return episode.get("all_history") or episode.get("history") or []
    return (
        getattr(episode, "all_history", None) or getattr(episode, "history", None) or []
    )


@register.filter
def score_display(score, user):
    """Format a score using the user's rating scale."""
    if score is None:
        return None
    if not user:
        return score
    try:
        formatted = user.format_score_for_display(score)
    except AttributeError:
        return score
    return formatted if formatted is not None else score


@register.filter
def match_percent(value):
    """Convert a 0-1 match score into a clamped integer percentage."""
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    normalized = max(0.0, min(1.0, normalized))
    return round(normalized * 100)


@register.filter
def is_list(arg1):
    """Return True if the object is a list."""
    return isinstance(arg1, list)


@register.filter
def source_readable(source):
    """Return the readable source name."""
    if not source:
        return ""
    try:
        return Sources(source).label
    except ValueError:
        return source


@register.filter
def detail_link_url(sections, brand):
    """Return the Links-dropdown URL for a provider, so rating chips can reuse it."""
    brand = str(brand or "").lower()
    for section in sections or ():
        for entry in section["entries"]:
            if entry.get("brand") == brand:
                return entry["url"]
    return ""


@register.filter
def media_type_readable(media_type):
    """Return the readable media type."""
    return _(MediaTypes(media_type).label)


@register.filter
def media_type_readable_plural(media_type):
    """Return the readable media type in plural form."""
    if media_type == ALL_SEARCH_TYPE:
        return _("All")
    # English suffixes do not produce correct plurals in other languages.
    return {
        MediaTypes.TV: _("TV Shows"),
        MediaTypes.SEASON: _("TV Seasons"),
        MediaTypes.EPISODE: _("Episodes"),
        MediaTypes.MOVIE: _("Movies"),
        MediaTypes.ANIME: _("Anime"),
        MediaTypes.MANGA: _("Manga"),
        MediaTypes.GAME: _("Games"),
        MediaTypes.BOOK: _("Books"),
        MediaTypes.COMIC: _("Comics"),
        MediaTypes.COMIC_ISSUE: _("Comic Issues"),
        MediaTypes.BOARDGAME: _("Board Games"),
        MediaTypes.MUSIC: _("Music"),
        MediaTypes.PODCAST: _("Podcasts"),
        MediaTypes.VIDEO: _("Videos"),
    }[MediaTypes(media_type)]


@register.filter
def media_status_readable(media_status):
    """Return the readable media status."""
    if not media_status:
        return _("No Status")
    try:
        return _(Status(media_status).label)
    except ValueError:
        # Imported/provider data can contain a metadata status that is not a
        # user tracking status (for example, "Released"). Keep list pages
        # renderable and preserve the value when no local label exists.
        return str(media_status)


@register.filter
def default_source(media_type):
    """Return the default source for the media type."""
    return config.get_default_source_name(media_type).label


@register.filter
def media_past_verb(media_type):
    """Return the past tense verb for the given media type."""
    return _(config.get_verb(media_type, past_tense=True))


@register.filter
def browse_url(media_type):
    """Return a Discover URL filtered to the given media type."""
    return config.get_browse_url(media_type)


@register.filter
def short_unit(media_type):
    """Return the short unit for the media type."""
    try:
        return config.get_unit(media_type, short=True)
    except (KeyError, TypeError):
        return ""


@register.filter
def long_unit(media_type):
    """Return the long unit for the media type."""
    try:
        return config.get_unit(media_type, short=False)
    except (KeyError, TypeError):
        return ""


@register.filter
def progress_unit_label(media_type, count):
    """Translate progress units for display without changing raw config units."""
    labels = {
        "Episode": npgettext("progress unit", "Episode", "Episodes", count),
        "Chapter": npgettext("progress unit", "Chapter", "Chapters", count),
        "Page": npgettext("progress unit", "Page", "Pages", count),
        "Issue": npgettext("progress unit", "Issue", "Issues", count),
        "Read": npgettext("progress unit", "Read", "Reads", count),
        "Play": npgettext("progress unit", "Play", "Plays", count),
        "Minute": npgettext("progress unit", "Minute", "Minutes", count),
    }
    unit = long_unit(media_type)
    return labels.get(unit, unit)


@register.filter
def safe_attr(obj, attr):
    """Safely get an attribute from an object, returning None if it doesn't exist."""
    if obj is None:
        return None
    return getattr(obj, attr, None)


@register.filter
def safe_count(value):
    """Coerce a value to an int for use as a blocktranslate 'count', defaulting to 0.

    blocktranslate's count argument must be an int/float/Decimal; a non-numeric
    value (e.g. bad provider metadata) would otherwise raise a TemplateSyntaxError.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_title_value(value):
    return Item._normalize_title_value(value)


def _resolve_title_pair(item, preference):
    """Resolve display/alternate titles for dicts and model-like objects."""
    if isinstance(item, dict):
        title = item.get("title")
        original_title = item.get("original_title")
        localized_title = item.get("localized_title")
    else:
        title = getattr(item, "title", None)
        original_title = getattr(item, "original_title", None)
        localized_title = getattr(item, "localized_title", None)

    return Item.resolve_title_variants(
        title=title,
        original_title=original_title,
        localized_title=localized_title,
        preference=preference,
    )


@register.filter
def display_title(item, user):
    """Return the preferred display title for an item."""
    if not item:
        return ""

    if hasattr(item, "get_display_title"):
        return item.get_display_title(user=user)

    preference = getattr(user, "title_display_preference", "localized")
    display, _ = _resolve_title_pair(item, preference)
    return display


@register.filter
def alternative_title(item, user):
    """Return the alternate title (opposite of display title) for tooltip use."""
    if not item:
        return None

    if hasattr(item, "get_alternative_title"):
        return item.get_alternative_title(user=user)

    preference = getattr(user, "title_display_preference", "localized")
    _, alternative = _resolve_title_pair(item, preference)
    return alternative


@register.simple_tag
def season_card_title(item):
    """Return the best display title for a season card."""
    if not item:
        return ""

    if isinstance(item, dict):
        provider_title = item.get("season_title")
        season_number = item.get("season_number")
        fallback_title = item.get("title", "")
    else:
        provider_title = getattr(item, "season_title", None)
        season_number = getattr(item, "season_number", None)
        fallback_title = getattr(item, "title", "")

    normalized_provider_title = _normalize_title_value(provider_title)
    normalized_fallback_title = _normalize_title_value(fallback_title)
    if (
        normalized_provider_title
        and normalized_provider_title != normalized_fallback_title
    ):
        if normalized_fallback_title:
            return f"{normalized_fallback_title}: {normalized_provider_title}"
        return normalized_provider_title

    try:
        season_number = int(season_number) if season_number is not None else None
    except (TypeError, ValueError):
        season_number = None

    if season_number is not None:
        season_str = "Specials" if season_number == 0 else f"Season {season_number}"
        if normalized_fallback_title:
            return f"{normalized_fallback_title} {season_str}"
        return season_str

    return normalized_fallback_title or ""


def _resolve_music_artist_url_target(value):
    """Resolve a music-artist-like value into an object or dict with id/name."""
    if not value:
        return None

    if isinstance(value, dict):
        nested_artist = value.get("artist")
        if nested_artist:
            return _resolve_music_artist_url_target(nested_artist)
        if value.get("id") is not None and value.get("name"):
            return value
        return None

    nested_artist = getattr(value, "artist", None)
    if nested_artist is not None and not (
        getattr(value, "id", None) is not None and getattr(value, "name", None)
    ):
        return _resolve_music_artist_url_target(nested_artist)

    if getattr(value, "id", None) is not None and getattr(value, "name", None):
        return value

    return None


def _resolve_music_album_url_target(value):
    """Resolve a music-album-like value into an object or dict with id/title."""
    if not value:
        return None

    if isinstance(value, dict):
        if value.get("album_id") is not None and value.get("album"):
            return {
                "id": value.get("album_id"),
                "title": value.get("album"),
                "artist_id": value.get("album_artist_id"),
                "artist_name": value.get("album_artist_name"),
            }
        nested_album = value.get("album")
        if isinstance(nested_album, dict):
            return _resolve_music_album_url_target(nested_album)
        if value.get("id") is not None and value.get("title"):
            return value
        return None

    nested_album = getattr(value, "album", None)
    if nested_album is not None and not (
        getattr(value, "id", None) is not None and getattr(value, "title", None)
    ):
        return _resolve_music_album_url_target(nested_album)

    if getattr(value, "id", None) is not None and getattr(value, "title", None):
        return value

    return None


def _resolve_studio_url_target(value):
    """Resolve a studio-like value into an object or dict with id/name."""
    if not value:
        return None

    if isinstance(value, dict):
        nested_studio = value.get("studio") or value.get("company")
        if nested_studio:
            return _resolve_studio_url_target(nested_studio)
        if value.get("source_studio_id") is not None and value.get("name"):
            return value
        if value.get("studio_id") is not None and value.get("name"):
            return value
        if value.get("id") is not None and value.get("name"):
            return value
        return None

    nested_studio = getattr(value, "studio", None)
    if nested_studio is not None and not (
        getattr(value, "source_studio_id", None) is not None
        and getattr(value, "name", None)
    ):
        return _resolve_studio_url_target(nested_studio)

    if getattr(value, "source_studio_id", None) is not None and getattr(
        value, "name", None
    ):
        return value
    if getattr(value, "studio_id", None) is not None and getattr(value, "name", None):
        return value

    return None


def _music_slug(value, fallback):
    """Return a stable slug with a safe fallback."""
    normalized = slug(value or "")
    if normalized and "%" not in normalized:
        return normalized
    fallback_value = str(fallback or "item")
    return slug(fallback_value) or "item"


@register.filter
def music_artist_url(artist):
    """Return the canonical shared media-details URL for a music artist."""
    resolved_artist = _resolve_music_artist_url_target(artist)
    if not resolved_artist:
        return ""

    if isinstance(resolved_artist, dict):
        artist_id = resolved_artist.get("id")
        artist_name = resolved_artist.get("name")
    else:
        artist_id = resolved_artist.id
        artist_name = resolved_artist.name

    if artist_id is None:
        return ""

    return reverse(
        "music_artist_details",
        kwargs={
            "artist_id": artist_id,
            "artist_slug": _music_slug(artist_name, artist_id),
        },
    )


@register.filter
def music_album_url(album):
    """Return the canonical shared media-details URL for a music album."""
    resolved_album = _resolve_music_album_url_target(album)
    if not resolved_album:
        return ""

    if isinstance(resolved_album, dict):
        album_id = resolved_album.get("id")
        album_title = resolved_album.get("title")
        artist = resolved_album.get("artist")
        artist_id = resolved_album.get("artist_id")
        artist_name = resolved_album.get("artist_name")
    else:
        album_id = resolved_album.id
        album_title = resolved_album.title
        artist = getattr(resolved_album, "artist", None)
        artist_id = getattr(artist, "id", None)
        artist_name = getattr(artist, "name", None)

    if isinstance(artist, dict):
        artist_id = artist.get("id", artist_id)
        artist_name = artist.get("name", artist_name)

    if album_id is None:
        return ""

    return reverse(
        "music_album_details",
        kwargs={
            "artist_id": artist_id or 0,
            "artist_slug": _music_slug(
                artist_name or "Unknown Artist",
                artist_id or "artist",
            ),
            "album_id": album_id,
            "album_slug": _music_slug(album_title, album_id),
        },
    )


@register.filter
def music_track_url(track):
    """Return the canonical shared media-details URL for a music track."""
    if track is None or isinstance(track, dict):
        return ""

    track_id = getattr(track, "id", None)
    album = getattr(track, "album", None)
    if track_id is None or album is None or getattr(album, "id", None) is None:
        return ""

    artist = getattr(album, "artist", None)
    artist_id = getattr(artist, "id", None)
    artist_name = getattr(artist, "name", None)
    return reverse(
        "music_track_details",
        kwargs={
            "artist_id": artist_id or 0,
            "artist_slug": _music_slug(
                artist_name or "Unknown Artist",
                artist_id or "artist",
            ),
            "album_id": album.id,
            "album_slug": _music_slug(album.title, album.id),
            "track_id": track_id,
            "track_slug": _music_slug(getattr(track, "title", ""), track_id),
        },
    )


def _studio_slug(value, fallback):
    """Return a stable slug with a safe fallback for studio links."""
    normalized = slug(value or "")
    if normalized and "%" not in normalized:
        return normalized
    fallback_value = str(fallback or "item")
    return slug(fallback_value) or "item"


@register.filter
def studio_url(studio):
    """Return the canonical shared media-details URL for a studio/company."""
    resolved_studio = _resolve_studio_url_target(studio)
    if not resolved_studio:
        return ""

    if isinstance(resolved_studio, dict):
        studio_id = (
            resolved_studio.get("source_studio_id")
            or resolved_studio.get("studio_id")
            or resolved_studio.get("id")
        )
        studio_name = resolved_studio.get("name")
        source = resolved_studio.get("source")
    else:
        studio_id = (
            getattr(resolved_studio, "source_studio_id", None)
            or getattr(resolved_studio, "studio_id", None)
            or getattr(resolved_studio, "id", None)
        )
        studio_name = getattr(resolved_studio, "name", None)
        source = getattr(resolved_studio, "source", None)

    if studio_id is None or not source:
        return ""

    return reverse(
        "studio_detail",
        kwargs={
            "source": source,
            "studio_id": studio_id,
            "name": _studio_slug(studio_name, studio_id),
        },
    )


@register.filter
def release_year(item, media=None):
    """Return a best-effort release year from dicts or model instances."""
    if media and hasattr(media, "item"):
        display_year = getattr(media.item, "display_release_year", None)
        if display_year:
            return display_year
        release_dt = getattr(media.item, "release_datetime", None)
        if release_dt:
            try:
                return timezone.localtime(release_dt).year
            except (OverflowError, ValueError, OSError):
                pass

    if not item:
        return None

    if isinstance(item, dict):
        year_value = item.get("year") or item.get("start_year")
        if not year_value:
            date_value = item.get("release_date") or item.get("first_air_date")
            if date_value:
                year_value = str(date_value).split("-")[0]
        try:
            return int(str(year_value)) if year_value is not None else None
        except (TypeError, ValueError):
            return None

    display_year = getattr(item, "display_release_year", None)
    if display_year:
        return display_year

    release_dt = getattr(item, "release_datetime", None)
    if release_dt:
        try:
            return timezone.localtime(release_dt).year
        except (OverflowError, ValueError, OSError):
            return None

    return None


@register.filter
def sources(media_type):
    """Template filter to get source options for a media type."""
    return metadata_resolution.available_metadata_sources(media_type)


@register.simple_tag
def get_search_media_types(user):
    """Return available media types for search based on user preferences."""
    # Handle anonymous users by returning all media types
    if not user or not user.is_authenticated:
        enabled_types = [
            mt
            for mt in MediaTypes.values
            if mt not in (MediaTypes.EPISODE.value, MediaTypes.SEASON.value)
        ]
    else:
        enabled_types = user.get_enabled_media_types()

    # Filter and format the types for search
    search_types = [
        {
            "display": media_type_readable_plural(media_type),
            "value": media_type,
        }
        for media_type in enabled_types
        if media_type != MediaTypes.SEASON.value
    ]
    if user and user.is_authenticated:
        # Library-wide search across every enabled type (#1160).
        search_types.insert(0, {"display": _("All"), "value": ALL_SEARCH_TYPE})
    return search_types


def _saved_views_by_type(user):
    """Group the user's saved views by type, reading them once per request."""
    if not user or not user.is_authenticated:
        return {}
    grouped = getattr(user, "_saved_views_by_type", None)
    if grouped is None:
        grouped = {}
        for saved_view in user.saved_views.all():
            grouped.setdefault(saved_view.media_type, []).append(saved_view)
        user._saved_views_by_type = grouped
    return grouped


@register.simple_tag
def get_history_saved_views(user):
    """Return the user's saved History views for the sidebar."""
    return _saved_views_by_type(user).get(HISTORY_VIEW_TYPE, [])


@register.simple_tag
def get_sidebar_media_types(user):
    """Return available media types for sidebar navigation based on user preferences."""
    # Handle anonymous users by returning all media types
    if not user or not user.is_authenticated:
        enabled_types = [
            mt
            for mt in MediaTypes.values
            if mt not in (MediaTypes.EPISODE.value, MediaTypes.COMIC_ISSUE.value)
        ]
    else:
        enabled_types = user.get_sidebar_media_types()

    saved_views_by_type = _saved_views_by_type(user)

    # Format the types for sidebar
    return [
        {
            "media_type": media_type,
            "display_name": media_type_readable_plural(media_type),
            "saved_views": saved_views_by_type.get(media_type, []),
        }
        for media_type in enabled_types
    ]


@register.filter
def media_color(media_type):
    """Return the color associated with the media type."""
    return config.get_text_color(media_type)


@register.filter
def status_color(status):
    """Return the color associated with the status."""
    return config.get_status_text_color(status)


@register.filter
def natural_day(datetime, user):
    """Format date with natural language (Today, Tomorrow, etc.)."""
    # Get today's date in the current timezone
    today = timezone.localdate()

    # Extract just the date part for comparison
    datetime_date = datetime.date()

    # Calculate the difference in days
    diff = datetime_date - today
    days = diff.days

    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"

    # For dates further away
    return datetime_format(datetime, user)


@register.filter
def user_event_time(event, user):
    """Format event time according to user's time format preference."""
    if not event or not user or event.is_sentinel_time:
        return ""

    try:
        local_dt = timezone.localtime(event.datetime)

        if user.time_format == TimeFormatChoices.SYSTEM_DEFAULT:
            time_str = formats.date_format(local_dt, "TIME_FORMAT")
        elif user.time_format == TimeFormatChoices.H_MM_AMPM:
            # Use %I and manually remove leading zero for cross-platform compatibility
            hour = str(local_dt.hour % 12 or 12)  # Convert 0 to 12 for 12-hour format
            time_str = f"{hour}:{local_dt.strftime('%M %p')}"
        elif user.time_format == TimeFormatChoices.HH_MM_AMPM:
            time_str = local_dt.strftime("%I:%M %p")
        elif user.time_format == TimeFormatChoices.HH_MM:
            time_str = local_dt.strftime("%H:%M")
        elif user.time_format == TimeFormatChoices.HH_MM_SS:
            time_str = local_dt.strftime("%H:%M:%S")
        else:
            time_str = formats.date_format(local_dt, "TIME_FORMAT")

    except (ValueError, TypeError, AttributeError):
        # Fallback to default format if there's an error
        local_dt = timezone.localtime(event.datetime)
        return f"at {local_dt.strftime('%H:%M')}"
    else:
        return f"at {time_str}"


@register.filter
def event_within_days(event, days):
    """Return True if an event is within the next N days (inclusive)."""
    if not event or not hasattr(event, "datetime"):
        return False

    try:
        days = int(days)
    except (TypeError, ValueError):
        return False

    try:
        event_date = timezone.localtime(event.datetime).date()
    except (ValueError, TypeError, AttributeError):
        return False

    today = timezone.localdate()
    delta_days = (event_date - today).days
    return 0 <= delta_days <= days


@register.filter
def media_url(media):
    """Return the media URL for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    if is_dict:
        actual_media_type = media.get("media_type")
        if not actual_media_type:
            return ""
    else:
        try:
            actual_media_type = media.media_type
        except (AttributeError, KeyError, TypeError):
            return ""
        if not actual_media_type:
            return ""
    # Route override — used to route TV items to anime show pages, or season items
    # to anime season URLs when the parent show is anime
    route_media_type = (
        media.get("route_media_type")
        if is_dict
        else getattr(media, "route_media_type", None)
    )

    source = media["source"] if is_dict else media.source
    media_id = media["media_id"] if is_dict else media.media_id
    title = media["title"] if is_dict else media.title
    slug_title = slug(title)
    if not slug_title:
        fallback = str(media_id) if media_id is not None else "item"
        slug_title = slug(fallback) or "item"

    if actual_media_type == MediaTypes.EPISODE.value:
        season_number = media["season_number"] if is_dict else media.season_number
        episode_number = media["episode_number"] if is_dict else media.episode_number
        if route_media_type == MediaTypes.ANIME.value:
            return reverse(
                "anime_episode_details",
                kwargs={
                    "source": source,
                    "media_id": media_id,
                    "title": slug_title,
                    "season_number": season_number,
                    "episode_number": episode_number,
                },
            )
        return reverse(
            "episode_details",
            kwargs={
                "source": source,
                "media_id": media_id,
                "title": slug_title,
                "season_number": season_number,
                "episode_number": episode_number,
            },
        )

    if actual_media_type == MediaTypes.SEASON.value:
        season_number = media["season_number"] if is_dict else media.season_number
        if route_media_type == MediaTypes.ANIME.value:
            return reverse(
                "anime_season_details",
                kwargs={
                    "source": source,
                    "media_id": media_id,
                    "title": slug_title,
                    "season_number": season_number,
                },
            )
        return reverse(
            "season_details",
            kwargs={
                "source": source,
                "media_id": media_id,
                "title": slug_title,
                "season_number": season_number,
            },
        )

    # For show/movie/etc, route_media_type overrides actual_media_type
    # (e.g. a TV Item with library_media_type=anime routes to the anime show page)
    effective_media_type = route_media_type or actual_media_type
    return reverse(
        "media_details",
        kwargs={
            "source": source,
            "media_type": effective_media_type,
            "media_id": media_id,
            "title": slug_title,
        },
    )


def _untracked_season_next_episode_url(item, seasons, actual_media_type):
    """Return the first-episode URL of the next untracked season, if known.

    When every tracked season is complete the next unwatched episode lives in a
    season without a Season row. Untracked seasons are only visible through
    their calendar events (same source as _next_episode_air_date_value).
    """
    from events.models import Event, released_q

    is_dict = isinstance(item, dict)
    media_id = item["media_id"] if is_dict else getattr(item, "media_id", None)
    source = item["source"] if is_dict else getattr(item, "source", None)
    if not media_id or not source:
        return ""

    tracked_season_numbers = {
        season.item.season_number
        for season in seasons.all()
        if getattr(season, "item", None) is not None
    }
    event = (
        Event.objects.filter(
            item__media_id=media_id,
            item__source=source,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number__gt=0,
            content_number__isnull=False,
        )
        .exclude(item__season_number__in=tracked_season_numbers)
        .exclude(datetime__year__lt=1900)
        .filter(released_q("datetime", timezone.now()))
        .order_by("item__season_number", "content_number")
        .select_related("item")
        .first()
    )
    if event is None:
        return ""

    title = item["title"] if is_dict else getattr(item, "title", "")
    slug_title = slug(title) or slug(str(media_id)) or "item"
    is_anime = actual_media_type == MediaTypes.ANIME.value
    url_name = "anime_episode_details" if is_anime else "episode_details"
    return reverse(
        url_name,
        kwargs={
            "source": source,
            "media_id": media_id,
            "title": slug_title,
            "season_number": event.item.season_number,
            "episode_number": event.content_number,
        },
    )


def _next_episode_number_for_season_item(item, media):
    """Resolve a season-card target from locally known released episodes."""
    if hasattr(media, "next_episode_number"):
        return media.next_episode_number()

    is_dict = isinstance(item, dict)
    media_id = item["media_id"] if is_dict else getattr(item, "media_id", None)
    source = item["source"] if is_dict else getattr(item, "source", None)
    season_number = (
        item["season_number"] if is_dict else getattr(item, "season_number", None)
    )
    if not media_id or not source or season_number is None:
        return None

    from events.models import Event, released_q

    event_numbers = (
        Event.objects.filter(
            item__media_id=media_id,
            item__source=source,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number=season_number,
            content_number__isnull=False,
        )
        .exclude(datetime__year__lt=1900)
        .filter(released_q("datetime", timezone.now()))
        .values_list("content_number", flat=True)
    )
    episode_numbers = sorted({int(number) for number in event_numbers})
    if not episode_numbers:
        max_progress = getattr(media, "max_progress", None)
        try:
            max_progress = int(max_progress)
        except (TypeError, ValueError):
            max_progress = None
        if max_progress and max_progress > 0:
            episode_numbers = range(1, max_progress + 1)

    return tmdb.find_next_episode(
        getattr(media, "progress", 0) or 0,
        [{"episode_number": number} for number in episode_numbers],
    )


@register.simple_tag
def next_episode_url(item, media):
    """Return the episode detail URL for the next unwatched episode.

    Supports both Season cards (item.media_type == 'season') and TV/Anime show
    cards (item.media_type in ['tv', 'anime']).  For show cards the in-progress
    season is found via the already-prefetched seasons queryset.
    """
    is_dict = isinstance(item, dict)
    actual_media_type = (
        item["media_type"] if is_dict else getattr(item, "media_type", None)
    )

    # ── Season card ──────────────────────────────────────────────────────────
    if actual_media_type == MediaTypes.SEASON.value:
        season_number = (
            item["season_number"] if is_dict else getattr(item, "season_number", None)
        )
        if season_number is None:
            return ""
        title = item["title"] if is_dict else getattr(item, "title", "")
        media_id = item["media_id"] if is_dict else getattr(item, "media_id", None)
        source = item["source"] if is_dict else getattr(item, "source", None)
        if not media_id or not source:
            return ""
        slug_title = slug(title) or slug(str(media_id)) or "item"
        library_media_type = (
            item.get("library_media_type", "")
            if is_dict
            else getattr(item, "library_media_type", "")
        )
        is_anime = library_media_type == MediaTypes.ANIME.value
        url_name = "anime_episode_details" if is_anime else "episode_details"
        next_ep = _next_episode_number_for_season_item(item, media)
        if next_ep is None:
            return ""
        return reverse(
            url_name,
            kwargs={
                "source": source,
                "media_id": media_id,
                "title": slug_title,
                "season_number": season_number,
                "episode_number": next_ep,
            },
        )

    # ── TV / Anime show card ─────────────────────────────────────────────────
    if actual_media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        seasons = getattr(media, "seasons", None)
        if not media or seasons is None:
            # Flat MAL anime has no seasons — resolve the provider mapping at
            # click time via the redirect view instead of per card at render.
            source = item["source"] if is_dict else getattr(item, "source", None)
            media_id = item["media_id"] if is_dict else getattr(item, "media_id", None)
            if (
                actual_media_type == MediaTypes.ANIME.value
                and source == Sources.MAL.value
                and media_id
            ):
                title = item["title"] if is_dict else getattr(item, "title", "")
                slug_title = slug(title) or slug(str(media_id)) or "item"
                return reverse(
                    "anime_next_episode",
                    kwargs={"media_id": media_id, "title": slug_title},
                )
            return ""

        next_episode_target = (
            media.next_episode_target()
            if hasattr(media, "next_episode_target")
            else None
        )
        if next_episode_target is None:
            return _untracked_season_next_episode_url(item, seasons, actual_media_type)

        season, next_ep = next_episode_target
        season_item = season.item
        slug_title = (
            slug(season_item.title) or slug(str(season_item.media_id)) or "item"
        )
        is_anime = actual_media_type == MediaTypes.ANIME.value
        url_name = "anime_episode_details" if is_anime else "episode_details"
        return reverse(
            url_name,
            kwargs={
                "source": season_item.source,
                "media_id": season_item.media_id,
                "title": slug_title,
                "season_number": season_item.season_number,
                "episode_number": next_ep,
            },
        )

    return ""


@register.simple_tag
def media_view_url(view_name, media):
    """Return the modal URL for both metadata and model object cases."""
    is_dict = isinstance(media, dict)
    if not is_dict and not hasattr(media, "source"):
        return ""

    media_type = media["media_type"] if is_dict else media.media_type

    # Build kwargs using either dict access or object attribute
    kwargs = {
        "source": media["source"] if is_dict else media.source,
        "media_type": (
            media.get("route_media_type") or media_type
            if is_dict
            else getattr(media, "route_media_type", None) or media_type
        ),
        "media_id": str(media["media_id"] if is_dict else media.media_id),
    }

    # Providers may include season_number=0 on top-level TV/anime metadata.
    # Only season and episode objects use those values as URL path segments.
    if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
        season_number = (
            media.get("season_number")
            if is_dict
            else getattr(media, "season_number", None)
        )
        if season_number is not None:
            kwargs["season_number"] = season_number
    if media_type == MediaTypes.EPISODE.value:
        episode_number = (
            media.get("episode_number")
            if is_dict
            else getattr(media, "episode_number", None)
        )
        if episode_number is not None:
            kwargs["episode_number"] = episode_number

    # collection_modal and track_modal URLs do not accept episode in the path.
    if view_name in ("collection_modal", "track_modal"):
        kwargs.pop("episode_number", None)
    if view_name == "collection_modal":
        kwargs.pop("season_number", None)

    return reverse(view_name, kwargs=kwargs)


@register.simple_tag
def item_slot_id(prefix, item):
    """Return an id for a card slot that a save or score response swaps in place.

    Built from the item's own identity (source, type, ids), so it is the same
    on a card for something not tracked yet and in the response after saving.
    Unlike component_id it ignores route_media_type, which is only set on
    some cards (e.g. grouped anime in search), and it includes the source,
    since two providers can share a media id.
    """
    is_dict = isinstance(item, dict)
    if not is_dict and not hasattr(item, "media_type"):
        return ""

    def get(name):
        return item.get(name) if is_dict else getattr(item, name, None)

    parts = [
        prefix,
        get("source"),
        get("media_type"),
        get("media_id"),
        get("season_number"),
        get("episode_number"),
    ]
    slot_id = "-".join(str(part) for part in parts if part not in (None, ""))
    return re.sub(r"[^A-Za-z0-9_-]", "-", slot_id)


@register.simple_tag
def lists_modal_click(media, return_url=""):
    """Return the onclick attribute that opens the Add-to-lists form in the popup host."""
    import json

    from app.media_card_model import lists_modal_spec

    spec = lists_modal_spec(
        media,
        f"#{component_id('lists', media)}",
        return_url,
    )
    return format_html(
        "onclick=\"window.dispatchEvent(new CustomEvent('floppy-open-modal', "
        '{{detail: {}}}))"',
        json.dumps(spec),
    )


@register.simple_tag
def component_id(component_type, media, instance_id=None):
    """Return the component ID for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    if not is_dict and not hasattr(media, "media_type"):
        return ""

    # Get base attributes using either dict access or object attribute
    media_type = (
        media.get("route_media_type") or media["media_type"]
        if is_dict
        else getattr(media, "route_media_type", None) or media.media_type
    )
    media_id = media["media_id"] if is_dict else media.media_id

    component_id = f"{component_type}-{media_type}-{media_id}"

    # Handle season/episode numbers if they exist
    if is_dict:
        if "season_number" in media:
            component_id += f"-{media['season_number']}"
        if "episode_number" in media:
            component_id += f"-{media['episode_number']}"
    else:
        season_number = getattr(media, "season_number", None)
        episode_number = getattr(media, "episode_number", None)
        if season_number is not None:
            component_id += f"-{season_number}"
        if episode_number is not None:
            component_id += f"-{episode_number}"

    # Add instance id if provided
    if instance_id:
        component_id += f"-{instance_id}"

    # htmx resolves hx-target="#..." via querySelectorAll, so URL-shaped
    # media IDs must not leave CSS selector punctuation in the component ID.
    return re.sub(r"[^A-Za-z0-9_-]", "-", component_id)


@register.simple_tag
def unicode_icon(name):
    """Return the Unicode icon for the media type."""
    return config.get_unicode_icon(name)


@register.simple_tag
def icon(name, is_active, extra_classes="w-5 h-5"):
    """Return the SVG icon for the given name."""
    base_svg = """<svg xmlns="http://www.w3.org/2000/svg"
                      width="24"
                      height="24"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      class="{active_class}{extra_classes}">
                      {content}
                 </svg>"""

    other_icons = {
        "home": (
            """<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
               <polyline points="9 22 9 12 15 12 15 22"></polyline>"""
        ),
        "create": (
            """<circle cx="12" cy="12" r="10"></circle>
               <path d="M8 12h8"></path>
               <path d="M12 8v8"></path>"""
        ),
        "statistics": (
            """<line x1="18" x2="18" y1="20" y2="10"></line>
               <line x1="12" x2="12" y1="20" y2="4"></line>
               <line x1="6" x2="6" y1="20" y2="14"></line>"""
        ),
        "history": (
            """<path d="M3 3v5h5"></path>
               <path d="M3.05 13a9 9 0 1 0 .5-5.5"></path>
               <path d="M12 7v5l3 3"></path>"""
        ),
        "lists": (
            """<path d="M12 10v6"></path>
               <path d="M9 13h6"></path>
               <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9
               L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path>"""
        ),
        "calendar": (
            """<path d="M8 2v4"></path>
               <path d="M16 2v4"></path>
               <rect width="18" height="18" x="3" y="4" rx="2"></rect>
               <path d="M3 10h18"></path>"""
        ),
        "settings": (
            """<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2
               2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73
               2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0
               0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2
               2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1
               1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2
               0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2
               2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0
               1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
               <circle cx="12" cy="12" r="3"></circle>"""
        ),
        "logout": (
            """<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
               <polyline points="16 17 21 12 16 7"></polyline>
               <line x1="21" x2="9" y1="12" y2="12"></line>"""
        ),
    }

    if name in MediaTypes.values:
        content = config.get_svg_icon(name)
    else:
        content = other_icons[name]
    active_class = "text-[var(--color-link)] " if is_active else ""

    svg = base_svg.format(
        content=content,
        active_class=active_class,
        extra_classes=extra_classes,
    )

    return format_html(svg)


@register.filter
def str_equals(value, arg):
    """Return True if the string value is equal to the argument."""
    return str(value) == str(arg)


@register.filter
def get_range(value):
    """Return a range from 1 to the given value."""
    return range(1, int(value) + 1)


@register.simple_tag
def get_pagination_range(current_page, total_pages, window):
    """
    Return a list of page numbers to display in pagination.

    Args:
        current_page: The current page number
        total_pages: Total number of pages
        window: Number of pages to show before and after current page

    Returns:
        A list of page numbers and None values (for ellipses)
    """
    if total_pages <= 5 + window * 2:
        # If few pages, show all
        return list(range(1, total_pages + 1))

    # Calculate left and right boundaries
    left_boundary = max(2, current_page - window)
    right_boundary = min(total_pages - 1, current_page + window)

    # Add ellipsis indicators and page numbers
    result = [1]

    second_page = 2
    # Add left ellipsis if needed
    if left_boundary > second_page:
        result.append(None)  # None represents ellipsis

    # Add pages around current page
    result.extend(range(left_boundary, right_boundary + 1))

    # Add right ellipsis if needed
    if right_boundary < total_pages - 1:
        result.append(None)  # None represents ellipsis

    # Add last page if not already included
    if total_pages not in result:
        result.append(total_pages)

    return result


# Day-count deltas (end_date - start_date) that identify named preset date
# ranges. Each is one less than the calendar span, since both endpoints are
# inclusive (e.g. "This Week" spans 7 days -> a 6-day difference).
DAYS_DIFF_ONE_WEEK = 6
DAYS_DIFF_ONE_MONTH = 29
DAYS_DIFF_90_DAYS = 89
DAYS_DIFF_6_MONTHS_MIN = 175
DAYS_DIFF_6_MONTHS_MAX = 185
DAYS_DIFF_ONE_YEAR = 364


def _check_same_day_ranges(start_date, end_date, today):
    """Check for same-day date ranges like Today and Yesterday."""
    if start_date == end_date:
        if start_date == today:
            return "Today"
        if start_date == today - timedelta(days=1):
            return "Yesterday"
    return None


def _check_week_ranges(start_date, end_date, today):
    """Check for week-based date ranges."""
    days_diff = (end_date - start_date).days
    if days_diff == DAYS_DIFF_ONE_WEEK:  # 7 days including start and end
        if start_date == today - timedelta(days=6):
            return "This Week"
        if start_date == today - timedelta(days=13):
            return "Last Week"
        return "Last 7 Days"
    return None


def _check_month_ranges(start_date, end_date, today):
    """Check for month-based date ranges."""
    days_diff = (end_date - start_date).days
    month_start = today.replace(day=1)
    if start_date == month_start and end_date == today:
        return "This Month"
    if start_date == month_start and end_date == today - timedelta(days=1):
        return "This Month"
    if days_diff == DAYS_DIFF_ONE_MONTH:  # 30 days including start and end
        if start_date == today - timedelta(days=29):
            return "This Month"
        if start_date == today - timedelta(days=59):
            return "Last Month"
        return "Last 30 Days"
    return None


def _check_extended_ranges(start_date, end_date):
    """Check for extended date ranges like 90 days, 6 months, and 1 year."""
    days_diff = (end_date - start_date).days

    # Check for 90 days
    if days_diff == DAYS_DIFF_90_DAYS:  # 90 days including start and end
        return "Last 90 Days"

    # Check for 6 months (approximately 180 days)
    if DAYS_DIFF_6_MONTHS_MIN <= days_diff <= DAYS_DIFF_6_MONTHS_MAX:
        return "Last 6 Months"

    # Check for year ranges
    if days_diff == DAYS_DIFF_ONE_YEAR:  # 365 days including start and end
        return "Last 12 Months"

    return None


def _is_predefined_date_range(start_date, end_date, today):
    """Check if the date range matches any predefined ranges."""
    # Check same-day ranges
    result = _check_same_day_ranges(start_date, end_date, today)
    if result:
        return result

    # Check month ranges
    result = _check_month_ranges(start_date, end_date, today)
    if result:
        return result

    # Check week ranges
    result = _check_week_ranges(start_date, end_date, today)
    if result:
        return result

    # Check extended ranges
    result = _check_extended_ranges(start_date, end_date)
    if result:
        return result

    return None


@register.filter
def order_by_end_date(queryset):
    """Order a queryset by end_date in ascending order (oldest first, chronological)."""
    if queryset is None:
        return queryset
    try:
        return queryset.order_by("end_date")
    except (AttributeError, TypeError):
        # If it's not a queryset or doesn't have end_date, return as-is
        return queryset


@register.filter
def format_date_range_display(start_date, end_date):
    """Format date range for display in card titles.

    Returns a human-readable string like "Last 12 Months" or "Date Range"
    based on whether it's a predefined range or custom dates.
    """
    if start_date is None and end_date is None:
        return "All Time"

    if start_date is None or end_date is None:
        return "Date Range"

    # Convert to date objects if they're datetime
    if hasattr(start_date, "date"):
        start_date = start_date.date()
    if hasattr(end_date, "date"):
        end_date = end_date.date()

    today = date.today()  # noqa: DTZ011  # plain calendar date, matching upstream behaviour

    # Check for predefined ranges
    predefined_range = _is_predefined_date_range(start_date, end_date, today)
    if predefined_range:
        return predefined_range

    # If none of the predefined ranges match, return "Date Range"
    return "Date Range"


@register.filter
def filter_media_types(entries, media_types_str):
    """Filter entries to only include specified media types.

    Usage: {{ entries|filter_media_types:"music,podcast" }}

    Args:
        entries: List of history entry dicts with 'media_type' key
        media_types_str: Comma-separated list of media types to include

    Returns:
        List of entries matching the specified media types
    """
    if not entries or not media_types_str:
        return entries
    media_types = {mt.strip().lower() for mt in media_types_str.split(",")}
    return [
        entry for entry in entries if entry.get("media_type", "").lower() in media_types
    ]


@register.filter
def exclude_media_types(entries, media_types_str):
    """Filter entries to exclude specified media types.

    Usage: {{ entries|exclude_media_types:"music,podcast" }}

    Args:
        entries: List of history entry dicts with 'media_type' key
        media_types_str: Comma-separated list of media types to exclude

    Returns:
        List of entries NOT matching the specified media types
    """
    if not entries or not media_types_str:
        return entries
    media_types = {mt.strip().lower() for mt in media_types_str.split(",")}
    return [
        entry
        for entry in entries
        if entry.get("media_type", "").lower() not in media_types
    ]


@register.filter
def is_square_media_type(media_type):
    """Check if a media type uses square (1:1) aspect ratio artwork.

    Usage: {% if entry.media_type|is_square_media_type %}

    Returns:
        True for music and podcast types, False otherwise
    """
    if not media_type:
        return False
    return media_type.lower() in ("music", "podcast")


@register.filter
def filter_home_media_types(items, media_types_str):
    """Filter home page items to only include specified media types.

    Usage: {{ media_list.items|filter_home_media_types:"music,podcast" }}

    Args:
        items: List of BasicMedia objects with item.media_type attribute
        media_types_str: Comma-separated list of media types to include

    Returns:
        List of items matching the specified media types
    """
    if not items or not media_types_str:
        return items
    media_types = {mt.strip().lower() for mt in media_types_str.split(",")}
    return [
        item
        for item in items
        if getattr(getattr(item, "item", None), "media_type", "").lower() in media_types
    ]


@register.filter
def exclude_home_media_types(items, media_types_str):
    """Filter home page items to exclude specified media types.

    Usage: {{ media_list.items|exclude_home_media_types:"music,podcast" }}

    Args:
        items: List of BasicMedia objects with item.media_type attribute
        media_types_str: Comma-separated list of media types to exclude

    Returns:
        List of items NOT matching the specified media types
    """
    if not items or not media_types_str:
        return items
    media_types = {mt.strip().lower() for mt in media_types_str.split(",")}
    return [
        item
        for item in items
        if getattr(getattr(item, "item", None), "media_type", "").lower()
        not in media_types
    ]


@register.filter
def show_media_score(rating, user):
    """Return whether a media rating should be displayed for the user."""
    if rating is None:
        return False

    try:
        rating_value = float(rating)
    except (TypeError, ValueError):
        return True

    from users.card_metadata import hides_zero_rating

    hide_zero = hides_zero_rating(user, None)
    return not hide_zero or rating_value > 0


@register.simple_tag(takes_context=True)
def score_is_visible(context, rating, media_type=None):
    """Return whether ``rating`` should show for this card's media type."""
    from users.card_metadata import hides_zero_rating, shows_score

    user = context.get("user")
    if media_type is None:
        media_type = context.get("resolved_media_type")
    item = context.get("item")
    if media_type is None and item is not None:
        media_type = getattr(item, "media_type", None)
    if not shows_score(user, media_type):
        return False
    if rating is None:
        return False
    try:
        rating_value = float(rating)
    except (TypeError, ValueError):
        return True
    hide_zero = hides_zero_rating(user, media_type)
    return not hide_zero or rating_value > 0


@register.simple_tag(takes_context=True)
def card_field_on(context, media_type, field_id):
    """Return whether this type's profile includes ``field_id``."""
    from users.card_metadata import field_enabled

    user = context.get("user") or getattr(context.get("request"), "user", None)
    return field_enabled(user, media_type, field_id)


@register.simple_tag(takes_context=True)
def card_subtitle_class(context, media_type=None):
    """Return the always-visible subtitle class, or an empty string."""
    from users.card_metadata import (
        DISPLAY_ALWAYS,
        DISPLAY_HOVER,
        resolve_profile,
        subtitle_display,
    )

    user = context.get("user") or getattr(context.get("request"), "user", None)
    # A card-level always class would reveal lines the user set to hover.
    lines = resolve_profile(user, media_type).get("lines") or []
    if any(line.get("display") == DISPLAY_HOVER for line in lines):
        return ""
    if subtitle_display(user, media_type) == DISPLAY_ALWAYS:
        return " media-card-subtitle-always"
    return ""


@register.simple_tag(takes_context=True)
def card_title_classes(context, media_type=None):
    """Return the saved title treatment as classes for a hand-rolled card.

    Empty while the type keeps the default treatment, so those cards keep their
    own title clamps until the user changes a title option.
    """
    from users.card_metadata import (
        TITLE_LINE_ALL,
        default_title_options,
        resolve_profile,
        title_options,
    )

    user = context.get("user") or getattr(context.get("request"), "user", None)
    title = title_options(resolve_profile(user, media_type))
    if title == default_title_options():
        return ""
    classes = [
        f"media-card-title-{title['overflow']}",
        f"media-card-title-{title['hover']}",
    ]
    if title["lines"] == TITLE_LINE_ALL:
        classes += ["media-card-title-multiline", "media-card-title-lines-all"]
    else:
        classes.append(f"media-card-title-rest-{title['lines']}")
        if title["lines"] > 1:
            classes.append("media-card-title-multiline")
    if title["hover_lines"] == TITLE_LINE_ALL:
        classes.append("media-card-title-hover-all")
    else:
        classes.append(f"media-card-title-hover-{title['hover_lines']}")
    return " " + " ".join(classes)


def _as_int(value, default=0):
    """Coerce a row count/seed to int without trusting template data."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@register.simple_tag(takes_context=True)
def rails_row_props(context, row):
    """Return the shared Rail's props beside a server-rendered scrollable row.

    The fallback row keeps its cards and HTMX attributes. The browser adapter
    reads those cards as item slots, so this only carries row-level values.
    """
    if not isinstance(row, dict) or not row.get("row_id"):
        return {}

    row_id = row["row_id"]
    title = str(row.get("title_main") or row.get("title") or "")
    title_main = str(_(title))
    title_detail = str(row.get("title_detail") or "")
    if title_detail:
        title_detail = str(_(title_detail))
    summary_inline = str(row.get("summary_inline") or "")
    loaded = _as_int(row.get("loaded_count"))
    total = _as_int(row.get("total"))

    sort_url = ""
    if summary_inline:
        try:
            sort_url = reverse("toggle_home_screen_row_direction", args=[row_id])
        except Exception:
            sort_url = ""

    if sort_url:
        sort_label = str(_("Toggle sort direction for %(title)s") % {"title": title_main})
    else:
        sort_label = ""

    view_all = None
    if row.get("view_all_url") and row.get("view_all_text"):
        view_all = {
            "label": str(row["view_all_text"]),
            "href": str(row["view_all_url"]),
            "external": not bool(row.get("view_all_internal")),
        }

    return {
        "id": f"rails-row-{uuid4().hex[:8]}",
        "props": {
            "rowId": str(row_id),
            "title": title_main,
            "titleDetail": title_detail,
            "summary": str(row.get("summary") or ""),
            "summaryInline": summary_inline,
            "direction": str(row.get("direction") or "desc"),
            "total": total,
            "loadedCount": loaded,
            "hasMore": loaded < total,
            "seed": _as_int(row.get("seed")),
            "hxUrl": str(
                context.get("hx_url")
                or f"{reverse('home')}?load_row={row_id}"
            ),
            "sortUrl": sort_url,
            "sortLabel": sort_label,
            "csrfToken": str(context.get("csrf_token") or ""),
            "titleHref": str(row.get("url") or ""),
            "viewAll": view_all,
            "hideHeading": bool(context.get("hide_heading")),
            "collapseKey": str(context.get("collapse_key") or ""),
            "collapseDefault": "true" if context.get("collapse_default") in {True, "true", "True"} else "false",
            "labels": {
                "scrollLeft": str(_("Scroll left")),
                "scrollRight": str(_("Scroll right")),
                "toggleSection": str(_("Toggle section")),
                "loadingMore": str(_("Loading more")),
            },
        },
    }


@register.inclusion_tag("app/components/card_lines.html", takes_context=True)
def card_lines(context, media_type, item=None, media=None):
    """Render the enabled subtitle lines for one card."""
    from users.card_metadata import card_lines as render_lines

    user = context.get("user") or getattr(context.get("request"), "user", None)
    return {"lines": render_lines(user, media_type, item, media)}


@register.inclusion_tag("app/components/media_card.html", takes_context=True)
def media_card(context, surface, **values):
    """Render the shared media card as ``surface`` declares it.

    See ``app.card_surfaces`` for the surfaces and the values a card accepts.
    """
    return card_surfaces.card_context(context.flatten(), surface, values)


@register.simple_tag(takes_context=True)
def history_card_react(context, entry, card_class, history_mode):
    """Return the shared React card's props for one History entry."""
    from uuid import uuid4

    from django.utils.translation import gettext as _

    from app.media_card_model import history_card_model

    return {
        "id": f"media-card-{uuid4().hex[:8]}",
        "props": {
            "model": history_card_model(
                entry, card_class, history_mode, context.get("user"), context.get("request"),
            ),
            "labels": {
                "altTitle": str(_("Show alternative title")),
                "nextEpisode": str(_("Jump to next episode")),
            },
        },
    }


@register.simple_tag(takes_context=True)
def media_row_react(context, item, media, title, matched_title):
    """Return the shared React card's props for one Search list-view row."""
    from uuid import uuid4

    from django.utils.translation import gettext as _

    from app.media_card_model import row_card_model

    return {
        "id": f"media-card-{uuid4().hex[:8]}",
        "props": {
            "model": row_card_model(
                item, media, title, matched_title, context.get("user"), context.get("request"),
            ),
            "labels": {"altTitle": str(_("Show alternative title"))},
        },
    }



@register.simple_tag(takes_context=True)
def shell_topbar_react(context, public=False):
    """Return the props the shared shell top bar needs.

    Navigation values, translated labels and the public sign-in destination
    stay server-owned. The browser adapter supplies link semantics and theme
    tokens; the fallback top bar remains in the template.
    """
    from uuid import uuid4

    user = context.get("user")
    if public:
        return {
            "id": f"shell-{uuid4().hex[:8]}",
            "props": {
                "variant": "public",
                "showMenu": False,
                "showSearch": False,
                "themeToggle": False,
                "logo": _shell_logo(user),
                "logoText": "Floppy",
                "signIn": (
                    {
                        "label": str(_("Sign In")),
                        "href": reverse("account_login"),
                    }
                    if settings.REGISTRATION
                    else None
                ),
                "labels": _shell_labels(),
            },
        }

    request = context.get("request")
    if not request:
        return {"id": f"shell-{uuid4().hex[:8]}", "props": None}
    public_view = bool(context.get("public_view", False))
    return {
        "id": f"shell-{uuid4().hex[:8]}",
        "props": {
            "variant": "app",
            "showMenu": not public_view,
            "showSearch": not public_view,
            "themeToggle": bool(
                user
                and user.is_authenticated
                and is_basic_theme(user.theme),
            ),
            "logo": None,
            "logoText": None,
            "signIn": None,
            "labels": _shell_labels(),
        },
    }


def _shell_logo(user):
    mono = bool(
        user
        and user.is_authenticated
        and getattr(user, "logo_style", "") == "monochrome"
    )
    return {
        "src": static(
            "img/floppy-logo-white.png" if mono else "img/floppy-logo.png",
        ),
        "alt": "Floppy",
        "mono": mono,
    }


def _shell_labels():
    return {
        "menu": str(_("Toggle navigation menu")),
        "closeMenu": str(_("Close navigation menu")),
        "navigation": str(_("Navigation")),
        "toggleTheme": str(_("Toggle theme")),
        "logo": "Floppy",
    }


@register.simple_tag(takes_context=True)
def global_search_react(context, search_type):
    """Return the props the shared React header search form needs."""
    from uuid import uuid4

    from django.utils.translation import gettext as _

    request = context.get("request")
    return {
        "id": f"global-search-{uuid4().hex[:8]}",
        "props": {
            "action": reverse("search"),
            "query": request.GET.get("q", "") if request else "",
            "selectedType": {
                "display": str(media_type_readable_plural(search_type)),
                "value": str(search_type),
            },
            "mediaTypes": [
                {"display": str(item["display"]), "value": str(item["value"])}
                for item in get_search_media_types(context.get("user"))
            ],
            "barcodeTypes": ["all", "book", "manga", "comic", "comicissue"],
            "suggestionsUrl": reverse("search_suggestions"),
            "typeUrl": reverse("api_search_type"),
            "labels": {
                "inputLabel": str(_("Search your library or online")),
                "search": str(_("Search")),
                "scan": str(_("Scan barcode")),
                "dismiss": str(_("Dismiss error")),
                "placeholderAll": str(_("Search your whole library...")),
                "placeholderShort": str(_("Search %(media)s...")),
                "placeholderLong": str(
                    _("Search %(media)s in your library or online..."),
                ),
                "barcodeNotLoaded": str(
                    _("Barcode scanner utilities not loaded. Please refresh the page."),
                ),
                "barcodeUnreadable": str(
                    _(
                        "Could not read barcode from image. "
                        "Please try again or enter ISBN manually.",
                    ),
                ),
                "barcodeFailed": str(_("Error scanning barcode: ")),
                "tryAgain": str(_("Please try again.")),
                "barcodeUnavailable": str(
                    _("Barcode scanning is not available on this device. Enter an ISBN instead."),
                ),
                "suggestions": str(_("Search suggestions")),
                "suggestionsLoading": str(_("Loading suggestions…")),
                "suggestionsError": str(
                    _("Suggestions could not load. Keep typing or press Enter."),
                ),
                "suggestionsSeeAll": str(
                    _("See all library results for “%(query)s”"),
                ),
                "suggestionsSearchOnline": str(
                    _("Search online for “%(query)s”"),
                ),
                "suggestionsEmptyAll": str(
                    _(
                        "Nothing in your library matches “%(query)s”. "
                        "Pick a media type to search online.",
                    ),
                ),
                "suggestionsEmptyType": str(
                    _(
                        "Nothing saved matches “%(query)s”. "
                        "Keep typing and press Enter to search online.",
                    ),
                ),
            },
        },
    }


@register.simple_tag(takes_context=True)
def search_results_react(context):
    """Return the props the shared React search results surface needs."""
    from uuid import uuid4

    from app.search_views import build_search_results_props

    return {
        "id": f"search-results-{uuid4().hex[:8]}",
        "props": build_search_results_props(context.flatten()),
    }


@register.simple_tag(takes_context=True)
def parent_picker_react(context, kind):
    """Return the props the shared React parent picker needs."""
    from django.utils.translation import gettext as _

    pickers = {
        "tv": {
            "url": reverse("search_parent_tv"),
            "placeholder": _("Search for a TV show..."),
            "empty": _(
                'No TV shows found matching "%(query)s". '
                "Please try a different search term.",
            ),
        },
        "season": {
            "url": reverse("search_parent_season"),
            "placeholder": _("Search for a season..."),
            "empty": _(
                'No seasons found matching "%(query)s". '
                "Please try a different search term.",
            ),
        },
    }
    picker = pickers[kind]
    return {
        "id": f"parent-picker-{uuid4().hex[:8]}",
        "props": {
            "kind": kind,
            "url": picker["url"],
            "hiddenInputId": f"parent-{kind}-id",
            "placeholder": str(picker["placeholder"]),
            "labels": {
                "empty": str(picker["empty"]),
                "loading": str(_("Loading matches…")),
                "errorTitle": str(_("Parent search could not load")),
                "errorBody": str(_("Please try again.")),
                "retry": str(_("Try again")),
                "selected": str(_("Selected")),
            },
        },
    }


@register.simple_tag(takes_context=True)
def sidebar_react(context, media_type):
    """Return the props the shared React sidebar needs."""
    from uuid import uuid4

    from app.sidebar_model import sidebar_model

    request = context.get("request")
    return {
        "id": f"sidebar-{uuid4().hex[:8]}",
        # A page rendered without a request keeps the server sidebar.
        "props": sidebar_model(request, context["user"], media_type) if request else None,
    }


@register.simple_tag
def filter_chips_react(options, label):
    """Return the props the shared React chip row needs for ``options``."""
    from uuid import uuid4

    return {
        "id": f"filter-chips-{uuid4().hex[:8]}",
        "props": {
            "label": str(label),
            "items": [
                {"value": str(option["value"]), "label": str(_(option["label"]))}
                for option in options
            ],
        },
    }


@register.simple_tag(takes_context=True)
def discover_row_react(context, row, selected_media_type, show_more, discover_debug):
    """Return the shared React row props for one Discover row."""
    from uuid import uuid4

    from app.discover_presentation import card_labels, row_props, surface_labels

    return {
        "id": f"discover-row-props-{uuid4().hex[:8]}",
        "props": {
            "row": row_props(
                context.flatten(),
                row,
                selected_media_type=selected_media_type,
                show_more=show_more,
                discover_debug=discover_debug,
            ),
            "labels": {**surface_labels(), **card_labels()},
        },
    }


@register.simple_tag(takes_context=True)
def discover_hidden_react(context):
    """Return the shared React props for the hidden Discover grid."""
    from uuid import uuid4

    from app.discover_presentation import card_labels, hidden_props, surface_labels

    props = hidden_props(context.flatten())
    return {
        "id": f"discover-hidden-props-{uuid4().hex[:8]}",
        "props": {**props, "labels": {**surface_labels(), **card_labels()}},
    }


@register.simple_tag(takes_context=True)
def discover_preview_react(context, row, selected_media_type):
    """Return the shared React rail props for an RSS row preview."""
    from uuid import uuid4

    from app.discover_presentation import card_labels, row_props, surface_labels

    return {
        "id": f"discover-preview-props-{uuid4().hex[:8]}",
        "props": {
            "row": row_props(
                context.flatten(),
                row,
                selected_media_type=selected_media_type,
                show_more=False,
                discover_debug=False,
            ) if row else None,
            "labels": {**surface_labels(), **card_labels()},
        },
    }


@register.simple_tag
def discover_state_react(kind):
    """Return the shared React props for a Discover loading or empty state."""
    from uuid import uuid4

    from app.discover_presentation import surface_labels

    return {
        "id": f"discover-state-{uuid4().hex[:8]}",
        "props": {"kind": str(kind), "labels": surface_labels()},
    }


@register.simple_tag
def discover_feedback_react():
    """Return the shared React props for Discover refresh and action feedback."""
    from uuid import uuid4

    from app.discover_presentation import surface_labels

    return {
        "id": f"discover-feedback-{uuid4().hex[:8]}",
        "props": {"labels": surface_labels()},
    }


@register.simple_tag(takes_context=True)
def music_relations_react(context, relations):
    """Return the shared relation-card grid props for an artist page."""
    items = []
    for relation in relations or []:
        artist = relation.get("artist")
        if artist is None:
            continue
        tracker = relation.get("tracker")
        image = artist.image or ""
        if image == settings.IMG_NONE:
            image = ""
        items.append(
            {
                "id": artist.id,
                "name": artist.name,
                "image": image_cache.rewrite_image_url(image) if image else "",
                "href": music_artist_url(artist),
                "role": relation.get("role") or "",
                "current": bool(relation.get("is_current")),
                "status": tracker.status if tracker else None,
                "score": float(tracker.score)
                if tracker and tracker.score is not None
                else None,
            }
        )
    return {
        "id": f"music-relations-{uuid4().hex[:8]}",
        "props": {
            "relations": items,
            "labels": {
                "former": str(_("Former")),
                "rate": str(_("Rate")),
                "close": str(_("Close")),
            },
        },
    }


@register.simple_tag(takes_context=True)
def music_albums_react(context, albums):
    """Return the shared album-card grid props for an artist discography."""
    from users.card_metadata import card_lines

    user = context.get("user") or getattr(context.get("request"), "user", None)
    items = []
    for album in albums or []:
        image = album.image or ""
        if image == settings.IMG_NONE:
            image = ""
        items.append(
            {
                "id": album.id,
                "title": album.title,
                "image": image_cache.rewrite_image_url(image) if image else "",
                "href": music_album_url(album),
                "releaseType": album.release_type or "",
                "year": album.release_date.year if album.release_date else None,
                "plays": getattr(album, "play_count", 0) or 0,
                "score": float(album.score)
                if getattr(album, "score", None) is not None
                else None,
                "lines": [
                    {
                        "clamp": True,
                        "dormant": bool(line.get("dormant")),
                        "parts": [
                            {
                                "text": part["text"],
                                **({"score": True} if part.get("score") else {}),
                            }
                            for part in line["parts"]
                        ],
                    }
                    for line in card_lines(user, "music", album, None)
                ],
            }
        )
    return {
        "id": f"music-albums-{uuid4().hex[:8]}",
        "props": {
            "albums": items,
            "labels": {
                "rate": str(_("Rate")),
                "close": str(_("Close")),
                "track": str(_("track")),
                "tracks": str(_("tracks")),
                "play": str(_("play")),
                "plays": str(_("plays")),
            },
        },
    }


# Icons the detail action row may hold, in display order. Below 400px wide the
# row keeps this many cells (the More menu is one of them) so each stays tappable.
DETAIL_ROW_PHONE_CAP = 5


@register.simple_tag(takes_context=True)
def detail_action_row(context, media, media_type):
    """Say which icons the detail action row shows, and which fold into More on phones.

    Icons that cannot do anything for the item are left out: manual items have no
    provider to sync with, Seerr needs a configured server and a TMDB movie or
    show, and podcast shows keep their notes in their own tracker form.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    source = (
        media.get("source")
        if isinstance(media, dict)
        else getattr(media, "source", None)
    )
    show = {
        "link": bool(context.get("detail_link_sections")),
        "tags": True,
        "notes": not context.get("podcast_show"),
        "lists": True,
        "seerr": bool(
            user
            and user.seerr_url
            and user.seerr_api_key
            and user.seerr_user_id
            and source == Sources.TMDB.value
            and media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value),
        ),
        "history": True,
        "sync": source != Sources.MANUAL.value,
    }
    shown = [key for key, on in show.items() if on]
    # More takes the last icons, the ones used least, and is itself one cell.
    overflow = (
        shown[DETAIL_ROW_PHONE_CAP - 1 :] if len(shown) > DETAIL_ROW_PHONE_CAP else []
    )
    return {**show, "overflow": overflow}


@register.simple_tag(takes_context=True)
def media_track_actions(context, media_type, instance):
    """Return the detail-page split button actions, and when their phase should refresh.

    A running check-in changes its own button label (Pause becomes Finish, then
    the movie completes), so the controls refresh at those moments.
    """
    request = context.get("request")
    phase = checkin.phase(request.user, instance) if request and instance else None
    reload_in = phase["reload_in"] if phase else None
    checkin_state = None
    if phase and phase.get("phase") in (
        checkin.PHASE_ACTIVE,
        checkin.PHASE_FINISHABLE,
        checkin.PHASE_PAUSED,
        "session",
    ):
        checkin_state = {
            "phase": phase["phase"],
            "code": phase.get("code", ""),
            "started_at_ts": phase.get("started_at_ts"),
        }
    # Grouped anime (TMDB/TVDB) is stored as a TV show, which holds one tracker,
    # so the stored type decides the actions, not the route type.
    item = getattr(instance, "item", None)
    stored_type = getattr(item, "media_type", None) or media_type
    return {
        "actions": track_actions.track_actions(
            stored_type,
            instance.status if instance else None,
            phase,
        ),
        "reload_ms": max(1, reload_in + checkin.SETTLE_DELAY_SECONDS) * 1000
        if reload_in is not None
        else None,
        "checkin_state": checkin_state,
    }


@register.simple_tag(takes_context=True)
def playback_card_react(context, card):
    """Return the shared Now Playing card props for one server-built card."""
    control = card.get("checkin_control")
    model = {
        "title": card.get("title") or "",
        "episodeCode": card.get("episode_code") or "",
        "episodeTitle": card.get("episode_title") or "",
        "subtitleLine": card.get("subtitle") or "",
        "href": card.get("details_url") or "",
        "image": {
            "src": image_url(card.get("image")) or "",
            "alt": card.get("title") or "",
        },
        "status": card.get("status") or live_playback.PLAYBACK_STATUS_PLAYING,
        "offsetSeconds": max(0, int(card.get("offset_seconds") or 0)),
        "durationSeconds": max(0, int(card.get("duration_seconds") or 0)),
        "updatedAtSeconds": max(0, int(card.get("updated_at_ts") or 0)),
        "progressPercent": card.get("progress_percent") or 0,
        "staleAfterSeconds": (
            live_playback.PLAYBACK_PAUSE_STALE_SECONDS
            if card.get("status") in (
                live_playback.PLAYBACK_STATUS_PAUSED,
                live_playback.PLAYBACK_STATUS_STOPPED,
            )
            else live_playback.PLAYBACK_HARD_STALE_SECONDS
        ),
        "checkin": bool(control),
    }
    props = {
        "model": model,
        "control": None,
        "labels": {
            "nowPlaying": str(_("Now Playing")),
            "playing": str(_("Playing")),
            "paused": str(_("Paused")),
            "stale": str(_("Last updated")),
            "openDetails": str(_("Open details")),
        },
    }
    if control:
        props["labels"]["pauseCheckIn"] = str(_("Pause check-in"))
        props["labels"]["stopCheckIn"] = str(_("Stop check-in"))
        props["labels"]["cancelCheckIn"] = str(_("Cancel check-in"))
        url = media_view_url("checkin_action", {
            "source": control.get("source"),
            "media_type": control.get("media_type"),
            "media_id": control.get("media_id"),
        })
        props["control"] = {
            "token": control.get("token"),
            "pause": {
                "url": url,
                "action": "pause",
                "token": control.get("token"),
            } if control.get("action") == "pause" else None,
            "stop": {
                "url": url,
                "action": "finish" if control.get("action") == "pause" else "stop",
                "token": control.get("token"),
            },
            "cancel": {
                "url": url,
                "action": "cancel",
                "token": control.get("token"),
            } if control.get("action") == "stop" else None,
        }
    return {
        "id": f"playback-card-{uuid4().hex}",
        "props": props,
    }


@register.simple_tag
def checkin_state_react(media, media_type, track):
    """Return the shared detail check-in state props while a check-in is running."""
    state = (track or {}).get("checkin_state")
    if not state:
        return {"id": "", "props": None}
    return {
        "id": f"checkin-state-{uuid4().hex}",
        "props": {
            "model": {
                "phase": state.get("phase"),
                "code": state.get("code") or "",
                "startedAtSeconds": state.get("started_at_ts"),
                "title": str(
                    getattr(media, "title", None)
                    or (media.get("title") if isinstance(media, dict) else "")
                    or "",
                ),
            },
            "actions": [
                {"key": action["key"], "label": action["label"]}
                for action in (track or {}).get("actions", [])
            ],
            "labels": {
                "checkIn": str(_("Check-in")),
                "active": str(_("Check-in running")),
                "finishable": str(_("Ready to finish")),
                "pausedState": str(_("Check-in paused")),
                "session": str(_("Check-in running")),
                "elapsed": str(_("elapsed")),
            },
        },
    }


@register.filter
def month_abbreviation(value):
    """Localize cached English heatmap month labels at render time."""
    try:
        number = list(calendar.month_abbr).index(str(value))
    except ValueError:
        return value
    return formats.date_format(date(2000, number, 1), "M") if number else ""


@register.simple_tag
def collection_field_input_props(groups, values, form_id=None):
    """Return plain descriptors for the shared custom-field value inputs."""
    return {
        "groups": [
            {
                "id": group.id,
                "name": group.name,
                "fields": [
                    {
                        "id": field.id,
                        "label": field.label,
                        "field_type": field.field_type,
                        "options": field.options,
                        "media_types": field.media_types,
                    }
                    for field in getattr(group, "visible_fields", group.fields.all())
                ],
            }
            for group in groups or []
        ],
        "values": values or {},
        "formId": form_id or "",
    }


@register.simple_tag
def collection_copy_form_props(form, collection_fields=None):
    """Return plain descriptors for the shared collection copy editor."""
    names = list(collection_fields or [name for name in form.fields if name != "item"])
    fields = []
    values = {}
    errors = {}
    for name in names:
        field = form.fields.get(name)
        if field is None:
            continue
        widget_type = getattr(field.widget, "input_type", "text")
        if widget_type == "checkbox":
            field_type = "checkbox"
        elif widget_type == "select":
            field_type = "select"
        elif widget_type == "number":
            field_type = "number"
        elif widget_type == "datetime-local":
            field_type = "datetime"
        else:
            field_type = "text"
        options = [
            {"value": str(value), "label": str(label)}
            for value, label in getattr(field.widget, "choices", [])
        ]
        value = form[name].value()
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if field_type == "datetime" and value:
            value = str(value).replace(" ", "T")[:16]
        values[name] = "" if value is None else value
        if form.is_bound and form.errors.get(name):
            errors[name] = " ".join(str(message) for message in form.errors.get(name))
        fields.append({
            "name": name,
            "label": str(field.label),
            "type": field_type,
            "options": options,
            "required": bool(field.required),
            "help": str(field.help_text or ""),
            "placeholder": str(field.widget.attrs.get("placeholder") or ""),
        })
    return {"fields": fields, "values": values, "errors": errors}
