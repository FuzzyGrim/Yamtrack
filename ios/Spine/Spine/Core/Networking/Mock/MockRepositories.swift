import Foundation

struct MockAuthRepository: AuthRepository {
    var hasStoredTokens: Bool { true }

    func login(usernameOrEmail: String, password: String) async throws -> AuthUser {
        AuthUser(id: 1, username: usernameOrEmail, displayName: "Armaan", isPrivate: false)
    }

    func register(username: String, email: String, password: String) async throws -> AuthUser {
        AuthUser(id: 1, username: username, displayName: username, isPrivate: false)
    }

    func refresh() async throws {}
    func logout() async {}
}

struct MockMediaRepository: MediaRepository {
    func meta() async throws -> MetaResponse {
        MetaResponse(
            version: "mock",
            mediaTypes: APIConstants.fallbackMediaTypes,
            sources: [:],
            statusChoices: APIConstants.statusChoices,
            sourceChoices: ["tmdb", "openlibrary", "igdb"]
        )
    }

    func search(query: String, mediaType: String) async throws -> [MediaSummary] {
        [try await detail(ref: MockMediaFixtures.ref(for: mediaType)).summary]
    }

    func detail(ref: MediaRef) async throws -> MediaDetail {
        if ref.mediaType == "book" {
            return MockMediaFixtures.bookDetail
        }
        return MockMediaFixtures.movieDetail
    }

    func reviews(ref: MediaRef) async throws -> [MediaReview] {
        try JSONDecoder.api.decode(PagedResponse<MediaReview>.self, from: MockMediaFixtures.reviewsJSON.data(using: .utf8)!).results
    }
}

struct MockTrackingRepository: TrackingRepository {
    func list(mediaType: String) async throws -> [LibraryItem] {
        [
            LibraryItem(
                media: MockMediaFixtures.movieDetail.summary,
                tracking: MockMediaFixtures.trackingState
            ),
        ]
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        TrackingState(
            trackingId: 42,
            status: request.status,
            rating: request.rating.map { NSDecimalNumber(decimal: $0).stringValue },
            progress: request.progress.map {
                ProgressState(kind: "count", value: Decimal($0), max: nil, unit: "progress")
            },
            repeats: 1,
            startDate: nil,
            endDate: nil,
            notes: request.notes,
            updatedAt: "2026-06-20T12:00:00Z"
        )
    }
}

struct MockDiaryRepository: DiaryRepository {
    func list() async throws -> [DiaryEntry] { [] }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { throw APIError.invalidResponse }

    func setLike(entryId: Int, liked: Bool) async throws -> LikeState {
        LikeState(liked: liked, likeCount: liked ? 43 : 42)
    }
}

struct MockProfileRepository: ProfileRepository {
    func me() async throws -> UserProfile {
        UserProfile(
            id: 1,
            username: "armaan",
            displayName: "Armaan",
            email: "armaan@example.com",
            bio: "Tracking everything worth remembering.",
            pronouns: nil,
            location: nil,
            avatarUrl: nil,
            isPrivate: false,
            viewerRelationship: ViewerRelationship(following: false, followedBy: false, requested: false, blocked: false),
            counts: ProfileCounts(followers: 0, following: 0, diaryEntries: 0, lists: 0),
            hof: [:],
            preferences: UserPreferences(
                enabledMediaTypes: APIConstants.fallbackMediaTypes,
                dateFormat: "YYYY-MM-DD",
                timeFormat: "24h",
                weekStartDay: "monday",
                quickWatchDate: "today",
                releaseNotificationsEnabled: false,
                dailyDigestEnabled: false
            )
        )
    }
}

enum MockMediaFixtures {
    static let movieDetail: MediaDetail = try! JSONDecoder.api.decode(MediaDetail.self, from: richMediaDetailJSON.data(using: .utf8)!)
    static let bookDetail: MediaDetail = try! JSONDecoder.api.decode(MediaDetail.self, from: richBookDetailJSON.data(using: .utf8)!)

    static let trackingState = TrackingState(
        trackingId: 42,
        status: "Completed",
        rating: "9.2",
        progress: ProgressState(kind: "binary", value: 1, max: 1, unit: "movie"),
        repeats: 1,
        startDate: nil,
        endDate: "2026-06-19",
        notes: "Bleak, beautiful, and somehow funny.",
        updatedAt: "2026-06-20T12:00:00Z"
    )

    static func ref(for mediaType: String) -> MediaRef {
        mediaType == "book" ? bookDetail.ref : movieDetail.ref
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
      "user_state": { "is_tracked": true, "tracking_id": 42, "status": "Completed", "rating": "9.2", "in_lists": [1, 4] },
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
      "community": { "average_rating": "8.6", "rating_count": 1234, "diary_count": 318, "review_count": 86, "liked_count": 907 },
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
      "custom_poster_url": null
    }
    """

    static let richBookDetailJSON = """
    {
      "ref": { "item_id": 202, "source": "openlibrary", "media_type": "book", "media_id": "OL7353617M", "season_number": null, "episode_number": null },
      "title": "Dune",
      "subtitle": "Frank Herbert",
      "overview": "A desert planet, a family coup, and a young heir walking into myth.",
      "image_url": "https://covers.openlibrary.org/b/id/9259256-L.jpg",
      "poster_accent_color": "#D5973D",
      "release_date": "1965-08-01",
      "default_source": "openlibrary",
      "user_state": { "is_tracked": true, "tracking_id": 9, "status": "In progress", "rating": "9.0", "in_lists": [] },
      "backdrop_url": null,
      "details": {
        "authors": [{ "name": "Frank Herbert" }],
        "number_of_pages": 896,
        "publishers": ["Ace"],
        "release_date": "1965-08-01",
        "isbn": ["0441172717"],
        "genres": ["Science Fiction", "Adventure", "Politics"]
      },
      "related": {},
      "providers": null,
      "community": { "average_rating": "9.1", "rating_count": 2311, "diary_count": 902, "review_count": 188, "liked_count": 1204 },
      "external_ratings": [
        { "source": "Hardcover", "value": "4.6", "vote_count": 2311, "max_value": "5" }
      ],
      "reviews": null,
      "cast": null,
      "crew": null,
      "related_sections": [],
      "episodes": null,
      "seasons": null,
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

private extension MediaDetail {
    var summary: MediaSummary {
        MediaSummary(
            ref: ref,
            title: title,
            subtitle: subtitle,
            overview: overview,
            imageUrl: imageUrl,
            posterAccentColor: posterAccentColor,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState
        )
    }
}
