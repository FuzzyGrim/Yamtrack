import XCTest
@testable import Spine

final class SpineTests: XCTestCase {
    func testMediaTypeThemeLookup() {
        let movie = MediaTypeTheme.theme(for: "movie")
        let boardGame = MediaTypeTheme.theme(for: "boardgame")
        let anime = MediaTypeTheme.theme(for: "anime")

        XCTAssertEqual(movie.displayName, "Movies")
        XCTAssertEqual(movie.symbolName, "film")
        XCTAssertEqual(movie.gradientColors.count, 2)
        XCTAssertEqual(boardGame.displayName, "Board Games")
        XCTAssertEqual(boardGame.symbolName, "dice.fill")
        XCTAssertEqual(anime.symbolText, "オ")
        XCTAssertEqual(movie.gradientColors, boardGame.gradientColors)
    }

    func testMediaTypeThemeUnknownFallback() {
        let theme = MediaTypeTheme.theme(for: "podcast")

        XCTAssertEqual(theme.slug, "podcast")
        XCTAssertEqual(theme.displayName, "Podcast")
        XCTAssertEqual(theme.symbolName, "square.grid.2x2")
        XCTAssertEqual(theme.gradientColors.count, 2)
    }

    @MainActor
    func testSearchLensMediaTypesExcludeEpisodesAndSeasons() {
        let types = SearchViewModel.lensMediaTypes(from: ["movie", "episode", "season", "book"])
        let fallback = SearchViewModel.lensMediaTypes(from: ["episode", "season"])

        XCTAssertEqual(types, ["movie", "book"])
        XCTAssertFalse(fallback.contains("episode"))
        XCTAssertFalse(fallback.contains("season"))
    }

    @MainActor
    func testLibraryMediaTypesExcludeEpisodesAndSeasons() {
        let types = LibraryViewModel.libraryMediaTypes(from: ["movie", "episode", "season", "book"])
        let fallback = LibraryViewModel.libraryMediaTypes(from: ["episode", "season"])

        XCTAssertEqual(types, ["movie", "book"])
        XCTAssertFalse(fallback.contains("episode"))
        XCTAssertFalse(fallback.contains("season"))
    }

