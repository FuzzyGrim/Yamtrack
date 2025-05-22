import logging
from collections import defaultdict
from csv import DictReader
from datetime import datetime

from django.apps import apps
from django.utils import timezone

import app
import app.providers
from app.models import Media, MediaTypes, Sources
from integrations import helpers
from integrations.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from CSV file."""
    logger.info("Starting HowLongToBeat import with mode %s", mode)

    try:
        decoded_file = file.read().decode("utf-8").splitlines()
    except UnicodeDecodeError as e:
        msg = "Invalid file format. Please upload a CSV file."
        raise MediaImportError(msg) from e

    reader = DictReader(decoded_file)

    bulk_media = {MediaTypes.GAME.value: []}
    imported_counts = {}
    warnings = []

    # Track media IDs and their titles from the import file
    media_id_counts = defaultdict(int)
    media_id_titles = defaultdict(list)

    # First pass: identify duplicates
    rows = list(reader)

    try:
        for row in rows:
            game = search_game(row)
            if not game:
                warnings.append(
                    f"{row['Title']}: Couldn't find a game with this title in "
                    f"{Sources.IGDB.label}",
                )
                continue

            media_id = game["media_id"]

            # Count occurrences of each media_id
            media_id_counts[media_id] += 1
            media_id_titles[media_id].append(row["Title"])

        # Second pass: add non-duplicates to bulk_media
        for row in rows:
            game = search_game(row)
            if not game:
                continue  # Already added warning in first pass

            media_id = game["media_id"]

            # Skip if this media_id appears more than once
            if media_id_counts[media_id] > 1:
                continue

            item, _ = create_or_update_item(game)
            notes = format_notes(row)
            instance = create_media_instance(item, user, row, notes)
            bulk_media[MediaTypes.GAME.value].append(instance)

    except Exception as error:
        error_msg = f"Error processing entry: {row}"
        raise MediaImportUnexpectedError(error_msg) from error

    # Add consolidated warnings for duplicates
    for media_id, count in media_id_counts.items():
        if count > 1:
            titles = media_id_titles[media_id]
            title_list = helpers.join_with_commas_and(titles)
            warnings.append(
                f"{title_list}: They were matched to the same ID {media_id} "
                "- none imported",
            )

    imported_counts[MediaTypes.GAME.value] = import_media(
        MediaTypes.GAME.value,
        bulk_media[MediaTypes.GAME.value],
        user,
        mode,
    )

    return imported_counts, "\n".join(warnings) if warnings else None


def format_time(time):
    """Convert time from text to minutes.

    Could be '--' or '' or '8:35:30', '46:30' or '32'.
    """
    if time == "--":
        return None
    if time == "":
        return 0

    parts = time.split(":")
    if len(parts) == 3:  # format: '8:35:30' # noqa: PLR2004
        hours, minutes, seconds = parts
        return int(hours) * 60 + int(minutes) + round(int(seconds) / 60)
    if len(parts) == 2:  # format: '46:30' # noqa: PLR2004
        minutes, seconds = parts
        return int(minutes) + round(int(seconds) / 60)
    # format: '32' secs
    return round(int(time) / 60)


def search_game(row):
    """Search for game and return result if found."""
    results = app.providers.services.search(MediaTypes.GAME.value, row["Title"], 1).get(
        "results",
        [],
    )
    if not results:
        return None
    return results[0]


def create_or_update_item(game):
    """Create or update the item in database."""
    media_type = MediaTypes.GAME.value
    return app.models.Item.objects.update_or_create(
        media_id=game["media_id"],
        source=Sources.IGDB.value,
        media_type=media_type,
        title=game["title"],
        defaults={
            "title": game["title"],
            "image": game["image"],
        },
    )


def format_notes(row):
    """Format all notes with prefixes."""
    notes_mapping = {
        "General": row["General Notes"],
        "Review": row["Review Notes"],
        "Main Story": row["Main Story Notes"],
        "Main + Extras": row["Main + Extras Notes"],
        "Completionist": row["Completionist Notes"],
    }

    formatted_notes = [
        f"{prefix}: {text}" for prefix, text in notes_mapping.items() if text.strip()
    ]

    return "\n".join(formatted_notes)


def determine_status(row):
    """Determine media status based on row data."""
    status_mapping = {
        "Completed": Media.Status.COMPLETED,
        "Playing": Media.Status.IN_PROGRESS,
        "Backlog": Media.Status.PLANNING,
        "Replay": Media.Status.REPEATING,
        "Retired": Media.Status.DROPPED,
    }

    for field, status in status_mapping.items():
        if row[field] == "X":
            return status.value

    return Media.Status.COMPLETED.value


def parse_hltb_date(date_str):
    """Parse HLTB date string (YYYY-MM-DD) into datetime object."""
    if not date_str:
        return None

    return datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=0,
        minute=0,
        second=0,
        tzinfo=timezone.get_current_timezone(),
    )


def create_media_instance(item, user, row, notes):
    """Create media instance with all parameters."""
    progress = format_time(row["Progress"])
    main_story = format_time(row["Main Story"])
    main_extra = format_time(row["Main + Extras"])
    completionist = format_time(row["Completionist"])

    model = apps.get_model(app_label="app", model_name=MediaTypes.GAME.value)
    return model(
        item=item,
        user=user,
        score=int(row["Review"]) / 10,
        progress=max(
            [
                x
                for x in [progress, main_story, main_extra, completionist]
                if x is not None
            ],
            default=0,
        ),
        status=determine_status(row),
        repeats=0,
        start_date=parse_hltb_date(row["Start Date"]),
        end_date=parse_hltb_date(row["Completion Date"]),
        notes=notes,
    )


def import_media(media_type, bulk_data, user, mode):
    """Import media and return number of imported objects."""
    model = apps.get_model(app_label="app", model_name=media_type)
    return helpers.bulk_chunk_import(bulk_data, model, user, mode)
