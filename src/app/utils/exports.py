from app.models import TV, Season, Episode, Movie, Anime, Manga
import csv
import logging


logger = logging.getLogger(__name__)


def export_csv(response, user):
    """Export a CSV file of the user's media."""

    fields = [
        "media_id",
        "media_type",
        "title",
        "image",
        "score",
        "progress",
        "status",
        "start_date",
        "end_date",
        "notes",
        "season_number",
        "episode_number",
        "watch_date",
    ]

    writer = csv.writer(response, quoting=csv.QUOTE_ALL)
    writer.writerow(fields)

    export_model_data(writer, fields, TV.objects.filter(user=user), "tv")
    export_model_data(writer, fields, Movie.objects.filter(user=user), "movie")
    export_model_data(writer, fields, Season.objects.filter(user=user), "season")
    export_model_data(writer, fields, Episode.objects.filter(related_season__user=user), "episode")
    export_model_data(writer, fields, Anime.objects.filter(user=user), "anime")
    export_model_data(writer, fields, Manga.objects.filter(user=user), "manga")

    return response


def export_model_data(writer, fields, queryset, media_type):
    logger.info(f"Adding {media_type}s to CSV")

    for item in queryset:
        # write fields if they exist, otherwise write empty string
        row = [getattr(item, field, "") for field in fields]

        # replace media_type field with the correct value
        row[fields.index("media_type")] = media_type

        if media_type == "episode":
            row[fields.index("media_id")] = item.related_season.media_id
            row[fields.index("title")] = item.related_season.title
            row[fields.index("season_number")] = item.related_season.season_number

        writer.writerow(row)
