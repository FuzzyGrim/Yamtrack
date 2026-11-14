"""Scope vocabulary and the view scope map enforced by :class:`api.authentication.HasScope`.

Scopes constrain what an ``IntegrationToken`` may reach. The map is keyed by the
view's ``module.ClassName`` so a new endpoint is invisible to scoped tokens until
it is named here; ``api.tests.test_fork_scope_enforcement`` fails the build when a
routed view is missing, so the omission cannot ship silently.

Legacy ``User.token`` credentials and session logins are unaffected: they carry no
token object and keep ordinary access. Credential disclosure and issuance
require an actual session, including for legacy and first-party tokens.
"""

# Any authenticated credential may reach the endpoint. Used for endpoints that
# carry no user data of their own, or that a client needs before it knows its
# own grants.
ANY_SCOPE = "*any*"

# Ordinary scoped tokens are refused. First-party full-account tokens may pass.
NEVER = "*never*"

# Credential disclosure and issuance require a session, including for the
# first-party app. No bearer credential may create access that outlives itself.
SESSION_ONLY = "*session*"

SCOPE_DESCRIPTIONS = {
    "scrobble:write": "Submit playback events.",
    "progress:read": "Read resume positions and the now-playing state.",
    "progress:write": "Create, update, and clear resume positions.",
    "watchlist:read": "Read saved items, watched state, and consumption history.",
    "watchlist:write": "Change saved items, watched state, tags, and history.",
    "catalog:read": "Read Discover rows, the home surface, search, and the calendar.",
    "catalog:write": "Refresh Discover and the calendar, and hide Discover items.",
    "metadata:read": "Read provider preferences and item metadata.",
    "metadata:write": "Change item metadata, artwork, and provider preferences.",
    "lists:read": "Read lists, list items, collaborators, and list activity.",
    "lists:write": "Change lists, list membership, ordering, and recommendations.",
    "music:read": "Read artists, albums, and tracks.",
    "music:write": "Change music library entries and plays.",
    "podcasts:read": "Read podcast shows and episodes.",
    "podcasts:write": "Change podcast subscriptions and play state.",
    "statistics:read": "Read statistics.",
    "statistics:write": "Trigger a statistics recomputation.",
    "imports:read": "Read import activity.",
    "imports:write": "Start imports.",
    "exports:read": "Download exports.",
    "sync:read": "Read sync connections, change feeds, and conflicts.",
    "sync:write": "Resolve sync conflicts.",
    "user:read": "Read preferences, saved views, sidebar, and notification settings.",
    "user:write": "Change preferences, saved views, sidebar, and notification settings.",
}

ALL_SCOPES = frozenset(SCOPE_DESCRIPTIONS)

# The preset a tracking client (Nuvio, Stremio, Kodi) needs and no more. Kept in
# step with ``integrations.models.DEFAULT_INTEGRATION_SCOPES``.
TRACKING_PRESET = (
    "scrobble:write",
    "progress:read",
    "progress:write",
    "watchlist:read",
    "watchlist:write",
    "catalog:read",
    "sync:read",
)

_R = "watchlist:read"
_W = "watchlist:write"

