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


def add_bulk_media(row, user, bulk_media):
    """Add media to list for bulk creation."""
    results = app.providers.services.search(MediaTypes.GAME.value, row["Title"], 1).get('results', [])
    if not results:
        return
    game = results[0]
    print(media.item.media_id for media in bulk_media)
    if any(game['media_id'] == int(media.item.media_id) for media in bulk_media):
        return
    media_type = MediaTypes.GAME.value
    item, _ = app.models.Item.objects.update_or_create(
        media_id=game["media_id"],
        source=Sources.IGDB.value,
        media_type=media_type,
        title=game["title"],
        defaults={
            "title": game["title"],
            "image": game["image"],
        },
    )
    progress = format_time(row['Progress'])
    main_story = format_time(row['Main Story'])
    main_extra = format_time(row['Main + Extras'])
    completionist = format_time(row['Completionist'])
    general_notes = f"General: {row['General Notes']}" if row['General Notes'].strip() else ""
    review_notes = f"Review: {row['Review Notes']}" if row['Review Notes'].strip() else ""
    main_story_notes = f"Main Story: {row['Main Story Notes']}" if row['Main Story Notes'].strip() else ""
    main_extra_notes = f"Main + Extras: {row['Main + Extras Notes']}" if row['Main + Extras Notes'].strip() else ""
    completionist_notes = f"Completionist: {row['Completionist Notes']}" if row['Completionist Notes'].strip() else ""
    notes = "\n".join(filter(None, [
        general_notes,
        review_notes,
        main_story_notes,
        main_extra_notes,
        completionist_notes
    ]))
    if row['Completed'] == 'X':
        status = Media.Status.COMPLETED.value
    elif row['Playing'] == 'X':
        status = Media.Status.IN_PROGRESS.value
    elif row['Backlog'] == 'X':
        status = Media.Status.PLANNING.value
    elif row['Replay'] == 'X':
        status = Media.Status.REPEATING.value
    elif row['Retired'] == 'X':
        status = Media.Status.DROPPED.value
    else:
        status = Media.Status.COMPLETED.value
    formatted_added_date = row.get('Start Date', None) if row.get('Start Date', None) else None
    formatted_end_date = row.get('Completion Date', None) if row.get('Completion Date', None) else None
    model = apps.get_model(app_label="app", model_name=media_type)
    instance = model(
        item=item,
        user=user,
        score=int(row['Review']) / 10,
        progress=max([x for x in [progress, main_story, main_extra, completionist] if x is not None], default=0),
        status=status,
        repeats=0,
        start_date=formatted_added_date,
        end_date=formatted_end_date,
        notes=notes,
    )
    bulk_media.append(instance)


def import_media(media_type, bulk_data, user, mode):
    """Import media and return number of imported objects."""
    model = apps.get_model(app_label="app", model_name=media_type)
    return helpers.bulk_chunk_import(bulk_data, model, user, mode)
