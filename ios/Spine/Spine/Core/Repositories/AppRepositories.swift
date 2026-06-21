import Foundation

protocol AuthRepository {
    var hasStoredTokens: Bool { get }
    func login(usernameOrEmail: String, password: String) async throws -> AuthUser
    func register(username: String, email: String, password: String) async throws -> AuthUser
    func refresh() async throws
    func logout() async
}

protocol MediaRepository {
    func meta() async throws -> MetaResponse
    func search(query: String, mediaType: String) async throws -> [MediaSummary]
    func detail(ref: MediaRef) async throws -> MediaDetail
    func reviews(ref: MediaRef) async throws -> [MediaReview]
}

protocol TrackingRepository {
    func list(mediaType: String) async throws -> [LibraryItem]
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState
}

protocol DiaryRepository {
    func list() async throws -> [DiaryEntry]
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState
}

protocol ProfileRepository {
    func me() async throws -> UserProfile
}

struct AppRepositories {
    let auth: AuthRepository
    let media: MediaRepository
    let tracking: TrackingRepository
    let diary: DiaryRepository
    let profile: ProfileRepository

    static func current() -> AppRepositories {
        switch AppEnvironment.current {
        case .live:
            live()
        case .mock:
            mock()
        }
    }

    static func live(client: APIClient = AppEnvironment.current.apiClient) -> AppRepositories {
        AppRepositories(
            auth: APIAuthRepository(service: AuthService(client: client), tokenStore: client.tokenProvider),
            media: APIMediaRepository(client: client),
            tracking: APITrackingRepository(client: client),
            diary: APIDiaryRepository(client: client),
            profile: APIProfileRepository(client: client)
        )
    }

    static func mock() -> AppRepositories {
        AppRepositories(
            auth: MockAuthRepository(),
            media: MockMediaRepository(),
            tracking: MockTrackingRepository(),
            diary: MockDiaryRepository(),
            profile: MockProfileRepository()
        )
    }
}

struct APIAuthRepository: AuthRepository {
    let service: AuthService
    let tokenStore: KeychainTokenStore

    var hasStoredTokens: Bool {
        tokenStore.accessToken != nil || tokenStore.refreshToken != nil
    }

    func login(usernameOrEmail: String, password: String) async throws -> AuthUser {
        try await service.login(usernameOrEmail: usernameOrEmail, password: password)
    }

    func register(username: String, email: String, password: String) async throws -> AuthUser {
        try await service.register(username: username, email: email, password: password)
    }

    func refresh() async throws {
        try await service.refresh()
    }

    func logout() async {
        await service.logout()
    }
}

struct APIMediaRepository: MediaRepository {
    let client: APIClient

    func meta() async throws -> MetaResponse {
        try await client.get("/meta/")
    }

    func search(query: String, mediaType: String) async throws -> [MediaSummary] {
        let response: PagedResponse<MediaSummary> = try await client.get(
            "/media/search/",
            query: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "media_type", value: mediaType),
            ],
            authenticated: true
        )
        return response.results
    }

    func detail(ref: MediaRef) async throws -> MediaDetail {
        var query: [URLQueryItem] = []
        if let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        return try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/",
            query: query,
            authenticated: client.tokenProvider.accessToken != nil
        )
    }

    func reviews(ref: MediaRef) async throws -> [MediaReview] {
        var query = [
            URLQueryItem(name: "sort", value: "popular"),
        ]
        if let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        do {
            let response: PagedResponse<MediaReview> = try await client.get(
                "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/reviews/",
                query: query,
                authenticated: client.tokenProvider.accessToken != nil
            )
            return response.results
        } catch APIError.httpStatus(404, _), APIError.httpStatus(501, _) {
            return []
        }
    }
}

struct APITrackingRepository: TrackingRepository {
    let client: APIClient

    func list(mediaType: String) async throws -> [LibraryItem] {
        let response: PagedResponse<LibraryItem> = try await client.get(
            "/tracking/",
            query: [URLQueryItem(name: "media_type", value: mediaType)],
            authenticated: true
        )
        return response.results
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        try await client.patch(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/",
            body: request,
            authenticated: true
        )
    }
}

struct APIDiaryRepository: DiaryRepository {
    let client: APIClient

    func list() async throws -> [DiaryEntry] {
        let response: PagedResponse<DiaryEntry> = try await client.get("/diary/", authenticated: true)
        return response.results
    }

    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry {
        try await client.post("/diary/", body: request, authenticated: true)
    }

    func setLike(entryId: Int, liked: Bool) async throws -> LikeState {
        if liked {
            return try await client.post(
                "/diary/\(entryId)/like/",
                body: EmptyResponse(),
                authenticated: true
            )
        }
        return try await client.delete("/diary/\(entryId)/like/", authenticated: true)
    }
}

struct APIProfileRepository: ProfileRepository {
    let client: APIClient

    func me() async throws -> UserProfile {
        try await client.get("/me/", authenticated: true)
    }
}
