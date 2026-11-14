"""Registry and resolver for metadata provider credentials.

Credentials resolve in one order everywhere: a user's personal override, then
the environment, then an instance-wide value stored from Settings > Metadata,
then an intentionally public shared default baked into the image.
``settings.SHARED_DEFAULT_CREDENTIALS`` is what lets "supplied by the
environment" be told apart from "still on the bundled default" - a plain
``bool(setting)`` cannot, because the bundled providers are always truthy.

Shared defaults are app-owned metadata credentials, never user or account
credentials. Private provider secrets must come from the environment, a
Docker secret, or the encrypted instance/user credential stores.
"""

import contextlib
import contextvars
import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from django.conf import settings
from django.core.cache import cache

from app.log_safety import redact_secrets

logger = logging.getLogger(__name__)

CACHE_KEY = "provider_credentials_instance"
CACHE_TIMEOUT = 60 * 60

# Where a resolved value came from, in precedence order.
SOURCE_USER = "user"
SOURCE_ENV = "env"
SOURCE_DB = "db"
SOURCE_DEFAULT = "default"

GROUP_METADATA = "metadata"
GROUP_ACCOUNTS = "accounts"

GROUP_ORDER = (GROUP_METADATA, GROUP_ACCOUNTS)

GROUP_LABELS = {
    GROUP_METADATA: "Metadata providers",
    GROUP_ACCOUNTS: "Imports and accounts",
}

GROUP_DESCRIPTIONS = {
    GROUP_METADATA: (
        "Where titles, artwork and details come from. Every one of these takes a "
        "key of your own."
    ),
    GROUP_ACCOUNTS: (
        "Credentials for connecting an account and importing a library. Every one "
        "of these takes a key of your own."
    ),
}


# Personal credentials for these providers live on their own account model.
USER_STORAGE_GENERIC = "generic"
USER_STORAGE_TRAKT = "trakt_account"


@dataclass(frozen=True, slots=True)
class CredentialField:
    """One input of a provider credential."""

    name: str
    label: str
    setting: str
    secret: bool = True
    required: bool = True
    placeholder: str = ""


@dataclass(frozen=True, slots=True)
class ProviderCredentialSpec:
    """A provider whose credentials can be managed from Settings > Metadata."""

    slug: str
    label: str
    description: str
    docs_url: str
    fields: tuple[CredentialField, ...]
    group: str = GROUP_METADATA
    user_scope: bool = False
    user_storage: str = USER_STORAGE_GENERIC
    logo_slug: str = ""
    media_tags: tuple[str, ...] = ()
    validator: object = None
    user_fields: tuple[str, ...] = dataclass_field(default_factory=tuple)
    setup_hint: str = ""
    # The key belongs to Floppy, not to its users: the provider only issues it
    # to the app. No credential form, "Get a key" link or credential status is
    # shown, nothing can be saved over it from Settings, and only the
    # environment can replace it (a rebrand or public host with its own key).
    bundled_only: bool = False

    def personal_fields(self):
        """Return the fields a user may override personally."""
        if not self.user_scope:
            return ()
        if self.user_fields:
            return tuple(f for f in self.fields if f.name in self.user_fields)
        return self.fields


VALIDATION_TIMEOUT = 10


