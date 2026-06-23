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

        XCTAssertEqual(try JSONDecoder.api.decode(TrackingState.self, from: tracking).rating, "8.5")
        XCTAssertEqual(try JSONDecoder.api.decode(DiaryEntry.self, from: diary).media.title, "Fight Club")
        XCTAssertEqual(try JSONDecoder.api.decode(UserProfile.self, from: profile).counts.diaryEntries, 1)
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
        profile: FakeProfileRepository(),
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

private struct FakeTrackingRepository: TrackingRepository {
    func list(mediaType: String) async throws -> [Spine.LibraryItem] { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

private struct FakeDiaryRepository: DiaryRepository {
    func list() async throws -> [DiaryEntry] { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private struct FakeProfileRepository: ProfileRepository {
    func me() async throws -> UserProfile { fatalError("Not used") }
}

private struct FakeImportRepository: ImportRepository {
    func queueLetterboxdImport(
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

private func makeTemporaryZip() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("letterboxd-\(UUID().uuidString)")
        .appendingPathExtension("zip")
    try Data("zip-bytes".utf8).write(to: url)
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
    func list() async throws -> [DiaryEntry] { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { LikeState(liked: liked, likeCount: 99) }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private final class RecordingDiaryRepository: DiaryRepository {
    var createdRequests: [DiaryEntryWriteRequest] = []
    let error: Error?

    init(error: Error? = nil) {
        self.error = error
    }

    func list() async throws -> [DiaryEntry] { fatalError("Not used") }

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

private final class RecordingTrackingRepository: TrackingRepository {
    var updateRequests: [(ref: MediaRef, request: TrackingWriteRequest)] = []
    var consumedRefs: [(ref: MediaRef, consumedAt: Date?)] = []
    var watchedSeasons: [(source: String, mediaId: String, seasonNumber: Int)] = []
    var bookProgressRequests: [(source: String, mediaId: String, progressType: String, value: Decimal, notes: String)] = []
    var completedBooks: [(source: String, mediaId: String, completedAt: Date?)] = []

    func list(mediaType: String) async throws -> [Spine.LibraryItem] { fatalError("Not used") }

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
      "user_state": { "is_tracked": true, "tracking_id": 42, "status": "Completed", "rating": "9.2", "diary_rating": "10.0", "diary_consumed_at": "2026-06-20T12:00:00Z", "in_lists": [1, 4] },
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
