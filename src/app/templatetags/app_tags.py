from pathlib import Path

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.dateparse import parse_date
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy, pgettext_lazy
from unidecode import unidecode

from app import config, helpers
from app.models import MediaTypes, Sources, Status
from users.models import WATCH_PROVIDER_REGION_UNSET

register = template.Library()

MEDIA_TYPE_PLURALS = {
    MediaTypes.TV.value: gettext_lazy("TV Shows"),
    MediaTypes.SEASON.value: gettext_lazy("TV Seasons"),
    MediaTypes.EPISODE.value: gettext_lazy("Episodes"),
    MediaTypes.MOVIE.value: gettext_lazy("Movies"),
    MediaTypes.ANIME.value: gettext_lazy("Anime"),
    MediaTypes.MANGA.value: gettext_lazy("Manga"),
    MediaTypes.GAME.value: gettext_lazy("Games"),
    MediaTypes.BOOK.value: gettext_lazy("Books"),
    MediaTypes.COMIC.value: gettext_lazy("Comics"),
    MediaTypes.BOARDGAME.value: gettext_lazy("Boardgames"),
}

PAST_VERBS = {
    "watched": pgettext_lazy("past participle", "watched"),
    "read": pgettext_lazy("past participle", "read"),
    "played": pgettext_lazy("past participle", "played"),
}

LONG_UNITS = {
    "Episode": gettext_lazy("Episode"),
    "Chapter": gettext_lazy("Chapter"),
    "Page": gettext_lazy("Page"),
    "Issue": gettext_lazy("Issue"),
    "Play": gettext_lazy("Play"),
}

# Keys of the metadata details and related sections, made translatable
DETAIL_LABELS = {
    "format": gettext_lazy("format"),
    "release_date": gettext_lazy("release date"),
    "first_air_date": gettext_lazy("first air date"),
    "last_air_date": gettext_lazy("last air date"),
    "status": gettext_lazy("status"),
    "runtime": gettext_lazy("runtime"),
    "total_runtime": gettext_lazy("total runtime"),
    "studios": gettext_lazy("studios"),
    "country": gettext_lazy("country"),
    "languages": gettext_lazy("languages"),
    "seasons": gettext_lazy("seasons"),
    "episodes": gettext_lazy("episodes"),
    "recommendations": gettext_lazy("recommendations"),
    "genres": gettext_lazy("genres"),
    "source": gettext_lazy("source"),
    "start_date": gettext_lazy("start date"),
    "end_date": gettext_lazy("end date"),
    "authors": gettext_lazy("authors"),
    "author": gettext_lazy("author"),
    "publishers": gettext_lazy("publishers"),
    "publisher": gettext_lazy("publisher"),
    "publish_date": gettext_lazy("publish date"),
    "number_of_pages": gettext_lazy("number of pages"),
    "pages": gettext_lazy("pages"),
    "chapters": gettext_lazy("chapters"),
    "volumes": gettext_lazy("volumes"),
    "platforms": gettext_lazy("platforms"),
    "developers": gettext_lazy("developers"),
    "themes": gettext_lazy("themes"),
    "year": gettext_lazy("year"),
    "players": gettext_lazy("players"),
    "playtime": gettext_lazy("playtime"),
    "issues": gettext_lazy("issues"),
    "upcoming": gettext_lazy("upcoming"),
    "recent": gettext_lazy("recent"),
    "completion": gettext_lazy("completion"),
    "episodes_left": gettext_lazy("episodes left"),
    "title": gettext_lazy("title"),
    "dlcs": gettext_lazy("DLCs"),
    "related": gettext_lazy("related"),
    "parent": gettext_lazy("parent"),
    "sequels": gettext_lazy("sequels"),
    "prequels": gettext_lazy("prequels"),
    "side_stories": gettext_lazy("side stories"),
    "spin_offs": gettext_lazy("spin offs"),
    "adaptations": gettext_lazy("adaptations"),
    "alternative_versions": gettext_lazy("alternative versions"),
    "expansions": gettext_lazy("expansions"),
    "remakes": gettext_lazy("remakes"),
    "remasters": gettext_lazy("remasters"),
    "similar_games": gettext_lazy("similar games"),
    "collections": gettext_lazy("collections"),
    "series": gettext_lazy("series"),
}

