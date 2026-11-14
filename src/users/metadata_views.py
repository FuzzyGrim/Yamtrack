"""Settings > Metadata: manage provider credentials and provider defaults."""

import os

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_GET, require_POST

from app import config, preflight
from app.helpers import safe_return_url
from app.models import MediaTypes, Sources
from app.providers import credentials
from app.services import metadata_resolution
from users.media_tags import media_tag_for_type
from users.models import AnimeLibraryModeChoices

MASK_VISIBLE_CHARS = 4

# Media types with a stored per-user default provider (a `<type>_metadata_source_default`
# field on User). Other media types have no such field, so their modal is informational
# only: it still lists every supported source and links out to configure one, but there
# is nothing to save.
PROVIDER_DEFAULT_MEDIA_TYPES = (
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
)


def _provider_default_field(media_type):
    """Return the User field name storing the default provider for a media type."""
    return f"{media_type}_metadata_source_default"


def _settable_media_types(slug):
    """Return the provider-default media types a provider slug can be set for."""
    types = []
    for media_type in PROVIDER_DEFAULT_MEDIA_TYPES:
        sources = {
            source.value if isinstance(source, Sources) else str(source)
            for source in config.get_sources(media_type) or []
        }
        if slug in sources:
            types.append(media_type)
    return types


def _existing_library_count(user, media_type, current_provider):
    """Return how many tracked entries are not on the current default.

    These are deliberately left alone when the provider changes. The count is
    what makes the settings page describe the real state instead of relying on
    a dismissible one-time prompt.
    """
    if media_type == MediaTypes.TV.value:
        from app.services import library_migration

        return library_migration.tv_items_to_move(user, current_provider).count()

    if media_type == MediaTypes.ANIME.value:
        from app.tasks_anime_library_repair import anime_rows_needing_conversion

        return len(anime_rows_needing_conversion(user))

    return 0


def _provider_summary(user, slug=None, related_media=()):
    """Return one entry per enabled media type, showing its resolved provider.

    Every entry lists all sources the media type supports, whether or not each
    one is currently configured, so the modal can point at what's missing
    instead of just hiding it. A media type is only editable (has a working
    dropdown + Save) when it has a stored per-user default field.

    ``existing_count`` is the number of tracked entries still on the other
    provider or in the other Anime shape. The page offers moving them as a
    separate, optional action; changing the default never does it implicitly.
    """
    summary = []
    media_types = user.get_sidebar_media_types()
    if slug:
        # Provider configuration remains discoverable even for a library type
        # that the viewer has not enabled in their sidebar yet.
        media_types = [
            media_type
            for media_type in config.MEDIA_TYPE_CONFIG
            if media_type
            not in {
                MediaTypes.SEASON.value,
                MediaTypes.EPISODE.value,
                MediaTypes.COMIC_ISSUE.value,
            }
        ]
    for media_type in media_types:
        all_sources = config.get_sources(media_type) or []
        if not all_sources or (
            slug and slug not in {source.value for source in all_sources} and media_type not in related_media
        ):
            continue
        current = metadata_resolution.metadata_default_source(user, media_type)
        summary.append(
            {
                "media_type": media_type,
                "media_tag": media_tag_for_type(media_type),
                "media_tag_keys": [t for t in [media_tag_for_type(media_type)] if t],
                "current_provider": current,
                "current_label": metadata_resolution.metadata_provider_label(current),
                "choices": [
                    {
                        "value": source.value,
                        "label": source.label,
                        "configured": metadata_resolution.provider_is_enabled(
                            source.value,
                            user,
                        ),
                    }
                    for source in all_sources
                ],
                "configurable": media_type in PROVIDER_DEFAULT_MEDIA_TYPES,
                "existing_count": (
                    _existing_library_count(user, media_type, current)
                    if media_type in PROVIDER_DEFAULT_MEDIA_TYPES
                    else 0
                ),
            },
        )
    return summary


def _mask(value):
    """Return a preview that proves a value is stored without revealing it."""
    if not value:
        return ""
    if len(value) <= MASK_VISIBLE_CHARS:
        return "•" * len(value)
    return f"{'•' * 8}{value[-MASK_VISIBLE_CHARS:]}"


def _field_view(spec, field, user):
    """Return the template view-model for one credential field."""
    source = credentials.source_of(spec.slug, field.name, user)
    env = credentials.env_value(field)
    stored = credentials.instance_value(spec.slug, field.name)
    default = credentials.default_value(field)
    personal = credentials.user_value(spec, field, user)
    return {
        "name": field.name,
        "label": field.label,
        "secret": field.secret,
        "required": field.required,
        "placeholder": field.placeholder,
        "setting": field.setting,
        "source": source,
        "locked": bool(env),
        "preview": _mask(env or stored or default),
        "has_fallback_value": bool(env or stored or default),
        "using_default": bool(default and not (env or stored)),
        "has_instance_value": bool(stored),
        "personal": field.name in {f.name for f in spec.personal_fields()},
        "has_personal_value": bool(personal),
        "personal_preview": _mask(personal),
    }