    @MainActor
    func testLibraryViewModelSeparatesPlanningFromTrackedItems() async {
        let repository = ScriptedLibraryTrackingRepository(responses: [
            "movie:": PagedResponse(
                count: 2,
                next: nil,
                previous: nil,
                results: [
                    libraryItem(id: "1", title: "Watched", status: "Completed"),
                    libraryItem(id: "2", title: "Planned", status: "Planning")
                ]
            )
        ])
        let viewModel = LibraryViewModel(
            mediaRepository: FakeMediaRepository(),
            trackingRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.reload()
        XCTAssertEqual(viewModel.displayedItems.map(\.media.title), ["Watched"])

        viewModel.shelf = .planning
        await viewModel.reload()
        XCTAssertEqual(viewModel.displayedItems.map(\.media.title), ["Planned"])
        XCTAssertEqual(repository.requests, [
            LibraryTrackingRequest(mediaType: "movie", page: nil),
            LibraryTrackingRequest(mediaType: "movie", page: nil, status: "Planning")
        ])
    }

    @MainActor
    func testLibraryViewModelAppendsNextPageWithoutDuplicates() async {
        let repository = ScriptedLibraryTrackingRepository(responses: [
            "movie:": PagedResponse(
                count: 3,
                next: "https://spine.test/api/v1/tracking/?media_type=movie&page=2",
                previous: nil,
                results: [
                    libraryItem(id: "1", title: "One"),
                    libraryItem(id: "2", title: "Two")
                ]
            ),
            "movie:2": PagedResponse(
                count: 3,
                next: nil,
                previous: "https://spine.test/api/v1/tracking/?media_type=movie",
                results: [
                    libraryItem(id: "2", title: "Two"),
                    libraryItem(id: "3", title: "Three")
                ]
            )
        ])
        let viewModel = LibraryViewModel(
            mediaRepository: FakeMediaRepository(),
            trackingRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.reload()
        await viewModel.loadNextPage()

        XCTAssertEqual(viewModel.items.map(\.media.title), ["One", "Two", "Three"])
        XCTAssertEqual(viewModel.totalCount, 3)
        XCTAssertFalse(viewModel.hasMorePages)
        XCTAssertEqual(repository.requests, [
            LibraryTrackingRequest(mediaType: "movie", page: nil),
            LibraryTrackingRequest(mediaType: "movie", page: "2")
        ])
    }

    @MainActor
    func testLibraryViewModelIgnoresStaleMediaTypeResponses() async {
        let repository = DelayedLibraryTrackingRepository(responses: [
            "tv": (
                delay: .milliseconds(80),
                response: PagedResponse(count: 1, next: nil, previous: nil, results: [libraryItem(id: "tv", title: "Slow TV", mediaType: "tv")])
            ),
            "book": (
                delay: .milliseconds(1),
                response: PagedResponse(count: 1, next: nil, previous: nil, results: [libraryItem(id: "book", title: "Fast Book", mediaType: "book")])
            )
        ])
        let viewModel = LibraryViewModel(
            mediaRepository: FakeMediaRepository(),
            trackingRepository: repository,
            onUnauthorized: {}
        )

        viewModel.mediaType = "tv"
        let staleLoad = Task { await viewModel.reload() }
        try? await Task.sleep(for: .milliseconds(10))
        await viewModel.selectMediaType("book")
        await staleLoad.value

        XCTAssertEqual(viewModel.mediaType, "book")
        XCTAssertEqual(viewModel.items.map(\.media.title), ["Fast Book"])
        XCTAssertEqual(viewModel.totalCount, 1)
    }

    @MainActor
    func testLibraryViewModelPassesSearchQuery() async {
        let repository = ScriptedLibraryTrackingRepository(responses: [:])
        let viewModel = LibraryViewModel(
            mediaRepository: FakeMediaRepository(),
            trackingRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.setSearchQuery("  Dune  ")

        XCTAssertEqual(viewModel.query, "Dune")
        XCTAssertEqual(repository.requests, [
            LibraryTrackingRequest(mediaType: "movie", page: nil, query: "Dune")
        ])
    }

    @MainActor
    func testLibraryViewModelClearsSearchQuery() async {
        let repository = ScriptedLibraryTrackingRepository(responses: [:])
        let viewModel = LibraryViewModel(
            mediaRepository: FakeMediaRepository(),
            trackingRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.setSearchQuery("Dune")
        await viewModel.clearSearch()

        XCTAssertEqual(viewModel.query, "")
        XCTAssertEqual(repository.requests, [
            LibraryTrackingRequest(mediaType: "movie", page: nil, query: "Dune"),
            LibraryTrackingRequest(mediaType: "movie", page: nil)
        ])
    }

    @MainActor
    func testLibraryViewModelKeepsSearchQueryAcrossMediaTypeAndShelfChanges() async {
        let repository = ScriptedLibraryTrackingRepository(responses: [:])
        let viewModel = LibraryViewModel(
            mediaRepository: FakeMediaRepository(),
            trackingRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.setSearchQuery("Halo")
        await viewModel.selectMediaType("game")
        viewModel.shelf = .planning
        await viewModel.reload()

        XCTAssertEqual(repository.requests, [
            LibraryTrackingRequest(mediaType: "movie", page: nil, query: "Halo"),
            LibraryTrackingRequest(mediaType: "game", page: nil, query: "Halo"),
            LibraryTrackingRequest(mediaType: "game", page: nil, status: "Planning", query: "Halo")
        ])
    }

    @MainActor
    func testLibraryViewModelIgnoresStaleSearchResponses() async {
        let repository = DelayedLibraryTrackingRepository(responses: [
            "movie:Dune": (
                delay: .milliseconds(80),
                response: PagedResponse(count: 1, next: nil, previous: nil, results: [libraryItem(id: "1", title: "Dune")])
            ),
            "movie:Halo": (
                delay: .milliseconds(1),
                response: PagedResponse(count: 1, next: nil, previous: nil, results: [libraryItem(id: "2", title: "Halo")])
            )
        ])
        let viewModel = LibraryViewModel(
            mediaRepository: FakeMediaRepository(),
            trackingRepository: repository,
            onUnauthorized: {}
        )

        let staleSearch = Task { await viewModel.setSearchQuery("Dune") }
        try? await Task.sleep(for: .milliseconds(10))
        await viewModel.setSearchQuery("Halo")
        await staleSearch.value

        XCTAssertEqual(viewModel.query, "Halo")
        XCTAssertEqual(viewModel.items.map(\.media.title), ["Halo"])
        XCTAssertEqual(viewModel.totalCount, 1)
    }

    func testInProgressLoaderMapsMediaTypesAndSortsWithLimit() {
        let profile = profileFixture(hof: [:], enabledMediaTypes: ["movie", "tv", "episode", "book"])

        XCTAssertEqual(InProgressLibraryLoader.mediaTypes(from: profile), ["movie", "season", "book"])

        let sorted = InProgressLibraryLoader.limitedSortedItems([
            libraryItem(id: "1", title: "Older", updatedAt: "2026-06-20T10:00:00Z"),
            libraryItem(id: "2", title: "Newest", updatedAt: "2026-06-21T10:00:00.000Z"),
            libraryItem(id: "3", title: "No Date")
        ], limit: 2)

        XCTAssertEqual(sorted.map(\.media.title), ["Newest", "Older"])
    }

    @MainActor
    func testHomeViewModelLoadsProfileInProgressAndActivity() async throws {
        let profile = profileFixture(hof: [:], enabledMediaTypes: ["movie"])
        let tracking = ScriptedLibraryTrackingRepository(responses: [
            "movie:": PagedResponse(
                count: 1,
                next: nil,
                previous: nil,
                results: [libraryItem(id: "1", title: "Watching", status: "In progress")]
            )
        ])
        let activity = ScriptedHomeActivityRepository(items: [activityItem(id: 1, title: "Logged")])
        let viewModel = HomeViewModel(
            profileRepository: HallOfFameProfileRepository(profile: profile, setResponse: [:], clearResponse: [:]),
            trackingRepository: tracking,
            activityRepository: activity,
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.profile?.username, "mobile")
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Watching"])
        XCTAssertEqual(viewModel.activityItems.compactMap(\.media?.title), ["Logged"])
        XCTAssertEqual(tracking.requests, [LibraryTrackingRequest(mediaType: "movie", page: nil, status: "In progress")])
        XCTAssertEqual(activity.requests, [ActivityRequest(username: "mobile", limit: 6)])
        XCTAssertFalse(viewModel.isLoading)
        XCTAssertNil(viewModel.profileErrorMessage)
        XCTAssertNil(viewModel.inProgressErrorMessage)
        XCTAssertNil(viewModel.activityErrorMessage)
    }

    @MainActor
    func testHomeViewModelExposesEmptyState() async {
        let profile = profileFixture(hof: [:], enabledMediaTypes: ["movie"])
        let viewModel = HomeViewModel(
            profileRepository: HallOfFameProfileRepository(profile: profile, setResponse: [:], clearResponse: [:]),
            trackingRepository: ScriptedLibraryTrackingRepository(responses: [:]),
            activityRepository: ScriptedHomeActivityRepository(items: []),
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.inProgressItems.count, 0)
        XCTAssertEqual(viewModel.activityItems.count, 0)
        XCTAssertNil(viewModel.profileErrorMessage)
        XCTAssertNil(viewModel.inProgressErrorMessage)
        XCTAssertNil(viewModel.activityErrorMessage)
    }

    @MainActor
    func testHomeViewModelUnauthorizedCallsHandler() async {
        let profile = profileFixture(hof: [:], enabledMediaTypes: ["movie"])
        var unauthorizedCount = 0
        let viewModel = HomeViewModel(
            profileRepository: HallOfFameProfileRepository(profile: profile, setResponse: [:], clearResponse: [:]),
            trackingRepository: ThrowingTrackingRepository(error: APIError.unauthorized),
            activityRepository: ScriptedHomeActivityRepository(items: []),
            onUnauthorized: { unauthorizedCount += 1 }
        )

        await viewModel.load()

        XCTAssertEqual(unauthorizedCount, 1)
        XCTAssertNotNil(viewModel.inProgressErrorMessage)
        XCTAssertFalse(viewModel.isLoading)
    }

    func testRecentSearchesKeepMediaTypeAndReadLegacyText() throws {
        let searches = [
            RecentSearch(text: "Halo", mediaType: "game"),
            RecentSearch(text: "Dune", mediaType: "book")
        ]
        let data = try JSONEncoder().encode(searches)
        let encoded = String(data: data, encoding: .utf8)!
        let decoded = RecentSearch.decodeList(from: encoded, fallbackMediaType: "movie")
        let legacy = RecentSearch.decodeList(from: "[\"Alien\"]", fallbackMediaType: "movie")

        XCTAssertEqual(decoded, searches)
        XCTAssertEqual(legacy, [RecentSearch(text: "Alien", mediaType: "movie")])
        XCTAssertTrue(RecentSearch(text: "HALO", mediaType: "game").matches(searches[0]))
        XCTAssertFalse(RecentSearch(text: "HALO", mediaType: "movie").matches(searches[0]))
    }

    @MainActor
    func testMediaLensStorePersistsSelectionAndIgnoresNoOp() {
        let defaults = UserDefaults(suiteName: "MediaLensStoreTests")!
        defaults.removeObject(forKey: MediaLensStore.persistenceKey)

        let store = MediaLensStore(defaults: defaults)
        XCTAssertEqual(store.selectedMediaType, "movie")

        store.setMediaType("game")
        store.setMediaType("game")

        XCTAssertEqual(store.selectedMediaType, "game")
        XCTAssertEqual(defaults.string(forKey: MediaLensStore.persistenceKey), "game")
        XCTAssertEqual(MediaLensStore(defaults: defaults).selectedMediaType, "game")

        defaults.removeObject(forKey: MediaLensStore.persistenceKey)
    }

    func testMediaDiscoverRequestBuildsQueryItems() {
        let request = MediaDiscoverRequest(
            mediaType: "game",
            source: "igdb",
            filter: .platform("PlayStation 5"),
            page: "2",
            pageSize: 24
        )
        let query = Dictionary(uniqueKeysWithValues: request.queryItems.map { ($0.name, $0.value) })

        XCTAssertEqual(query["media_type"]!, "game")
        XCTAssertEqual(query["source"]!, "igdb")
        XCTAssertEqual(query["platform"]!, "PlayStation 5")
        XCTAssertEqual(query["sort"]!, "vote_count")
        XCTAssertEqual(query["page"]!, "2")
        XCTAssertEqual(query["page_size"]!, "24")
        XCTAssertEqual(request.title, "PlayStation 5 · Games")
    }

    func testMediaDiscoverRequestBuildsBookDetailPillRequests() {
        let ref = MediaRef(itemId: nil, source: "openlibrary", mediaType: "book", mediaId: "OL27448M", seasonNumber: nil, episodeNumber: nil)
        let genre = MediaDiscoverRequest.detailPillRequest(ref: ref, filter: .genre("Fiction"))
        let year = MediaDiscoverRequest.detailPillRequest(ref: ref, filter: .year("1965"))
        let platform = MediaDiscoverRequest.detailPillRequest(ref: ref, filter: .platform("Kindle"))

        XCTAssertEqual(genre?.mediaType, "book")
        XCTAssertEqual(genre?.source, "openlibrary")
        XCTAssertEqual(genre?.filter, .genre("Fiction"))
        XCTAssertEqual(genre?.title, "Fiction · Books")
        XCTAssertEqual(year?.mediaType, "book")
        XCTAssertEqual(year?.source, "openlibrary")
        XCTAssertEqual(year?.filter, .year("1965"))
        XCTAssertNil(platform)
    }

    func testMediaDiscoverRequestMapsSeasonDetailPillsToTV() {
        let ref = MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 1, episodeNumber: nil)
        let genre = MediaDiscoverRequest.detailPillRequest(ref: ref, filter: .genre("Fantasy"))
        let year = MediaDiscoverRequest.detailPillRequest(ref: ref, filter: .year("2011"))
        let platform = MediaDiscoverRequest.detailPillRequest(ref: ref, filter: .platform("Netflix"))
        let query = Dictionary(uniqueKeysWithValues: (genre?.queryItems ?? []).map { ($0.name, $0.value) })

        XCTAssertEqual(genre?.mediaType, "tv")
        XCTAssertEqual(genre?.source, "tmdb")
        XCTAssertEqual(genre?.filter, .genre("Fantasy"))
        XCTAssertEqual(genre?.title, "Fantasy · TV")
        XCTAssertEqual(year?.mediaType, "tv")
        XCTAssertEqual(year?.filter, .year("2011"))
        XCTAssertNil(platform)
        XCTAssertEqual(query["media_type"]!, "tv")
        XCTAssertNil(query["season_number"])
    }

    func testAuthTokenDecoding() throws {
        let data = """
        {
          "access": "access-token",
          "refresh": "refresh-token",
          "user": {
            "id": 1,
            "username": "mobile",
            "display_name": "Mobile User",
            "is_private": false
          }
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.api.decode(AuthTokenResponse.self, from: data)

        XCTAssertEqual(response.access, "access-token")
        XCTAssertEqual(response.refresh, "refresh-token")
        XCTAssertEqual(response.user.displayName, "Mobile User")
    }

    func testSettingsRequestsEncodeSnakeCase() throws {
        let profile = ProfileUpdateRequest(
            username: "mika",
            displayName: "Mika",
            bio: "Bio",
            pronouns: "they/them",
            location: "Portland",
            isPrivate: true
        )
        let preferences = PreferencesUpdateRequest(
            enabledMediaTypes: ["movie", "book"],
            dateFormat: "Y-m-d",
            timeFormat: "H:i",
            weekStartDay: "monday",
            quickWatchDate: "current_date",
            releaseNotificationsEnabled: false,
            dailyDigestEnabled: true
        )

        let profileJSON = try JSONSerialization.jsonObject(with: JSONEncoder.api.encode(profile)) as! [String: Any]
        let preferencesJSON = try JSONSerialization.jsonObject(with: JSONEncoder.api.encode(preferences)) as! [String: Any]

        XCTAssertEqual(profileJSON["display_name"] as? String, "Mika")
        XCTAssertEqual(profileJSON["is_private"] as? Bool, true)
        XCTAssertEqual(preferencesJSON["enabled_media_types"] as? [String], ["movie", "book"])
        XCTAssertEqual(preferencesJSON["week_start_day"] as? String, "monday")
        XCTAssertEqual(preferencesJSON["daily_digest_enabled"] as? Bool, true)
    }

    func testMetaResponseDecodesSettingsOptions() throws {
        let data = """
        {
          "version": "v1",
          "media_types": ["movie", "book"],
          "sources": {},
          "status_choices": [],
          "source_choices": [],
          "date_formats": [{ "value": "Y-m-d", "label": "2026-01-18 (ISO)" }],
          "time_formats": [{ "value": "H:i", "label": "14:30 (24-hour)" }],
          "week_start_days": [{ "value": "monday", "label": "Monday" }],
          "quick_watch_dates": [{ "value": "current_date", "label": "Current Date" }]
        }
        """.data(using: .utf8)!

        let meta = try JSONDecoder.api.decode(MetaResponse.self, from: data)

        XCTAssertEqual(meta.settingsOptions?.dateFormats.first?.value, "Y-m-d")
        XCTAssertEqual(meta.settingsOptions?.timeFormats.first?.label, "14:30 (24-hour)")
        XCTAssertEqual(meta.settingsOptions?.weekStartDays.first?.value, "monday")
        XCTAssertEqual(meta.settingsOptions?.quickWatchDates.first?.label, "Current Date")
    }

    @MainActor
    func testProfileSettingsViewModelSavesProfilePatch() async {
        let profile = profileFixture(hof: [:], enabledMediaTypes: ["movie"])
        let repository = RecordingProfileRepository(profile: profile)
        let viewModel = ProfileSettingsViewModel(
            profileRepository: repository,
            mediaRepository: FakeMediaRepository(),
            onUnauthorized: {}
        )
        viewModel.load(profile: profile)
        viewModel.displayName = "Updated"
        viewModel.username = "updated"

        let updated = await viewModel.saveProfile()

        XCTAssertEqual(repository.profileRequests.first?.displayName, "Updated")
        XCTAssertEqual(repository.profileRequests.first?.username, "updated")
        XCTAssertEqual(updated?.displayName, "Updated")
        XCTAssertEqual(viewModel.profile?.username, "updated")
    }

    @MainActor
    func testProfileSettingsPreferencesRequireOneMediaType() async {
        let profile = profileFixture(hof: [:], enabledMediaTypes: ["movie"])
        let repository = RecordingProfileRepository(profile: profile)
        let viewModel = ProfileSettingsViewModel(
            profileRepository: repository,
            mediaRepository: FakeMediaRepository(),
            onUnauthorized: {}
        )
        viewModel.load(profile: profile)
        viewModel.enabledMediaTypes = []

        let updated = await viewModel.savePreferences()

        XCTAssertNil(updated)
        XCTAssertTrue(repository.preferenceRequests.isEmpty)
        XCTAssertEqual(viewModel.fieldErrors["enabled_media_types"], "Enable at least one media type.")
    }

    @MainActor
    func testProfileSettingsAvatarUploadRecordsMime() async {
        let profile = profileFixture(hof: [:], enabledMediaTypes: ["movie"])
        let repository = RecordingProfileRepository(profile: profile)
        let viewModel = ProfileSettingsViewModel(
            profileRepository: repository,
            mediaRepository: FakeMediaRepository(),
            onUnauthorized: {}
        )
        viewModel.load(profile: profile)

        let updated = await viewModel.uploadAvatar(
            imageData: Data("avatar".utf8),
            fileName: "avatar.webp",
            mimeType: "image/webp"
        )

        XCTAssertEqual(repository.avatarUploads.first?.fileName, "avatar.webp")
        XCTAssertEqual(repository.avatarUploads.first?.mimeType, "image/webp")
        XCTAssertEqual(updated?.avatarUrl, "https://example.com/avatar.webp")
    }

    func testImportModeRawValuesMatchAPI() {
        XCTAssertEqual(ImportMode.new.rawValue, "new")
        XCTAssertEqual(ImportMode.overwrite.rawValue, "overwrite")
    }

    func testImportResponsesDecodeSnakeCase() throws {
        let queueData = """
        {
          "task_id": "letterboxd-task",
          "status": "queued"
        }
        """.data(using: .utf8)!
        let statusData = """
        {
          "task_id": "letterboxd-task",
          "task_name": "Import from Letterboxd",
          "status": "SUCCESS",
          "date_created": "2026-06-22T12:00:00Z",
          "date_done": "2026-06-22T12:01:00Z",
          "result": "Imported 1 Movie."
        }
        """.data(using: .utf8)!

        let queue = try JSONDecoder.api.decode(ImportQueueResponse.self, from: queueData)
        let status = try JSONDecoder.api.decode(ImportTaskStatus.self, from: statusData)

        XCTAssertEqual(queue.taskId, "letterboxd-task")
        XCTAssertEqual(queue.status, "queued")
        XCTAssertEqual(status.taskId, "letterboxd-task")
        XCTAssertEqual(status.taskName, "Import from Letterboxd")
        XCTAssertEqual(status.status, "SUCCESS")
        XCTAssertEqual(status.result, "Imported 1 Movie.")
    }

    @MainActor
    func testLetterboxdImportCoordinatorTransitionsToSuccess() async throws {
        let defaults = isolatedDefaults("LetterboxdImportCoordinatorTransitions")
        let repository = ScriptedLetterboxdImportRepository(statuses: [
            ImportTaskStatus(taskId: "task-1", taskName: nil, status: "SUCCESS", dateCreated: nil, dateDone: nil, result: "Imported 3 movies.")
        ])
        let coordinator = LetterboxdImportCoordinator(
            importRepository: repository,
            defaults: defaults,
            pollInterval: .milliseconds(10),
            timeout: 5
        )
        let fileURL = try makeTemporaryZip()
        var notifiedTaskId: String?
        let observer = NotificationCenter.default.addObserver(
            forName: .letterboxdImportDidSucceed,
            object: nil,
            queue: nil,
        ) { notification in
            notifiedTaskId = notification.userInfo?["taskId"] as? String
        }
        defer {
            NotificationCenter.default.removeObserver(observer)
        }

        XCTAssertEqual(coordinator.phase, .idle)

        coordinator.startImport(fileURL: fileURL, mode: .new)

        try await waitUntil {
            if case let .uploading(fileName, progress) = coordinator.phase {
                return fileName == fileURL.lastPathComponent && progress == 1
            }
            return false
        }

        try await waitUntil {
            if case let .processing(taskId, _, _) = coordinator.phase {
                return taskId == "task-1"
            }
            return false
        }

        try await waitUntil {
            coordinator.phase == .succeeded(message: "Imported 3 movies.")
        }

        XCTAssertEqual(repository.queuedFileName, fileURL.lastPathComponent)
        XCTAssertEqual(repository.queuedMode, .new)
        XCTAssertEqual(repository.statusRequests, ["task-1"])
        XCTAssertEqual(notifiedTaskId, "task-1")
    }

    @MainActor
    func testLetterboxdImportCoordinatorPersistsAndResumesTask() async throws {
        let defaults = isolatedDefaults("LetterboxdImportCoordinatorPersistence")
        let repository = ScriptedLetterboxdImportRepository(statuses: [
            ImportTaskStatus(taskId: "task-2", taskName: nil, status: "PENDING", dateCreated: nil, dateDone: nil, result: nil)
        ])
        let coordinator = LetterboxdImportCoordinator(
            importRepository: repository,
            defaults: defaults,
            pollInterval: .seconds(60),
            timeout: 5
        )

        coordinator.startImport(fileURL: try makeTemporaryZip(), mode: .overwrite)

        try await waitUntil {
            if case let .processing(taskId, _, _) = coordinator.phase {
                return taskId == "task-2"
            }
            return false
        }

        let resumed = LetterboxdImportCoordinator(
            importRepository: ScriptedLetterboxdImportRepository(statuses: []),
            defaults: defaults,
            pollInterval: .seconds(60),
            timeout: 5
        )
        resumed.resumeIfNeeded()

        guard case let .processing(taskId, _, _) = resumed.phase else {
            XCTFail("Expected persisted task to resume.")
            return
        }
        XCTAssertEqual(taskId, "task-2")

        coordinator.clearFinishedJob()
        resumed.clearFinishedJob()
    }

    @MainActor
    func testStoryGraphImportCoordinatorTransitionsToSuccess() async throws {
        let defaults = isolatedDefaults("StoryGraphImportCoordinatorTransitions")
        let repository = ScriptedStoryGraphImportRepository(statuses: [
            ImportTaskStatus(taskId: "storygraph-task-1", taskName: nil, status: "SUCCESS", dateCreated: nil, dateDone: nil, result: "Imported 3 books.")
        ])
        let coordinator = StoryGraphImportCoordinator(
            importRepository: repository,
            defaults: defaults,
            pollInterval: .milliseconds(10),
            timeout: 5
        )
        let fileURL = try makeTemporaryCSV()
        var notifiedTaskId: String?
        let observer = NotificationCenter.default.addObserver(
            forName: .storygraphImportDidSucceed,
            object: nil,
            queue: nil,
        ) { notification in
            notifiedTaskId = notification.userInfo?["taskId"] as? String
        }
        defer {
            NotificationCenter.default.removeObserver(observer)
        }

        coordinator.startImport(fileURL: fileURL, mode: .new)

        try await waitUntil {
            if case let .uploading(fileName, progress) = coordinator.phase {
                return fileName == fileURL.lastPathComponent && progress == 1
            }
            return false
        }

        try await waitUntil {
            if case let .processing(taskId, _, _) = coordinator.phase {
                return taskId == "storygraph-task-1"
            }
            return false
        }

        try await waitUntil {
            coordinator.phase == .succeeded(message: "Imported 3 books.")
        }

        XCTAssertEqual(repository.queuedFileName, fileURL.lastPathComponent)
        XCTAssertEqual(repository.queuedMode, .new)
        XCTAssertEqual(repository.statusRequests, ["storygraph-task-1"])
        XCTAssertEqual(notifiedTaskId, "storygraph-task-1")
    }

    @MainActor
    func testStoryGraphImportCoordinatorPersistsAndResumesTask() async throws {
        let defaults = isolatedDefaults("StoryGraphImportCoordinatorPersistence")
        let repository = ScriptedStoryGraphImportRepository(statuses: [
            ImportTaskStatus(taskId: "storygraph-task-2", taskName: nil, status: "PENDING", dateCreated: nil, dateDone: nil, result: nil)
        ])
        let coordinator = StoryGraphImportCoordinator(
            importRepository: repository,
            defaults: defaults,
            pollInterval: .seconds(60),
            timeout: 5
        )

        coordinator.startImport(fileURL: try makeTemporaryCSV(), mode: .overwrite)

        try await waitUntil {
            if case let .processing(taskId, _, _) = coordinator.phase {
                return taskId == "storygraph-task-2"
            }
            return false
        }

        let resumed = StoryGraphImportCoordinator(
            importRepository: ScriptedStoryGraphImportRepository(statuses: []),
            defaults: defaults,
            pollInterval: .seconds(60),
            timeout: 5
        )
        resumed.resumeIfNeeded()

        guard case let .processing(taskId, _, _) = resumed.phase else {
            XCTFail("Expected persisted task to resume.")
            return
        }
        XCTAssertEqual(taskId, "storygraph-task-2")

        coordinator.clearFinishedJob()
        resumed.clearFinishedJob()
    }

    func testMultipartBodyIncludesFieldsAndFile() {
        let body = MultipartFormData.body(
            boundary: "TestBoundary",
            fields: ["mode": "new"],
            fileFieldName: "file",
            fileName: "letterboxd.zip",
            fileData: Data("zip-bytes".utf8),
            mimeType: "application/zip"
        )
        let text = String(data: body, encoding: .utf8)!

        XCTAssertTrue(text.contains("--TestBoundary\r\n"))
        XCTAssertTrue(text.contains("Content-Disposition: form-data; name=\"mode\"\r\n\r\nnew\r\n"))
        XCTAssertTrue(text.contains("Content-Disposition: form-data; name=\"file\"; filename=\"letterboxd.zip\""))
        XCTAssertTrue(text.contains("Content-Type: application/zip\r\n\r\nzip-bytes\r\n"))
        XCTAssertTrue(text.hasSuffix("--TestBoundary--\r\n"))
    }

    func testStoryGraphMultipartBodyIncludesModeAndCSVFile() {
        let body = MultipartFormData.body(
            boundary: "TestBoundary",
            fields: ["mode": "overwrite"],
            fileFieldName: "file",
            fileName: "storygraph.csv",
            fileData: Data("Title,Authors\nBook,Author\n".utf8),
            mimeType: "text/csv"
        )
        let text = String(data: body, encoding: .utf8)!

        XCTAssertTrue(text.contains("Content-Disposition: form-data; name=\"mode\"\r\n\r\noverwrite\r\n"))
        XCTAssertTrue(text.contains("Content-Disposition: form-data; name=\"file\"; filename=\"storygraph.csv\""))
        XCTAssertTrue(text.contains("Content-Type: text/csv\r\n\r\nTitle,Authors\nBook,Author\n\r\n"))
    }


    func testMediaSearchDecoding() throws {
        let data = """
        {
          "count": 1,
          "next": null,
          "previous": null,
          "results": [
            {
              "ref": {
                "item_id": null,
                "source": "tmdb",
                "media_type": "movie",
                "media_id": "550",
                "season_number": null,
                "episode_number": null
              },
              "title": "Fight Club",
              "subtitle": "1999",
              "overview": "A detail.",
              "image_url": "https://example.com/poster.jpg",
              "poster_url": "https://example.com/poster-normalized.jpg",
              "backdrop_url": "https://example.com/backdrop.jpg",
              "poster_orientation": "portrait",
              "poster_aspect_ratio": 0.667,
              "poster_width": 500,
              "poster_height": 750,
              "poster_accent_color": null,
              "release_date": "1999-10-15",
              "default_source": "tmdb",
              "user_state": null
            }
          ]
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.api.decode(PagedResponse<MediaSummary>.self, from: data)

        XCTAssertEqual(response.results.first?.ref.source, "tmdb")
        XCTAssertEqual(response.results.first?.title, "Fight Club")
        XCTAssertEqual(response.results.first?.imageUrl, "https://example.com/poster.jpg")
        XCTAssertEqual(response.results.first?.posterUrl, "https://example.com/poster-normalized.jpg")
        XCTAssertEqual(response.results.first?.displayPosterURL, "https://example.com/poster-normalized.jpg")
        XCTAssertEqual(response.results.first?.backdropUrl, "https://example.com/backdrop.jpg")
        XCTAssertEqual(response.results.first?.posterOrientation, .portrait)
        XCTAssertEqual(response.results.first?.posterAspectRatio, 0.667)
        XCTAssertEqual(response.results.first?.posterWidth, 500)
        XCTAssertEqual(response.results.first?.posterHeight, 750)
    }

    func testMediaSummaryLegacyImageFallback() throws {
        let data = """
        {
          "ref": {
            "item_id": null,
            "source": "openlibrary",
            "media_type": "book",
            "media_id": "OL1M",
            "season_number": null,
            "episode_number": null
          },
          "title": "A Book",
          "image_url": "https://example.com/legacy.jpg"
        }
        """.data(using: .utf8)!

        let summary = try JSONDecoder.api.decode(MediaSummary.self, from: data)

        XCTAssertEqual(summary.posterUrl, "https://example.com/legacy.jpg")
        XCTAssertEqual(summary.displayPosterURL, "https://example.com/legacy.jpg")
        XCTAssertNil(summary.backdropUrl)
    }

    func testCustomListSummaryPreviewItemsDecoding() throws {
        let data = """
        {
          "count": 1,
          "next": null,
          "previous": null,
          "results": [
            {
              "id": 7,
              "name": "Weekend Watchlist",
              "slug": "weekend-watchlist",
              "description": "",
              "visibility": "public",
              "owner": {
                "id": 1,
                "username": "mika",
                "display_name": "Mika",
                "avatar_url": null
              },
              "image_url": null,
              "preview_items": [
                {
                  "ref": {
                    "item_id": 42,
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "550",
                    "season_number": null,
                    "episode_number": null
                  },
                  "title": "Fight Club",
                  "subtitle": "1999",
                  "overview": null,
                  "image_url": "https://example.com/fight-club.jpg",
                  "poster_url": "https://example.com/fight-club-poster.jpg",
                  "poster_orientation": "portrait",
                  "release_date": "1999-10-15",
                  "default_source": "tmdb",
                  "user_state": null
                }
              ],
              "items_count": 3,
              "updated_at": "2026-06-24T12:00:00Z",
              "like_count": 5
            }
          ]
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.api.decode(PagedResponse<CustomListSummary>.self, from: data)
        let list = try XCTUnwrap(response.results.first)
        let preview = try XCTUnwrap(list.previewItems?.first)

        XCTAssertEqual(list.name, "Weekend Watchlist")
        XCTAssertEqual(list.itemsCount, 3)
        XCTAssertEqual(preview.title, "Fight Club")
        XCTAssertEqual(preview.displayPosterURL, "https://example.com/fight-club-poster.jpg")
        XCTAssertEqual(preview.posterOrientation, .portrait)
    }

    func testCustomListRankedMembershipAndPositionDecoding() throws {
        let data = """
        {
          "count": 1,
          "next": null,
          "previous": null,
          "results": [
            {
              "id": 7,
              "name": "Ranked Watchlist",
              "slug": "ranked-watchlist",
              "description": "",
              "visibility": "public",
              "is_ranked": true,
              "has_item": true,
              "owner": {
                "id": 1,
                "username": "mika",
                "display_name": "Mika",
                "avatar_url": null
              },
              "image_url": null,
              "preview_items": [
                {
                  "ref": {
                    "item_id": 42,
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "550",
                    "season_number": null,
                    "episode_number": null
                  },
                  "title": "Fight Club",
                  "image_url": "https://example.com/fight-club.jpg",
                  "position": 2
                }
              ],
              "items_count": 3,
              "updated_at": null,
              "like_count": 0
            }
          ]
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.api.decode(PagedResponse<CustomListSummary>.self, from: data)
        let list = try XCTUnwrap(response.results.first)
        let preview = try XCTUnwrap(list.previewItems?.first)

        XCTAssertTrue(list.isRanked)
        XCTAssertEqual(list.hasItem, true)
        XCTAssertEqual(preview.position, 2)
    }

    func testMediaDetailDisplayPosterFallbackChain() throws {
        let legacy = """
        {
          "ref": {
            "item_id": null,
            "source": "mal",
            "media_type": "anime",
            "media_id": "1",
            "season_number": null,
            "episode_number": null
          },
          "title": "Anime",
          "image_url": "https://example.com/legacy.jpg",
          "custom_poster_url": null
        }
        """.data(using: .utf8)!
        let customized = """
        {
          "ref": {
            "item_id": null,
            "source": "tmdb",
            "media_type": "movie",
            "media_id": "550",
            "season_number": null,
            "episode_number": null
          },
          "title": "Movie",
          "image_url": "https://example.com/image.jpg",
          "poster_url": "https://example.com/poster.jpg",
          "poster_orientation": "landscape",
          "logo_url": "https://image.tmdb.org/t/p/w500/logo.png",
          "logo_width": 1493,
          "logo_height": 482,
          "logo_aspect_ratio": 3.1,
          "custom_poster_url": "https://example.com/custom.jpg"
        }
        """.data(using: .utf8)!

        let legacyDetail = try JSONDecoder.api.decode(MediaDetail.self, from: legacy)
        let customDetail = try JSONDecoder.api.decode(MediaDetail.self, from: customized)

        XCTAssertEqual(legacyDetail.displayPosterURL, "https://example.com/legacy.jpg")
        XCTAssertEqual(customDetail.displayPosterURL, "https://example.com/custom.jpg")
        XCTAssertEqual(customDetail.posterOrientation, .landscape)
        XCTAssertEqual(customDetail.logoUrl, "https://image.tmdb.org/t/p/w500/logo.png")
        XCTAssertEqual(customDetail.logoWidth, 1493)
        XCTAssertEqual(customDetail.logoHeight, 482)
        XCTAssertEqual(customDetail.logoAspectRatio, 3.1)
    }

    func testTitleLogoSupportRequiresTmdbMovieOrTVWithLogo() {
        let movie = MediaDetail(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "550", seasonNumber: nil, episodeNumber: nil),
            title: "Movie",
            logoUrl: "https://image.tmdb.org/t/p/w500/logo.png"
        )
        let season = MediaDetail(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 1, episodeNumber: nil),
            title: "Season",
            logoUrl: "https://image.tmdb.org/t/p/w500/logo.png"
        )
        let anime = MediaDetail(
            ref: MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: "1", seasonNumber: nil, episodeNumber: nil),
            title: "Anime",
            logoUrl: "https://image.tmdb.org/t/p/w500/logo.png"
        )
        let missingLogo = MediaDetail(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "tv", mediaId: "1399", seasonNumber: nil, episodeNumber: nil),
            title: "TV"
        )

        XCTAssertTrue(supportsTitleLogo(movie))
        XCTAssertFalse(supportsTitleLogo(season))
        XCTAssertFalse(supportsTitleLogo(anime))
        XCTAssertFalse(supportsTitleLogo(missingLogo))
    }

    func testPosterOptionsAndSaveResponseDecoding() throws {
        let options = """
        {
          "posters": [
            {
              "url": "https://example.com/original.jpg",
              "thumbnail_url": "https://example.com/thumb.jpg",
              "width": 1000,
              "height": 1500,
              "aspect_ratio": 0.667,
              "vote_average": 8.1,
              "vote_count": 42,
              "language": "en",
              "is_original": true,
              "is_selected": true
            }
          ]
        }
        """.data(using: .utf8)!
        let save = """
        {
          "poster_url": "https://example.com/new.jpg",
          "custom_poster_url": "https://example.com/new.jpg",
          "poster_accent_color": "#123456"
        }
        """.data(using: .utf8)!
        let backdropOptions = """
        {
          "backdrops": [
            {
              "url": "https://example.com/backdrop.jpg",
              "thumbnail_url": "https://example.com/backdrop-thumb.jpg",
              "width": 1920,
              "height": 1080,
              "aspect_ratio": 1.778,
              "vote_average": 8.1,
              "vote_count": 42,
              "language": "en",
              "is_original": true,
              "is_selected": true
            }
          ]
        }
        """.data(using: .utf8)!
        let backdropSave = """
        {
          "backdrop_url": "https://example.com/new-backdrop.jpg",
          "custom_backdrop_url": "https://example.com/new-backdrop.jpg"
        }
        """.data(using: .utf8)!
        let bookOptions = """
        {
          "posters": [
            {
              "url": "https://example.com/book-original.jpg",
              "thumbnail_url": "https://example.com/book-original-thumb.jpg",
              "width": 0,
              "height": 0,
              "aspect_ratio": 0.667,
              "vote_average": 0,
              "vote_count": 0,
              "language": null,
              "is_original": true,
              "is_selected": true
            }
          ]
        }
        """.data(using: .utf8)!

        let decodedOptions = try JSONDecoder.api.decode(PosterOptionsResponse.self, from: options)
        let decodedSave = try JSONDecoder.api.decode(PosterSaveResponse.self, from: save)
        let decodedBackdropOptions = try JSONDecoder.api.decode(BackdropOptionsResponse.self, from: backdropOptions)
        let decodedBackdropSave = try JSONDecoder.api.decode(BackdropSaveResponse.self, from: backdropSave)
        let decodedBookOptions = try JSONDecoder.api.decode(PosterOptionsResponse.self, from: bookOptions)

        XCTAssertEqual(decodedOptions.posters.first?.thumbnailUrl, "https://example.com/thumb.jpg")
        XCTAssertEqual(decodedOptions.posters.first?.language, "en")
        XCTAssertEqual(decodedSave.customPosterUrl, "https://example.com/new.jpg")
        XCTAssertEqual(decodedSave.posterAccentColor, "#123456")
        XCTAssertEqual(decodedBackdropOptions.backdrops.first?.thumbnailUrl, "https://example.com/backdrop-thumb.jpg")
        XCTAssertEqual(decodedBackdropSave.customBackdropUrl, "https://example.com/new-backdrop.jpg")
        XCTAssertNil(decodedBookOptions.posters.first?.language)
        XCTAssertEqual(decodedBookOptions.posters.first?.voteAverage, 0)
        XCTAssertEqual(decodedBookOptions.posters.first?.voteCount, 0)
        XCTAssertEqual(decodedBookOptions.posters.first?.thumbnailUrl, "https://example.com/book-original-thumb.jpg")
    }

    func testMediaArtworkCustomizationEligibility() {
        XCTAssertTrue(MediaArtworkCustomization.supportsPoster(source: "tmdb", mediaType: "movie"))
        XCTAssertTrue(MediaArtworkCustomization.supportsPoster(source: "tmdb", mediaType: "tv"))
        XCTAssertTrue(MediaArtworkCustomization.supportsPoster(source: "openlibrary", mediaType: "book"))
        XCTAssertTrue(MediaArtworkCustomization.supportsPoster(source: "hardcover", mediaType: "book"))
        XCTAssertFalse(MediaArtworkCustomization.supportsPoster(source: "mal", mediaType: "anime"))
        XCTAssertFalse(MediaArtworkCustomization.supportsBackdrop(source: "openlibrary", mediaType: "book"))
        XCTAssertTrue(MediaArtworkCustomization.supportsBackdrop(source: "tmdb", mediaType: "movie"))
    }

    func testTrackingDiaryAndProfileDecoding() throws {
        let tracking = """
        {
          "tracking_id": 9,
          "status": "In progress",
          "rating": "8.5",
          "progress": { "kind": "pages", "value": 42, "max": 300, "unit": "page" },
          "repeats": 1,
          "start_date": null,
          "end_date": null,
          "notes": "",
          "updated_at": "2026-06-20T12:00:00Z"
        }
        """.data(using: .utf8)!

        let diary = """
        {
          "id": 3,
          "user": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
          "media": {
            "ref": { "item_id": 4, "source": "tmdb", "media_type": "movie", "media_id": "550", "season_number": null, "episode_number": null },
            "title": "Fight Club",
            "image_url": null
          },
          "consumed_at": "2026-06-20T12:00:00Z",
          "rating": "9.0",
          "review_title": "Strong",
          "review": "Review",
          "contains_spoilers": false,
          "liked": true,
          "is_rewatch": false,
          "tags": ["drama"],
          "visibility": "public",
          "like_count": 0,
          "viewer_has_liked": false,
          "created_at": "2026-06-20T12:00:00Z",
          "updated_at": "2026-06-20T12:00:00Z"
        }
        """.data(using: .utf8)!

        let profile = """
        {
          "id": 1,
          "username": "mobile",
          "display_name": "Mobile",
          "email": "mobile@example.com",
          "bio": "",
          "pronouns": "",
          "location": "",
          "avatar_url": null,
          "is_private": false,
          "viewer_relationship": { "following": false, "followed_by": false, "requested": false, "blocked": false },
          "counts": { "followers": 0, "following": 0, "diary_entries": 1, "lists": 0 },
          "hof": { "movie": null },
          "preferences": {
            "enabled_media_types": ["movie"],
            "date_format": "YYYY-MM-DD",
            "time_format": "24h",
            "week_start_day": "monday",
            "quick_watch_date": "today",
            "release_notifications_enabled": false,
            "daily_digest_enabled": false
          }
        }
        """.data(using: .utf8)!

        let decodedTracking = try JSONDecoder.api.decode(TrackingState.self, from: tracking)
        XCTAssertEqual(decodedTracking.rating, "8.5")
        XCTAssertEqual(decodedTracking.progress?.compactDisplayText, "42/300 pages")
        XCTAssertEqual(decodedTracking.progress?.detailDisplayText, "42 of 300 pages")
        XCTAssertEqual(try JSONDecoder.api.decode(DiaryEntry.self, from: diary).media.title, "Fight Club")
        XCTAssertEqual(try JSONDecoder.api.decode(UserProfile.self, from: profile).counts.diaryEntries, 1)
    }

    func testTrackingStateDecodesLatestProgressChange() throws {
        let data = """
        {
          "tracking_id": 9,
          "status": "In progress",
          "rating": null,
          "progress": { "kind": "percentage", "value": 58, "max": 100, "unit": "percent" },
          "latest_progress_change": {
            "id": 77,
            "previous": { "kind": "percentage", "value": 42, "max": 100, "unit": "percent" },
            "current": { "kind": "percentage", "value": 58, "max": 100, "unit": "percent" },
            "created_at": "2026-06-20T12:00:00Z"
          },
          "repeats": null,
          "start_date": null,
          "end_date": null,
          "notes": "",
          "updated_at": "2026-06-20T12:00:00Z"
        }
        """.data(using: .utf8)!

        let decoded = try JSONDecoder.api.decode(TrackingState.self, from: data)

        XCTAssertEqual(decoded.latestProgressChange?.id, 77)
        XCTAssertEqual(decoded.latestProgressChange?.compactDisplayText(preferredMode: nil), "42% → 58%")
        XCTAssertEqual(decoded.homeProgressText(preferredMode: nil), "42% → 58%")
    }

    func testPercentageProgressDisplayUsesPercentEverywhere() {
        let progress = ProgressState(kind: "percentage", value: Decimal(52), max: Decimal(100), unit: "percent")

        XCTAssertEqual(progress.compactDisplayText, "52%")
        XCTAssertEqual(progress.detailDisplayText, "52%")
    }

    func testPreferredPercentageDisplayConvertsPageProgress() {
        let progress = ProgressState(kind: "pages", value: Decimal(54), max: Decimal(777), unit: "page")

        XCTAssertEqual(progress.compactDisplayText(preferredMode: .percentage), "7%")
        XCTAssertEqual(progress.detailDisplayText(preferredMode: .percentage), "7%")
    }

    func testProgressDeltaFormattingRespectsPreferredMode() {
        let change = ProgressChangeState(
            id: 1,
            previous: ProgressState(kind: "pages", value: Decimal(120), max: Decimal(300), unit: "page"),
            current: ProgressState(kind: "pages", value: Decimal(174), max: Decimal(300), unit: "page"),
            createdAt: nil
        )

        XCTAssertEqual(change.compactDisplayText(preferredMode: .pages), "120/300 pages → 174/300 pages")
        XCTAssertEqual(change.compactDisplayText(preferredMode: .percentage), "40% → 58%")
    }

    func testProfileRecentActivityRailMetadata() {
        let diary = activityItem(
            id: 1,
            title: "Liked Log",
            type: "diary_created",
            previous: nil,
            current: nil,
            rating: "9.0",
            liked: true
        )
        let progress = activityItem(
            id: 2,
            title: "Progress",
            previous: ProgressState(kind: "percentage", value: Decimal(96), max: Decimal(100), unit: "percent"),
            current: ProgressState(kind: "percentage", value: Decimal(98), max: Decimal(100), unit: "percent")
        )
        let noMedia = activityItem(id: 3, title: "No Media", hasMedia: false)

        let railItems = ProfileRecentActivityRailModel.items(from: [diary, progress, noMedia])

        XCTAssertEqual(railItems.map(\.activity.id), [1, 2])
        XCTAssertEqual(ProfileRecentActivityRailModel.rating(for: diary), "9.0")
        XCTAssertTrue(ProfileRecentActivityRailModel.isLikedDiary(diary))
        XCTAssertEqual(
            ProfileRecentActivityRailModel.progressDelta(for: progress, media: railItems[1].media),
            ProgressChangeDisplay(previous: "96%", current: "98%")
        )
    }

    func testHomeProgressFallsBackWhenProgressDeltaIsMissingOrUnchanged() {
        let unchanged = TrackingState(
            trackingId: 1,
            status: "In progress",
            rating: nil,
            progress: ProgressState(kind: "percentage", value: Decimal(58), max: Decimal(100), unit: "percent"),
            latestProgressChange: ProgressChangeState(
                id: 1,
                previous: ProgressState(kind: "percentage", value: Decimal(58), max: Decimal(100), unit: "percent"),
                current: ProgressState(kind: "percentage", value: Decimal(58), max: Decimal(100), unit: "percent"),
                createdAt: nil
            ),
            repeats: nil,
            startDate: nil,
            endDate: nil,
            notes: nil,
            updatedAt: nil
        )
        let missing = TrackingState(
            trackingId: 2,
            status: "In progress",
            rating: nil,
            progress: ProgressState(kind: "percentage", value: Decimal(58), max: Decimal(100), unit: "percent"),
            repeats: nil,
            startDate: nil,
            endDate: nil,
            notes: nil,
            updatedAt: nil
        )

        XCTAssertEqual(unchanged.homeProgressText(preferredMode: nil), "58%")
        XCTAssertEqual(missing.homeProgressText(preferredMode: nil), "58%")
    }

    func testPreferredPercentageDisplayTreatsGameMinuteProgressAsPercent() {
        let progress = ProgressState(kind: "progress", value: Decimal(58), max: nil, unit: "minutes")

        XCTAssertEqual(progress.value(in: .percentage), 58)
        XCTAssertEqual(progress.compactDisplayText(preferredMode: .percentage), "58%")
        XCTAssertEqual(progress.detailDisplayText(preferredMode: .percentage), "58%")
    }

    func testZeroProgressDisplaysAsStartedOnHome() {
        let tracking = TrackingState(
            trackingId: 1,
            status: "In progress",
            rating: nil,
            progress: ProgressState(kind: "progress", value: Decimal(0), max: nil, unit: "minutes"),
            repeats: nil,
            startDate: nil,
            endDate: nil,
            notes: nil,
            updatedAt: nil
        )

        XCTAssertNil(tracking.progress?.compactDisplayText(preferredMode: .percentage))
        XCTAssertNil(tracking.progress?.compactDisplayText)
        XCTAssertEqual(tracking.homeProgressText(preferredMode: nil), "Started")
        XCTAssertEqual(tracking.homeProgressText(preferredMode: .percentage), "Started")
    }

    func testUserMediaStateDecodesProgress() throws {
        let data = """
        {
          "ref": { "item_id": 7, "source": "openlibrary", "media_type": "book", "media_id": "OL1M", "season_number": null, "episode_number": null },
          "title": "Progress Book",
          "image_url": null,
          "user_state": {
            "is_tracked": true,
            "tracking_id": 9,
            "status": "In progress",
            "rating": null,
            "progress": { "kind": "percentage", "value": 52, "max": 100, "unit": "percent" },
            "diary_entry_id": null,
            "diary_rating": null,
            "diary_consumed_at": null,
            "in_lists": []
          }
        }
        """.data(using: .utf8)!

        let media = try JSONDecoder.api.decode(MediaSummary.self, from: data)

        XCTAssertEqual(media.userState?.progress?.detailDisplayText, "52%")
    }

    func testDiaryTagSuggestionDecoding() throws {
        let data = """
        {
          "results": [
            { "name": "netflix", "usage_count": 12 }
          ]
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.api.decode(DiaryTagSuggestionsResponse.self, from: data)

        XCTAssertEqual(response.results.first?.name, "netflix")
        XCTAssertEqual(response.results.first?.usageCount, 12)
    }

    func testTrackingRepositorySendsStatusQuery() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APITrackingRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")

            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(components?.path, "/api/v1/tracking/")
            XCTAssertEqual(query.first { $0.name == "media_type" }?.value, "season")
            XCTAssertEqual(query.first { $0.name == "status" }?.value, "In progress")
            XCTAssertEqual(query.first { $0.name == "q" }?.value, "Dune")

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"count":0,"next":null,"previous":null,"results":[]}"#.data(using: .utf8)!
            )
        }

        let response = try await repository.list(mediaType: "season", page: nil, status: "In progress", query: "Dune")
        XCTAssertEqual(response.count, 0)
    }

    func testTrackingRepositoryBuildsDetailRequest() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APITrackingRepository(client: client)
        let ref = MediaRef(itemId: nil, source: "openlibrary", mediaType: "book", mediaId: "OL1M", seasonNumber: nil, episodeNumber: nil)

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/tracking/openlibrary/book/OL1M/")

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"tracking_id":1,"status":"In progress","rating":null,"progress":{"kind":"pages","value":54,"max":777,"unit":"page"},"repeats":null,"start_date":null,"end_date":null,"notes":null,"updated_at":null}"#.data(using: .utf8)!
            )
        }

        let state = try await repository.detail(ref: ref)

        XCTAssertEqual(state.progress?.value(in: .percentage), 7)
    }

    func testActivityRepositoryLoadsUserActivity() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIActivityRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")

            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(components?.path, "/api/v1/users/mobile/activity/")
            XCTAssertEqual(query.first { $0.name == "page_size" }?.value, "6")

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "next_cursor": null,
                  "previous_cursor": null,
                  "results": [
                    {
                      "id": 1,
                      "type": "progress_updated",
                      "created_at": "2026-06-20T12:00:00Z",
                      "actor": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
                      "media": {
                        "ref": { "item_id": 4, "source": "openlibrary", "media_type": "book", "media_id": "OL1M", "season_number": null, "episode_number": null },
                        "title": "Progress Book",
                        "image_url": null
                      },
                      "object": {
                        "type": "progress_change",
                        "id": 9,
                        "previous": { "kind": "percentage", "value": 42, "max": 100, "unit": "percent" },
                        "current": { "kind": "percentage", "value": 58, "max": 100, "unit": "percent" },
                        "liked": false
                      },
                      "viewer": { "can_view": true, "has_liked": false }
                    }
                  ]
                }
                """.data(using: .utf8)!
            )
        }

        let items = try await repository.userActivity(username: "mobile", limit: 6)

        XCTAssertEqual(items.first?.type, "progress_updated")
        XCTAssertEqual(items.first?.object.current?.compactDisplayText, "58%")
        XCTAssertEqual(items.first?.object.liked, false)
        client.tokenProvider.clear()
    }

