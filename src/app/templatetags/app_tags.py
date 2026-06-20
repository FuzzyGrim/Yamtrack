from datetime import timedelta
from pathlib import Path

from django import template
from django.conf import settings
from django.db.models import Avg
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.html import format_html
from unidecode import unidecode

from app import config, helpers
from app.models import MediaTypes, Sources, Status

register = template.Library()


@register.simple_tag(takes_context=True)
def absolute_app_url(context, path):
    """Build an absolute app URL using configured public origin when available."""
    request = context.get("request")
    return helpers.build_absolute_app_url(request, path)


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
def date_tracker_format(date):
    """Format a datetime object to a readable string."""
    return datetime_format(date)


@register.filter
def datetime_format(date, user=None):
    """Format a datetime object using app or user date/time preferences."""
    if not date:
        return None

    local_dt = timezone.localtime(date)

    if user and getattr(user, "date_format", None):
        date_format = user.date_format
        if settings.TRACK_TIME and getattr(user, "time_format", None):
            date_format = f"{date_format} {user.time_format}"
    else:
        date_format = "DATETIME_FORMAT" if settings.TRACK_TIME else "DATE_FORMAT"

    return formats.date_format(
        local_dt,
        date_format,
    )


@register.filter
def time_format(date, user=None):
    """Format a datetime object using app or user time preferences."""
    if not date:
        return None

    local_dt = timezone.localtime(date)
    return formats.date_format(
        local_dt,
        getattr(user, "time_format", None) or "TIME_FORMAT",
    )


@register.simple_tag
def now_plus_minutes(minutes):
    """Return a datetime-local compatible value offset by minutes from now."""
    local_dt = timezone.localtime(timezone.now() + timedelta(minutes=minutes or 0))
    if settings.TRACK_TIME:
        return local_dt.strftime("%Y-%m-%dT%H:%M")
    return local_dt.strftime("%Y-%m-%d")


@register.filter
def is_list(arg1):
    """Return True if the object is a list."""
    return isinstance(arg1, list)


@register.filter
def credits_by_type(credits, media_type):
    """Filter credits (list of dicts) by media_type. For use on person filmography."""
    if not credits:
        return []
    return [c for c in credits if (c.get("media_type") if isinstance(c, dict) else getattr(c, "media_type", None)) == media_type]


@register.filter
def source_readable(source):
    """Return the readable source name."""
    return Sources(source).label


@register.filter
def media_type_readable(media_type):
    """Return the readable media type."""
    return MediaTypes(media_type).label


@register.filter
def media_type_readable_plural(media_type):
    """Return the readable media type in plural form."""
    # Handle empty or invalid media types
    if not media_type:
        return ""
    
    try:
        singular = MediaTypes(media_type).label
    except (ValueError, KeyError):
        return media_type  # Return as-is if invalid
    
    # Special cases that don't change in plural form
    if singular.lower() in [MediaTypes.ANIME.value, MediaTypes.MANGA.value]:
        return singular

    return f"{singular}s"


@register.filter
def media_status_readable(media_status):
    """Return the readable media status."""
    return Status(media_status).label


@register.filter
def default_source(media_type):
    """Return the default source for the media type."""
    return config.get_default_source_name(media_type).label


@register.filter
def media_past_verb(media_type):
    """Return the past tense verb for the given media type."""
    return config.get_verb(media_type, past_tense=True)


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
    return config.get_unit(media_type, short=False)


@register.filter
def sources(media_type):
    """Template filter to get source options for a media type."""
    return config.get_sources(media_type)


@register.simple_tag
def get_search_media_types(user):
    """Return available media types for search based on user preferences."""
    # Handle anonymous users
    if not user.is_authenticated or not hasattr(user, 'get_enabled_media_types'):
        enabled_types = MediaTypes.values
    else:
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
    # Handle anonymous users
    if not user.is_authenticated or not hasattr(user, 'get_enabled_media_types'):
        return []
    
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
def status_color(status):
    """Return the color associated with the status."""
    return config.get_status_text_color(status)


