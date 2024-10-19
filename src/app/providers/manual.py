from app import models


def metadata(media_id):
    """Return the metadata for a manual media item."""
    item = models.Item.objects.get(media_id=media_id, source="manual")
    return {
        "media_id": item.media_id,
        "source": "manual",
        "media_type": item.media_type,
        "title": item.title,
        "max_progress": None,
        "image": item.image,
        "synopsis": "No synopsis available.",
    }
