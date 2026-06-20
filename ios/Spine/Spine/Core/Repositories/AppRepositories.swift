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
}

protocol TrackingRepository {
    func list(mediaType: String) async throws -> [LibraryItem]
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState
}

protocol DiaryRepository {
    func list() async throws -> [DiaryEntry]
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry
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

    static func live(client: APIClient = AppEnvironment.current.apiClient) -> AppRepositories {
        AppRepositories(
            auth: APIAuthRepository(service: AuthService(client: client), tokenStore: client.tokenProvider),
            media: APIMediaRepository(client: client),
            tracking: APITrackingRepository(client: client),
            diary: APIDiaryRepository(client: client),
            profile: APIProfileRepository(client: client)
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
}

struct APIProfileRepository: ProfileRepository {
    let client: APIClient

    func me() async throws -> UserProfile {
        try await client.get("/me/", authenticated: true)
    }
}