def _provider_view(spec, user):
    """Return the template view-model for one provider row."""
    fields = [_field_view(spec, field, user) for field in spec.fields]
    sources = {field["source"] for field in fields if field["source"]}
    if credentials.has_user_value(spec.slug, user):
        status = credentials.SOURCE_USER
    elif credentials.SOURCE_ENV in sources:
        status = credentials.SOURCE_ENV
    elif credentials.SOURCE_DB in sources:
        status = credentials.SOURCE_DB
    elif credentials.SOURCE_DEFAULT in sources:
        status = credentials.SOURCE_DEFAULT
    else:
        status = ""
    return {
        "slug": spec.slug,
        "label": spec.label,
        "logo_slug": spec.logo_slug or spec.slug,
        "description": spec.description,
        "docs_url": spec.docs_url,
        "setup_hint": spec.setup_hint,
        "user_scope": spec.user_scope,
        "configured": credentials.is_configured(spec.slug, user),
        "status": status,
        "fields": fields,
        "personal_fields": [field for field in fields if field["personal"]],
        "locked": all(field["locked"] for field in fields),
        "has_instance_value": any(field["has_instance_value"] for field in fields),
        "settable_media_types": _settable_media_types(spec.slug),
        "media_tags": list(spec.media_tags),
    }


def _promote_command(username):
    """Return the promote command for the environment Floppy is running in.

    Reuses app.preflight.in_container so a Podman install is not handed
    Docker-only advice, and the container name follows the same
    HOST_CONTAINERNAME convention the SQLite recovery page uses.
    """
    if preflight.in_container():
        container = os.environ.get("HOST_CONTAINERNAME", "floppy")
        return (
            f"docker exec -it {container} python manage.py promote_superuser {username}"
        )
    return f"python src/manage.py promote_superuser {username}"


def metadata_context(user, slug=None, related_media=()):
    """Build the shared credentials and source-selection context.

    A provider page gets only its own credential fields and the global source
    settings for its supported library types. Source choices remain shared:
    choosing a default on one provider page changes the same User field seen
    from every other applicable page.
    """
    # Every provider takes a personal key now, so "who can set it" no longer
    # separates anything; grouping by what the provider does is what is left.
    can_edit_instance = user.is_superuser
    groups = []
    for group in credentials.GROUP_ORDER:
        providers = sorted(
            (
                _provider_view(spec, user)
                for spec in credentials.REGISTRY.values()
                if spec.group == group
                and not spec.bundled_only
                and (not slug or spec.slug == slug)
            ),
            key=lambda provider: provider["label"].casefold(),
        )
        if providers:
            groups.append(
                {
                    "key": group,
                    "label": gettext(credentials.GROUP_LABELS[group]),
                    "description": gettext(credentials.GROUP_DESCRIPTIONS[group]),
                    "providers": providers,
                    "media_tags": sorted(
                        {
                            tag
                            for provider in providers
                            for tag in provider["media_tags"]
                        },
                    ),
                },
            )

    duplicate_anime_count = 0
    if user.anime_enabled and not slug:
        from app.tasks_anime_library_repair import duplicate_pair_count

        duplicate_anime_count = duplicate_pair_count(user_id=user.id)

    provider_summary = _provider_summary(user, slug, related_media)
    present_tags = {entry["media_tag"] for entry in provider_summary}
    for group in groups:
        for provider in group["providers"]:
            present_tags.update(provider["media_tags"])

    context = {
        "credential_groups": groups,
        "media_tag_keys": " ".join(sorted(present_tags - {""})),
        "can_edit_instance": can_edit_instance,
        "provider_summary": provider_summary,
        "anime_library_mode_choices": AnimeLibraryModeChoices.choices,
        "duplicate_anime_count": duplicate_anime_count,
        "metadata_provider": next(
            (provider for group in groups for provider in group["providers"]),
            None,
        ),
        "provider_navigation": bool(slug),
        "tmdb_proxy_configured": bool(user.tmdb_proxy_url),
        "metadata_source_connections": {
            choice["value"]: (
                reverse("provider_detail", args=[choice["value"]])
                + (
                    "?tab=connection"
                    if choice["value"] in credentials.REGISTRY
                    else "?tab=metadata"
                )
            )
            for entry in provider_summary
            for choice in entry["choices"]
        }
        if slug
        else {},
    }
    if not can_edit_instance:
        # Floppy's setup never flags anyone, so the owner of a fresh install is
        # usually not a superuser and has no way to discover that.
        context["instance_has_superuser"] = (
            get_user_model().objects.filter(is_superuser=True).exists()
        )
        context["in_container"] = preflight.in_container()
        context["promote_command"] = _promote_command(user.username)
    return context


