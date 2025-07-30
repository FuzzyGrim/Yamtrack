from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()


@register.filter
def get_attr(obj, attr):
    """Get attribute from object dynamically."""
    return getattr(obj, attr, None)


@register.simple_tag
def source_display(source_name):
    """
    Generate HTML display for a media source with logo and name.

    Args:
        source_name: The source identifier (e.g., 'kitsu', 'trakt')

    Returns:
        HTML markup for the source with logo and name
    """
    sources = {
        "kitsu": {
            "name": "Kitsu",
            "logo": static("img/kitsu-logo.png"),
        },
        "trakt": {
            "name": "Trakt",
            "logo": static("img/trakt-logo.svg"),
        },
        "myanimelist": {
            "name": "MyAnimeList",
            "logo": static("img/mal-logo.ico"),
        },
        "anilist": {
            "name": "AniList",
            "logo": static("img/anilist-logo.svg"),
        },
        "simkl": {
            "name": "SIMKL",
            "logo": static("img/simkl-logo.png"),
        },
        "yamtrack": {
            "name": "YamTrack",
            "logo": static("favicon//apple-touch-icon.png"),
        },
        "hltb": {
            "name": "HowLongToBeat",
            "logo": static("img/hltb-logo.png"),
        },
        "imdb": {
            "name": "IMDB",
            "logo": static("img/imdb-logo.png"),
        },
        "steam": {
            "name": "Steam",
            "logo": "https://store.steampowered.com/favicon.ico",
        },
        "goodreads": {"name": "GoodReads", "logo": static("img/logo-goodreads.svg")},
    }

    # Get source info or use defaults if source not found
    info = sources[source_name]

    html = f"""
        <div class="flex items-center">
            <img alt="{info["name"]}" class="w-6 h-6 mr-2" src="{info["logo"]}">
            <h4 class="font-medium">{info["name"]}</h4>
        </div>
    """

    return format_html(html)