    func testDiaryRepositorySendsTagQueryAndLoadsPages() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIDiaryRepository(client: client)
        var requestedURLs: [String] = []

        RequestCaptureURLProtocol.handler = { request in
            requestedURLs.append(request.url?.absoluteString ?? "")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")

            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(query.first { $0.name == "tag" }?.value, "comfort")

            if query.contains(where: { $0.name == "page" && $0.value == "2" }) {
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    """
                    {
                      "count": 2,
                      "next": null,
                      "previous": "https://example.com/api/v1/diary/?tag=comfort",
                      "results": [\(TestFixtures.diaryEntryJSON(id: 2, mediaId: "551", title: "Second Tagged Log", tags: ["comfort"]))]
                    }
                    """.data(using: .utf8)!
                )
            }

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "count": 2,
                  "next": "https://example.com/api/v1/diary/?tag=comfort&page=2",
                  "previous": null,
                  "results": [\(TestFixtures.diaryEntryJSON(id: 1, mediaId: "550", title: "First Tagged Log", tags: ["comfort"]))]
                }
                """.data(using: .utf8)!
            )
        }

        let entries = try await repository.list(tag: " comfort ")

        XCTAssertEqual(entries.map(\.id), [1, 2])
        XCTAssertEqual(requestedURLs, [
            "https://example.com/api/v1/diary/?tag=comfort",
            "https://example.com/api/v1/diary/?tag=comfort&page=2",
        ])
        client.tokenProvider.clear()
    }

    func testDiaryRepositorySendsProfileMenuFilters() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIDiaryRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(components?.path, "/api/v1/diary/")
            XCTAssertEqual(query.first { $0.name == "has_review" }?.value, "true")
            XCTAssertEqual(query.first { $0.name == "liked" }?.value, "true")

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"count":0,"next":null,"previous":null,"results":[]}"#.data(using: .utf8)!
            )
        }

        let entries = try await repository.list(filter: DiaryFilter(hasReview: true, liked: true))

        XCTAssertTrue(entries.isEmpty)
        client.tokenProvider.clear()
    }

    func testDiaryRepositoryUpdatesEntry() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIDiaryRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "PATCH")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            XCTAssertEqual(request.url?.path, "/api/v1/diary/42")

            let body = try JSONSerialization.jsonObject(with: requestBodyData(for: request)) as! [String: Any]
            XCTAssertEqual(body["review_title"] as? String, "Better")
            XCTAssertEqual(body["review"] as? String, "Updated")
            XCTAssertEqual(body["rating"] as? Int, 8)
            XCTAssertEqual(body["liked"] as? Bool, true)
            XCTAssertEqual(body["is_rewatch"] as? Bool, true)
            XCTAssertEqual(body["contains_spoilers"] as? Bool, true)
            XCTAssertEqual(body["visibility"] as? String, "private")
            XCTAssertEqual(body["tags"] as? [String], ["sharp"])

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                TestFixtures.diaryEntryJSON(id: 42, mediaId: "550", title: "Liquid Form", tags: ["sharp"]).data(using: .utf8)!
            )
        }

        let entry = try await repository.update(
            id: 42,
            request: DiaryEntryUpdateRequest(
                consumedAt: nil,
                rating: Decimal(8),
                review: "Updated",
                reviewTitle: "Better",
                tags: ["sharp"],
                liked: true,
                isRewatch: true,
                containsSpoilers: true,
                visibility: "private"
            )
        )

        XCTAssertEqual(entry.id, 42)
        client.tokenProvider.clear()
    }

    func testDiaryRepositoryDeletesEntry() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIDiaryRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            XCTAssertEqual(request.url?.path, "/api/v1/diary/42")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 204, httpVersion: nil, headerFields: nil)!,
                Data()
            )
        }

        try await repository.delete(id: 42)
        client.tokenProvider.clear()
    }

    func testProfileRepositoryLoadsLikedMediaPages() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIProfileRepository(client: client)
        var requestedURLs: [String] = []

        RequestCaptureURLProtocol.handler = { request in
            requestedURLs.append(request.url?.absoluteString ?? "")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")

            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(components?.path, "/api/v1/me/liked-media/")

            if query.contains(where: { $0.name == "page" && $0.value == "2" }) {
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    """
                    {
                      "count": 2,
                      "next": null,
                      "previous": "https://example.com/api/v1/me/liked-media/",
                      "results": [\(TestFixtures.mediaSummaryJSON(mediaId: "551", title: "Second Like"))]
                    }
                    """.data(using: .utf8)!
                )
            }

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "count": 2,
                  "next": "https://example.com/api/v1/me/liked-media/?page=2",
                  "previous": null,
                  "results": [\(TestFixtures.mediaSummaryJSON(mediaId: "550", title: "First Like"))]
                }
                """.data(using: .utf8)!
            )
        }

        let media = try await repository.likedMedia()

        XCTAssertEqual(media.map(\.title), ["First Like", "Second Like"])
        XCTAssertEqual(requestedURLs, [
            "https://example.com/api/v1/me/liked-media/",
            "https://example.com/api/v1/me/liked-media/?page=2",
        ])
        client.tokenProvider.clear()
    }

    func testListRepositorySendsManagementRequests() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIListRepository(client: client)
        let ref = MediaRef(itemId: 42, source: "tmdb", mediaType: "movie", mediaId: "550", seasonNumber: nil, episodeNumber: nil)
        var requests: [(method: String?, url: String)] = []

        RequestCaptureURLProtocol.handler = { request in
            requests.append((request.httpMethod, request.url!.absoluteString))
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")

            let method = request.httpMethod ?? ""
            let path = request.url!.path
            if method == "GET", path.hasSuffix("/lists/") || path.hasSuffix("/lists") {
                let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
                XCTAssertEqual(query.first { $0.name == "ref[source]" }?.value, "tmdb")
                XCTAssertEqual(query.first { $0.name == "ref[media_type]" }?.value, "movie")
                XCTAssertEqual(query.first { $0.name == "ref[media_id]" }?.value, "550")
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    #"{"count":0,"next":null,"previous":null,"results":[]}"#.data(using: .utf8)!
                )
            }
            if method == "POST", path.hasSuffix("/lists/") || path.hasSuffix("/lists") {
                let body = try JSONDecoder.api.decode(CustomListWriteRequestEcho.self, from: requestBodyData(for: request))
                XCTAssertEqual(body.name, "Watch")
                XCTAssertEqual(body.visibility, "private")
                XCTAssertEqual(body.isRanked, true)
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 201, httpVersion: nil, headerFields: nil)!,
                    TestFixtures.customListSummaryJSON(id: 9, name: "Watch").data(using: .utf8)!
                )
            }
            if method == "POST", path.hasSuffix("/lists/9/items/") || path.hasSuffix("/lists/9/items") {
                let body = try JSONDecoder.api.decode(ListItemWriteRequestEcho.self, from: requestBodyData(for: request))
                XCTAssertEqual(body.ref.mediaId, "550")
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 201, httpVersion: nil, headerFields: nil)!,
                    #"{"item":{"ref":{"item_id":42,"source":"tmdb","media_type":"movie","media_id":"550","season_number":null,"episode_number":null},"title":"Fight Club","image_url":null}}"#.data(using: .utf8)!
                )
            }
            if method == "PATCH", path.hasSuffix("/lists/9/items/reorder/") || path.hasSuffix("/lists/9/items/reorder") {
                let body = try JSONDecoder.api.decode(ListItemsReorderRequestEcho.self, from: requestBodyData(for: request))
                XCTAssertEqual(body.itemIds, [42, 17])
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    TestFixtures.customListDetailJSON(id: 9, name: "Watch").data(using: .utf8)!
                )
            }
            if method == "DELETE", path.hasSuffix("/lists/9/") || path.hasSuffix("/lists/9") {
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 204, httpVersion: nil, headerFields: nil)!,
                    Data()
                )
            }
            XCTFail("Unexpected request \(request.httpMethod ?? "") \(request.url!.absoluteString)")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!,
                Data()
            )
        }

        _ = try await repository.list(membershipFor: ref)
        _ = try await repository.create(CustomListWriteRequest(name: "Watch", description: "", visibility: "private", isRanked: true))
        _ = try await repository.addItem(listId: 9, ref: ref)
        _ = try await repository.reorderItems(listId: 9, itemIds: [42, 17])
        try await repository.delete(id: 9)

        XCTAssertEqual(requests.map(\.method), ["GET", "POST", "POST", "PATCH", "DELETE"])
        client.tokenProvider.clear()
    }

    func testMediaRepositorySendsLikedMediaWriteBody() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIMediaRepository(client: client)
        let ref = MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "550", seasonNumber: nil, episodeNumber: nil)
        var methods: [String] = []

        RequestCaptureURLProtocol.handler = { request in
            methods.append(request.httpMethod ?? "")
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/me/liked-media/")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            let body = try JSONDecoder.api.decode(HallOfFameItemWriteRequest.self, from: requestBodyData(for: request))
            XCTAssertEqual(body.ref.mediaId, "550")

            let liked = request.httpMethod == "POST"
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "liked": \(liked ? "true" : "false"),
                  "media": \(TestFixtures.mediaSummaryJSON(mediaId: "550", title: "Fight Club"))
                }
                """.data(using: .utf8)!
            )
        }

        let liked = try await repository.setLiked(ref: ref, liked: true)
        let unliked = try await repository.setLiked(ref: ref, liked: false)

        XCTAssertEqual(methods, ["POST", "DELETE"])
        XCTAssertTrue(liked.liked)
        XCTAssertFalse(unliked.liked)
        client.tokenProvider.clear()
    }

    func testDiaryRepositorySendsItemFilter() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIDiaryRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(components?.path, "/api/v1/diary/")
            XCTAssertEqual(query.first { $0.name == "item_id" }?.value, "42")

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"count":0,"next":null,"previous":null,"results":[]}"#.data(using: .utf8)!
            )
        }

        let entries = try await repository.list(filter: DiaryFilter(itemId: 42))

        XCTAssertTrue(entries.isEmpty)
        client.tokenProvider.clear()
    }

    func testDiaryRepositorySendsMineTagQuery() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIDiaryRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(components?.path, "/api/v1/diary/tags/")
            XCTAssertEqual(query.first { $0.name == "mine" }?.value, "true")

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"results":[{"name":"comfort","usage_count":2}]}"#.data(using: .utf8)!
            )
        }

        let tags = try await repository.tags(query: "", mine: true)

        XCTAssertEqual(tags.first?.name, "comfort")
        XCTAssertEqual(tags.first?.usageCount, 2)
        client.tokenProvider.clear()
    }

    func testDiaryRepositorySendsAllTagsQuery() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIDiaryRepository(client: client)

        RequestCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = components?.queryItems ?? []
            XCTAssertEqual(components?.path, "/api/v1/diary/tags/")
            XCTAssertEqual(query.first { $0.name == "mine" }?.value, "true")
            XCTAssertEqual(query.first { $0.name == "all" }?.value, "true")

            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"results":[{"name":"comfort","usage_count":2},{"name":"rewatch","usage_count":1}]}"#.data(using: .utf8)!
            )
        }

        let tags = try await repository.allTags(mine: true)

        XCTAssertEqual(tags.map(\.name), ["comfort", "rewatch"])
        client.tokenProvider.clear()
    }

    @MainActor
    func testTaggedDiaryGridDeduplicatesMediaInOrder() throws {
        let first = try JSONDecoder.api.decode(
            DiaryEntry.self,
            from: TestFixtures.diaryEntryJSON(id: 1, mediaId: "550", title: "First Log", tags: ["comfort"]).data(using: .utf8)!
        )
        let duplicate = try JSONDecoder.api.decode(
            DiaryEntry.self,
            from: TestFixtures.diaryEntryJSON(id: 2, mediaId: "550", title: "Duplicate Log", tags: ["comfort"]).data(using: .utf8)!
        )
        let second = try JSONDecoder.api.decode(
            DiaryEntry.self,
            from: TestFixtures.diaryEntryJSON(id: 3, mediaId: "551", title: "Second Media", tags: ["comfort"]).data(using: .utf8)!
        )

        let media = TaggedDiaryViewModel.uniqueMedia(from: [first, duplicate, second])

        XCTAssertEqual(media.map(\.entry.id), [1, 3])
        XCTAssertEqual(media.map(\.media.ref.mediaId), ["550", "551"])
    }

    @MainActor
    func testTaggedDiaryViewModelCallsUnauthorizedHandler() async {
        let repository = TaggedDiaryFixtureRepository(result: .failure(APIError.unauthorized))
        var didAuthorize = false
        let viewModel = TaggedDiaryViewModel(tag: "comfort", diaryRepository: repository) {
            didAuthorize = true
        }

        await viewModel.load()

        XCTAssertEqual(repository.requestedTags, ["comfort"])
        XCTAssertTrue(didAuthorize)
        XCTAssertNotNil(viewModel.errorMessage)
    }

    func testDiaryLogDateLabelKeepsImportedCalendarDate() {
        XCTAssertEqual(DiaryLogFormat.dateLabel("2026-06-06T00:00:00Z"), "Jun 6, 2026")
        XCTAssertEqual(DiaryLogFormat.dateLabel("2026-06-06"), "Jun 6, 2026")
    }

    func testDiaryLogAgeLabelUsesCalendarComponents() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let now = try XCTUnwrap(calendar.date(from: DateComponents(year: 2024, month: 5, day: 10, hour: 12)))
        let oneYearAndOneDayLater = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 6, day: 24, hour: 12)))

        XCTAssertNil(DiaryLogFormat.ageLabel("2024-05-10", now: now, calendar: calendar))
        XCTAssertNil(DiaryLogFormat.ageLabel("2024-05-11", now: now, calendar: calendar))
        XCTAssertEqual(DiaryLogFormat.ageLabel("2024-05-09", now: now, calendar: calendar), "1 day ago")
        XCTAssertEqual(DiaryLogFormat.ageLabel("2025-06-23", now: oneYearAndOneDayLater, calendar: calendar), "1 year and 1 day ago")
        XCTAssertEqual(DiaryLogFormat.ageLabel("2023-03-06", now: now, calendar: calendar), "1 year, 2 months and 4 days ago")
        XCTAssertEqual(DiaryLogFormat.ageLabel("2024-03-06", now: now, calendar: calendar), "2 months and 4 days ago")
        XCTAssertEqual(DiaryLogFormat.ageLabel("2023-03-06T00:00:00Z", now: now, calendar: calendar), "1 year, 2 months and 4 days ago")
    }

    func testRichMediaDetailAndReviewDecoding() throws {
        let detail = try JSONDecoder.api.decode(
            MediaDetail.self,
            from: TestFixtures.richMediaDetailJSON.data(using: .utf8)!
        )
        let tv = try JSONDecoder.api.decode(
            MediaDetail.self,
            from: TestFixtures.tvDetailJSON.data(using: .utf8)!
        )
        let season = try JSONDecoder.api.decode(
            MediaDetail.self,
            from: TestFixtures.seasonDetailJSON.data(using: .utf8)!
        )
        let anime = try JSONDecoder.api.decode(
            MediaDetail.self,
            from: TestFixtures.animeDetailJSON.data(using: .utf8)!
        )
        let reviews = try JSONDecoder.api.decode(
            PagedResponse<MediaReview>.self,
            from: TestFixtures.reviewsJSON.data(using: .utf8)!
        )

        XCTAssertEqual(detail.title, "Liquid Form")
        XCTAssertEqual(detail.userState?.diaryRating, "10.0")
        XCTAssertEqual(detail.userState?.diaryConsumedAt, "2026-06-20T12:00:00Z")
        XCTAssertEqual(detail.externalRatings?.count, 4)
        XCTAssertEqual(detail.externalRatings?.first { $0.source == "IMDb" }?.voteCount, 84231)
        XCTAssertEqual(detail.customBackdropUrl, "https://example.com/custom-backdrop.jpg")
        XCTAssertEqual(detail.cast?.first?.character, "Mika")
        XCTAssertEqual(detail.relatedSections?.first?.items.first?.title, "Pulp Fiction")
        XCTAssertEqual(tv.seasons?.first?.seasonNumber, 1)
        XCTAssertEqual(season.episodes?.first?.runtime, "49m")
        XCTAssertEqual(season.episodes?.first?.overview, "Ada finds the first card.")
        XCTAssertEqual(anime.relatedSections?.count, 2)
        XCTAssertEqual(reviews.results.first?.reviewTitle, "A pulse under glass")
        XCTAssertEqual(reviews.results.last?.containsSpoilers, true)
    }

    func testHallOfFameItemsResponseDecodesMediaSummaryMap() throws {
        let data = """
        {
          "items": {
            "movie": {
              "ref": {
                "item_id": 42,
                "source": "tmdb",
                "media_type": "movie",
                "media_id": "550",
                "season_number": null,
                "episode_number": null
              },
              "title": "Fight Club",
              "image_url": "https://example.com/fight-club.jpg",
              "poster_url": "https://example.com/fight-club.jpg",
              "poster_orientation": "portrait"
            },
            "tv": null
          }
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.api.decode(HallOfFameItemsResponse.self, from: data)

        XCTAssertEqual(response.items["movie"]??.title, "Fight Club")
        XCTAssertEqual(response.items["movie"]??.ref.itemId, 42)
        XCTAssertNil(response.items["tv"]!)
    }

    @MainActor
    func testProfileViewModelUpdatesHallOfFameLocally() async throws {
        let movie = MediaSummary(
            ref: MediaRef(itemId: 42, source: "tmdb", mediaType: "movie", mediaId: "550", seasonNumber: nil, episodeNumber: nil),
            title: "Fight Club",
            posterUrl: "https://example.com/fight-club.jpg"
        )
        let repository = HallOfFameProfileRepository(
            profile: profileFixture(hof: ["movie": nil]),
            setResponse: ["movie": movie],
            clearResponse: ["movie": nil]
        )
        let viewModel = ProfileViewModel(
            profileRepository: repository,
            trackingRepository: ScriptedLibraryTrackingRepository(responses: [:]),
            activityRepository: ScriptedHomeActivityRepository(items: []),
            onUnauthorized: {}
        )

        await viewModel.load()
        let didSet = await viewModel.setHallOfFameItem(mediaType: "movie", ref: movie.ref)
        XCTAssertEqual(viewModel.profile?.hof["movie"]??.title, "Fight Club")

        let didClear = await viewModel.clearHallOfFameItem(mediaType: "movie")

        XCTAssertTrue(didSet)
        XCTAssertTrue(didClear)
        XCTAssertEqual(repository.setMediaType, "movie")
        XCTAssertEqual(repository.setRef, movie.ref)
        XCTAssertEqual(repository.clearMediaType, "movie")
        XCTAssertNil(viewModel.profile?.hof["movie"]!)
    }

    @MainActor
    func testProfileViewModelLoadsInProgressAcrossEnabledMediaTypes() async {
        let trackingRepository = ScriptedLibraryTrackingRepository(responses: [
            "movie:": PagedResponse(
                count: 1,
                next: nil,
                previous: nil,
                results: [
                    libraryItem(
                        id: "1",
                        title: "Older Movie",
                        mediaType: "movie",
                        status: "In progress",
                        updatedAt: "2026-06-20T12:00:00Z"
                    )
                ]
            ),
            "season:": PagedResponse(
                count: 1,
                next: nil,
                previous: nil,
                results: [
                    libraryItem(
                        id: "2",
                        title: "Newest Season",
                        mediaType: "season",
                        status: "In progress",
                        updatedAt: "2026-06-22T12:00:00Z",
                        seasonNumber: 2
                    )
                ]
            ),
            "book:": PagedResponse(
                count: 1,
                next: nil,
                previous: nil,
                results: [
                    libraryItem(
                        id: "3",
                        title: "Middle Book",
                        mediaType: "book",
                        status: "In progress",
                        updatedAt: "2026-06-21T12:00:00Z"
                    )
                ]
            )
        ])
        let viewModel = ProfileViewModel(
            profileRepository: HallOfFameProfileRepository(
                profile: profileFixture(hof: [:], enabledMediaTypes: ["movie", "tv", "book"]),
                setResponse: [:],
                clearResponse: [:]
            ),
            trackingRepository: trackingRepository,
            activityRepository: ScriptedHomeActivityRepository(items: []),
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Newest Season", "Middle Book", "Older Movie"])
        XCTAssertEqual(viewModel.inProgressItems.first?.media.ref.mediaType, "season")
        XCTAssertEqual(trackingRepository.requests, [
            LibraryTrackingRequest(mediaType: "movie", page: nil, status: "In progress"),
            LibraryTrackingRequest(mediaType: "season", page: nil, status: "In progress"),
            LibraryTrackingRequest(mediaType: "book", page: nil, status: "In progress")
        ])
    }

    @MainActor
    func testProfileViewModelLoadsRecentActivityFromActivityRepository() async {
        let activityRepository = ScriptedHomeActivityRepository(items: [
            activityItem(id: 1, title: "Progress"),
            activityItem(id: 2, title: "Diary", type: "diary_created", previous: nil, current: nil, rating: "8.0", liked: true)
        ])
        let viewModel = ProfileViewModel(
            profileRepository: HallOfFameProfileRepository(
                profile: profileFixture(hof: [:], enabledMediaTypes: ["movie"]),
                setResponse: [:],
                clearResponse: [:]
            ),
            trackingRepository: ScriptedLibraryTrackingRepository(responses: [:]),
            activityRepository: activityRepository,
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.recentActivityItems.map(\.type), ["progress_updated", "diary_created"])
        XCTAssertEqual(activityRepository.requests, [ActivityRequest(username: "mobile", limit: 6)])
    }

    func testHardcoverBookSeriesRelatedSectionDecoding() throws {
        let data = """
        {
          "ref": { "item_id": null, "source": "hardcover", "media_type": "book", "media_id": "377193", "season_number": null, "episode_number": null },
          "title": "Harry Potter and the Sorcerer's Stone",
          "related_sections": [
            {
              "id": "series",
              "title": "Harry Potter",
              "items": [
                { "ref": { "item_id": null, "source": "hardcover", "media_type": "book", "media_id": "377193", "season_number": null, "episode_number": null }, "title": "Harry Potter and the Sorcerer's Stone", "image_url": "https://example.com/hp1.jpg" },
                { "ref": { "item_id": null, "source": "hardcover", "media_type": "book", "media_id": "377194", "season_number": null, "episode_number": null }, "title": "Harry Potter and the Chamber of Secrets", "image_url": "https://example.com/hp2.jpg" }
              ]
            }
          ]
        }
        """.data(using: .utf8)!

        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: data)

        XCTAssertEqual(detail.relatedSections?.first?.id, "series")
        XCTAssertEqual(detail.relatedSections?.first?.title, "Harry Potter")
        XCTAssertEqual(detail.relatedSections?.first?.items.map(\.title), [
            "Harry Potter and the Sorcerer's Stone",
            "Harry Potter and the Chamber of Secrets",
        ])
    }

    func testCommunityStatsDecodesRatingDistribution() throws {
        let data = """
        {
          "average_rating": "8.0",
          "rating_count": 2,
          "diary_count": 3,
          "review_count": 1,
          "liked_count": 0,
          "rating_distribution": [
            { "rating": "8.0", "count": 2 }
          ]
        }
        """.data(using: .utf8)!

        let stats = try JSONDecoder.api.decode(CommunityStats.self, from: data)

        XCTAssertEqual(stats.averageRating, "8.0")
        XCTAssertEqual(stats.ratingDistribution.first?.rating, "8.0")
        XCTAssertEqual(stats.ratingDistribution.first?.count, 2)
    }

    func testMediaDetailDecodesSynopsisFallback() throws {
        let data = """
        {
          "ref": { "item_id": null, "source": "tmdb", "media_type": "movie", "media_id": "24428", "season_number": null, "episode_number": null },
          "title": "The Avengers",
          "subtitle": "2012",
          "synopsis": "Earth's mightiest heroes must come together.",
          "image_url": "https://example.com/poster.jpg",
          "poster_accent_color": null,
          "release_date": "2012-05-04",
          "default_source": "tmdb",
          "user_state": null,
          "backdrop_url": null,
          "details": {},
          "related": {},
          "providers": null,
          "community": null,
          "external_ratings": null,
          "reviews": null,
          "cast": null,
          "crew": null,
          "related_sections": null,
          "episodes": null,
          "seasons": null,
          "custom_poster_url": null
        }
        """.data(using: .utf8)!

        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: data)

        XCTAssertEqual(detail.synopsis, "Earth's mightiest heroes must come together.")
        XCTAssertEqual(detail.displaySynopsis, "Earth's mightiest heroes must come together.")
    }

    func testMediaDetailPrefersOverviewOverSynopsis() throws {
        let data = """
        {
          "ref": { "item_id": null, "source": "tmdb", "media_type": "movie", "media_id": "24428", "season_number": null, "episode_number": null },
          "title": "The Avengers",
          "subtitle": "2012",
          "overview": "When an unexpected enemy emerges.",
          "synopsis": "Earth's mightiest heroes must come together.",
          "image_url": null,
          "poster_accent_color": null,
          "release_date": "2012-05-04",
          "default_source": "tmdb",
          "user_state": null,
          "backdrop_url": null,
          "details": {},
          "related": {},
          "providers": null,
          "community": null,
          "external_ratings": null,
          "reviews": null,
          "cast": null,
          "crew": null,
          "related_sections": null,
          "episodes": null,
          "seasons": null,
          "custom_poster_url": null
        }
        """.data(using: .utf8)!

        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: data)

        XCTAssertEqual(detail.displaySynopsis, "When an unexpected enemy emerges.")
    }

    func testCloudflareTunnelErrorMessage() {
        let error = APIError.httpStatus(
            530,
            """
            {"errors":[{"code":1033,"message":"Cloudflare Tunnel error","detail":"Cloudflare is currently unable to resolve the requested application."}]}
            """
        )

        let message = error.localizedDescription
        XCTAssertTrue(message.contains("Can't reach"))
        XCTAssertTrue(message.contains("Cloudflare tunnel"))
    }

    func testAPIClientBuildsPrefixedPaths() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com/root")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/root/api/v1/health/")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"status":"ok","version":"v1","time":"now"}"#.data(using: .utf8)!
            )
        }

        let response: HealthResponse = try await client.get("/health/")
        XCTAssertEqual(response.status, "ok")
    }

    func testAPIClientPreservesTrailingSlashForDjangoEndpoints() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/auth/login/")
            XCTAssertEqual(request.httpMethod, "POST")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "access": "access",
                  "refresh": "refresh",
                  "user": {
                    "id": 1,
                    "username": "mobile",
                    "display_name": "Mobile",
                    "is_private": false
                  }
                }
                """.data(using: .utf8)!
            )
        }

        let service = AuthService(client: client)
        let user = try await service.login(usernameOrEmail: "mobile", password: "password")
        XCTAssertEqual(user.username, "mobile")
    }

    func testAPIClientRefreshesAccessTokenAndRetriesAuthenticatedRequest() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.clear()
        client.tokenProvider.accessToken = "expired"
        client.tokenProvider.refreshToken = "refresh-token"
        defer {
            RequestCaptureURLProtocol.handler = nil
            client.tokenProvider.clear()
        }

        var paths: [String] = []
        RequestCaptureURLProtocol.handler = { request in
            paths.append(request.url?.path ?? "")
            switch paths.count {
            case 1:
                XCTAssertEqual(request.url?.path, "/api/v1/health")
                XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer expired")
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 401, httpVersion: nil, headerFields: nil)!,
                    Data()
                )
            case 2:
                XCTAssertEqual(request.url?.path, "/api/v1/auth/refresh")
                XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
                XCTAssertTrue(String(data: requestBodyData(for: request), encoding: .utf8)?.contains("refresh-token") == true)
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    #"{"access":"fresh-access","refresh":"new-refresh"}"#.data(using: .utf8)!
                )
            case 3:
                XCTAssertEqual(request.url?.path, "/api/v1/health")
                XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fresh-access")
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    #"{"status":"ok","version":"v1","time":"now"}"#.data(using: .utf8)!
                )
            default:
                XCTFail("Unexpected request \(request.url?.absoluteString ?? "")")
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!,
                    Data()
                )
            }
        }

        let response: HealthResponse = try await client.get("/health/", authenticated: true)

        XCTAssertEqual(response.status, "ok")
        XCTAssertEqual(paths, ["/api/v1/health", "/api/v1/auth/refresh", "/api/v1/health"])
        XCTAssertEqual(client.tokenProvider.accessToken, "fresh-access")
        XCTAssertEqual(client.tokenProvider.refreshToken, "new-refresh")
    }

    func testAPIClientClearsTokensWhenRefreshFails() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.clear()
        client.tokenProvider.accessToken = "expired"
        client.tokenProvider.refreshToken = "refresh-token"
        defer {
            RequestCaptureURLProtocol.handler = nil
            client.tokenProvider.clear()
        }

        var paths: [String] = []
        RequestCaptureURLProtocol.handler = { request in
            paths.append(request.url?.path ?? "")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 401, httpVersion: nil, headerFields: nil)!,
                Data()
            )
        }

        do {
            let _: HealthResponse = try await client.get("/health/", authenticated: true)
            XCTFail("Expected unauthorized after refresh failure")
        } catch APIError.unauthorized {
            XCTAssertEqual(paths, ["/api/v1/health", "/api/v1/auth/refresh"])
            XCTAssertNil(client.tokenProvider.accessToken)
            XCTAssertNil(client.tokenProvider.refreshToken)
        }
    }

    func testMediaRepositoryBuildsPosterRequests() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIMediaRepository(client: client)
        let ref = MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "550", seasonNumber: nil, episodeNumber: nil)

        client.tokenProvider.accessToken = "access"
        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/media/tmdb/movie/550/posters/")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"posters":[]}"#.data(using: .utf8)!
            )
        }
        _ = try await repository.posters(ref: ref)

        let bookRef = MediaRef(itemId: nil, source: "openlibrary", mediaType: "book", mediaId: "OL7353617M", seasonNumber: nil, episodeNumber: nil)
        client.tokenProvider.accessToken = "access"
        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/media/openlibrary/book/OL7353617M/posters/")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"posters":[]}"#.data(using: .utf8)!
            )
        }
        _ = try await repository.posters(ref: bookRef)

        client.tokenProvider.accessToken = "access"
        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/media/tmdb/movie/550/poster/")
            XCTAssertEqual(request.httpMethod, "PUT")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {"poster_url":"https://example.com/new.jpg","custom_poster_url":"https://example.com/new.jpg","poster_accent_color":"#123456"}
                """.data(using: .utf8)!
            )
        }
        let response = try await repository.savePoster(ref: ref, posterURL: "https://example.com/new.jpg")

        XCTAssertEqual(response.posterUrl, "https://example.com/new.jpg")

        client.tokenProvider.accessToken = "access"
        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/media/tmdb/movie/550/backdrops/")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                #"{"backdrops":[]}"#.data(using: .utf8)!
            )
        }
        _ = try await repository.backdrops(ref: ref)

        client.tokenProvider.accessToken = "access"
        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/media/tmdb/movie/550/backdrop/")
            XCTAssertEqual(request.httpMethod, "PUT")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            let body = try? JSONSerialization.jsonObject(with: requestBodyData(for: request)) as? [String: String]
            XCTAssertEqual(body?["backdrop_url"], "https://example.com/new.jpg")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {"backdrop_url":"https://example.com/new.jpg","custom_backdrop_url":"https://example.com/new.jpg"}
                """.data(using: .utf8)!
            )
        }
        let backdropResponse = try await repository.saveBackdrop(ref: ref, backdropURL: "https://example.com/new.jpg")

        XCTAssertEqual(backdropResponse.backdropUrl, "https://example.com/new.jpg")
        client.tokenProvider.clear()
    }

    func testMediaRepositoryBuildsDiscoverRequest() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        defer {
            RequestCaptureURLProtocol.handler = nil
            client.tokenProvider.clear()
        }
        let repository = APIMediaRepository(client: client)
        let discoverRequest = MediaDiscoverRequest(mediaType: "movie", source: "tmdb", filter: .genre("Drama"))

        RequestCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })
            XCTAssertEqual(components.path, "/api/v1/media/discover/")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer access")
            XCTAssertEqual(query["media_type"]!, "movie")
            XCTAssertEqual(query["source"]!, "tmdb")
            XCTAssertEqual(query["genre"]!, "Drama")
            XCTAssertEqual(query["sort"]!, "vote_count")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "count": 1,
                  "next": null,
                  "previous": null,
                  "results": [
                    \(TestFixtures.mediaSummaryJSON(mediaId: "550", title: "Fight Club"))
                  ]
                }
                """.data(using: .utf8)!
            )
        }

        let response = try await repository.discover(discoverRequest)

        XCTAssertEqual(response.count, 1)
        XCTAssertEqual(response.results.first?.title, "Fight Club")
    }

    func testMediaRepositoryBuildsBookDiscoverRequest() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        defer {
            RequestCaptureURLProtocol.handler = nil
            client.tokenProvider.clear()
        }
        let repository = APIMediaRepository(client: client)
        let discoverRequest = MediaDiscoverRequest(mediaType: "book", source: "openlibrary", filter: .year("1965"))

        RequestCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })
            XCTAssertEqual(components.path, "/api/v1/media/discover/")
            XCTAssertEqual(query["media_type"]!, "book")
            XCTAssertEqual(query["source"]!, "openlibrary")
            XCTAssertEqual(query["year"]!, "1965")
            XCTAssertEqual(query["sort"]!, "vote_count")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "count": 1,
                  "next": null,
                  "previous": null,
                  "results": [
                    \(TestFixtures.mediaSummaryJSON(mediaId: "OL27448M", source: "openlibrary", mediaType: "book", title: "Dune"))
                  ]
                }
                """.data(using: .utf8)!
            )
        }

        let response = try await repository.discover(discoverRequest)

        XCTAssertEqual(response.count, 1)
        XCTAssertEqual(response.results.first?.ref.mediaType, "book")
    }

    func testMediaRepositoryBuildsTVDiscoverRequest() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        client.tokenProvider.accessToken = "access"
        defer {
            RequestCaptureURLProtocol.handler = nil
            client.tokenProvider.clear()
        }
        let repository = APIMediaRepository(client: client)
        let discoverRequest = MediaDiscoverRequest(mediaType: "tv", source: "tmdb", filter: .genre("Fantasy"))

        RequestCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })
            XCTAssertEqual(components.path, "/api/v1/media/discover/")
            XCTAssertEqual(query["media_type"]!, "tv")
            XCTAssertEqual(query["source"]!, "tmdb")
            XCTAssertEqual(query["genre"]!, "Fantasy")
            XCTAssertEqual(query["sort"]!, "vote_count")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                """
                {
                  "count": 1,
                  "next": null,
                  "previous": null,
                  "results": [
                    \(TestFixtures.mediaSummaryJSON(mediaId: "1399", source: "tmdb", mediaType: "tv", title: "Game of Thrones"))
                  ]
                }
                """.data(using: .utf8)!
            )
        }

        let response = try await repository.discover(discoverRequest)

        XCTAssertEqual(response.count, 1)
        XCTAssertEqual(response.results.first?.ref.mediaType, "tv")
    }

    func testMediaRepositoryBuildsSeasonDetailRequest() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RequestCaptureURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
        let repository = APIMediaRepository(client: client)
        let ref = MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 1, episodeNumber: nil)

        RequestCaptureURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/media/tmdb/tv/1399/seasons/1/")
            XCTAssertEqual(request.httpMethod, "GET")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                TestFixtures.seasonDetailJSON.data(using: .utf8)!
            )
        }

        let detail = try await repository.detail(ref: ref)

        XCTAssertEqual(detail.ref.mediaType, "season")
        XCTAssertEqual(detail.episodes?.count, 3)
    }

    @MainActor
    func testAuthGateSignsOutWithoutTokens() async {
        let auth = FakeAuthRepository(hasStoredTokens: false)
        let session = AppSession(repositories: fakeRepositories(auth: auth))

        await session.start()

        guard case .signedOut = session.state else {
            XCTFail("Expected signed out state.")
            return
        }
    }

    @MainActor
    func testAuthGateRefreshesStoredTokens() async {
        let auth = FakeAuthRepository(hasStoredTokens: true)
        let session = AppSession(repositories: fakeRepositories(auth: auth))

        await session.start()

        XCTAssertEqual(auth.refreshCallCount, 1)
        guard case .signedIn = session.state else {
            XCTFail("Expected signed in state.")
            return
        }
    }

    @MainActor
    func testAuthGateLogsOutAfterRefreshFailure() async {
        let auth = FakeAuthRepository(hasStoredTokens: true, refreshError: APIError.unauthorized)
        let session = AppSession(repositories: fakeRepositories(auth: auth))

        await session.start()

        XCTAssertEqual(auth.logoutCallCount, 1)
        guard case .signedOut = session.state else {
            XCTFail("Expected signed out state.")
            return
        }
    }

    @MainActor
    func testMediaDetailViewModelLoadsReviewsAndTogglesLike() async {
        let viewModel = MediaDetailViewModel(
            ref: TestFixtures.movieDetail.ref,
            mediaRepository: MediaDetailFixtureRepository(),
            trackingRepository: FakeTrackingRepository(),
            diaryRepository: LikeFixtureDiaryRepository(),
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.detail?.title, "Liquid Form")
        XCTAssertEqual(viewModel.reviews.count, 2)
        XCTAssertEqual(viewModel.reviews.first?.viewerHasLiked, false)

        guard let review = viewModel.reviews.first else {
            XCTFail("Expected a review.")
            return
        }
        await viewModel.toggleLike(for: review)

        XCTAssertEqual(viewModel.reviews.first?.viewerHasLiked, true)
        XCTAssertEqual(viewModel.reviews.first?.likeCount, 99)
    }

    @MainActor
    func testMediaDetailLoadFetchesTrackingProgress() async {
        let tracking = RecordingTrackingRepository()
        tracking.detailResponse = trackingProgress(kind: "pages", value: 54, max: 777, unit: "page")
        let viewModel = MediaDetailViewModel(
            ref: TestFixtures.movieDetail.ref,
            mediaRepository: MediaDetailFixtureRepository(),
            trackingRepository: tracking,
            diaryRepository: LikeFixtureDiaryRepository(),
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(tracking.detailRequests, [TestFixtures.movieDetail.ref])
        XCTAssertEqual(viewModel.tracking?.progress?.value(in: .percentage), 7)
    }

    @MainActor
    func testMediaDetailBookFinishedQuickActionUsesCompleteBook() async {
        let detail = TestFixtures.logDetail(mediaType: "book")
        let tracking = RecordingTrackingRepository()
        let diary = RecordingDiaryRepository()
        let viewModel = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: FakeMediaRepository(),
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {}
        )
        let completedAt = Date(timeIntervalSince1970: 1_797_120_000)

        let didSave = await viewModel.performQuickAction(.finished, for: detail, completedAt: completedAt)

        XCTAssertTrue(didSave)
        XCTAssertEqual(tracking.completedBooks.first?.source, "manual")
        XCTAssertEqual(tracking.completedBooks.first?.mediaId, "log-book")
        XCTAssertEqual(tracking.completedBooks.first?.completedAt, completedAt)
        XCTAssertEqual(tracking.consumedRefs.count, 0)
        XCTAssertEqual(diary.createdRequests.count, 0)
    }

    @MainActor
    func testMediaDetailGameFinishedQuickActionUsesConsume() async {
        let detail = TestFixtures.logDetail(mediaType: "game")
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: FakeMediaRepository(),
            trackingRepository: tracking,
            diaryRepository: RecordingDiaryRepository(),
            onUnauthorized: {}
        )
        let completedAt = Date(timeIntervalSince1970: 1_797_120_000)

        let didSave = await viewModel.performQuickAction(.finished, for: detail, completedAt: completedAt)

        XCTAssertTrue(didSave)
        XCTAssertEqual(tracking.consumedRefs.first?.ref, detail.ref)
        XCTAssertEqual(tracking.consumedRefs.first?.consumedAt, completedAt)
        XCTAssertEqual(tracking.completedBooks.count, 0)
    }

    @MainActor
    func testMediaDetailCurrentlyQuickActionSetsInProgress() async {
        let detail = TestFixtures.logDetail(mediaType: "book")
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: FakeMediaRepository(),
            trackingRepository: tracking,
            diaryRepository: RecordingDiaryRepository(),
            onUnauthorized: {}
        )

        let didSave = await viewModel.performQuickAction(.currently, for: detail)

        XCTAssertTrue(didSave)
        XCTAssertEqual(tracking.updateRequests.first?.ref, detail.ref)
        XCTAssertEqual(tracking.updateRequests.first?.request.status, "In progress")
    }

    @MainActor
    func testMediaDetailStoppedQuickActionSetsDropped() async {
        let detail = TestFixtures.logDetail(mediaType: "game")
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: FakeMediaRepository(),
            trackingRepository: tracking,
            diaryRepository: RecordingDiaryRepository(),
            onUnauthorized: {}
        )

        let didSave = await viewModel.performQuickAction(.stopped, for: detail)

        XCTAssertTrue(didSave)
        XCTAssertEqual(tracking.updateRequests.first?.ref, detail.ref)
        XCTAssertEqual(tracking.updateRequests.first?.request.status, "Dropped")
    }

    @MainActor
    func testProgressUpdateBookDefaultsToPagesAndConvertsInput() {
        let detail = TestFixtures.logDetail(mediaType: "book")
        ProgressDisplayPreferences.removeMode(for: detail.ref)
        defer { ProgressDisplayPreferences.removeMode(for: detail.ref) }
        let tracking = trackingProgress(kind: "pages", value: 150, max: 300, unit: "page")
        let viewModel = ProgressUpdateViewModel(
            detail: detail,
            progress: tracking.progress
        )

        XCTAssertEqual(viewModel.mode, .pages)
        XCTAssertEqual(viewModel.totalPages, 300)
        XCTAssertEqual(viewModel.lastValue, 150)
        XCTAssertEqual(viewModel.currentValue, 150)
        XCTAssertFalse(viewModel.canSave)

        viewModel.input = "225"
        viewModel.selectMode(.percentage)

        XCTAssertEqual(viewModel.mode, .percentage)
        XCTAssertEqual(viewModel.input, "75")
        XCTAssertEqual(viewModel.lastValue, 50)
        XCTAssertEqual(viewModel.deltaText, "+25% read")
    }

    @MainActor
    func testProgressUpdateUsesSavedPercentModeForPageProgress() {
        let detail = TestFixtures.logDetail(mediaType: "book")
        ProgressDisplayPreferences.setMode(.percentage, for: detail.ref)
        defer { ProgressDisplayPreferences.removeMode(for: detail.ref) }
        let tracking = trackingProgress(kind: "pages", value: 21, max: 300, unit: "page")

        let viewModel = ProgressUpdateViewModel(
            detail: detail,
            progress: tracking.progress
        )

        XCTAssertEqual(viewModel.mode, .percentage)
        XCTAssertEqual(viewModel.lastValue, 7)
        XCTAssertEqual(viewModel.currentValue, 7)
        XCTAssertEqual(viewModel.lastValueText, "7%")
    }

    @MainActor
    func testProgressUpdateGameUsesSavedPercentModeForMinuteProgress() {
        let detail = TestFixtures.logDetail(mediaType: "game")
        ProgressDisplayPreferences.setMode(.percentage, for: detail.ref)
        defer { ProgressDisplayPreferences.removeMode(for: detail.ref) }
        let tracking = trackingProgress(kind: "progress", value: 58, max: nil, unit: "minutes")

        let viewModel = ProgressUpdateViewModel(
            detail: detail,
            progress: tracking.progress
        )

        XCTAssertEqual(viewModel.mode, .percentage)
        XCTAssertEqual(viewModel.lastValue, 58)
        XCTAssertEqual(viewModel.currentValue, 58)
        XCTAssertEqual(viewModel.lastValueText, "58%")
    }

    @MainActor
    func testProgressUpdateFirstTypedDigitReplacesPrefilledValue() {
        let detail = TestFixtures.logDetail(mediaType: "book")
        ProgressDisplayPreferences.setMode(.percentage, for: detail.ref)
        defer { ProgressDisplayPreferences.removeMode(for: detail.ref) }
        let tracking = trackingProgress(kind: "percentage", value: 25, max: 100, unit: "%")
        let viewModel = ProgressUpdateViewModel(
            detail: detail,
            progress: tracking.progress
        )

        XCTAssertEqual(viewModel.input, "25")
        XCTAssertEqual(viewModel.currentValue, 25)
        XCTAssertEqual(viewModel.keyboardInputText, "")

        let fieldText = viewModel.applyKeyboardInput(
            currentText: viewModel.keyboardInputText,
            range: NSRange(location: 0, length: 0),
            replacement: "6"
        )
        XCTAssertEqual(fieldText, "6")
        XCTAssertEqual(viewModel.input, "6")
        XCTAssertTrue(viewModel.canSave)
    }

    @MainActor
    func testProgressUpdateValidationAndFullProgress() {
        let bookDetail = TestFixtures.logDetail(mediaType: "book")
        ProgressDisplayPreferences.removeMode(for: bookDetail.ref)
        defer { ProgressDisplayPreferences.removeMode(for: bookDetail.ref) }
        let bookViewModel = ProgressUpdateViewModel(
            detail: bookDetail,
            progress: nil
        )

        bookViewModel.input = "301"
        XCTAssertNotNil(bookViewModel.validationMessage)
        XCTAssertFalse(bookViewModel.canSave)

        bookViewModel.input = "300"
        XCTAssertNil(bookViewModel.validationMessage)
        XCTAssertTrue(bookViewModel.canSave)
        XCTAssertTrue(bookViewModel.isFullProgress)

        let gameViewModel = ProgressUpdateViewModel(
            detail: TestFixtures.logDetail(mediaType: "game"),
            progress: nil
        )

        XCTAssertEqual(gameViewModel.mode, .percentage)
        gameViewModel.input = "101"
        XCTAssertNotNil(gameViewModel.validationMessage)
        XCTAssertFalse(gameViewModel.canSave)

        gameViewModel.input = "100"
        XCTAssertNil(gameViewModel.validationMessage)
        XCTAssertTrue(gameViewModel.isFullProgress)
    }

    @MainActor
    func testMediaDetailBookProgressSaveUsesBookEndpoint() async {
        let detail = TestFixtures.logDetail(mediaType: "book")
        ProgressDisplayPreferences.removeMode(for: detail.ref)
        defer { ProgressDisplayPreferences.removeMode(for: detail.ref) }
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: FakeMediaRepository(),
            trackingRepository: tracking,
            diaryRepository: RecordingDiaryRepository(),
            onUnauthorized: {}
        )

        let didSave = await viewModel.saveProgress(
            ProgressUpdateSaveRequest(mode: .percentage, value: 42),
            for: detail
        )

        XCTAssertTrue(didSave)
        XCTAssertEqual(tracking.bookProgressRequests.first?.source, "manual")
        XCTAssertEqual(tracking.bookProgressRequests.first?.mediaId, "log-book")
        XCTAssertEqual(tracking.bookProgressRequests.first?.progressType, "percentage")
        XCTAssertEqual(tracking.bookProgressRequests.first?.value, Decimal(42))
        XCTAssertEqual(tracking.bookProgressRequests.first?.notes, "")
        XCTAssertEqual(tracking.updateRequests.count, 0)
        XCTAssertEqual(viewModel.tracking?.status, "In progress")
        XCTAssertEqual(viewModel.tracking?.progress?.kind, "percentage")
        XCTAssertEqual(viewModel.tracking?.progress?.value, Decimal(42))
        XCTAssertEqual(viewModel.tracking?.progress?.compactDisplayText, "42%")
        XCTAssertEqual(ProgressDisplayPreferences.mode(for: detail.ref), .percentage)
    }

    @MainActor
    func testMediaDetailGameProgressSaveUsesGenericTrackingUpdateWithoutCompleting() async {
        let detail = TestFixtures.logDetail(mediaType: "game")
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: FakeMediaRepository(),
            trackingRepository: tracking,
            diaryRepository: RecordingDiaryRepository(),
            onUnauthorized: {}
        )

        let didSave = await viewModel.saveProgress(
            ProgressUpdateSaveRequest(mode: .percentage, value: 100),
            for: detail
        )

        XCTAssertTrue(didSave)
        XCTAssertEqual(tracking.updateRequests.first?.ref, detail.ref)
        XCTAssertEqual(tracking.updateRequests.first?.request.status, "In progress")
        XCTAssertEqual(tracking.updateRequests.first?.request.progress, 100)
        XCTAssertEqual(tracking.bookProgressRequests.count, 0)
        XCTAssertEqual(tracking.consumedRefs.count, 0)
        XCTAssertEqual(tracking.completedBooks.count, 0)
        XCTAssertEqual(viewModel.tracking?.progress?.compactDisplayText, "100%")
    }

    @MainActor
    func testDiaryLogDetailViewModelLoadsEntryAndKeepsFallbackOnMediaFailure() async {
        let diary = DiaryLogFixtureDiaryRepository(entry: TestFixtures.diaryEntry)
        let media = DiaryLogFixtureMediaRepository(result: .success(TestFixtures.movieDetail))
        let viewModel = DiaryLogDetailViewModel(
            entryId: 1,
            diaryRepository: diary,
            mediaRepository: media,
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(diary.requestedIds, [1])
        XCTAssertEqual(media.requestedRefs, [TestFixtures.diaryEntry.media.ref])
        XCTAssertEqual(viewModel.entry?.id, 1)
        XCTAssertEqual(viewModel.mediaDetail?.title, "Liquid Form")
        XCTAssertNil(viewModel.errorMessage)

        let failingMedia = DiaryLogFixtureMediaRepository(result: .failure(APIError.httpStatus(503, nil)))
        let fallbackViewModel = DiaryLogDetailViewModel(
            entryId: 1,
            diaryRepository: DiaryLogFixtureDiaryRepository(entry: TestFixtures.diaryEntry),
            mediaRepository: failingMedia,
            onUnauthorized: {}
        )

        await fallbackViewModel.load()

        XCTAssertEqual(fallbackViewModel.entry?.id, 1)
        XCTAssertNil(fallbackViewModel.mediaDetail)
        XCTAssertNil(fallbackViewModel.errorMessage)
    }

    @MainActor
    func testPosterPickerViewModelFiltersAndSaves() async {
        var savedResponse: PosterSaveResponse?
        let viewModel = PosterPickerViewModel(
            ref: TestFixtures.movieDetail.ref,
            mediaRepository: PosterFixtureRepository(),
            onUnauthorized: {},
            onSaved: { savedResponse = $0 }
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.selectedLanguage, "en")
        XCTAssertEqual(viewModel.filteredPosters.map(\.url), ["https://example.com/en.jpg"])
        XCTAssertEqual(viewModel.selectedPosterURL, "https://example.com/en.jpg")

        viewModel.selectedLanguage = "none"
        XCTAssertEqual(viewModel.filteredPosters.map(\.url), ["https://example.com/en.jpg", "https://example.com/no-language.jpg"])

        viewModel.selectedPosterURL = "https://example.com/no-language.jpg"
        await viewModel.save()

        XCTAssertEqual(savedResponse?.customPosterUrl, "https://example.com/no-language.jpg")
    }

    @MainActor
    func testBackdropPickerViewModelFiltersAndSaves() async {
        var savedResponse: BackdropSaveResponse?
        let viewModel = BackdropPickerViewModel(
            ref: TestFixtures.movieDetail.ref,
            mediaRepository: PosterFixtureRepository(),
            onUnauthorized: {},
            onSaved: { savedResponse = $0 }
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.selectedLanguage, "all")
        XCTAssertEqual(viewModel.filteredBackdrops.map(\.url), [
            "https://example.com/backdrop-en.jpg",
            "https://example.com/backdrop-fr.jpg",
            "https://example.com/backdrop-no-language.jpg",
        ])
        XCTAssertEqual(viewModel.selectedBackdropURL, "https://example.com/backdrop-en.jpg")

        viewModel.selectedLanguage = "none"
        XCTAssertEqual(viewModel.filteredBackdrops.map(\.url), ["https://example.com/backdrop-en.jpg", "https://example.com/backdrop-no-language.jpg"])

        viewModel.selectedBackdropURL = "https://example.com/backdrop-no-language.jpg"
        await viewModel.save()

        XCTAssertEqual(savedResponse?.customBackdropUrl, "https://example.com/backdrop-no-language.jpg")
    }

    @MainActor
    func testMediaLogRatingMapping() {
        XCTAssertNil(MediaLogViewModel.ratingDecimal(for: 0))
        XCTAssertEqual(MediaLogViewModel.ratingDecimal(for: 1), Decimal(1))
        XCTAssertEqual(MediaLogViewModel.ratingDecimal(for: 10), Decimal(10))

        let viewModel = MediaLogViewModel(
            detail: TestFixtures.movieDetail,
            trackingRepository: RecordingTrackingRepository(),
            diaryRepository: RecordingDiaryRepository(),
            onUnauthorized: {},
            onSaved: {}
        )
        viewModel.setRating(star: 4, half: true)

        XCTAssertEqual(viewModel.ratingSteps, 7)
        XCTAssertEqual(viewModel.ratingLabel(), "3.5/5")

        viewModel.setRating(locationX: 25, width: 100)
        XCTAssertEqual(viewModel.ratingSteps, 3)
    }

    @MainActor
    func testMediaLogCreatesFinishedDiaryLog() async {
        let diary = RecordingDiaryRepository()
        let tracking = RecordingTrackingRepository()
        var savedCount = 0
        let viewModel = MediaLogViewModel(
            detail: TestFixtures.movieDetail,
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {},
            onSaved: { savedCount += 1 }
        )
        viewModel.ratingSteps = 9
        viewModel.review = "Still cuts."
        viewModel.tags = ["noir"]
        viewModel.liked = true
        viewModel.isRepeat = true
        viewModel.containsSpoilers = true

        let didSave = await viewModel.save()

        XCTAssertTrue(didSave)
        XCTAssertEqual(savedCount, 1)
        XCTAssertEqual(diary.createdRequests.count, 1)
        XCTAssertEqual(diary.createdRequests.first?.ref.mediaType, "movie")
        XCTAssertEqual(diary.createdRequests.first?.rating, Decimal(9))
        XCTAssertEqual(diary.createdRequests.first?.autoMarkConsumed, true)
        XCTAssertEqual(diary.createdRequests.first?.reviewTitle, "")
        XCTAssertEqual(diary.createdRequests.first?.visibility, "public")
        XCTAssertEqual(tracking.updateRequests.count, 0)
    }

    @MainActor
    func testMediaLogProgressDoesNotCreateDiary() async {
        let diary = RecordingDiaryRepository()
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaLogViewModel(
            detail: TestFixtures.logDetail(mediaType: "manga"),
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {},
            onSaved: {}
        )
        viewModel.mode = .progress
        viewModel.progressText = "12"
        viewModel.review = "Chapter notes"

        let didSave = await viewModel.save()

        XCTAssertTrue(didSave)
        XCTAssertEqual(diary.createdRequests.count, 0)
        XCTAssertEqual(tracking.updateRequests.first?.request.progress, 12)
        XCTAssertEqual(tracking.updateRequests.first?.request.notes, "Chapter notes")
    }

    @MainActor
    func testMediaLogBookProgressUsesBookEndpoint() async {
        let diary = RecordingDiaryRepository()
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaLogViewModel(
            detail: TestFixtures.logDetail(mediaType: "book"),
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {},
            onSaved: {}
        )
        viewModel.mode = .progress
        viewModel.progressType = "percentage"
        viewModel.progressText = "42"
        viewModel.review = "Reading session"

        let didSave = await viewModel.save()

        XCTAssertTrue(didSave)
        XCTAssertEqual(diary.createdRequests.count, 0)
        XCTAssertEqual(tracking.bookProgressRequests.first?.progressType, "percentage")
        XCTAssertEqual(tracking.bookProgressRequests.first?.value, Decimal(42))
        XCTAssertEqual(tracking.bookProgressRequests.first?.notes, "Reading session")
    }

    @MainActor
    func testMediaLogSeasonMarkOnlyUsesSeasonWatch() async {
        let tracking = RecordingTrackingRepository()
        let viewModel = MediaLogViewModel(
            detail: TestFixtures.tvDetail,
            trackingRepository: tracking,
            diaryRepository: RecordingDiaryRepository(),
            onUnauthorized: {},
            onSaved: {}
        )
        viewModel.selectedSeasonNumber = 1

        let didSave = await viewModel.markOnly()

        XCTAssertTrue(didSave)
        XCTAssertEqual(tracking.watchedSeasons.first?.source, "tmdb")
        XCTAssertEqual(tracking.watchedSeasons.first?.mediaId, "1399")
        XCTAssertEqual(tracking.watchedSeasons.first?.seasonNumber, 1)
    }

    @MainActor
    func testMediaLogUnauthorizedCallsHandler() async {
        let diary = RecordingDiaryRepository(error: APIError.unauthorized)
        var unauthorizedCount = 0
        let viewModel = MediaLogViewModel(
            detail: TestFixtures.movieDetail,
            trackingRepository: RecordingTrackingRepository(),
            diaryRepository: diary,
            onUnauthorized: { unauthorizedCount += 1 },
            onSaved: {}
        )

        let didSave = await viewModel.save()

        XCTAssertFalse(didSave)
        XCTAssertEqual(unauthorizedCount, 1)
    }

    func testProfileCountsDecodeOldAndNewPayloads() throws {
        let oldCounts = try JSONDecoder.api.decode(
            ProfileCounts.self,
            from: #"{"followers":1,"following":2,"diary_entries":3,"lists":4}"#.data(using: .utf8)!
        )
        let newCounts = try JSONDecoder.api.decode(
            ProfileCounts.self,
            from: """
            {
              "followers": 1,
              "following": 2,
              "diary_entries": 3,
              "lists": 4,
              "library_items": 5,
              "reviews": 6,
              "planned_items": 7,
              "liked_items": 8,
              "tags": 9
            }
            """.data(using: .utf8)!
        )

        XCTAssertEqual(oldCounts.libraryItems, 0)
        XCTAssertEqual(oldCounts.reviews, 0)
        XCTAssertEqual(newCounts.libraryItems, 5)
        XCTAssertEqual(newCounts.reviews, 6)
        XCTAssertEqual(newCounts.plannedItems, 7)
        XCTAssertEqual(newCounts.likedItems, 8)
        XCTAssertEqual(newCounts.tags, 9)
    }

    func testProfileMenuDestinationOrderAndCounts() {
        let counts = ProfileCounts(
            followers: 0,
            following: 0,
            diaryEntries: 2,
            lists: 4,
            libraryItems: 1,
            reviews: 3,
            plannedItems: 5,
            likedItems: 6,
            tags: 7
        )

        XCTAssertEqual(ProfileMenuDestination.allCases.map(\.title), [
            "Library",
            "Diary",
            "Reviews",
            "Lists",
            "Planned",
            "Likes",
            "Tags",
        ])
        XCTAssertEqual(ProfileMenuDestination.allCases.map { $0.count(from: counts) }, [1, 2, 3, 4, 5, 6, 7])
    }

    private func profileFixture(
        hof: [String: MediaSummary?],
        enabledMediaTypes: [String] = ["movie"]
    ) -> UserProfile {
        UserProfile(
            id: 1,
            username: "mobile",
            displayName: "Mobile",
            email: "mobile@example.com",
            bio: nil,
            pronouns: nil,
            location: nil,
            avatarUrl: nil,
            isPrivate: false,
            viewerRelationship: ViewerRelationship(following: false, followedBy: false, requested: false, blocked: false),
            counts: ProfileCounts(followers: 0, following: 0, diaryEntries: 0, lists: 0),
            hof: hof,
            preferences: UserPreferences(
                enabledMediaTypes: enabledMediaTypes,
                dateFormat: nil,
                timeFormat: nil,
                weekStartDay: nil,
                quickWatchDate: nil,
                releaseNotificationsEnabled: false,
                dailyDigestEnabled: false
            )
        )
    }

    private func diaryEntry(id: Int, title: String) throws -> DiaryEntry {
        let json = """
        {
          "id": \(id),
          "user": {
            "id": 1,
            "username": "mobile",
            "display_name": "Mobile",
            "avatar_url": null
          },
          "media": {
            "ref": {
              "item_id": null,
              "source": "tmdb",
              "media_type": "movie",
              "media_id": "\(id)",
              "season_number": null,
              "episode_number": null
            },
            "title": "\(title)",
            "image_url": null,
            "poster_url": null,
            "poster_orientation": "portrait"
          },
          "consumed_at": "2026-06-21T10:00:00Z",
          "rating": "8.0",
          "review_title": null,
          "review": null,
          "contains_spoilers": false,
          "liked": false,
          "is_rewatch": false,
          "tags": [],
          "visibility": "public",
          "like_count": 0,
          "viewer_has_liked": false,
          "created_at": "2026-06-21T10:00:00Z",
          "updated_at": "2026-06-21T10:00:00Z"
        }
        """
        return try JSONDecoder.api.decode(DiaryEntry.self, from: Data(json.utf8))
    }
}

private final class RequestCaptureURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: APIError.invalidResponse)
            return
        }

        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private func requestBodyData(for request: URLRequest) -> Data {
    if let body = request.httpBody {
        return body
    }
    guard let stream = request.httpBodyStream else {
        return Data()
    }

    let bufferSize = 1024
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
    defer { buffer.deallocate() }

    var data = Data()
    stream.open()
    defer { stream.close() }
    while stream.hasBytesAvailable {
        let count = stream.read(buffer, maxLength: bufferSize)
        if count <= 0 {
            break
        }
        data.append(buffer, count: count)
    }
    return data
}

private struct CustomListWriteRequestEcho: Decodable {
    let name: String?
    let description: String?
    let visibility: String?
    let isRanked: Bool?
}

private struct ListItemWriteRequestEcho: Decodable {
    let ref: MediaRef
}

private struct ListItemsReorderRequestEcho: Decodable {
    let itemIds: [Int]
}

private final class FakeAuthRepository: AuthRepository {
    let hasStoredTokens: Bool
    let refreshError: Error?
    var refreshCallCount = 0
    var logoutCallCount = 0

    init(hasStoredTokens: Bool, refreshError: Error? = nil) {
        self.hasStoredTokens = hasStoredTokens
        self.refreshError = refreshError
    }

    func login(usernameOrEmail: String, password: String) async throws -> AuthUser {
        AuthUser(id: 1, username: usernameOrEmail, displayName: usernameOrEmail, isPrivate: false)
    }

    func register(username: String, email: String, password: String) async throws -> AuthUser {
        AuthUser(id: 1, username: username, displayName: username, isPrivate: false)
    }

    func refresh() async throws {
        refreshCallCount += 1
        if let refreshError {
            throw refreshError
        }
    }

    func logout() async {
        logoutCallCount += 1
    }
}

private func fakeRepositories(auth: AuthRepository) -> AppRepositories {
    AppRepositories(
        auth: auth,
        media: FakeMediaRepository(),
        tracking: FakeTrackingRepository(),
        diary: FakeDiaryRepository(),
        activity: FakeActivityRepository(),
        profile: FakeProfileRepository(),
        lists: FakeListRepository(),
        imports: FakeImportRepository()
    )
}

private struct FakeMediaRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
}

private struct LibraryTrackingRequest: Equatable {
    let mediaType: String
    let page: String?
    let status: String?
    let query: String?

    init(mediaType: String, page: String?, status: String? = nil, query: String? = nil) {
        self.mediaType = mediaType
        self.page = page
        self.status = status
        self.query = query
    }
}

private struct ActivityRequest: Equatable {
    let username: String
    let limit: Int
}

private final class ScriptedLibraryTrackingRepository: TrackingRepository {
    private let responses: [String: PagedResponse<Spine.LibraryItem>]
    var requests: [LibraryTrackingRequest] = []

    init(responses: [String: PagedResponse<Spine.LibraryItem>]) {
        self.responses = responses
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        requests.append(LibraryTrackingRequest(mediaType: mediaType, page: page, status: status, query: query))
        guard let response = responses["\(mediaType):\(page ?? ""):\(query ?? "")"] ?? responses["\(mediaType):\(page ?? "")"] else {
            return PagedResponse(count: 0, next: nil, previous: nil, results: [])
        }
        return response
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

private final class DelayedLibraryTrackingRepository: TrackingRepository {
    private let responses: [String: (delay: Duration, response: PagedResponse<Spine.LibraryItem>)]

    init(responses: [String: (delay: Duration, response: PagedResponse<Spine.LibraryItem>)]) {
        self.responses = responses
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        guard let scripted = responses["\(mediaType):\(query ?? "")"] ?? responses[mediaType] else {
            return PagedResponse(count: 0, next: nil, previous: nil, results: [])
        }
        try await Task.sleep(for: scripted.delay)
        return scripted.response
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

private func libraryItem(
    id: String,
    title: String,
    mediaType: String = "movie",
    status: String = "Completed",
    updatedAt: String? = nil,
    seasonNumber: Int? = nil,
    progress: ProgressState? = nil,
    latestProgressChange: ProgressChangeState? = nil
) -> Spine.LibraryItem {
    Spine.LibraryItem(
        media: MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: mediaType, mediaId: id, seasonNumber: seasonNumber, episodeNumber: nil),
            title: title,
            posterUrl: nil,
            posterOrientation: .portrait
        ),
        tracking: TrackingState(
            trackingId: Int(id) ?? 1,
            status: status,
            rating: "8.0",
            progress: progress,
            latestProgressChange: latestProgressChange,
            repeats: nil,
            startDate: nil,
            endDate: nil,
            notes: nil,
            updatedAt: updatedAt
        )
    )
}

private func activityItem(
    id: Int,
    title: String,
    type: String = "progress_updated",
    previous: ProgressState? = ProgressState(kind: "percentage", value: Decimal(42), max: Decimal(100), unit: "percent"),
    current: ProgressState? = ProgressState(kind: "percentage", value: Decimal(58), max: Decimal(100), unit: "percent"),
    rating: String? = nil,
    liked: Bool? = nil,
    hasMedia: Bool = true
) -> ActivityItem {
    ActivityItem(
        id: id,
        type: type,
        createdAt: "2026-06-20T12:00:00Z",
        actor: UserSummary(id: 1, username: "mobile", displayName: "Mobile", avatarUrl: nil),
        media: hasMedia ? MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "\(id)", seasonNumber: nil, episodeNumber: nil),
            title: title,
            posterUrl: nil,
            posterOrientation: .portrait
        ) : nil,
        object: ActivityObject(
            type: type == "progress_updated" ? "progress_change" : "diary",
            id: id,
            previous: previous,
            current: current,
            rating: rating,
            liked: liked,
            name: nil
        )
    )
}

private struct FakeTrackingRepository: TrackingRepository {
    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> TrackingState { TestFixtures.trackingState }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

private struct ThrowingTrackingRepository: TrackingRepository {
    let error: Error

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> { throw error }
    func detail(ref: MediaRef) async throws -> TrackingState { throw error }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { throw error }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { throw error }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { throw error }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { throw error }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { throw error }
}

private struct FakeDiaryRepository: DiaryRepository {
    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private struct FakeActivityRepository: ActivityRepository {
    func userActivity(username: String, limit: Int) async throws -> [ActivityItem] { fatalError("Not used") }
}

private final class ScriptedHomeDiaryRepository: DiaryRepository {
    let entries: [DiaryEntry]
    let error: Error?
    var recentLimits: [Int] = []

    init(entries: [DiaryEntry], error: Error? = nil) {
        self.entries = entries
        self.error = error
    }

    func list(tag: String?) async throws -> [DiaryEntry] {
        if let error { throw error }
        return entries
    }

    func recent(limit: Int) async throws -> [DiaryEntry] {
        recentLimits.append(limit)
        if let error { throw error }
        return Array(entries.prefix(limit))
    }

    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private final class ScriptedHomeActivityRepository: ActivityRepository {
    let items: [ActivityItem]
    let error: Error?
    var requests: [ActivityRequest] = []

    init(items: [ActivityItem], error: Error? = nil) {
        self.items = items
        self.error = error
    }

    func userActivity(username: String, limit: Int) async throws -> [ActivityItem] {
        requests.append(ActivityRequest(username: username, limit: limit))
        if let error { throw error }
        return Array(items.prefix(limit))
    }
}

private struct FakeProfileRepository: ProfileRepository {
    func me() async throws -> UserProfile {
        UserProfile(
            id: 1,
            username: "mobile",
            displayName: "Mobile",
            email: nil,
            bio: nil,
            pronouns: nil,
            location: nil,
            avatarUrl: nil,
            isPrivate: false,
            viewerRelationship: ViewerRelationship(following: false, followedBy: false, requested: false, blocked: false),
            counts: ProfileCounts(followers: 0, following: 0, diaryEntries: 0, lists: 0, reviews: 0, tags: 0),
            hof: [:],
            preferences: UserPreferences(
                enabledMediaTypes: ["movie"],
                dateFormat: "Y-m-d",
                timeFormat: "H:i",
                weekStartDay: "monday",
                quickWatchDate: "current_date",
                releaseNotificationsEnabled: false,
                dailyDigestEnabled: false
            )
        )
    }
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile { fatalError("Not used") }
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? { fatalError("Not used") }
    func deleteAvatar() async throws -> String? { fatalError("Not used") }
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences { fatalError("Not used") }
    func changePassword(_ request: PasswordChangeRequest) async throws { fatalError("Not used") }
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] { fatalError("Not used") }
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] { fatalError("Not used") }
}

private final class RecordingProfileRepository: ProfileRepository {
    var profile: UserProfile
    var profileRequests: [ProfileUpdateRequest] = []
    var preferenceRequests: [PreferencesUpdateRequest] = []
    var passwordRequests: [PasswordChangeRequest] = []
    var avatarUploads: [(data: Data, fileName: String, mimeType: String)] = []
    var didDeleteAvatar = false

    init(profile: UserProfile) {
        self.profile = profile
    }

    func me() async throws -> UserProfile {
        profile
    }

    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile {
        profileRequests.append(request)
        profile = UserProfile(
            id: profile.id,
            username: request.username ?? profile.username,
            displayName: request.displayName ?? profile.displayName,
            email: profile.email,
            bio: request.bio ?? profile.bio,
            pronouns: request.pronouns ?? profile.pronouns,
            location: request.location ?? profile.location,
            avatarUrl: profile.avatarUrl,
            isPrivate: request.isPrivate ?? profile.isPrivate,
            viewerRelationship: profile.viewerRelationship,
            counts: profile.counts,
            hof: profile.hof,
            preferences: profile.preferences
        )
        return profile
    }

    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? {
        avatarUploads.append((imageData, fileName, mimeType))
        return "https://example.com/\(fileName)"
    }

    func deleteAvatar() async throws -> String? {
        didDeleteAvatar = true
        return nil
    }

    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences {
        preferenceRequests.append(request)
        let preferences = UserPreferences(
            enabledMediaTypes: request.enabledMediaTypes ?? profile.preferences.enabledMediaTypes,
            dateFormat: request.dateFormat ?? profile.preferences.dateFormat,
            timeFormat: request.timeFormat ?? profile.preferences.timeFormat,
            weekStartDay: request.weekStartDay ?? profile.preferences.weekStartDay,
            quickWatchDate: request.quickWatchDate ?? profile.preferences.quickWatchDate,
            releaseNotificationsEnabled: request.releaseNotificationsEnabled ?? profile.preferences.releaseNotificationsEnabled,
            dailyDigestEnabled: request.dailyDigestEnabled ?? profile.preferences.dailyDigestEnabled
        )
        profile = profile.replacingPreferences(preferences)
        return preferences
    }

    func changePassword(_ request: PasswordChangeRequest) async throws {
        passwordRequests.append(request)
    }

    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] {
        profile.hof
    }

    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] {
        profile.hof
    }
}

private struct FakeListRepository: ListRepository {
    func list(membershipFor ref: MediaRef?) async throws -> [CustomListSummary] { fatalError("Not used") }
    func detail(id: Int) async throws -> CustomListDetail { fatalError("Not used") }
    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary { fatalError("Not used") }
    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail { fatalError("Not used") }
    func delete(id: Int) async throws {}
    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary { fatalError("Not used") }
    func removeItem(listId: Int, itemId: Int) async throws {}
    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail { fatalError("Not used") }
}

private final class HallOfFameProfileRepository: ProfileRepository {
    let profile: UserProfile
    let setResponse: [String: MediaSummary?]
    let clearResponse: [String: MediaSummary?]
    var setMediaType: String?
    var setRef: MediaRef?
    var clearMediaType: String?

    init(profile: UserProfile, setResponse: [String: MediaSummary?], clearResponse: [String: MediaSummary?]) {
        self.profile = profile
        self.setResponse = setResponse
        self.clearResponse = clearResponse
    }

    func me() async throws -> UserProfile {
        profile
    }

    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile {
        fatalError("Not used")
    }

    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? {
        fatalError("Not used")
    }

    func deleteAvatar() async throws -> String? {
        fatalError("Not used")
    }

    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences {
        fatalError("Not used")
    }

    func changePassword(_ request: PasswordChangeRequest) async throws {
        fatalError("Not used")
    }

    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] {
        setMediaType = mediaType
        setRef = ref
        return setResponse
    }

    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] {
        clearMediaType = mediaType
        return clearResponse
    }
}

private struct EmptyDiaryRepository: DiaryRepository {
    func list(tag: String?) async throws -> [DiaryEntry] { [] }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private struct FakeImportRepository: ImportRepository {
    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse { fatalError("Not used") }

    func queueStoryGraphImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse { fatalError("Not used") }

    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus { fatalError("Not used") }
}

private final class ScriptedLetterboxdImportRepository: ImportRepository {
    private(set) var queuedFileName: String?
    private(set) var queuedMode: ImportMode?
    private(set) var statusRequests: [String] = []
    private var statuses: [ImportTaskStatus]

    init(statuses: [ImportTaskStatus]) {
        self.statuses = statuses
    }

    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        queuedFileName = fileName
        queuedMode = mode
        progressHandler?(1)
        try await Task.sleep(for: .milliseconds(40))
        return ImportQueueResponse(taskId: statuses.first?.taskId ?? "task-2", status: "queued")
    }

    func queueStoryGraphImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        fatalError("Not used")
    }

    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus {
        statusRequests.append(taskId)
        try await Task.sleep(for: .milliseconds(40))
        if statuses.count > 1 {
            return statuses.removeFirst()
        }
        return statuses.first ?? ImportTaskStatus(taskId: taskId, taskName: nil, status: "PENDING", dateCreated: nil, dateDone: nil, result: nil)
    }
}

private final class ScriptedStoryGraphImportRepository: ImportRepository {
    private(set) var queuedFileName: String?
    private(set) var queuedMode: ImportMode?
    private(set) var statusRequests: [String] = []
    private var statuses: [ImportTaskStatus]

    init(statuses: [ImportTaskStatus]) {
        self.statuses = statuses
    }

    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        fatalError("Not used")
    }

    func queueStoryGraphImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        queuedFileName = fileName
        queuedMode = mode
        progressHandler?(1)
        try await Task.sleep(for: .milliseconds(40))
        return ImportQueueResponse(taskId: statuses.first?.taskId ?? "storygraph-task-2", status: "queued")
    }

    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus {
        statusRequests.append(taskId)
        try await Task.sleep(for: .milliseconds(40))
        if statuses.count > 1 {
            return statuses.removeFirst()
        }
        return statuses.first ?? ImportTaskStatus(taskId: taskId, taskName: nil, status: "PENDING", dateCreated: nil, dateDone: nil, result: nil)
    }
}

private func isolatedDefaults(_ name: String) -> UserDefaults {
    let defaults = UserDefaults(suiteName: name)!
    defaults.removePersistentDomain(forName: name)
    return defaults
}

private func trackingProgress(kind: String, value: Int, max: Int?, unit: String) -> TrackingState {
    TrackingState(
        trackingId: 1,
        status: "In progress",
        rating: nil,
        progress: ProgressState(
            kind: kind,
            value: Decimal(value),
            max: max.map { Decimal($0) },
            unit: unit
        ),
        repeats: nil,
        startDate: nil,
        endDate: nil,
        notes: nil,
        updatedAt: nil
    )
}

private func makeTemporaryZip() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("letterboxd-\(UUID().uuidString)")
        .appendingPathExtension("zip")
    try Data("zip-bytes".utf8).write(to: url)
    return url
}

private func makeTemporaryCSV() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("storygraph-\(UUID().uuidString)")
        .appendingPathExtension("csv")
    try Data("Title,Authors\nBook,Author\n".utf8).write(to: url)
    return url
}

@MainActor
private func waitUntil(timeout: TimeInterval = 1, predicate: @escaping () -> Bool) async throws {
    let deadline = Date().addingTimeInterval(timeout)
    while !predicate() {
        if Date() >= deadline {
            XCTFail("Timed out waiting for condition.")
            return
        }
        try await Task.sleep(for: .milliseconds(10))
    }
}

private struct MediaDetailFixtureRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> MediaDetail { TestFixtures.movieDetail }

    func reviews(ref: MediaRef) async throws -> [MediaReview] {
        try JSONDecoder.api.decode(PagedResponse<MediaReview>.self, from: TestFixtures.reviewsJSON.data(using: .utf8)!).results
    }

    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
}

private struct LikeFixtureDiaryRepository: DiaryRepository {
    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { LikeState(liked: liked, likeCount: 99) }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private final class DiaryLogFixtureDiaryRepository: DiaryRepository {
    let entry: DiaryEntry
    var requestedIds: [Int] = []

    init(entry: DiaryEntry) {
        self.entry = entry
    }

    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }

    func detail(id: Int) async throws -> DiaryEntry {
        requestedIds.append(id)
        return entry
    }

    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private final class DiaryLogFixtureMediaRepository: MediaRepository {
    let result: Result<MediaDetail, Error>
    var requestedRefs: [MediaRef] = []

    init(result: Result<MediaDetail, Error>) {
        self.result = result
    }

    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }

    func detail(ref: MediaRef) async throws -> MediaDetail {
        requestedRefs.append(ref)
        return try result.get()
    }

    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
}

private final class RecordingDiaryRepository: DiaryRepository {
    var createdRequests: [DiaryEntryWriteRequest] = []
    let error: Error?

    init(error: Error? = nil) {
        self.error = error
    }

    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }

    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry {
        if let error {
            throw error
        }
        createdRequests.append(request)
        return TestFixtures.diaryEntry
    }

    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }

    func tags(query: String) async throws -> [DiaryTagSuggestion] {
        [DiaryTagSuggestion(name: "netflix", usageCount: 12)]
    }
}

private final class TaggedDiaryFixtureRepository: DiaryRepository {
    let result: Result<[DiaryEntry], Error>
    var requestedTags: [String?] = []

    init(result: Result<[DiaryEntry], Error>) {
        self.result = result
    }

    func list(tag: String?) async throws -> [DiaryEntry] {
        requestedTags.append(tag)
        return try result.get()
    }

    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private final class RecordingTrackingRepository: TrackingRepository {
    var detailRequests: [MediaRef] = []
    var detailResponse = TestFixtures.trackingState
    var updateRequests: [(ref: MediaRef, request: TrackingWriteRequest)] = []
    var consumedRefs: [(ref: MediaRef, consumedAt: Date?)] = []
    var watchedSeasons: [(source: String, mediaId: String, seasonNumber: Int)] = []
    var bookProgressRequests: [(source: String, mediaId: String, progressType: String, value: Decimal, notes: String)] = []
    var completedBooks: [(source: String, mediaId: String, completedAt: Date?)] = []

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> { fatalError("Not used") }

    func detail(ref: MediaRef) async throws -> TrackingState {
        detailRequests.append(ref)
        return detailResponse
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        updateRequests.append((ref, request))
        return TestFixtures.trackingState
    }

    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState {
        consumedRefs.append((ref, consumedAt))
        return TestFixtures.trackingState
    }

    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState {
        watchedSeasons.append((source, mediaId, seasonNumber))
        return TestFixtures.trackingState
    }

    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState {
        bookProgressRequests.append((source, mediaId, progressType, value, notes))
        return TestFixtures.trackingState
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState {
        completedBooks.append((source, mediaId, completedAt))
        return TestFixtures.trackingState
    }
}

private struct PosterFixtureRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }

    func posters(ref: MediaRef) async throws -> [PosterOption] {
        [
            PosterOption(
                url: "https://example.com/en.jpg",
                thumbnailUrl: nil,
                width: 1000,
                height: 1500,
                aspectRatio: 0.667,
                voteAverage: 8,
                voteCount: 10,
                language: "en",
                isOriginal: false,
                isSelected: true
            ),
            PosterOption(
                url: "https://example.com/fr.jpg",
                thumbnailUrl: nil,
                width: 1000,
                height: 1500,
                aspectRatio: 0.667,
                voteAverage: 7,
                voteCount: 5,
                language: "fr",
                isOriginal: false,
                isSelected: false
            ),
            PosterOption(
                url: "https://example.com/no-language.jpg",
                thumbnailUrl: nil,
                width: 1000,
                height: 1500,
                aspectRatio: 0.667,
                voteAverage: 6,
                voteCount: 4,
                language: nil,
                isOriginal: true,
                isSelected: false
            ),
        ]
    }

    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse {
        PosterSaveResponse(posterUrl: posterURL, customPosterUrl: posterURL, posterAccentColor: "#123456")
    }

    func backdrops(ref: MediaRef) async throws -> [PosterOption] {
        [
            PosterOption(
                url: "https://example.com/backdrop-en.jpg",
                thumbnailUrl: nil,
                width: 1920,
                height: 1080,
                aspectRatio: 1.778,
                voteAverage: 8,
                voteCount: 10,
                language: "en",
                isOriginal: false,
                isSelected: true
            ),
            PosterOption(
                url: "https://example.com/backdrop-fr.jpg",
                thumbnailUrl: nil,
                width: 1920,
                height: 1080,
                aspectRatio: 1.778,
                voteAverage: 7,
                voteCount: 5,
                language: "fr",
                isOriginal: false,
                isSelected: false
            ),
            PosterOption(
                url: "https://example.com/backdrop-no-language.jpg",
                thumbnailUrl: nil,
                width: 1920,
                height: 1080,
                aspectRatio: 1.778,
                voteAverage: 6,
                voteCount: 4,
                language: nil,
                isOriginal: true,
                isSelected: false
            ),
        ]
    }

    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse {
        BackdropSaveResponse(backdropUrl: backdropURL, customBackdropUrl: backdropURL)
    }
}

private enum TestFixtures {
    static let movieDetail: MediaDetail = try! JSONDecoder.api.decode(
        MediaDetail.self,
        from: richMediaDetailJSON.data(using: .utf8)!
    )

    static let tvDetail: MediaDetail = try! JSONDecoder.api.decode(
        MediaDetail.self,
        from: tvDetailJSON.data(using: .utf8)!
    )

    static let trackingState = TrackingState(
        trackingId: 1,
        status: "In progress",
        rating: nil,
        progress: nil,
        repeats: nil,
        startDate: nil,
        endDate: nil,
        notes: nil,
        updatedAt: nil
    )

    static func customListSummaryJSON(id: Int, name: String) -> String {
        """
        {
          "id": \(id),
          "name": "\(name)",
          "slug": "\(name.lowercased())",
          "description": "",
          "visibility": "private",
          "is_ranked": false,
          "owner": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
          "image_url": null,
          "preview_items": [],
          "items_count": 0,
          "updated_at": null,
          "like_count": 0
        }
        """
    }

    static func customListDetailJSON(id: Int, name: String) -> String {
        """
        {
          "id": \(id),
          "name": "\(name)",
          "slug": "\(name.lowercased())",
          "description": "",
          "visibility": "private",
          "is_ranked": true,
          "owner": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
          "image_url": null,
          "items_count": 0,
          "updated_at": null,
          "like_count": 0,
          "items": []
        }
        """
    }

    static let diaryEntry: DiaryEntry = try! JSONDecoder.api.decode(
        DiaryEntry.self,
        from: """
        {
          "id": 1,
          "user": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
          "media": {
            "ref": { "item_id": 101, "source": "tmdb", "media_type": "movie", "media_id": "550", "season_number": null, "episode_number": null },
            "title": "Liquid Form",
            "image_url": null
          },
          "consumed_at": "2026-06-20T12:00:00Z",
          "rating": "9.0",
          "review_title": "",
          "review": "",
          "contains_spoilers": false,
          "liked": false,
          "is_rewatch": false,
          "tags": [],
          "visibility": "public",
          "like_count": 0,
          "viewer_has_liked": false,
          "created_at": "2026-06-20T12:00:00Z",
          "updated_at": "2026-06-20T12:00:00Z"
        }
        """.data(using: .utf8)!
    )

    static func diaryEntryJSON(id: Int, mediaId: String, title: String, tags: [String]) -> String {
        let encodedTags = tags
            .map { #""\#($0)""# }
            .joined(separator: ",")
        return """
        {
          "id": \(id),
          "user": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
          "media": {
            "ref": { "item_id": \(100 + id), "source": "tmdb", "media_type": "movie", "media_id": "\(mediaId)", "season_number": null, "episode_number": null },
            "title": "\(title)",
            "image_url": null,
            "poster_url": null,
            "poster_orientation": "portrait"
          },
          "consumed_at": "2026-06-20T12:00:00Z",
          "rating": "9.0",
          "review_title": "",
          "review": "",
          "contains_spoilers": false,
          "liked": false,
          "is_rewatch": false,
          "tags": [\(encodedTags)],
          "visibility": "public",
          "like_count": 0,
          "viewer_has_liked": false,
          "created_at": "2026-06-20T12:00:00Z",
          "updated_at": "2026-06-20T12:00:00Z"
        }
        """
    }

    static func mediaSummaryJSON(mediaId: String, source: String = "tmdb", mediaType: String = "movie", title: String) -> String {
        """
        {
          "ref": { "item_id": null, "source": "\(source)", "media_type": "\(mediaType)", "media_id": "\(mediaId)", "season_number": null, "episode_number": null },
          "title": "\(title)",
          "subtitle": null,
          "overview": null,
          "image_url": "https://example.com/\(mediaId).jpg",
          "poster_url": "https://example.com/\(mediaId).jpg",
          "custom_poster_url": null,
          "backdrop_url": null,
          "poster_orientation": "portrait",
          "poster_aspect_ratio": null,
          "poster_width": null,
          "poster_height": null,
          "poster_accent_color": null,
          "logo_url": null,
          "logo_width": null,
          "logo_height": null,
          "logo_aspect_ratio": null,
          "release_date": null,
          "default_source": "\(source)",
          "user_state": { "is_tracked": false, "status": null, "rating": null, "in_lists": [], "has_liked": true }
        }
        """
    }

    static func logDetail(mediaType: String) -> MediaDetail {
        try! JSONDecoder.api.decode(
            MediaDetail.self,
            from: """
            {
              "ref": { "item_id": 201, "source": "manual", "media_type": "\(mediaType)", "media_id": "log-\(mediaType)", "season_number": null, "episode_number": null },
              "title": "Log Fixture",
              "subtitle": null,
              "overview": null,
              "image_url": null,
              "poster_accent_color": null,
              "release_date": null,
              "default_source": "manual",
              "user_state": null,
              "backdrop_url": null,
              "details": {
                "number_of_pages": 300,
                "number_of_chapters": 40,
                "issues_count": 12
              },
              "related": {},
              "providers": null,
              "community": null,
              "external_ratings": null,
              "reviews": null,
              "cast": null,
              "crew": null,
              "related_sections": null,
              "episodes": null,
              "seasons": null,
              "custom_poster_url": null
            }
            """.data(using: .utf8)!
        )
    }

    static let richMediaDetailJSON = """
    {
      "ref": { "item_id": 101, "source": "tmdb", "media_type": "movie", "media_id": "550", "season_number": null, "episode_number": null },
      "title": "Liquid Form",
      "subtitle": "The Alchemist",
      "overview": "A hypnotic fever dream about identity, reinvention, and the strange rituals people build around survival.",
      "image_url": "https://image.tmdb.org/t/p/w500/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
      "poster_accent_color": "#19A7CE",
      "release_date": "2026-06-19",
      "default_source": "tmdb",
      "user_state": { "is_tracked": true, "tracking_id": 42, "status": "Completed", "rating": "9.2", "diary_count": 2, "diary_rating": "10.0", "diary_consumed_at": "2026-06-20T12:00:00Z", "in_lists": [1, 4] },
      "backdrop_url": "https://image.tmdb.org/t/p/original/rr7E0NoGKxvbkb89eR1GwfoYjpA.jpg",
      "details": {
        "director": "The Alchemist",
        "runtime": "2h 7m",
        "rating": "R",
        "release_date": "2026-06-19",
        "studios": ["A24", "Spine Pictures"],
        "country": "United States",
        "languages": ["English", "Japanese"],
        "genres": ["Drama", "Science Fiction", "Thriller"]
      },
      "related": {},
      "providers": {
        "US": {
          "flatrate": [
            { "provider_name": "Max", "logo_path": "/max.png" },
            { "provider_name": "Hulu", "logo_path": "/hulu.png" }
          ]
        }
      },
      "community": { "average_rating": "8.6", "rating_count": 1234, "diary_count": 318, "review_count": 86, "liked_count": 907, "rating_distribution": [{ "rating": "10.0", "count": 1 }] },
      "external_ratings": [
        { "source": "Spine", "value": "8.6", "vote_count": 1234, "max_value": "10" },
        { "source": "IMDb", "value": "7.9", "vote_count": 84231, "max_value": "10" },
        { "source": "Letterboxd", "value": "4.1", "vote_count": 12044, "max_value": "5" },
        { "source": "Rotten Tomatoes", "value": "92%", "vote_count": 214, "max_value": "100%" }
      ],
      "reviews": null,
      "cast": [
        { "id": "p1", "name": "Rina Sawayama", "role": null, "character": "Mika", "image_url": null },
        { "id": "p2", "name": "Steven Yeun", "role": null, "character": "Elias", "image_url": null },
        { "id": "p3", "name": "Kiko Mizuhara", "role": null, "character": "Dr. Sato", "image_url": null }
      ],
      "crew": [
        { "id": "c1", "name": "The Alchemist", "role": "Director", "character": null, "image_url": null },
        { "id": "c2", "name": "Mica Levi", "role": "Composer", "character": null, "image_url": null }
      ],
      "related_sections": [
        {
          "id": "recommendations",
          "title": "You might also like",
          "items": [
            { "ref": { "item_id": null, "source": "tmdb", "media_type": "movie", "media_id": "680", "season_number": null, "episode_number": null }, "title": "Pulp Fiction", "subtitle": "1994", "overview": null, "image_url": "https://image.tmdb.org/t/p/w500/d5iIlFn5s0ImszYzBPb8JPIfbXD.jpg", "poster_accent_color": null, "release_date": "1994-10-14", "default_source": "tmdb", "user_state": null },
            { "ref": { "item_id": null, "source": "tmdb", "media_type": "movie", "media_id": "603", "season_number": null, "episode_number": null }, "title": "The Matrix", "subtitle": "1999", "overview": null, "image_url": "https://image.tmdb.org/t/p/w500/f89U3ADr1oiB1s9GkdPOEpXUk5H.jpg", "poster_accent_color": null, "release_date": "1999-03-31", "default_source": "tmdb", "user_state": null }
          ]
        }
      ],
      "episodes": null,
      "seasons": null,
      "custom_poster_url": null,
      "custom_backdrop_url": "https://example.com/custom-backdrop.jpg"
    }
    """

    static let tvDetailJSON = """
    {
      "ref": { "item_id": 303, "source": "tmdb", "media_type": "tv", "media_id": "1399", "season_number": null, "episode_number": null },
      "title": "The Archive",
      "subtitle": "2024",
      "overview": "A serialized mystery about memory, media, and the things people choose to preserve.",
      "image_url": "https://image.tmdb.org/t/p/w500/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg",
      "poster_accent_color": "#487A8F",
      "release_date": "2024-01-14",
      "default_source": "tmdb",
      "user_state": null,
      "backdrop_url": "https://image.tmdb.org/t/p/original/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
      "details": {
        "creator": "Lena Okafor",
        "format": "TV",
        "first_air_date": "2024-01-14",
        "last_air_date": "2025-03-02",
        "status": "Ended",
        "seasons": 2,
        "episodes": 16,
        "runtime": "48m",
        "rating": "TV-MA",
        "studios": ["Spine Studios"],
        "genres": ["Mystery", "Drama", "Science Fiction"]
      },
      "related": {},
      "providers": null,
      "community": {
        "average_rating": "8.4",
        "rating_count": 812,
        "diary_count": 220,
        "review_count": 31,
        "liked_count": 405,
        "rating_distribution": [
          { "rating": "7.0", "count": 20 },
          { "rating": "8.0", "count": 90 },
          { "rating": "9.0", "count": 140 }
        ]
      },
      "external_ratings": [
        { "source": "TMDB", "value": "8.2", "vote_count": 812, "max_value": "10" },
        { "source": "IMDb", "value": "8.6", "vote_count": 43321, "max_value": "10" }
      ],
      "reviews": null,
      "cast": [
        { "id": "tv1", "name": "Michaela Coel", "role": null, "character": "Ada Vale", "image_url": null },
        { "id": "tv2", "name": "Rahul Kohli", "role": null, "character": "Jon Bell", "image_url": null }
      ],
      "crew": [
        { "id": "tv3", "name": "Lena Okafor", "role": "Creator", "character": null, "image_url": null }
      ],
      "related_sections": [],
      "episodes": [],
      "seasons": [
        { "season_number": 1, "title": "Season 1", "episode_count": 8, "image_url": "https://image.tmdb.org/t/p/w500/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg", "release_date": "2024-01-14" },
        { "season_number": 2, "title": "Season 2", "episode_count": 8, "image_url": "https://image.tmdb.org/t/p/w500/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg", "release_date": "2025-01-05" }
      ],
      "custom_poster_url": null
    }
    """

    static let seasonDetailJSON = """
    {
      "ref": { "item_id": 304, "source": "tmdb", "media_type": "season", "media_id": "1399", "season_number": 1, "episode_number": null },
      "title": "The Archive",
      "subtitle": "Season 1",
      "overview": "Ada follows a broken index into a city where every missing thing has been carefully catalogued.",
      "image_url": "https://image.tmdb.org/t/p/w500/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg",
      "poster_accent_color": "#487A8F",
      "release_date": "2024-01-14",
      "default_source": "tmdb",
      "user_state": null,
      "backdrop_url": "https://image.tmdb.org/t/p/original/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
      "details": {
        "format": "Season",
        "first_air_date": "2024-01-14",
        "episodes": 8,
        "runtime": "48m",
        "genres": ["Mystery", "Drama"]
      },
      "related": {},
      "providers": null,
      "community": { "average_rating": "8.5", "rating_count": 401, "diary_count": 120, "review_count": 14, "liked_count": 155, "rating_distribution": [] },
      "external_ratings": [
        { "source": "IMDb", "value": "8.4", "vote_count": 12000, "max_value": "10" }
      ],
      "reviews": null,
      "cast": [],
      "crew": [],
      "related_sections": [],
      "episodes": [
        { "episode_number": 1, "title": "Intake", "overview": "Ada finds the first card.", "air_date": "2024-01-14", "runtime": "49m", "image_url": null, "rating": "8.2" },
        { "episode_number": 2, "title": "Cross Reference", "overview": "A second shelf opens.", "air_date": "2024-01-21", "runtime": "47m", "image_url": null, "rating": "8.5" },
        { "episode_number": 3, "title": "Missing Holdings", "overview": "Jon rewrites the map.", "air_date": "2024-01-28", "runtime": "51m", "image_url": null, "rating": "8.7" }
      ],
      "seasons": [],
      "custom_poster_url": null
    }
    """

    static let animeDetailJSON = """
    {
      "ref": { "item_id": 404, "source": "mal", "media_type": "anime", "media_id": "1", "season_number": null, "episode_number": null },
      "title": "Cowboy Bebop",
      "subtitle": "1998",
      "overview": "A crew of bounty hunters drifts through space, chasing marks and old ghosts.",
      "image_url": "https://cdn.myanimelist.net/images/anime/4/19644.jpg",
      "poster_accent_color": "#6B5D4B",
      "release_date": "1998-04-03",
      "default_source": "mal",
      "user_state": null,
      "backdrop_url": null,
      "details": {
        "format": "TV",
        "start_date": "1998-04-03",
        "end_date": "1999-04-24",
        "status": "Finished Airing",
        "episodes": 26,
        "runtime": "24m",
        "studios": ["Sunrise"],
        "season": "Spring 1998",
        "broadcast": "Saturdays at 01:00",
        "source": "Original",
        "genres": ["Action", "Award Winning", "Sci-Fi"]
      },
      "related": {},
      "providers": null,
      "community": { "average_rating": "9.0", "rating_count": 1800, "diary_count": 721, "review_count": 84, "liked_count": 1133, "rating_distribution": [] },
      "external_ratings": [
        { "source": "MAL", "value": "8.75", "vote_count": 1000000, "max_value": "10" }
      ],
      "reviews": null,
      "cast": [],
      "crew": [],
      "related_sections": [
        {
          "id": "related_anime",
          "title": "Related Anime",
          "items": [
            { "ref": { "item_id": null, "source": "mal", "media_type": "anime", "media_id": "5", "season_number": null, "episode_number": null }, "title": "Cowboy Bebop: The Movie", "subtitle": "2001", "overview": null, "image_url": "https://cdn.myanimelist.net/images/anime/1439/93480.jpg", "poster_accent_color": null, "release_date": "2001-09-01", "default_source": "mal", "user_state": null }
          ]
        },
        {
          "id": "recommendations",
          "title": "Recommendations",
          "items": [
            { "ref": { "item_id": null, "source": "mal", "media_type": "anime", "media_id": "205", "season_number": null, "episode_number": null }, "title": "Samurai Champloo", "subtitle": "2004", "overview": null, "image_url": "https://cdn.myanimelist.net/images/anime/11/29134.jpg", "poster_accent_color": null, "release_date": "2004-05-20", "default_source": "mal", "user_state": null }
          ]
        }
      ],
      "episodes": [],
      "seasons": [],
      "custom_poster_url": null
    }
    """

    static let reviewsJSON = """
    {
      "count": 2,
      "next": null,
      "previous": null,
      "results": [
        {
          "id": 701,
          "user": { "id": 7, "username": "mika", "display_name": "Mika", "avatar_url": null },
          "rating": "9.0",
          "review_title": "A pulse under glass",
          "review": "Cold surface, hot center. Every frame feels deliberate without turning into homework.",
          "contains_spoilers": false,
          "like_count": 42,
          "viewer_has_liked": false,
          "consumed_at": "2026-06-19T20:30:00Z",
          "created_at": "2026-06-20T02:11:00Z"
        },
        {
          "id": 702,
          "user": { "id": 8, "username": "noor", "display_name": "Noor", "avatar_url": null },
          "rating": "8.5",
          "review_title": "Spoiler thoughts",
          "review": "The final reversal makes the whole first act snap into focus.",
          "contains_spoilers": true,
          "like_count": 18,
          "viewer_has_liked": true,
          "consumed_at": "2026-06-18T21:00:00Z",
          "created_at": "2026-06-19T08:10:00Z"
        }
      ]
    }
    """
}
