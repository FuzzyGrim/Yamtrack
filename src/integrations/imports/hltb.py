import logging
from csv import DictReader

from django.apps import apps

import app
import app.providers
from app.models import Media, MediaTypes, Sources
from integrations import helpers

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from CSV file."""
    logger.info("Starting HowLongToBeat import with mode %s", mode)

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    bulk_media = {'game': []}
    imported_counts = {}

    for row in reader:
        add_bulk_media(row, user, bulk_media['game'])

    imported_counts['game'] = import_media(
        MediaTypes.GAME.value,
        bulk_media['game'],
        user,
        mode,
    )

    return imported_counts


def format_time(time):
    """Convert time from text to minutes. Could be '--' or '' or '8:35:30', '46:30' or '32'"""
    if time == '--':
        return None
    if time == '':
        return 0

    parts = time.split(':')
    if len(parts) == 3:  # format: '8:35:30'
        hours, minutes, seconds = parts
        return int(hours) * 60 + int(minutes) + round(int(seconds) / 60)
    elif len(parts) == 2:  # format: '46:30'
        minutes, seconds = parts
        return int(minutes) + round(int(seconds) / 60)
    else:  # format: '32'
        return round(int(time) / 60)


def search_game(row):
    """Search for game and return result if found."""
    results = app.providers.services.search(MediaTypes.GAME.value, row["Title"], 1).get('results', [])
    if not results:
        return None
    return results[0]


def is_duplicate_media(game, bulk_media):
    """Check if media already exists in bulk_media list."""
    return any(game['media_id'] == int(media.item.media_id) for media in bulk_media)


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
        'General': row['General Notes'],
        'Review': row['Review Notes'],
        'Main Story': row['Main Story Notes'],
        'Main + Extras': row['Main + Extras Notes'],
        'Completionist': row['Completionist Notes']
    }

    formatted_notes = [
        f"{prefix}: {text}" for prefix, text in notes_mapping.items()
        if text.strip()
    ]

    return "\n".join(formatted_notes)


def determine_status(row):
    """Determine media status based on row data."""
    status_mapping = {
        'Completed': Media.Status.COMPLETED,
        'Playing': Media.Status.IN_PROGRESS,
        'Backlog': Media.Status.PLANNING,
        'Replay': Media.Status.REPEATING,
        'Retired': Media.Status.DROPPED
    }

    for field, status in status_mapping.items():
        if row[field] == 'X':
            return status.value

    return Media.Status.COMPLETED.value


def create_media_instance(item, user, row, notes):
    """Create media instance with all parameters."""
    progress = format_time(row['Progress'])
    main_story = format_time(row['Main Story'])
    main_extra = format_time(row['Main + Extras'])
    completionist = format_time(row['Completionist'])

    model = apps.get_model(app_label="app", model_name=MediaTypes.GAME.value)

    return model(
        item=item,
        user=user,
        score=int(row['Review']) / 10,
        progress=max([x for x in [progress, main_story, main_extra, completionist] if x is not None], default=0),
        status=determine_status(row),
        repeats=0,
        start_date=row.get('Start Date', None),
        end_date=row.get('Completion Date', None),
        notes=notes,
    )


def add_bulk_media(row, user, bulk_media):
    """Add media to list for bulk creation."""
    game = search_game(row)
    if not game or is_duplicate_media(game, bulk_media):
        return

    item, _ = create_or_update_item(game)
    notes = format_notes(row)
    instance = create_media_instance(item, user, row, notes)
    bulk_media.append(instance)


def import_media(media_type, bulk_data, user, mode):
    """Import media and return number of imported objects."""
    model = apps.get_model(app_label="app", model_name=media_type)
    return helpers.bulk_chunk_import(bulk_data, model, user, mode)