@require_GET
def metadata_settings(request):
    """Render the metadata provider credentials page."""
    return render(request, "users/metadata.html", metadata_context(request.user))


def _metadata_redirect(request):
    """Return to an explicitly supplied local provider tab after a form save."""
    return redirect(
        safe_return_url(
            request,
            request.POST.get("next", ""),
            fallback=reverse("metadata_settings"),
        ),
    )


@require_POST
def set_media_type_provider(request, media_type):
    """Save the current user's preferred metadata provider for one media type."""
    if media_type not in PROVIDER_DEFAULT_MEDIA_TYPES:
        return HttpResponse(status=404)
    if request.user.is_demo:
        messages.error(request, "This section is view-only for demo accounts.")
        return _metadata_redirect(request)

    source = request.POST.get("source", "")
    valid_sources = {
        choice.value
        for choice in metadata_resolution.available_metadata_sources(media_type, request.user)
    }
    if source not in valid_sources:
        messages.error(request, "That provider isn't available for this media type.")
        return _metadata_redirect(request)

    field = _provider_default_field(media_type)
    if getattr(request.user, field) != source:
        # The default only decides where new titles come from. Existing homes
        # are left alone; moving them is an explicit action on this page.
        setattr(request.user, field, source)
        request.user.save(update_fields=[field])

    if media_type == MediaTypes.ANIME.value:
        anime_library_mode = request.POST.get("anime_library_mode")
        if (
            anime_library_mode in AnimeLibraryModeChoices.values
            and request.user.anime_library_mode != anime_library_mode
        ):
            request.user.anime_library_mode = anime_library_mode
            request.user.save(update_fields=["anime_library_mode"])

    messages.success(request, "Metadata provider updated.")
    return _metadata_redirect(request)


def _spec_or_none(slug):
    """Return the registry entry a user may manage, or None.

    A bundled-only key is not manageable from Settings, so it reads as absent.
    """
    spec = credentials.get_spec(slug)
    return None if spec is not None and spec.bundled_only else spec


def _reopen_game_groups_if_igdb(slug, user=None):
    """Retry franchise backfill that failed while IGDB had no key."""
    if slug == "igdb":
        from app.tasks_game_groups import reopen_unconfigured_game_groups

        reopen_unconfigured_game_groups(user)


@require_POST
def save_provider_credential(request, slug):
    """Store instance-wide credentials for a provider."""
    if not request.user.is_superuser:
        return HttpResponse(status=403)

    spec = _spec_or_none(slug)
    if spec is None:
        return HttpResponse(status=404)

    values = {
        field.name: request.POST.get(field.name, "").strip() for field in spec.fields
    }
    # A locked field is rendered read-only, so never let a post overwrite it.
    for field in spec.fields:
        if credentials.env_value(field):
            values.pop(field.name, None)

    if spec.validator is not None and any(values.values()):
        error = spec.validator(values)
        if error:
            messages.error(request, f"{spec.label}: {error}")
            return _metadata_redirect(request)

    credentials.set_instance(slug, values, actor=request.user)
    _reopen_game_groups_if_igdb(slug)
    messages.success(request, f"{spec.label} credentials saved.")
    return _metadata_redirect(request)


@require_POST
def clear_provider_credential(request, slug):
    """Remove the instance-wide credentials for a provider."""
    if not request.user.is_superuser:
        return HttpResponse(status=403)

    spec = _spec_or_none(slug)
    if spec is None:
        return HttpResponse(status=404)

    credentials.clear_instance(slug)
    messages.success(request, f"{spec.label} credentials removed.")
    return _metadata_redirect(request)


@require_POST
def save_personal_credential(request, slug):
    """Store the current user's personal credentials for a provider."""
    spec = _spec_or_none(slug)
    if spec is None or not spec.user_scope:
        return HttpResponse(status=404)

    values = {
        field.name: request.POST.get(field.name, "").strip()
        for field in spec.personal_fields()
    }
    if spec.validator is not None and any(values.values()):
        error = spec.validator(values)
        if error:
            messages.error(request, f"{spec.label}: {error}")
            return _metadata_redirect(request)

    credentials.set_user(slug, request.user, values)
    _reopen_game_groups_if_igdb(slug, request.user)
    if any(values.values()):
        messages.success(request, f"Your {spec.label} key was saved.")
    else:
        messages.success(request, f"Your {spec.label} key was removed.")
    return _metadata_redirect(request)
