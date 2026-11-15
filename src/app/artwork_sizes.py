"""Pick the provider's own image size for where a picture is shown.

Providers serve every picture at several sizes from the URL path, so a card can
ask for a smaller file without any resizing on Floppy's side. One function
decides, on the server, so web and native receive the same URL and neither
client needs to know a provider's URL scheme.

The card size is chosen for a 2x screen at a card's largest layout width (the
library grid stretches a card to about 225 px, so 450 px is needed). Sizes are
only ever made smaller: a stored URL already at or under the target is left as
it is, and an unknown host, a local cover or a data URL passes through.

Providers left out on purpose, until their stored URLs are checked:
Wikimedia (sizes live in a separate ``/thumb/`` path), Apple podcasts
(``600x600bb`` is already near card size), MyAnimeList, TheTVDB, Cover Art
Archive (``-500`` already).
"""

import re

CARD = "card"

# TMDB posters are stored at w500, the right size for a 2x card. Episode stills
# and some posters are stored at ``original`` (up to 3000 px, about 1 MB);
# w780 keeps landscape stills sharp on a 2x card and is a fraction of the bytes.
_TMDB = re.compile(r"^(https://image\.tmdb\.org/t/p/)original(/[^/?#]+)$")
# IGDB covers are stored at t_original (600x800 at about 1 MB). The retina
# cover size, t_cover_big_2x, is 528 px wide and about 80 KB.
_IGDB = re.compile(
    r"^(https://images\.igdb\.com/igdb/image/upload/)"
    r"t_original(/[^/?#]+)$",
)

_CARD_RULES = (
    (_TMDB, r"\1w780\2"),
    (_IGDB, r"\1t_cover_big_2x\2"),
)


def sized_image_url(url, intent=CARD):
    """Return ``url`` at the provider size for ``intent``; anything else unchanged."""
    if intent != CARD or not isinstance(url, str) or not url.startswith("https://"):
        return url
    for pattern, replacement in _CARD_RULES:
        if pattern.match(url):
            return pattern.sub(replacement, url)
    return url


def card_image_url(url):
    """Return the URL a media card should load for ``url``."""
    return sized_image_url(url, CARD)