# Common values returned by the metadata providers, made translatable
DETAIL_VALUES = {
    "Movie": gettext_lazy("Movie"),
    "TV": gettext_lazy("TV"),
    "Released": gettext_lazy("Released"),
    "Ended": gettext_lazy("Ended"),
    "Returning Series": gettext_lazy("Returning Series"),
    "Canceled": gettext_lazy("Canceled"),
    "In Production": gettext_lazy("In Production"),
    "Planned": gettext_lazy("Planned"),
    "Post Production": gettext_lazy("Post Production"),
    "Rumored": gettext_lazy("Rumored"),
    "Pilot": gettext_lazy("Pilot"),
    "Unknown": gettext_lazy("Unknown"),
    "Finished Airing": gettext_lazy("Finished Airing"),
    "Currently Airing": gettext_lazy("Currently Airing"),
    "Not yet aired": gettext_lazy("Not yet aired"),
    "Finished": gettext_lazy("Finished"),
    "Publishing": gettext_lazy("Publishing"),
    "On Hiatus": gettext_lazy("On Hiatus"),
    "Discontinued": gettext_lazy("Discontinued"),
}


@register.filter
def detail_label(key):
    """Return the translated, readable label of a metadata key."""
    label = DETAIL_LABELS.get(key)
    if label is not None:
        return str(label)
    return str(key).replace("_", " ")


@register.filter
def translate_value(value):
    """Translate common metadata values, leaving anything else untouched."""
    if isinstance(value, str) and value in DETAIL_VALUES:
        return str(DETAIL_VALUES[value])
    return value


@register.simple_tag
def get_static_file_mtime(file_path):
    """Return the last modification time of a static file for cache busting."""
    full_path = Path(settings.STATIC_ROOT) / file_path
    try:
        mtime = int(full_path.stat().st_mtime)
    except OSError:
        # If file doesn't exist or can't be accessed
        return ""
    else:
        return f"?{mtime}"


@register.simple_tag(takes_context=True)
def absolute_app_url(context, path):
    """Return an absolute app URL for links copied into external services."""
    return helpers.build_absolute_app_url(context.get("request"), path)


@register.filter
def no_underscore(arg1):
    """Return the title case of the string."""
    return arg1.replace("_", " ")


@register.filter
def slug(arg1):
    """Return the slug of the string.

    Sometimes slugify removes all characters from a string, so we need to
    urlencode the special characters first.
    e.g Anime: 31687
    """
    cleaned = template.defaultfilters.slugify(arg1)
    if cleaned == "":
        cleaned = template.defaultfilters.slugify(
            template.defaultfilters.urlencode(unidecode(arg1)),
        )
        if cleaned == "":
            cleaned = template.defaultfilters.urlencode(unidecode(arg1))

            if cleaned == "":
                cleaned = template.defaultfilters.urlencode(arg1)

    return cleaned


@register.filter
def date_format(datetime, user):
    """Format a datetime using user's preferred date format (date only, no time).

    Args:
        datetime: The datetime object to format
        user: User object to get preferred date format
    """
    if not datetime:
        return None
    local_dt = timezone.localtime(datetime)
    return formats.date_format(local_dt, user.date_format)


@register.filter
def iso_date_format(value, user):
    """Format an ISO date string (YYYY-MM-DD) using user's preferred date format.

    If value is not a valid ISO date string, returns the original value.
    """
    if isinstance(value, str):
        date_obj = parse_date(value)
        if date_obj:
            return formats.date_format(date_obj, user.date_format)

    return value


@register.filter
def time_format(datetime, user):
    """Format a datetime using user's preferred time format (time only, no date)."""
    if not datetime:
        return None
    local_dt = timezone.localtime(datetime)
    return formats.time_format(local_dt, user.time_format)


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
    local_dt = timezone.localtime(datetime)
    formatted_date = formats.date_format(local_dt, user.date_format)

    if settings.TRACK_TIME:
        formatted_time = formats.time_format(local_dt, user.time_format)
        return f"{formatted_date} {formatted_time}"
    return formatted_date


