from simple_history.utils import bulk_create_with_history


def bulk_chunk_import(media_list, model, user):
    """Bulk import media in chunks.

    Fixes bulk_create_with_history limit
    https://github.com/jazzband/django-simple-history/issues/1216#issuecomment-1903240831
    """
    chunk_size = 500
    for i in range(0, len(media_list), chunk_size):
        bulk_create_with_history(
            media_list[i : i + chunk_size],
            model,
            ignore_conflicts=True,
            default_user=user,
        )
