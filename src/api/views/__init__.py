from .calendar import (
    CalendarUpdateView,
    CalendarView,
)
from .episodes import (
    MediaEpisodeChangesHistoryView,
    MediaEpisodeConsumptionEntryDetailView,
    MediaEpisodeConsumptionHistoryView,
    MediaEpisodeDetailView,
    MediaEpisodeListDetailView,
    MediaEpisodeListsView,
    MediaEpisodeSyncView,
)
from .lists import (
    ListDetailView,
    ListItemsView,
    ListItemView,
    ListsView,
)
from .media import (
    MediaChangesHistoryView,
    MediaConsumptionEntryDetailView,
    MediaConsumptionHistoryView,
    MediaDetailView,
    MediaListDetailView,
    MediaListsView,
    MediaListView,
    MediaRecommendationsView,
    MediaSeasonsView,
    MediaSyncView,
    MediaTypeChangesHistoryDetailView,
    MediaTypeListView,
)
from .search import (
    SearchProviderView,
)
from .seasons import (
    MediaSeasonChangesHistoryView,
    MediaSeasonConsumptionEntryDetailView,
    MediaSeasonConsumptionHistoryView,
    MediaSeasonDetailView,
    MediaSeasonEpisodesView,
    MediaSeasonListDetailView,
    MediaSeasonListsView,
    MediaSeasonSyncView,
)
from .statistics import (
    StatisticsView,
)
from .system import (
    HealthView,
    InfoView,
)

# TODO!: check sorters and filters in paginate_data since data is not serialized yet. Maybe data should be serialized first and then sorted/paginated later?? Sorting/filtering should occur at db search level, pagination should be done right after, always at the db search level, then the data should be serialized.  # noqa: E501, W505

# TODO!: for children items, it should return an error if user is trying to access a non existing season/episode (for example if it's requested the season 4 of a 2 season show)  # noqa: E501, W505

# TODO: Implement search for already tracked media (item_id and tracked fields)  # noqa: E501, FIX002, W505

# TODO: Implement global search endpoint for every media_type  # noqa: E501, FIX002, W505

# TODO: Implement admin commands to manage users (add admins, remove/add users, etc)  # noqa: E501, FIX002, W505

# TODO: Move operations on db to `models` file of the relative django app  # noqa: E501, FIX002, W505

# TODO!!: since it's possible to add to lists untracked items, the id field can be null, so it's impossible to get these elements from the list, while it should be possible. The untracked added element is in the Items table, but not in the media tables. Add the list of lists an item is in to the model of the medias, so they can be retrieved and computed easily.  # noqa: E501, FIX002, W505

# TODO: look into django.core.paginator Paginator  # noqa: FIX002

# TODO: Review children endpoints performance and avoid repeated list lookups per item.  # noqa: E501, FIX002, W505

__all__ = [
    "CalendarUpdateView",
    "CalendarView",
    "HealthView",
    "InfoView",
    "ListDetailView",
    "ListItemView",
    "ListItemsView",
    "ListsView",
    "MediaChangesHistoryView",
    "MediaConsumptionEntryDetailView",
    "MediaConsumptionHistoryView",
    "MediaDetailView",
    "MediaEpisodeChangesHistoryView",
    "MediaEpisodeConsumptionEntryDetailView",
    "MediaEpisodeConsumptionHistoryView",
    "MediaEpisodeDetailView",
    "MediaEpisodeListDetailView",
    "MediaEpisodeListsView",
    "MediaEpisodeSyncView",
    "MediaListDetailView",
    "MediaListView",
    "MediaListsView",
    "MediaRecommendationsView",
    "MediaSeasonChangesHistoryView",
    "MediaSeasonConsumptionEntryDetailView",
    "MediaSeasonConsumptionHistoryView",
    "MediaSeasonDetailView",
    "MediaSeasonEpisodesView",
    "MediaSeasonListDetailView",
    "MediaSeasonListsView",
    "MediaSeasonSyncView",
    "MediaSeasonsView",
    "MediaSyncView",
    "MediaTypeChangesHistoryDetailView",
    "MediaTypeListView",
    "SearchProviderView",
    "StatisticsView",
]