@register.filter
def is_list(arg1):
    """Return True if the object is a list."""
    return isinstance(arg1, list)


@register.filter
def source_readable(source):
    """Return the readable source name."""
    return Sources(source).label


@register.filter
def media_type_readable(media_type):
    """Return the readable media type."""
    return str(MediaTypes(media_type).label)


@register.filter
def media_type_readable_plural(media_type):
    """Return the readable media type in plural form."""
    return str(MEDIA_TYPE_PLURALS[MediaTypes(media_type).value])


@register.filter
def media_status_readable(media_status):
    """Return the readable media status."""
    return str(Status(media_status).label)


@register.filter
def default_source(media_type):
    """Return the default source for the media type."""
    return config.get_default_source_name(media_type).label


@register.filter
def media_past_verb(media_type):
    """Return the past tense verb for the given media type."""
    verb = config.get_verb(media_type, past_tense=True)
    return str(PAST_VERBS.get(verb, verb))


@register.filter
def sample_search(media_type):
    """Return a sample search URL for the given media type using GET parameters."""
    return config.get_sample_search_url(media_type)


@register.filter
def short_unit(media_type):
    """Return the short unit for the media type."""
    return config.get_unit(media_type, short=True)


@register.filter
def long_unit(media_type):
    """Return the long unit for the media type."""
    unit = config.get_unit(media_type, short=False)
    return str(LONG_UNITS.get(unit, unit)) if unit else unit


@register.filter
def sources(media_type):
    """Template filter to get source options for a media type."""
    return config.get_sources(media_type)


@register.simple_tag
def get_search_media_types(user):
    """Return available media types for search based on user preferences."""
    enabled_types = user.get_enabled_media_types()

    # Filter and format the types for search
    return [
        {
            "display": media_type_readable_plural(media_type),
            "value": media_type,
        }
        for media_type in enabled_types
        if media_type != MediaTypes.SEASON.value
    ]


@register.simple_tag
def get_sidebar_media_types(user):
    """Return available media types for sidebar navigation based on user preferences."""
    enabled_types = user.get_enabled_media_types()

    # Format the types for sidebar
    return [
        {
            "media_type": media_type,
            "display_name": media_type_readable_plural(media_type),
        }
        for media_type in enabled_types
    ]


@register.filter
def media_color(media_type):
    """Return the color associated with the media type."""
    return config.get_text_color(media_type)


@register.filter
def journal_accent(accent):
    """Return the badge background class and icon template for a journal accent."""
    return config.get_journal_accent(accent)


@register.filter
def status_config(status):
    """Return the config dict for a status, or None if it is unrecognized."""
    return config.get_status_config(status)


@register.filter
def status_color(status):
    """Return the color associated with the status."""
    return config.get_status_text_color(status)


@register.filter
def status_icon(status):
    """Return the icon template associated with the status."""
    return config.get_status_icon(status)


@register.filter
def status_background_color(status):
    """Return the background color associated with the status."""
    return config.get_status_background_color(status)


@register.filter
def status_button_color(status):
    """Return the button background and hover classes for the status."""
    return config.get_status_button_color(status)


@register.filter
def natural_day(datetime, user):
    """Format date with natural language (Today, Tomorrow, etc.)."""
    if not datetime:
        return None

    today = timezone.localdate()

    local_dt = timezone.localtime(datetime)
    datetime_date = local_dt.date()
    formatted_date = formats.date_format(local_dt, user.date_format)
    formatted_time = formats.time_format(local_dt, user.time_format)
    days = (datetime_date - today).days

    if days == 0:
        return gettext("Today %(time)s") % {"time": formatted_time}
    if days == 1:
        return gettext("Tomorrow %(time)s") % {"time": formatted_time}

    return f"{formatted_date} {formatted_time}"


