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
            "logo": "https://kitsu.app/favicon-194x194-2f4dbec5ffe82b8f61a3c6d28a77bc6e.png",
        },
        "trakt": {
            "name": "Trakt",
            "logo": "https://trakt.tv/assets/logos/logomark.square.gradient-b644b16c38ff775861b4b1f58c1230f6a097a2466ab33ae00445a505c33fcb91.svg",
        },
        "myanimelist": {
            "name": "MyAnimeList",
            "logo": "https://cdn.myanimelist.net/images/favicon.ico",
        },
        "anilist": {
            "name": "AniList",
            "logo": "https://anilist.co/img/icons/icon.svg",
        },
        "simkl": {
            "name": "SIMKL",
            "logo": "https://eu.simkl.in/img_favicon/v2/favicon-192x192.png",
        },
        "yamtrack": {
            "name": "YamTrack",
            "logo": static("favicon//apple-touch-icon.png"),
        },
        "hltb": {
            "name": "HowLongToBeat",
            "logo": "https://howlongtobeat.com/img/icons/favicon-96x96.png",
        },
        "imdb": {
            "name": "IMDB",
            "logo": static("img/logo-imdb.svg"),
        },
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
