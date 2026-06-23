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
    func posters(ref: MediaRef) async throws -> [PosterOption]
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse
    func backdrops(ref: MediaRef) async throws -> [PosterOption]
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse
}

protocol TrackingRepository {
    func list(mediaType: String, page: String?, status: String?) async throws -> PagedResponse<LibraryItem>
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState
}

extension TrackingRepository {
    func list(mediaType: String, page: String?) async throws -> PagedResponse<LibraryItem> {
        try await list(mediaType: mediaType, page: page, status: nil)
    }
}

protocol DiaryRepository {
    func list(tag: String?) async throws -> [DiaryEntry]
    func detail(id: Int) async throws -> DiaryEntry
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState
    func tags(query: String) async throws -> [DiaryTagSuggestion]
}

extension DiaryRepository {
    func list() async throws -> [DiaryEntry] {
        try await list(tag: nil)
    }
}

protocol ProfileRepository {
    func me() async throws -> UserProfile
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?]
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?]
}

protocol ImportRepository {
    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse
    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus
}

struct AppRepositories {
    let auth: AuthRepository
    let media: MediaRepository
    let tracking: TrackingRepository
    let diary: DiaryRepository
    let profile: ProfileRepository
    let imports: ImportRepository

    static func current() -> AppRepositories {
        live()
    }

    static func live(client: APIClient = AppEnvironment.apiClient) -> AppRepositories {
        AppRepositories(
            auth: APIAuthRepository(service: AuthService(client: client), tokenStore: client.tokenProvider),
            media: APIMediaRepository(client: client),
            tracking: APITrackingRepository(client: client),
            diary: APIDiaryRepository(client: client),
            profile: APIProfileRepository(client: client),
            imports: APIImportRepository(client: client)
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
        if ref.mediaType != "season", let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        let path: String
        if ref.mediaType == "season", let seasonNumber = ref.seasonNumber {
            path = "/media/\(ref.source)/tv/\(ref.mediaId)/seasons/\(seasonNumber)/"
        } else {
            path = "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/"
        }
        return try await client.get(
            path,
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
            let mediaType = ref.mediaType == "season" ? "tv" : ref.mediaType
            let response: PagedResponse<MediaReview> = try await client.get(
                "/media/\(ref.source)/\(mediaType)/\(ref.mediaId)/reviews/",
                query: query,
                authenticated: client.tokenProvider.accessToken != nil
            )
            return response.results
        } catch APIError.httpStatus(404, _), APIError.httpStatus(501, _) {
            return []
        }
    }

    func posters(ref: MediaRef) async throws -> [PosterOption] {
        let response: PosterOptionsResponse = try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/posters/",
            authenticated: true
        )
        return response.posters
    }

    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse {
        try await client.put(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/poster/",
            body: PosterSaveRequest(posterUrl: posterURL),
            authenticated: true
        )
    }

    func backdrops(ref: MediaRef) async throws -> [PosterOption] {
        let response: BackdropOptionsResponse = try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/backdrops/",
            authenticated: true
        )
        return response.backdrops
    }

    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse {
        try await client.put(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/backdrop/",
            body: BackdropSaveRequest(backdropUrl: backdropURL),
            authenticated: true
        )
    }
}

struct APITrackingRepository: TrackingRepository {
    let client: APIClient

    func list(mediaType: String, page: String?, status: String? = nil) async throws -> PagedResponse<LibraryItem> {
        var query = [URLQueryItem(name: "media_type", value: mediaType)]
        if let page {
            query.append(URLQueryItem(name: "page", value: page))
        }
        if let status {
            query.append(URLQueryItem(name: "status", value: status))
        }
        return try await client.get(
            "/tracking/",
            query: query,
            authenticated: true
        )
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        try await client.patch(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/",
            body: request,
            authenticated: true
        )
    }

    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/actions/consume/",
            body: TrackingConsumeRequest(consumedAt: consumedAt),
            authenticated: true
        )
    }

    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/tv/\(mediaId)/seasons/\(seasonNumber)/watch/",
            body: EmptyResponse(),
            authenticated: true
        )
    }

    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/book/\(mediaId)/progress/",
            body: BookProgressRequest(progressType: progressType, value: value, notes: notes),
            authenticated: true
        )
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/book/\(mediaId)/complete/",
            body: BookCompleteRequest(completedAt: completedAt),
            authenticated: true
        )
    }
}

struct APIDiaryRepository: DiaryRepository {
    let client: APIClient

    func list(tag: String? = nil) async throws -> [DiaryEntry] {
        let tag = tag?.trimmingCharacters(in: .whitespacesAndNewlines)
        let baseQuery = tag.map { $0.isEmpty ? [] : [URLQueryItem(name: "tag", value: $0)] } ?? []
        var page: String?
        var entries: [DiaryEntry] = []

        repeat {
            var query = baseQuery
            if let page {
                query.append(URLQueryItem(name: "page", value: page))
            }

            let response: PagedResponse<DiaryEntry> = try await client.get("/diary/", query: query, authenticated: true)
            entries += response.results
            page = APIPageCursor.nextPage(from: response.next)
        } while page != nil

        return entries
    }

    func detail(id: Int) async throws -> DiaryEntry {
        try await client.get("/diary/\(id)/", authenticated: true)
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

    func tags(query: String) async throws -> [DiaryTagSuggestion] {
        let response: DiaryTagSuggestionsResponse = try await client.get(
            "/diary/tags/",
            query: query.isEmpty ? [] : [URLQueryItem(name: "q", value: query)],
            authenticated: true
        )
        return response.results
    }

}

enum APIPageCursor {
    static func nextPage(from next: String?) -> String? {
        guard let next, let url = URL(string: next) else { return nil }
        return URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first { $0.name == "page" }?
            .value
    }
}

struct APIProfileRepository: ProfileRepository {
    let client: APIClient

    func me() async throws -> UserProfile {
        try await client.get("/me/", authenticated: true)
    }

    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] {
        let response: HallOfFameItemsResponse = try await client.put(
            "/me/hof/\(mediaType)/",
            body: HallOfFameItemWriteRequest(ref: ref),
            authenticated: true
        )
        return response.items
    }

    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] {
        let response: HallOfFameItemsResponse = try await client.delete(
            "/me/hof/\(mediaType)/",
            authenticated: true
        )
        return response.items
    }
}

struct APIImportRepository: ImportRepository {
    let client: APIClient

    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)? = nil
    ) async throws -> ImportQueueResponse {
        try await client.uploadMultipart(
            "/imports/letterboxd/",
            formFields: ["mode": mode.rawValue],
            fileFieldName: "file",
            fileName: fileName,
            fileData: fileData,
            mimeType: "application/zip",
            authenticated: true,
            progressHandler: progressHandler
        )
    }

    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus {
        try await client.get("/imports/tasks/\(taskId)/", authenticated: true)
    }
}