@register.filter
def media_url(media):
    """Return the media URL for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    # Get attributes using either dict access or object attribute
    media_type = media["media_type"] if is_dict else media.media_type
    source = media["source"] if is_dict else media.source
    media_id = media["media_id"] if is_dict else media.media_id
    title = media["title"] if is_dict else media.title

    if media_type in [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]:
        season_number = media["season_number"] if is_dict else media.season_number
        return reverse(
            "season_details",
            kwargs={
                "source": source,
                "media_id": media_id,
                "title": slug(title),
                "season_number": season_number,
            },
        )

    return reverse(
        "media_details",
        kwargs={
            "source": source,
            "media_type": media_type,
            "media_id": media_id,
            "title": slug(title),
        },
    )


@register.simple_tag
def media_view_url(view_name, media):
    """Return the modal URL for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    # Build kwargs using either dict access or object attribute
    kwargs = {
        "source": media["source"] if is_dict else media.source,
        "media_type": media["media_type"] if is_dict else media.media_type,
        "media_id": media["media_id"] if is_dict else media.media_id,
    }

    # Handle season/episode numbers if they exist
    if is_dict:
        if "season_number" in media:
            kwargs["season_number"] = media["season_number"]
        if "episode_number" in media:
            kwargs["episode_number"] = media["episode_number"]
    else:
        if media.season_number is not None:
            kwargs["season_number"] = media.season_number
        if media.episode_number is not None:
            kwargs["episode_number"] = media.episode_number

    return reverse(view_name, kwargs=kwargs)


@register.simple_tag
def component_id(component_type, media, instance_id=None):
    """Return the component ID for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    # Get base attributes using either dict access or object attribute
    media_type = media["media_type"] if is_dict else media.media_type
    media_id = media["media_id"] if is_dict else media.media_id

    component_id = f"{component_type}-{media_type}-{media_id}"

    # Handle season/episode numbers if they exist
    if is_dict:
        if "season_number" in media:
            component_id += f"-{media['season_number']}"
        if "episode_number" in media:
            component_id += f"-{media['episode_number']}"
    else:
        if media.season_number is not None:
            component_id += f"-{media.season_number}"
        if media.episode_number is not None:
            component_id += f"-{media.episode_number}"

    # Add instance id if provided
    if instance_id:
        component_id += f"-{instance_id}"

    return component_id


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

    content = config.get_svg_icon(name)
    active_class = "text-indigo-400 " if is_active else ""

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


@register.filter
def show_media_score(rating, user):
    """
    Return if we should show the rating of a media.

    Args:
        rating: the rating value of the media
        user: the user to check preferences for

    Returns:
        True if we should show the media score
    """
    return rating is not None and (not user.hide_zero_rating or rating > 0)


@register.simple_tag
def media_section_count(
    media,
    user_medias,
    watch_providers=None,
    watch_provider_region=None,
):
    """Return the number of content sections on the media details page."""
    count = 0
    if media.get("cast"):
        count += 1
    related = media.get("related") or {}
    count += sum(1 for related_items in related.values() if related_items)
    if len(user_medias) > 1:
        count += 1
    if media.get("episodes"):
        count += 1
    has_streaming = (
        watch_provider_region == WATCH_PROVIDER_REGION_UNSET or watch_providers
    )
    if (
        media.get("media_type") in (MediaTypes.MOVIE.value, MediaTypes.TV.value)
        and has_streaming
    ):
        count += 1
    if media.get("time_to_beat"):
        count += 1
    return count


@register.filter
def seconds_to_duration(seconds):
    """Convert seconds to human-readable duration.

    Under 30 min: rounds to nearest 5 min. 30 min and above: rounds to nearest 30 min.
    """
    if not seconds:
        return None
    total_minutes = seconds // 60
    if total_minutes < 30:  # noqa: PLR2004
        return f"{max(5, round(total_minutes / 5) * 5)}m"
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return "30m" if minutes < 45 else "1h"  # noqa: PLR2004
    if minutes >= 45:  # noqa: PLR2004
        return f"{hours + 1}h"
    return f"{hours}h" if minutes < 15 else f"{hours}h 30m"  # noqa: PLR2004