@register.filter
def natural_day(value, user=None):
    """Format date with natural language (Today, Tomorrow, etc.)."""
    local_value = timezone.localtime(value)

    # Get today's date in the current timezone
    today = timezone.localdate()

    # Extract just the date part for comparison
    value_date = local_value.date()

    # Calculate the difference in days
    diff = value_date - today
    days = diff.days

    threshold = 5
    if days == 0:
        label = "Today"
    elif days == 1:
        label = "Tomorrow"
    elif days > 1 and days <= threshold:
        label = f"In {days} days"
    elif user and getattr(user, "date_format", None):
        label = formats.date_format(local_value, user.date_format)
    else:
        # For dates further away
        label = local_value.strftime("%b %d")

    if user and getattr(user, "time_format", None):
        return f"{label} {formats.date_format(local_value, user.time_format)}"

    return label


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

    if view_name not in {"history_modal", "track_modal"}:
        # Handle season/episode numbers if they exist and the target route accepts them.
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
        "user": (
            """<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"></path>
               <circle cx="12" cy="7" r="4"></circle>"""
        ),
        "diary": (
            """<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
               <polyline points="14 2 14 8 20 8"></polyline>
               <line x1="16" x2="8" y1="13" y2="13"></line>
               <line x1="16" x2="8" y1="17" y2="17"></line>
               <polyline points="10 9 9 9 8 9"></polyline>"""
        ),
    }

    if name in MediaTypes.values:
        content = config.get_svg_icon(name)
    else:
        content = other_icons[name]

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


# Define which details to exclude for each media type
DETAILS_EXCLUSIONS = {
    'default': [],  # Applied to all types unless overridden
    'movie': ['director', 'runtime', 'release_date'],  # Since director is shown at the top
    'tv': ['creator', 'runtime', 'format', 'seasons', 'episodes'],      # Since creator is shown at the top
    'book': ['author'],     # If author is shown elsewhere
    'manga': ['author'],
    'anime': ['studios'],   # If studios are shown elsewhere
    'game': ['companies'],
}

@register.filter
def filter_details(details, media_type=None):
    """
    Filter out specific keys from media details.
    
    Args:
        details: The details dictionary to filter
        media_type: String indicating the media type (e.g. 'movie', 'tv', etc.)
    """
    if not details or not isinstance(details, dict):
        return {}
        
    # Get exclusions for this media type
    exclusions = set(DETAILS_EXCLUSIONS['default'])
    if media_type and media_type in DETAILS_EXCLUSIONS:
        exclusions.update(DETAILS_EXCLUSIONS[media_type])
    
    # Filter out excluded keys
    return {k: v for k, v in details.items() if k not in exclusions}


@register.filter
def media_type_label(media_type):
    """Convert media type to a human-readable label."""
    labels = {
        'tv': 'TV Show',
        'movie': 'Movie',
        'anime': 'Anime',
        'manga': 'Manga',
        'game': 'Game',
        'book': 'Book',
        'comic': 'Comic'
    }
    return labels.get(media_type, media_type.title())


@register.filter
def get_item(dictionary, key):
    """Return the value for a key in a dictionary."""
    return dictionary.get(key)


@register.filter
def split_string(value, delimiter):
    """Split a string by delimiter and return a list."""
    if not value:
        return []
    return value.split(delimiter)


@register.simple_tag
def get_user_poster_image(item, user):
    """Get the user's preferred poster image for an item.
    
    Args:
        item: Item model instance or dictionary with item data
        user: User model instance
        
    Returns:
        URL of the poster image to display
    """
    if not user.is_authenticated:
        return item.image if hasattr(item, 'image') else item.get('image')
        
    # Handle both model instances and dictionaries
    source = item.source if hasattr(item, 'source') else item.get('source')
    media_type = item.media_type if hasattr(item, 'media_type') else item.get('media_type')
    media_id = item.media_id if hasattr(item, 'media_id') else item.get('media_id')
    default_image = item.image if hasattr(item, 'image') else item.get('image')
    
    if not all([source, media_type, media_id]):
        return default_image
    
    from app.models import CustomPosterPreference, Item
    try:
        # First try to get the Item instance
        filters = {
            'source': source,
            'media_type': media_type,
            'media_id': media_id,
        }
        if media_type == 'season':
            season_number = item.season_number if hasattr(item, 'season_number') else item.get('season_number')
            filters['season_number'] = season_number

        db_item = Item.objects.filter(**filters).first()
        if db_item is None:
            return default_image
        # Then get the custom preference
        pref = CustomPosterPreference.objects.get(user=user, item=db_item)
        return pref.custom_image_url
    except (CustomPosterPreference.DoesNotExist, AttributeError):
        return default_image


