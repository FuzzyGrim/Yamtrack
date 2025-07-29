"""Contains views for importing and exporting media data from various sources."""

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

import users
from integrations import exports, tasks
from integrations.imports import helpers, simkl
from integrations.webhooks import emby, jellyfin, plex

logger = logging.getLogger(__name__)


@require_POST
def import_trakt(request):
    """View for importing anime and manga data from Trakt."""
    username = request.POST.get("user")
    if not username:
        messages.error(request, "Trakt username is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]

    if frequency == "once":
        tasks.import_trakt.delay(username=username, user_id=request.user.id, mode=mode)
        messages.info(request, "The task to import media from Trakt has been queued.")
    else:
        import_time = request.POST["time"]
        helpers.create_import_schedule(
            username,
            request,
            mode,
            frequency,
            import_time,
            "Trakt",
        )
    return redirect("import_data")


@require_POST
def simkl_oauth(request):
    """View for initiating the SIMKL OAuth2 authorization flow."""
    redirect_uri = request.build_absolute_uri(reverse("import_simkl"))
    url = "https://simkl.com/oauth/authorize"

    # store in session because simkl drops all additional parameters
    request.session["simkl_import_mode"] = request.POST["mode"]
    request.session["simkl_import_frequency"] = request.POST["frequency"]
    request.session["simkl_import_time"] = request.POST["time"]

    return redirect(
        f"{url}?client_id={settings.SIMKL_ID}&redirect_uri={redirect_uri}&response_type=code",
    )


@require_GET
def import_simkl(request):
    """View for getting the SIMKL OAuth2 token."""
    token = simkl.get_token(request)
    enc_token = helpers.encrypt(token)
    frequency = request.session.pop("simkl_import_frequency")
    mode = request.session.pop("simkl_import_mode")
    import_time = request.session.pop("simkl_import_time")

    if frequency == "once":
        tasks.import_simkl.delay(username=enc_token, user_id=request.user.id, mode=mode)
        messages.info(request, "The task to import media from Simkl has been queued.")
    else:
        helpers.create_import_schedule(
            enc_token,
            request,
            mode,
            frequency,
            import_time,
            "SIMKL",
        )

    return redirect("import_data")


@require_POST
def import_mal(request):
    """View for importing anime and manga data from MyAnimeList."""
    username = request.POST.get("user")
    if not username:
        messages.error(request, "MyAnimeList username is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]

    if frequency == "once":
        tasks.import_mal.delay(username=username, user_id=request.user.id, mode=mode)
        messages.info(
            request,
            "The task to import media from MyAnimeList has been queued.",
        )
    else:
        import_time = request.POST["time"]
        helpers.create_import_schedule(
            username,
            request,
            mode,
            frequency,
            import_time,
            "MyAnimeList",
        )
    return redirect("import_data")


@require_POST
def import_anilist(request):
    """View for importing anime and manga data from AniList."""
    username = request.POST.get("user")
    if not username:
        messages.error(request, "AniList username is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]

    if frequency == "once":
        tasks.import_anilist.delay(
            username=username,
            user_id=request.user.id,
            mode=mode,
        )
        messages.info(request, "The task to import media from AniList has been queued.")
    else:
        import_time = request.POST["time"]
        helpers.create_import_schedule(
            username,
            request,
            mode,
            frequency,
            import_time,
            "AniList",
        )
    return redirect("import_data")


@require_POST
def import_kitsu(request):
    """View for importing anime and manga data from Kitsu by user ID."""
    kitsu_id = request.POST.get("user")
    if not kitsu_id:
        messages.error(request, "Kitsu user ID is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]

    if frequency == "once":
        tasks.import_kitsu.delay(username=kitsu_id, user_id=request.user.id, mode=mode)
        messages.info(request, "The task to import media from Kitsu has been queued.")
    else:
        import_time = request.POST["time"]
        helpers.create_import_schedule(
            kitsu_id,
            request,
            mode,
            frequency,
            import_time,
            "Kitsu",
        )
    return redirect("import_data")


@require_POST
def import_yamtrack(request):
    """View for importing anime and manga data from Yamtrack CSV."""
    file = request.FILES.get("yamtrack_csv")

    if not file:
        messages.error(request, "Yamtrack CSV file is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    tasks.import_yamtrack.delay(
        file=request.FILES["yamtrack_csv"],
        user_id=request.user.id,
        mode=mode,
    )
    messages.info(
        request,
        "The task to import media from Yamtrack CSV file has been queued.",
    )
    return redirect("import_data")


@require_POST
def import_hltb(request):
    """View for importing game date from HowLongToBeat."""
    file = request.FILES.get("hltb_csv")

    if not file:
        messages.error(request, "HowLongToBeat CSV file is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    tasks.import_hltb.delay(
        file=request.FILES["hltb_csv"],
        user_id=request.user.id,
        mode=mode,
    )
    messages.info(
        request,
        "The task to import media from HowLongToBeat CSV file has been queued.",
    )
    return redirect("import_data")


@require_POST
def import_imdb(request):
    """View for importing data from IMDB."""
    file = request.FILES.get("imdb_csv")

    if not file:
        messages.error(request, "IMDB CSV file is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    tasks.import_imdb.delay(
        file=request.FILES["imdb_csv"],
        user_id=request.user.id,
        mode=mode,
    )
    messages.info(
        request,
        "The task to import media from IMDB CSV file has been queued.",
    )
    return redirect("import_data")


@require_GET
def export_csv(request):
    """View for exporting all media data to a CSV file."""
    now = timezone.localtime()
    response = StreamingHttpResponse(
        streaming_content=exports.generate_rows(request.user),
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="yamtrack_{now}.csv"'},
    )
    logger.info("User %s started CSV export", request.user.username)
    return response


@login_not_required
@csrf_exempt
@require_POST
def jellyfin_webhook(request, token):
    """Handle Jellyfin webhook notifications for media playback."""
    try:
        user = users.models.User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Jellyfin webhook: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    # Attach User instance so history_user_id is populated
    request.user = user
    data = request.body
    if not data:
        logger.warning("Missing payload in Jellyfin webhook request")
        return HttpResponse("Missing payload", status=400)

    payload = json.loads(data)
    processor = jellyfin.JellyfinWebhookProcessor()
    processor.process_payload(payload, user)
    return HttpResponse(status=200)


@login_not_required
@csrf_exempt
@require_POST
def plex_webhook(request, token):
    """Handle Plex webhook notifications for media playback."""
    try:
        user = users.models.User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Plex webhook: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    # Attach User instance so history_user_id is populated
    request.user = user

    # https://support.plex.tv/hc/en-us/articles/115002267687-Webhooks
    # As stated above, the payload is sent in JSON format inside a multipart
    # HTTP POST request. For the media.play and media.rate events, a second part of
    # the POST request contains a JPEG thumbnail for the media.

    data = request.POST.get("payload")
    if not data:
        logger.warning("Missing payload in Plex webhook request")
        return HttpResponse("Missing payload", status=400)

    payload = json.loads(data)
    processor = plex.PlexWebhookProcessor()
    processor.process_payload(payload, user)
    return HttpResponse(status=200)


@login_not_required
@csrf_exempt
@require_POST
def emby_webhook(request, token):
    """Handle Emby webhook notifications for media playback."""
    try:
        user = users.models.User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Emby webhook: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    # Attach User instance so history_user_id is populated
    request.user = user

    # The payload is sent in JSON format inside a multipart
    # HTTP POST request.

    data = request.POST.get("data")
    if not data:
        logger.warning("Missing payload in Emby webhook request")
        return HttpResponse("Missing payload", status=400)

    payload = json.loads(data)
    processor = emby.EmbyWebhookProcessor()
    processor.process_payload(payload, user)
    return HttpResponse(status=200)
