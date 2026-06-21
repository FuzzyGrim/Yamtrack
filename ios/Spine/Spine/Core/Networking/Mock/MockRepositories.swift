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
        switch ref.mediaType {
        case "book":
            MockMediaFixtures.bookDetail
        case "tv":
            MockMediaFixtures.tvDetail
        case "season":
            MockMediaFixtures.seasonDetail
        case "anime":
            MockMediaFixtures.animeDetail
        default:
            MockMediaFixtures.movieDetail
        }
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
    static let tvDetail: MediaDetail = try! JSONDecoder.api.decode(MediaDetail.self, from: tvDetailJSON.data(using: .utf8)!)
    static let seasonDetail: MediaDetail = try! JSONDecoder.api.decode(MediaDetail.self, from: seasonDetailJSON.data(using: .utf8)!)
    static let animeDetail: MediaDetail = try! JSONDecoder.api.decode(MediaDetail.self, from: animeDetailJSON.data(using: .utf8)!)

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
        switch mediaType {
        case "book":
            bookDetail.ref
        case "tv":
            tvDetail.ref
        case "season":
            seasonDetail.ref
        case "anime":
            animeDetail.ref
        default:
            movieDetail.ref
        }
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