@register.simple_tag
def can_customize_poster(item):
    """Check if poster customization is available for this item.
    
    Args:
        item: Item model instance or dictionary with item data
        
    Returns:
        Boolean indicating if poster can be customized
    """
    from app.models import Sources, MediaTypes
    
    # Handle both model instances and dictionaries
    source = item.source if hasattr(item, 'source') else item.get('source')
    media_type = item.media_type if hasattr(item, 'media_type') else item.get('media_type')

    # TMDB movies, TV shows, and seasons can have custom posters
    # IGDB games only have one cover per game, so no customization available
    if source == Sources.TMDB.value:
        return media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value]
    # Books can have custom covers (OpenLibrary provides multiple covers)
    elif source == Sources.OPENLIBRARY.value:
        return media_type == MediaTypes.BOOK.value
    return False


@register.filter
def compact_number(value):
    """Return a shortened representation for large integers (e.g. 3.2k)."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return value

    absolute = abs(number)

    if absolute >= 1_000_000_000:
        short = f"{number / 1_000_000_000:.1f}b"
    elif absolute >= 1_000_000:
        short = f"{number / 1_000_000:.1f}m"
    elif absolute >= 1_000:
        short = f"{number / 1_000:.1f}k"
    else:
        return str(number)

    if short.endswith(".0b") or short.endswith(".0m") or short.endswith(".0k"):
        short = short.replace(".0", "")

    return short


@register.filter
def money_compact(value):
    """Format large currency values into compact strings (e.g. $300M)."""

    try:
        amount = int(value)
    except (TypeError, ValueError):
        return "-"

    if amount <= 0:
        return "-"

    absolute = abs(amount)

    if absolute >= 1_000_000_000:
        formatted = f"${amount / 1_000_000_000:.1f}B"
    elif absolute >= 1_000_000:
        formatted = f"${amount / 1_000_000:.1f}M"
    elif absolute >= 1_000:
        formatted = f"${amount / 1_000:.1f}K"
    else:
        formatted = f"${amount}"

    if formatted.endswith(".0B") or formatted.endswith(".0M") or formatted.endswith(".0K"):
        formatted = formatted.replace(".0", "")

    return formatted


@register.filter
def personal_average_rating(diary_entries):
    """Calculate the average rating from a user's diary entries."""
    if not diary_entries:
        return None
    
    # Filter entries that have ratings
    rated_entries = [entry for entry in diary_entries if entry.rating is not None]
    
    if not rated_entries:
        return None
    
    # Calculate average rating
    total_rating = sum(float(entry.rating) for entry in rated_entries)
    avg_rating = total_rating / len(rated_entries)
    
    return avg_rating


@register.simple_tag
def has_diary_entries(item, user):
    """Check if an item has diary entries for a user."""
    from app.models import DiaryEntry, Item
    
    # Handle case where item is a dict (from search results)
    if isinstance(item, dict):
        try:
            lookup_params = {
                "media_id": str(item["media_id"]),
                "source": item["source"],
                "media_type": item["media_type"],
            }
            # Only include season_number if it's present and not None
            if "season_number" in item and item.get("season_number") is not None:
                lookup_params["season_number"] = item["season_number"]
            
            item_instance = Item.objects.get(**lookup_params)
        except Item.DoesNotExist:
            return False
    else:
        # item is already an Item instance
        item_instance = item
    
    return DiaryEntry.objects.filter(item=item_instance, user=user).exists()


@register.filter
def to_five_scale(value):
    """Convert a 10-point score into a 5-point scale."""

    try:
        score = float(value)
    except (TypeError, ValueError):
        return value

    return score / 2


@register.filter
def show_media_score(rating, user):
    """Return whether a media rating should be shown for the user's preferences."""
    return rating is not None and (not user.hide_zero_rating or rating > 0)


@register.filter
def seconds_to_duration(seconds):
    """Convert seconds to a compact human-readable duration."""
    if not seconds:
        return None
    total_minutes = seconds // 60
    if total_minutes < 30:
        return f"{max(5, round(total_minutes / 5) * 5)}m"
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return "30m" if minutes < 45 else "1h"
    if minutes >= 45:
        return f"{hours + 1}h"
    return f"{hours}h" if minutes < 15 else f"{hours}h 30m"