def _probe_reason(response):
    """Return the provider's own explanation for a rejected credential.

    The status code alone cannot tell a user why a key failed: Hardcover says
    "Missing scopes: read:me" in its error body. Read the common JSON shapes
    and fall back to a short, redacted slice of the response text.
    """
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        # OAuth-style errors, like Hardcover's insufficient_scope body.
        for key in ("error_description", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return redact_secrets(value.strip())
        # Some providers wrap the message in an error object.
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return redact_secrets(error.strip())
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return redact_secrets(message.strip())
        # GraphQL errors array, used by several metadata providers.
        errors = payload.get("errors")
        if isinstance(errors, list):
            messages = [
                item.get("message", "").strip()
                for item in errors
                if isinstance(item, dict) and item.get("message")
            ]
            if messages:
                return redact_secrets("; ".join(messages))
    text = getattr(response, "text", "") or ""
    if not isinstance(text, str):
        text = ""
    return redact_secrets(text.strip()[:200])


def _probe(method, url, **kwargs):
    """Run a credential probe, returning an error string or None.

    Only an explicit auth rejection blocks a save. An unreachable provider is
    not evidence the key is wrong, so it is allowed through.
    """
    import requests

    try:
        response = requests.request(
            method,
            url,
            timeout=VALIDATION_TIMEOUT,
            **kwargs,
        )
    except requests.exceptions.RequestException:
        logger.warning("Credential probe could not reach %s", url)
        return None

    if response.status_code in {
        requests.codes.unauthorized,
        requests.codes.forbidden,
        requests.codes.bad_request,
    }:
        reason = _probe_reason(response)
        logger.warning(
            "Credential probe rejected by %s (HTTP %s): %s",
            url,
            response.status_code,
            reason or "no response body",
        )
        if reason:
            return f"the provider rejected that credential ({reason})"
        return "the provider rejected that credential"
    return None


def _validate_hardcover(values):
    """Check a Hardcover token with the catalog query metadata uses.

    Hardcover's ``me`` query needs the read:me scope, but metadata search and
    lookup only need read:catalog. Probing ``me`` rejected valid read:catalog
    tokens with an unexplained failure (#1857).
    """
    token = values.get("api_key", "")
    if not token:
        return None
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    return _probe(
        "POST",
        "https://api.hardcover.app/v1/graphql",
        json={"query": "{ books_by_pk(id: 1) { id } }"},
        headers={"Authorization": token},
    )


def _validate_tvdb(values):
    """Check a TVDB key (and optional PIN) against the login endpoint."""
    api_key = values.get("api_key", "")
    if not api_key:
        return None
    payload = {"apikey": api_key}
    if values.get("pin"):
        payload["pin"] = values["pin"]
    return _probe(
        "POST",
        "https://api4.thetvdb.com/v4/login",
        json=payload,
        headers={"Content-Type": "application/json"},
    )


def _validate_googlebooks(values):
    """Check a Google Books key with a one-result search."""
    api_key = values.get("api_key", "")
    if not api_key:
        return None
    return _probe(
        "GET",
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": "test", "maxResults": 1, "key": api_key},
    )


def _validate_steam(values):
    """Check a Steam key against a cheap authenticated endpoint."""
    api_key = values.get("api_key", "")
    if not api_key:
        return None
    return _probe(
        "GET",
        "https://api.steampowered.com/ISteamWebAPIUtil/GetSupportedAPIList/v1/",
        params={"key": api_key},
    )


def _validate_gcd(values):
    """Check a GCD login against a one-request series lookup."""
    username = values.get("username", "")
    password = values.get("password", "")
    if not username or not password:
        return None
    return _probe(
        "GET",
        "https://www.comics.org/api/series/1/",
        params={"format": "json"},
        auth=(username, password),
    )


def _validate_trakt(values):
    """Check a Trakt client ID against a public endpoint."""
    client_id = values.get("client_id", "")
    if not client_id:
        return None
    return _probe(
        "GET",
        "https://api.trakt.tv/movies/trending",
        params={"limit": 1},
        headers={
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": client_id,
        },
    )


def _validate_tmdb(values):
    """Check a TMDB key against the configuration endpoint."""
    api_key = values.get("api_key", "")
    if not api_key:
        return None
    return _probe(
        "GET",
        "https://api.themoviedb.org/3/configuration",
        params={"api_key": api_key},
    )


def _validate_steamgriddb(values):
    """Check a SteamGridDB key with a one-term search."""
    api_key = values.get("api_key", "")
    if not api_key:
        return None
    return _probe(
        "GET",
        "https://www.steamgriddb.com/api/v2/search/autocomplete/test",
        headers={"Authorization": f"Bearer {api_key}"},
    )


def _validate_thegamesdb(values):
    """Check a TheGamesDB key against its cheapest lookup."""
    api_key = values.get("api_key", "")
    if not api_key:
        return None
    return _probe(
        "GET",
        "https://api.thegamesdb.net/v1/Genres",
        params={"apikey": api_key},
    )


def _validate_screenscraper(values):
    """Require the username and password together; half a login is unusable."""
    if bool(values.get("ssid")) != bool(values.get("sspassword")):
        return "enter both the username and the password."
    return None


# TVDB and IGDB swap their key for a bearer token cached under a single global
# cache key (tvdb.TOKEN_CACHE_KEY, and igdb's token cache), so a personal key
# there would hand every other user someone else's token. They stay
# instance-only until that cache is keyed per credential.
REGISTRY: dict[str, ProviderCredentialSpec] = {
    spec.slug: spec
    for spec in (
        ProviderCredentialSpec(
            slug="tvdb",
            media_tags=("screen", "anime_manga"),
            user_scope=True,
            validator=_validate_tvdb,
            label="TheTVDB",
            description="TV and anime metadata. Needs a subscriber PIN for v4 keys.",
            docs_url="https://thetvdb.com/api-information",
            fields=(
                CredentialField("api_key", "API key", "TVDB_API_KEY"),
                CredentialField("pin", "Subscriber PIN", "TVDB_PIN", required=False),
            ),
        ),
        ProviderCredentialSpec(
            slug="hardcover",
            media_tags=("reading",),
            user_scope=True,
            validator=_validate_hardcover,
            label="Hardcover",
            description="Book metadata. Free tier is metered per account.",
            docs_url="https://hardcover.app/account/api",
            setup_hint=(
                "The token needs the read:catalog scope for book metadata. "
                "Add read:me as well to import your own Hardcover library."
            ),
            fields=(CredentialField("api_key", "API token", "HARDCOVER_API"),),
        ),
        ProviderCredentialSpec(
            slug="googlebooks",
            media_tags=("reading",),
            user_scope=True,
            validator=_validate_googlebooks,
            label="Google Books",
            description="Book metadata and cover art.",
            docs_url="https://developers.google.com/books/docs/v1/using",
            fields=(CredentialField("api_key", "API key", "GOOGLE_BOOKS_API_KEY"),),
        ),
        ProviderCredentialSpec(
            slug="steam",
            media_tags=("games",),
            group=GROUP_ACCOUNTS,
            user_scope=True,
            validator=_validate_steam,
            label="Steam",
            description="Used to import your Steam library and playtime.",
            docs_url="https://steamcommunity.com/dev/apikey",
            fields=(CredentialField("api_key", "API key", "STEAM_API_KEY"),),
        ),
        ProviderCredentialSpec(
            slug="lastfm",
            media_tags=("music",),
            group=GROUP_ACCOUNTS,
            user_scope=True,
            label="Last.fm",
            description="Scrobble history and music metadata.",
            docs_url="https://www.last.fm/api/account/create",
            fields=(CredentialField("api_key", "API key", "LASTFM_API_KEY"),),
        ),
        ProviderCredentialSpec(
            slug="tmdb",
            media_tags=("screen", "anime_manga"),
            user_scope=True,
            validator=_validate_tmdb,
            label="TMDB",
            description="Movie and TV metadata.",
            docs_url="https://www.themoviedb.org/settings/api",
            fields=(CredentialField("api_key", "API key", "TMDB_API"),),
        ),
        ProviderCredentialSpec(
            slug="mal",
            media_tags=("anime_manga",),
            user_scope=True,
            label="MyAnimeList",
            logo_slug="myanimelist",
            description="Anime and manga metadata, and syncing watch status back.",
            docs_url="https://myanimelist.net/apiconfig",
            fields=(
                CredentialField("client_id", "Client ID", "MAL_API"),
                CredentialField(
                    "client_secret",
                    "Client secret",
                    "MAL_API_SECRET",
                    required=False,
                    placeholder="Sync to MyAnimeList: set with the Client ID from the same app",
                ),
            ),
        ),
        ProviderCredentialSpec(
            slug="wetrakr",
            media_tags=("screen",),
            group=GROUP_ACCOUNTS,
            bundled_only=True,
            label="WeTrakr",
            description="Sending your plays, statuses and ratings to WeTrakr.",
            docs_url="https://api.wetrakr.com/",
            fields=(CredentialField("client_id", "App key", "WETRAKR_CLIENT_ID"),),
        ),
        ProviderCredentialSpec(
            slug="igdb",
            media_tags=("games",),
            user_scope=True,
            label="IGDB",
            description="Game metadata, authenticated through Twitch.",
            docs_url="https://dev.twitch.tv/console/apps/create",
            setup_hint=(
                "Register an application with the Twitch Developer Console. "
                "Set OAuth Redirect URL to https://localhost (Floppy only uses "
                "client credentials, so it is never called), Category to "
                "Application Integration, and Client Type to Confidential. "
                "Then click Manage and New Secret."
            ),
            fields=(
                CredentialField("client_id", "Client ID", "IGDB_ID"),
                CredentialField("client_secret", "Client secret", "IGDB_SECRET"),
            ),
        ),
        ProviderCredentialSpec(
            slug="steamgriddb",
            media_tags=("games",),
            user_scope=True,
            validator=_validate_steamgriddb,
            label="SteamGridDB",
            description="Recommended. Logos and backdrops for any game.",
            docs_url="https://www.steamgriddb.com/profile/preferences/api",
            fields=(CredentialField("api_key", "API key", "STEAMGRIDDB_API_KEY"),),
        ),
        ProviderCredentialSpec(
            slug="thegamesdb",
            media_tags=("games",),
            user_scope=True,
            validator=_validate_thegamesdb,
            label="TheGamesDB",
            description="Extra coverage. Clear logos and fanart.",
            docs_url="https://api.thegamesdb.net/key.php",
            fields=(CredentialField("api_key", "API key", "THEGAMESDB_API_KEY"),),
        ),
        ProviderCredentialSpec(
            slug="screenscraper",
            media_tags=("games",),
            user_scope=True,
            label="ScreenScraper",
            description="Retro artwork. An account raises your limits.",
            docs_url="https://www.screenscraper.fr/membreinscription.php",
            validator=_validate_screenscraper,
            fields=(
                CredentialField("ssid", "Username", "SCREENSCRAPER_SSID", secret=False),
                CredentialField("sspassword", "Password", "SCREENSCRAPER_SSPASSWORD"),
            ),
        ),
        ProviderCredentialSpec(
            slug="opencritic",
            media_tags=("games",),
            user_scope=True,
            label="OpenCritic",
            description="Critic scores for games. The free RapidAPI plan is enough.",
            docs_url="https://rapidapi.com/opencritic-opencritic-default/api/opencritic-api",
            fields=(CredentialField("api_key", "RapidAPI key", "OPENCRITIC_API_KEY"),),
        ),
        ProviderCredentialSpec(
            slug="podcastindex",
            media_tags=("podcasts",),
            user_scope=True,
            label="Podcast Index",
            description="Open podcast directory. Finds shows other sources miss.",
            docs_url="https://api.podcastindex.org/signup",
            fields=(
                CredentialField("api_key", "API key", "PODCASTINDEX_API_KEY"),
                CredentialField(
                    "api_secret",
                    "API secret",
                    "PODCASTINDEX_API_SECRET",
                ),
            ),
        ),
        ProviderCredentialSpec(
            slug="bgg",
            media_tags=("games",),
            user_scope=True,
            label="BoardGameGeek",
            description="Board game metadata.",
            docs_url="https://boardgamegeek.com/using_the_xml_api",
            fields=(CredentialField("token", "API token", "BGG_API_TOKEN"),),
        ),
        ProviderCredentialSpec(
            slug="comicvine",
            media_tags=("reading",),
            user_scope=True,
            label="Comic Vine",
            description="Comic and graphic novel metadata.",
            docs_url="https://comicvine.gamespot.com/api/",
            fields=(CredentialField("api_key", "API key", "COMICVINE_API"),),
        ),
        ProviderCredentialSpec(
            slug="gcd",
            media_tags=("reading",),
            user_scope=True,
            validator=_validate_gcd,
            label="Grand Comics Database",
            description=(
                "Comic and issue metadata. Needs a free GCD account: anonymous "
                "access is limited to 30 requests an hour."
            ),
            docs_url="https://www.comics.org/accounts/register/",
            fields=(
                CredentialField("username", "Username", "GCD_USERNAME", secret=False),
                CredentialField("password", "Password", "GCD_PASSWORD"),
            ),
        ),
        ProviderCredentialSpec(
            slug="trakt",
            media_tags=("screen",),
            group=GROUP_ACCOUNTS,
            user_scope=True,
            validator=_validate_trakt,
            label="Trakt",
            description="Popularity data, plus Trakt list and history imports.",
            docs_url="https://trakt.tv/oauth/applications",
            fields=(
                CredentialField("client_id", "Client ID", "TRAKT_API"),
                CredentialField(
                    "client_secret",
                    "Client secret",
                    "TRAKT_API_SECRET",
                    required=False,
                ),
            ),
            user_storage=USER_STORAGE_TRAKT,
        ),
        ProviderCredentialSpec(
            slug="anilist",
            media_tags=("anime_manga",),
            group=GROUP_ACCOUNTS,
            user_scope=True,
            label="AniList",
            description="AniList account import.",
            docs_url="https://anilist.co/settings/developer",
            fields=(
                CredentialField("client_id", "Client ID", "ANILIST_ID"),
                CredentialField("client_secret", "Client secret", "ANILIST_SECRET"),
            ),
        ),
        ProviderCredentialSpec(
            slug="simkl",
            media_tags=("screen", "anime_manga"),
            group=GROUP_ACCOUNTS,
            user_scope=True,
            label="SIMKL",
            description="SIMKL account import. Needs a “Server apps & services” app's Client ID and secret.",
            docs_url="https://simkl.com/settings/developer/",
            fields=(
                CredentialField("client_id", "Client ID", "SIMKL_ID"),
                CredentialField("client_secret", "Client secret", "SIMKL_SECRET"),
            ),
        ),
    )
}


# Threading a user argument through every provider function would mean touching
# ~150 signatures across TMDB, TVDB and friends. Instead the request-serving
# user is published here, mirroring services.interactive_request_scope(). A
# Celery task runs no middleware, so a task acting for one user (an import, a
# webhook, a sync) must open this scope itself; work that does not keeps using
# the instance key.
_current_user: contextvars.ContextVar = contextvars.ContextVar(
    "_provider_credential_user",
    default=None,
)


@contextlib.contextmanager
def current_user_scope(user):
    """Publish the user whose personal keys apply for the duration of a request."""
    token = _current_user.set(user if getattr(user, "is_authenticated", False) else None)
    try:
        yield
    finally:
        _current_user.reset(token)


def _resolve_user(user):
    """Return the explicit user, else whichever the current request belongs to."""
    return user if user is not None else _current_user.get()


def active_user():
    """Return the user whose personal keys apply right now, if one is published."""
    return _current_user.get()


def get_spec(slug):
    """Return the registry entry for a slug, or None."""
    return REGISTRY.get(slug)


def get_field(slug, field_name):
    """Return a spec's field definition, or None."""
    spec = REGISTRY.get(slug)
    if spec is None:
        return None
    for candidate in spec.fields:
        if candidate.name == field_name:
            return candidate
    return None


def _decrypt(value):
    """Decrypt a stored credential, treating failures as unset."""
    from cryptography.fernet import InvalidToken

    from integrations.imports.helpers import decrypt

    if not value:
        return ""
    try:
        return decrypt(value)
    except (InvalidToken, ValueError, TypeError):
        logger.warning(
            "Stored provider credential could not be decrypted; treating as unset. "
            "SECRET may have been rotated.",
        )
        return ""


def _instance_map():
    """Return ``{provider: {field: value}}`` for instance credentials."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    from app.models import InstanceProviderCredential

    resolved = {}
    rows = InstanceProviderCredential.objects.values_list("provider", "field", "value")
    for provider, field_name, value in rows:
        plaintext = _decrypt(value)
        if plaintext:
            resolved.setdefault(provider, {})[field_name] = plaintext

    cache.set(CACHE_KEY, resolved, CACHE_TIMEOUT)
    return resolved


def invalidate_cache():
    """Drop the cached instance credential map."""
    cache.delete(CACHE_KEY)


def env_value(field):
    """Return the environment-supplied value for a field, if any.

    A value equal to the bundled shared default did not come from the
    environment, so it is reported as absent here.
    """
    value = (getattr(settings, field.setting, "") or "").strip()
    if not value:
        return ""
    shared = settings.SHARED_DEFAULT_CREDENTIALS.get(field.setting)
    if shared is not None and value == shared:
        return ""
    return value


def default_value(field):
    """Return the bundled shared default for a field, if it still applies."""
    shared = settings.SHARED_DEFAULT_CREDENTIALS.get(field.setting)
    if not shared:
        return ""
    current = (getattr(settings, field.setting, "") or "").strip()
    return shared if current == shared else ""


def _user_cache_key(user_id):
    """Return the cache key holding one member's decrypted credentials."""
    return f"{CACHE_KEY}_user_{user_id}"


def _user_map(user):
    """Return ``{provider: {field: value}}`` for one member's personal keys.

    Credentials are read on every provider call, so this is cached the same way
    the instance map is; without it each call costs a query per field.
    """
    cached = cache.get(_user_cache_key(user.pk))
    if cached is not None:
        return cached

    from app.models import UserProviderCredential

    resolved = {}
    rows = UserProviderCredential.objects.filter(user=user).values_list(
        "provider",
        "field",
        "value",
    )
    for provider, field_name, value in rows:
        plaintext = _decrypt(value)
        if plaintext:
            resolved.setdefault(provider, {})[field_name] = plaintext

    cache.set(_user_cache_key(user.pk), resolved, CACHE_TIMEOUT)
    return resolved


def _trakt_user_values(user):
    """Return one member's Trakt client credentials from their account row.

    Kept out of _user_map so the common path costs a single query: only a Trakt
    lookup pays for the second table.
    """
    cache_key = f"{_user_cache_key(user.pk)}_trakt"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from integrations.models import TraktAccount

    resolved = {}
    account = TraktAccount.objects.filter(user=user).first()
    if account is not None:
        for name in ("client_id", "client_secret"):
            plaintext = _decrypt(getattr(account, name, ""))
            if plaintext:
                resolved[name] = plaintext

    cache.set(cache_key, resolved, CACHE_TIMEOUT)
    return resolved


def invalidate_user_cache(user):
    """Drop one member's cached credential maps."""
    cache.delete(_user_cache_key(user.pk))
    cache.delete(f"{_user_cache_key(user.pk)}_trakt")


def user_value(spec, field, user):
    """Return a user's personal value for a field, if stored."""
    if user is None or not spec.user_scope:
        return ""
    if field.name not in {f.name for f in spec.personal_fields()}:
        return ""
    if not getattr(user, "is_authenticated", False):
        return ""

    if spec.user_storage == USER_STORAGE_TRAKT:
        return _trakt_user_values(user).get(field.name, "")
    return _user_map(user).get(spec.slug, {}).get(field.name, "")


def has_user_value(slug, user):
    """Return whether a user stored any personal credential for a provider."""
    user = _resolve_user(user)
    spec = REGISTRY.get(slug)
    if spec is None:
        return False
    return any(user_value(spec, field, user) for field in spec.personal_fields())


def source_of(slug, field_name, user=None):
    """Return which tier supplies a field's value, or None when unset."""
    user = _resolve_user(user)
    spec = REGISTRY.get(slug)
    field = get_field(slug, field_name)
    if spec is None or field is None:
        return None
    if user_value(spec, field, user):
        return SOURCE_USER
    if env_value(field):
        return SOURCE_ENV
    if _instance_map().get(slug, {}).get(field_name):
        return SOURCE_DB
    if default_value(field):
        return SOURCE_DEFAULT
    return None


def get(slug, field_name, user=None):
    """Return a credential value using the full precedence chain."""
    user = _resolve_user(user)
    spec = REGISTRY.get(slug)
    field = get_field(slug, field_name)
    if spec is None or field is None:
        return ""
    return (
        user_value(spec, field, user)
        or env_value(field)
        or _instance_map().get(slug, {}).get(field_name, "")
        or default_value(field)
    )


def get_together(slug, field_names, user=None):
    """Return several fields from the first tier that supplies all of them.

    For credentials that only work as a set, like an OAuth client ID and
    secret: resolving each field on its own can pair values from two
    different apps. Returns empty strings when no single tier has them all.
    """
    user = _resolve_user(user)
    spec = REGISTRY.get(slug)
    fields = [get_field(slug, name) for name in field_names]
    if spec is None or None in fields:
        return tuple("" for _ in field_names)
    tiers = (
        lambda field: user_value(spec, field, user),
        env_value,
        lambda field: _instance_map().get(slug, {}).get(field.name, ""),
        default_value,
    )
    for tier in tiers:
        values = tuple(tier(field) for field in fields)
        if all(values):
            return values
    return tuple("" for _ in field_names)


def cache_suffix(slug, *field_names, user=None):
    """Return a stable digest of the credentials a cached token was minted from.

    Bearer tokens are cached, and a personal key mints a different token. Keying
    the cache by the credential keeps one member's token from being handed to
    everyone else.
    """
    import hashlib

    material = "\u0000".join(get(slug, name, user=user) for name in field_names)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def is_configured(slug, user=None):
    """Return whether every required field of a provider resolves."""
    user = _resolve_user(user)
    spec = REGISTRY.get(slug)
    if spec is None:
        return False
    return all(
        get(slug, field.name, user) for field in spec.fields if field.required
    )


def instance_value(slug, field_name):
    """Return the stored instance value for a field, ignoring other tiers."""
    return _instance_map().get(slug, {}).get(field_name, "")


def _note_game_artwork_key(slug, user=None):
    """Let games that lack artwork look again once a game artwork key is saved.

    A game page that found nothing waits hours before asking again, so without
    this a freshly added key would seem to do nothing.
    """
    from app import game_artwork

    if slug not in game_artwork.CREDENTIAL_PROVIDERS:
        return
    try:
        game_artwork.keys_saved(user)
    except Exception:
        # The key is already saved; a missed note only delays the new lookup.
        logger.warning("Could not note game artwork key for %s", slug, exc_info=True)


def set_instance(slug, values, actor=None):
    """Store instance credentials for a provider.

    ``values`` maps field names to plaintext. An empty value deletes the row so
    the provider falls back to the next tier.
    """
    from app.models import InstanceProviderCredential
    from integrations.imports.helpers import encrypt

    spec = REGISTRY[slug]
    known = {field.name for field in spec.fields}
    for field_name, raw in values.items():
        if field_name not in known:
            continue
        plaintext = (raw or "").strip()
        if plaintext:
            InstanceProviderCredential.objects.update_or_create(
                provider=slug,
                field=field_name,
                defaults={"value": encrypt(plaintext), "updated_by": actor},
            )
        else:
            InstanceProviderCredential.objects.filter(
                provider=slug,
                field=field_name,
            ).delete()
    invalidate_cache()
    _note_game_artwork_key(slug)


def clear_instance(slug):
    """Remove every stored instance credential for a provider."""
    from app.models import InstanceProviderCredential

    InstanceProviderCredential.objects.filter(provider=slug).delete()
    invalidate_cache()


def set_user(slug, user, values):
    """Store a user's personal credentials for a provider."""
    from integrations.imports.helpers import encrypt

    spec = REGISTRY[slug]
    if not spec.user_scope:
        return
    allowed = {field.name for field in spec.personal_fields()}

    if spec.user_storage == USER_STORAGE_TRAKT:
        from integrations.models import TraktAccount

        account, _ = TraktAccount.objects.get_or_create(user=user)
        for field_name, raw in values.items():
            if field_name not in allowed:
                continue
            plaintext = (raw or "").strip()
            setattr(account, field_name, encrypt(plaintext) if plaintext else None)
        account.save()
        invalidate_user_cache(user)
        return

    from app.models import UserProviderCredential

    for field_name, raw in values.items():
        if field_name not in allowed:
            continue
        plaintext = (raw or "").strip()
        if plaintext:
            UserProviderCredential.objects.update_or_create(
                user=user,
                provider=slug,
                field=field_name,
                defaults={"value": encrypt(plaintext)},
            )
        else:
            UserProviderCredential.objects.filter(
                user=user,
                provider=slug,
                field=field_name,
            ).delete()
    invalidate_user_cache(user)
    _note_game_artwork_key(slug, user)


def clear_user(slug, user):
    """Remove a user's personal credentials for a provider."""
    spec = REGISTRY[slug]
    if spec.user_storage == USER_STORAGE_TRAKT:
        from integrations.models import TraktAccount

        TraktAccount.objects.filter(user=user).update(
            client_id=None,
            client_secret=None,
        )
        invalidate_user_cache(user)
        return

    from app.models import UserProviderCredential

    UserProviderCredential.objects.filter(user=user, provider=slug).delete()
    invalidate_user_cache(user)