# module.ClassName -> {HTTP method: scope}
VIEW_SCOPES: dict[str, dict[str, str]] = {
    "api.episode_order_views.EpisodeOrderView": {"GET": _R, "POST": _W},
    "api.fork_views.CollectionView": {"GET": _R, "POST": _W},
    "api.fork_views_collection.CollectionFieldsView": {"GET": _R, "PUT": "user:write"},
    "api.fork_views.CollectionEntryView": {"GET": _R, "PATCH": _W, "DELETE": _W},
    "api.fork_views_detail.CollectionSeasonAuditView": {"PATCH": _W, "DELETE": _W},
    "api.fork_views.MediaCollectionView": {"PUT": _W, "DELETE": _W},
    "api.fork_views.MediaProgressView": {"POST": "progress:write"},
    "api.fork_views.TaskStatusView": {"GET": ANY_SCOPE},
    "api.fork_views_discover.CollectionStatusView": {"GET": _R},
    "api.fork_views_discover.CollectionSeasonView": {"DELETE": _W},
    "api.fork_views_discover.DiscoverRowsView": {"GET": "catalog:read"},
    "api.fork_views_discover.RecommendationsView": {"GET": "catalog:read"},
    "api.fork_views_discover.DiscoverStatusView": {"GET": "catalog:read"},
    "api.fork_views_search.SearchSourcesView": {"GET": "catalog:read"},
    "api.fork_views_search.SearchTypeView": {"PUT": "catalog:write"},
    "api.fork_views_search.SearchSuggestionsView": {"GET": "catalog:read"},
    "api.fork_views_search.SearchResultsView": {"GET": "catalog:read"},
    "api.fork_views_search.SearchParentsView": {"GET": "catalog:read"},
    "api.fork_views_search.ManualEntryView": {"GET": "catalog:read", "POST": NEVER},
    "api.fork_views_search.SearchMusicCreateView": {"POST": "music:write"},
    "api.fork_views_people.PersonView": {"GET": "catalog:read"},
    "api.fork_views_people.StudioView": {"GET": "catalog:read"},
    "api.fork_views_discover.DiscoverRefreshView": {"POST": "catalog:write"},
    "api.fork_views_discover.DiscoverHiddenView": {
        "GET": "catalog:read",
        "POST": "catalog:write",
    },
    "api.fork_views_discover.HomeView": {"GET": "catalog:read"},
    "api.fork_views_discover.HomeRowView": {"GET": "catalog:read"},
    "api.fork_views_discover.HomeRowDirectionView": {"POST": "catalog:write"},
    "api.fork_views_discover.HomeArtworkView": {"GET": "catalog:read"},
    "api.fork_views_integrations.ImportDispatchView": {"POST": "imports:write"},
    "api.fork_views_integrations.ImportActivityView": {"GET": "imports:read", "POST": NEVER},
    "api.fork_views_integrations.ExportCsvView": {"GET": "exports:read"},
    "api.fork_views_integrations.ExportTemplateView": {"GET": "exports:read"},
    "api.fork_views_lists.ListSmartRulesView": {
        "GET": "lists:read",
        "PUT": "lists:write",
    },
    "api.fork_views_lists.ListSmartRuleOptionsView": {"GET": "lists:read"},
    "api.fork_views_lists.ListSmartSyncView": {"POST": "lists:write"},
    "api.fork_views_lists.ListCollaboratorsView": {
        "GET": "lists:read",
        "PUT": "lists:write",
    },
    "api.fork_views_lists.ListViewersView": {
        "GET": "lists:read",
        "PUT": "lists:write",
    },
    "api.fork_views_lists.ListTiersView": {
        "GET": "lists:read",
        "PUT": "lists:write",
    },
    "api.fork_views_lists.ListTierMoveView": {"POST": "lists:write"},
    "api.fork_views_lists.ListTierFillView": {"POST": "lists:write"},
    "api.fork_views_lists.ListTierFillUndoView": {"POST": "lists:write"},
    "api.fork_views_lists.ListItemsReorderView": {"POST": "lists:write"},
    "api.fork_views_lists.ListItemsBulkAddView": {"POST": "lists:write"},
    "api.fork_views_lists.ListItemsOrderView": {"PUT": "lists:write"},
    "api.fork_views_lists.ListItemsRanksView": {"PUT": "lists:write"},
    "api.fork_views_lists.ListRecommendationsView": {
        "GET": "lists:read",
        "POST": "lists:write",
    },
    "api.fork_views_lists.ListRecommendationDecisionView": {"POST": "lists:write"},
    "api.fork_views_lists.ListActivityView": {"GET": "lists:read"},
    "api.fork_views_list_imports.ListImportStatusView": {"GET": "imports:read"},
    "api.fork_views_list_imports.ListImportCsvView": {"POST": "imports:write"},
    # These mixed import actions also connect, disconnect and store provider keys.
    "api.fork_views_list_imports.ListImportMdblistView": {"POST": NEVER},
    "api.fork_views_list_imports.ListImportTraktView": {"POST": NEVER},
    "api.fork_views_list_imports.ListExportCsvView": {"GET": "lists:read"},
    "api.fork_views_metadata.ItemImageView": {"PATCH": "metadata:write"},
    "api.fork_views_metadata.ItemCoverView": {"GET": "metadata:read", "POST": "metadata:write"},
    "api.fork_views_metadata.ItemHardcoverEditionsView": {"GET": "metadata:read", "POST": "metadata:write"},
    "api.fork_views_metadata.ItemMetadataView": {
        "GET": "metadata:read",
        "PATCH": "metadata:write",
    },
    "api.fork_views_metadata.MediaProviderPreferenceView": {
        "GET": "metadata:read",
        "PUT": "metadata:write",
    },
    "api.fork_views_metadata.MediaEpisodeScoreView": {"PATCH": _W},
    "api.fork_views_music.MusicArtistsView": {
        "GET": "music:read",
        "POST": "music:write",
    },
    "api.fork_views_music.MusicArtistDetailView": {
        "GET": "music:read",
        "PATCH": "music:write",
        "DELETE": "music:write",
    },
    "api.fork_views_music.MusicArtistSyncView": {"POST": "music:write"},
    "api.fork_views_music.MusicArtistPlaysView": {"DELETE": "music:write"},
    "api.fork_views_music.MusicAlbumsView": {
        "GET": "music:read",
        "POST": "music:write",
    },
    "api.fork_views_music.MusicAlbumDetailView": {
        "GET": "music:read",
        "PATCH": "music:write",
        "DELETE": "music:write",
    },
    "api.fork_views_music.MusicAlbumTracksView": {"GET": "music:read"},
    "api.fork_views_music.MusicAlbumPlaysView": {"DELETE": "music:write"},
    "api.fork_views_music.MusicSongPlayView": {"POST": "music:write"},
    "api.fork_views_music.MusicTrackScoreView": {"PATCH": "music:write"},
    "api.fork_views_music.MusicBulkPlaysView": {"POST": "music:write"},
    "api.fork_views_music.MusicArtistRelationsView": {"GET": "music:read"},
    "api.fork_views_music.MusicArtistMembersSyncView": {"POST": "music:write"},
    "api.fork_views_music.MusicAlbumSyncView": {"POST": "music:write"},
    "api.fork_views_music.MusicTrackNotesView": {"PATCH": "music:write"},
    "api.fork_views_music.MusicTrackDetailView": {"GET": "music:read"},
    "api.fork_views_music.MusicTrackPlaysView": {"GET": "music:read"},
    "api.fork_views_music.MusicTrackPlayDetailView": {"DELETE": "music:write"},
    "api.fork_views_music.MusicAlbumReleasesView": {
        "GET": "music:read",
        "POST": "music:write",
    },
    "api.fork_views_playback.PlaybackProgressView": {
        "GET": "progress:read",
        "PUT": "progress:write",
        "DELETE": "progress:write",
    },
    "api.fork_views_playback.NowPlayingView": {"GET": "progress:read"},
    # Home playback controls are session-only; scoped integration tokens may
    # not read the cards or invoke their actions.
    "api.fork_views_playback.HomePlaybackCardsView": {
        "GET": NEVER,
        "POST": NEVER,
    },
    "api.fork_views_podcast.PodcastShowsView": {
        "GET": "podcasts:read",
        "POST": "podcasts:write",
    },
    "api.fork_views_podcast.PodcastLookupView": {"GET": "podcasts:read"},
    "api.fork_views_podcast.PodcastShowDetailView": {
        "GET": "podcasts:read",
        "PATCH": "podcasts:write",
        "DELETE": "podcasts:write",
    },
    "api.fork_views_podcast.PodcastShowEpisodesView": {"GET": "podcasts:read"},
    "api.fork_views_podcast.PodcastMarkAllPlayedView": {"POST": "podcasts:write"},
    "api.fork_views_podcast.PodcastEpisodePlayView": {"POST": "podcasts:write"},
    "api.fork_views_podcast.PodcastEpisodePlayDetailView": {
        "GET": "podcasts:read",
        "PATCH": "podcasts:write",
        "DELETE": "podcasts:write",
    },
    "api.fork_views_podcast.PodcastEpisodeNotesView": {"PATCH": "podcasts:write"},
    "api.fork_views_podcast.PodcastShowEpisodeManageView": {
        "GET": "podcasts:read",
        "POST": "podcasts:write",
    },
    "api.fork_views_podcast.PodcastEpisodeDetailView": {
        "PATCH": "podcasts:write",
        "DELETE": "podcasts:write",
    },
    "api.fork_views_podcast.PodcastEpisodeScoreView": {"PATCH": "podcasts:write"},
    "api.fork_views_scrobble.ScrobbleView": {"POST": "scrobble:write"},
    "api.fork_views_statistics.StatisticsOverviewView": {"GET": "statistics:read"},
    "api.fork_views_statistics.StatisticsRefreshView": {"POST": "statistics:write"},
    "api.fork_views_statistics.StatisticsPreferencesView": {
        "GET": "statistics:read",
        "PATCH": "statistics:write",
    },
    "api.fork_views_statistics.StatisticsTalentView": {"GET": "statistics:read"},
    "api.fork_views_statistics.StatisticsFeaturedPersonView": {"POST": "statistics:write"},
    "api.fork_views_tracking.MediaEpisodeWatchView": {"POST": _W, "DELETE": _W},
    "api.fork_views_tracking.MediaMovieWatchView": {"POST": _W, "DELETE": _W},
    "api.fork_views_video.VideoPlayView": {"GET": _R, "POST": _W},
    "api.fork_views_video.VideoPlayDetailView": {"DELETE": _W},
    "api.fork_views_video.VideoGameView": {"GET": _R, "PUT": _W, "DELETE": _W},
    "api.fork_views_tracking.MediaEpisodeDropView": {"POST": _W},
    "api.fork_views_tracking.MediaEpisodeBulkView": {"POST": _W},
    "api.fork_views_consumption_batch.ConsumptionBatchView": {"POST": _W},
    "api.fork_views_consumption_batch.ConsumptionBatchDetailView": {"GET": _R},
    "api.fork_views_library.LibraryFilterOptionsView": {"GET": _R},
    "api.fork_views_library.LibraryItemIdsView": {"GET": _R},
    "api.fork_views_library.LibraryPreferencesView": {"GET": "user:read", "PATCH": "user:write"},
    "api.fork_views_library.LibraryShareView": {"POST": "lists:write"},
    "api.fork_views_library.LibraryBulkStatusView": {"POST": _W},
    "api.fork_views_library.LibraryBulkTagView": {"POST": _W},
    "api.fork_views_tracking.TagsView": {"GET": _R, "POST": _W},
    "api.fork_views_tracking.TagDetailView": {"PATCH": _W, "DELETE": _W},
    "api.fork_views_tracking.MediaTagsView": {"GET": _R, "PUT": _W},
    "api.fork_views_tracking.HistoryView": {"GET": _R},
    "api.fork_views_tracking.MediaStateView": {"GET": _R},
    "api.fork_views_tracking.MediaSeasonStateView": {"GET": _R},
    "api.fork_views_tracking.MediaEpisodeStateView": {"GET": _R},
    "api.fork_views_tracking.HistoryRecordView": {"DELETE": _W},
    "api.fork_views_history.HistoryGenresView": {"GET": _R},
    "api.fork_views_history.HistorySessionsView": {"GET": _R},
    "api.fork_views_history.HistoryDayView": {"GET": _R},
    "api.fork_views_history.HistoryReleaseDaysView": {"GET": _R},
    "api.fork_views_history.HistoryEntryView": {"PATCH": _W, "DELETE": _W},
    "api.fork_views_history.HistorySavedViewsView": {"POST": "user:write"},
    # The links embed the account token (full access), like token rotation.
    "api.fork_views_calendar.CalendarLinksView": {"GET": SESSION_ONLY},
    "api.fork_views_calendar.CalendarStatusesView": {"GET": "catalog:read"},
    "api.fork_views_watched_state.WatchedStateView": {"GET": _R, "PUT": _W},
    "api.fork_views_watched_state.WatchedStateChangeFeedView": {"GET": "sync:read"},
    "api.fork_views_progress_changes.ProgressChangeFeedView": {"GET": "sync:read"},
    "api.fork_views_sync.SyncBootstrapView": {"POST": "sync:read"},
    "api.fork_views_sync.SyncSnapshotView": {"GET": "sync:read"},
    "api.fork_views_watched_state.SyncConnectionsView": {"GET": "sync:read"},
    "api.fork_views_watched_state.SyncConflictsView": {"GET": "sync:read"},
    "api.fork_views_watched_state.SyncConflictResolveView": {"POST": "sync:write"},
    "api.fork_views_metadata.MediaMetadataView": {"GET": "metadata:read"},
    "api.fork_views_metadata.MediaSeasonMetadataView": {"GET": "metadata:read"},
    "api.fork_views_metadata.MediaEpisodeMetadataView": {"GET": "metadata:read"},
    "api.fork_views_detail.MediaCollectionEntriesView": {"GET": _R},
    "api.fork_views_detail.MediaSeasonCollectionEntriesView": {"GET": _R},
    "api.fork_views_detail.MediaEpisodeCollectionEntriesView": {"GET": _R},
    "api.fork_views_detail.MediaProgressCardView": {"GET": _R},
    "api.fork_views_detail.MediaActivityTimelineView": {"GET": _R},
    "api.fork_views_detail.MediaSeasonActivityTimelineView": {"GET": _R},
    "api.fork_views_detail.MediaEpisodeActivityTimelineView": {"GET": _R},
    "api.fork_views_detail.MediaScoresView": {"GET": _R},
    "api.fork_views_detail.MediaIssuesView": {"GET": _R},
    "api.fork_views_detail.MediaAnimeEpisodesView": {"GET": _R},
    "api.fork_views_library.LibraryGameGroupsView": {"GET": _R},
    "api.fork_views_detail.MediaSeasonScoresView": {"GET": _R},
    "api.fork_views_detail.MediaSeasonProgressCardView": {"GET": _R},
    "api.fork_views_detail.MediaCheckInView": {"GET": _R, "POST": _W},
    "api.fork_views_detail.MediaShowCollectView": {"PUT": _W},
    "api.fork_views_detail.MediaSeasonCollectView": {"PUT": _W},
    "api.fork_views_detail.MediaSeerrRequestView": {
        "GET": "catalog:read",
        "POST": "catalog:write",
    },
    "api.fork_views_detail.DetailPageView": {"GET": "watchlist:read"},
    "api.fork_views_detail.DetailGalleryView": {"GET": "metadata:read"},
    "api.fork_views_users.UserNavigationView": {"GET": "user:read"},
    "api.fork_views_users.UserPreferencesView": {
        "GET": "user:read",
        "PATCH": "user:write",
    },
    "api.fork_views_onboarding.UserOnboardingView": {
        "GET": "user:read",
        "POST": "user:write",
    },
    "api.fork_views_users.UserSidebarView": {
        "GET": "user:read",
        "PUT": "user:write",
    },
    "api.fork_views_users.UserNotificationsView": {
        "GET": "user:read",
        "PATCH": "user:write",
    },
    "api.fork_views_users.UserNotificationExclusionsView": {
        "GET": "user:read",
        "POST": "user:write",
        "DELETE": "user:write",
    },
    "api.fork_views_users.UserNotificationTestView": {"POST": "user:write"},
    # Rotating the account token would let a scoped token mint an unscoped one.
    "api.fork_views_users.UserTokenRegenerateView": {"POST": SESSION_ONLY},
    "api.fork_views_settings.UserPreferencesChoicesView": {"GET": "user:read"},
    "api.fork_views_settings.UserAppearanceView": {"GET": "user:read", "PUT": "user:write"},
    "api.fork_views_settings.UserCardsView": {"GET": "user:read", "PUT": "user:write"},
    "api.fork_views_settings.UserCardPreviewView": {"POST": "user:read"},
    "api.fork_views_settings.UserHomeScreenView": {"GET": "user:read", "PUT": "user:write"},
    "api.fork_views_settings.UserHomeScreenListsView": {"GET": "user:read"},
    "api.fork_views_settings.UserHomeScreenFilterFieldsView": {"GET": "user:read"},
    "api.fork_views_settings.UserTokenView": {"GET": SESSION_ONLY},
    # Password, authenticator and account deletion: session or the Floppy app only.
    "api.fork_views_settings.UserAccountView": {"GET": NEVER, "POST": NEVER},
    "api.fork_views_settings.UserIntegrationTokensView": {"GET": NEVER, "POST": SESSION_ONLY},
    "api.fork_views_settings.UserIntegrationTokenDetailView": {"DELETE": NEVER},
    "api.fork_views_settings.UserCatalogGrantsView": {"GET": NEVER, "POST": SESSION_ONLY},
    "api.fork_views_settings.UserCatalogGrantDetailView": {"DELETE": NEVER},
    "api.fork_views_settings.UserOAuthApplicationsView": {"GET": NEVER},
    "api.fork_views_settings.UserOAuthApplicationDetailView": {"DELETE": NEVER},
    "api.fork_views_settings.ManagedUsersView": {"GET": NEVER, "POST": SESSION_ONLY},
    "api.fork_views_settings.ManagedUserDetailView": {"PATCH": SESSION_ONLY, "DELETE": NEVER},
    "api.fork_views_settings.ManagedUserPasswordView": {"POST": SESSION_ONLY},
    "api.fork_views_settings.AdvancedSettingsView": {"GET": NEVER},
    "api.fork_views_settings.ImageCacheView": {"PUT": NEVER, "DELETE": NEVER},
    "api.fork_views_settings.CacheClearView": {"DELETE": NEVER},
    "api.fork_views_settings.ServerPortView": {"PUT": NEVER, "DELETE": NEVER},
    "api.fork_views_settings.LogsExportView": {"GET": NEVER},
    "api.fork_views_settings.DiagnosticsExportView": {"GET": NEVER},
    "api.fork_views_settings.AboutSettingsView": {"GET": "user:read"},
    "api.fork_views_settings.WhatsNewView": {"GET": "user:read"},
    "api.fork_views_settings.ExportSchedulesView": {"GET": "exports:read", "POST": NEVER, "DELETE": NEVER},
    "api.fork_views_settings.ExportHistoryView": {"GET": "exports:read"},
    "api.fork_views_settings.ExportBackupStatusView": {"GET": NEVER},
    "api.fork_views_settings.LetterboxdExportView": {"GET": "exports:read"},
    "api.fork_views_settings.UserImportSettingsView": {"GET": "user:read", "PUT": "user:write"},
    "api.fork_views_settings.RestoreBackupView": {"POST": NEVER},
    "api.fork_views_settings.UserRssView": {"GET": SESSION_ONLY},
    "api.fork_views_settings.UserRssPreviewView": {"GET": NEVER},
    # Provider, connection and match-review routes carry or act on credentials: session only.
    "api.fork_views_providers.ProvidersView": {"GET": NEVER},
    "api.fork_views_providers.ProviderPinView": {"POST": NEVER},
    "api.fork_views_providers.ProviderDetailView": {"GET": NEVER},
    "api.fork_views_providers.ProviderConnectView": {"POST": NEVER},
    "api.fork_views_provider_connect.ProviderConnectStartView": {"POST": NEVER},
    "api.fork_views_provider_connect.ProviderConnectPollView": {"GET": NEVER},
    "api.fork_views_provider_connect.ProviderConnectCancelView": {"POST": NEVER},
    "api.fork_views_providers.ProviderFormView": {"POST": NEVER},
    "api.fork_views_providers.ProviderFileImportView": {"POST": NEVER},
    "api.fork_views_providers.ProviderMetadataActionView": {"POST": NEVER},
    "api.fork_views_providers.ProviderDisconnectView": {"POST": NEVER},
    "api.fork_views_providers.ProviderCredentialsView": {"PUT": NEVER, "DELETE": NEVER},
    "api.fork_views_providers.ProviderTestView": {"POST": NEVER},
    "api.fork_views_providers.ProviderSyncView": {"POST": NEVER},
    "api.fork_views_providers.ProviderImportView": {"POST": NEVER},
    "api.fork_views_providers.ProviderSchedulesView": {"GET": NEVER, "DELETE": NEVER},
    "api.fork_views_providers.ConnectionsView": {"GET": NEVER},
    "api.fork_views_providers.DeviceApprovalView": {"GET": NEVER, "POST": SESSION_ONLY},
    "api.fork_views_providers.ConnectionDirectionView": {"POST": NEVER},
    "api.fork_views_providers.ConnectionPauseView": {"POST": NEVER},
    "api.fork_views_providers.MetadataReviewQueueView": {"GET": NEVER},
    "api.fork_views_providers.MetadataReviewReferenceView": {"POST": NEVER},
    "api.fork_views_providers.MetadataMatchCandidatesView": {"GET": NEVER},
    "api.fork_views_providers.MetadataMatchPreviewView": {"POST": NEVER},
    "api.fork_views_providers.MetadataMatchApplyView": {"POST": NEVER},
    "api.fork_views_settings.LibraryMaintenanceView": {"GET": "user:read", "POST": NEVER},
    "api.listenbrainz_views.SubmitListensView": {"POST": "scrobble:write"},
    "api.listenbrainz_views.UserListensView": {"GET": "music:read"},
    "api.listenbrainz_views.UserArtistsView": {"GET": "music:read"},
    "api.listenbrainz_views.UserReleaseGroupsView": {"GET": "music:read"},
    "api.listenbrainz_views.UserRecordingsView": {"GET": "music:read"},
    "api.listenbrainz_views.UserGenreActivityView": {"GET": "music:read"},
    # A client must be able to check its own credential before it knows its grants.
    "api.listenbrainz_views.ValidateTokenView": {"GET": ANY_SCOPE},
    "api.schema.LiveSchemaView": {"GET": ANY_SCOPE},
    "api.views.HealthView": {"GET": ANY_SCOPE},
    "api.views.InfoView": {"GET": ANY_SCOPE},
    "api.views.CalendarView": {"GET": "catalog:read"},
    "api.views.CalendarUpdateView": {"POST": "catalog:write"},
    "api.views.SearchProviderView": {"GET": "catalog:read"},
    "api.views.StatisticsView": {"GET": "statistics:read"},
    "api.views.MediaListView": {"GET": _R},
    "api.views.MediaTypeListView": {"GET": _R, "POST": _W},
    "api.fork_views_library_changes.LibraryActivityView": {"GET": _R},
    "api.fork_views_library_changes.LibraryDeletedView": {"GET": _R},
    "api.fork_views_library.LibrarySavedViewsView": {"GET": "user:read", "POST": "user:write"},
    "api.fork_views_library.LibrarySavedViewDetailView": {"GET": "user:read", "DELETE": "user:write"},
    "api.fork_views_library.LibrarySavedViewsReorderView": {"POST": "user:write"},
    "api.fork_views_library.LibraryBulkCollectionView": {"POST": _W},
    "api.fork_views_library.LibraryBulkPlaysView": {"POST": _W},
    "api.fork_views_library.LibraryBulkPlaysStatusView": {"GET": _R},
    "api.views.MediaDetailView": {"GET": _R, "PATCH": _W, "DELETE": _W},
    "api.views.MediaSeasonsView": {"GET": _R},
    "api.views.MediaSeasonDetailView": {"GET": _R, "PATCH": _W, "DELETE": _W},
    "api.views.MediaSeasonEpisodesView": {"GET": _R},
    "api.views.MediaEpisodeDetailView": {"GET": _R, "PATCH": _W, "DELETE": _W},
    "api.views.MediaRecommendationsView": {"GET": "catalog:read"},
    "api.views.MediaChangesHistoryView": {"GET": _R},
    "api.views.MediaSeasonChangesHistoryView": {"GET": _R},
    "api.views.MediaEpisodeChangesHistoryView": {"GET": _R},
    "api.views.MediaTypeChangesHistoryDetailView": {"GET": _R, "DELETE": _W},
    "api.views.MediaConsumptionHistoryView": {"GET": _R},
    "api.views.MediaConsumptionEntryDetailView": {
        "GET": _R,
        "PATCH": _W,
        "DELETE": _W,
    },
    "api.views.MediaSeasonConsumptionHistoryView": {"GET": _R},
    "api.views.MediaSeasonConsumptionEntryDetailView": {
        "GET": _R,
        "PATCH": _W,
        "DELETE": _W,
    },
    "api.views.MediaEpisodeConsumptionHistoryView": {"GET": _R},
    "api.views.MediaEpisodeConsumptionEntryDetailView": {
        "GET": _R,
        "PATCH": _W,
        "DELETE": _W,
    },
    # Sync refreshes provider metadata for the item.
    "api.views.MediaSyncView": {"POST": "metadata:write"},
    "api.views.MediaSeasonSyncView": {"POST": "metadata:write"},
    "api.views.MediaEpisodeSyncView": {"POST": "metadata:write"},
    "api.views.ListsView": {"GET": "lists:read", "POST": "lists:write"},
    "api.views.ListDetailView": {
        "GET": "lists:read",
        "PATCH": "lists:write",
        "DELETE": "lists:write",
    },
    "api.views.ListItemsView": {"GET": "lists:read"},
    "api.views.ListItemView": {"GET": "lists:read", "DELETE": "lists:write"},
    "api.views.MediaListsView": {"GET": "lists:read"},
    "api.views.MediaListDetailView": {"PUT": "lists:write", "DELETE": "lists:write"},
    "api.views.MediaSeasonListsView": {"GET": "lists:read"},
    "api.views.MediaSeasonListDetailView": {
        "PUT": "lists:write",
        "DELETE": "lists:write",
    },
    "api.views.MediaEpisodeListsView": {"GET": "lists:read"},
    "api.views.MediaEpisodeListDetailView": {
        "PUT": "lists:write",
        "DELETE": "lists:write",
    },
}


def view_key(view) -> str:
    """Return the ``module.ClassName`` key used by :data:`VIEW_SCOPES`."""
    cls = type(view)
    return f"{cls.__module__}.{cls.__name__}"


def resolve_required_scope(view, method: str) -> str | None:
    """Return the scope a scoped token needs for ``method`` on ``view``.

    Returns :data:`ANY_SCOPE` when any valid token is enough, :data:`NEVER` when
    no scoped token may pass, :data:`SESSION_ONLY` for session authentication only,
    and ``None`` when the endpoint is unmapped. An
    unmapped endpoint is denied to scoped tokens; see the module docstring.
    """
    method = "GET" if method.upper() == "HEAD" else method.upper()
    explicit = getattr(view, "required_scopes", None)
    if explicit:
        return explicit.get(method.upper())
    single = getattr(view, "required_scope", None)
    if single:
        return single
    mapped = VIEW_SCOPES.get(view_key(view))
    if mapped is None:
        return None
    return mapped.get(method.upper())
