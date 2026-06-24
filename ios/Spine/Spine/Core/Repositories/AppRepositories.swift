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
    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse
    func reviews(ref: MediaRef) async throws -> [MediaReview]
    func posters(ref: MediaRef) async throws -> [PosterOption]
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse
    func backdrops(ref: MediaRef) async throws -> [PosterOption]
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse
}

extension MediaRepository {
    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse {
        fatalError("Not implemented")
    }
}

protocol TrackingRepository {
    func list(mediaType: String, page: String?, status: String?) async throws -> PagedResponse<LibraryItem>
    func detail(ref: MediaRef) async throws -> TrackingState
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
    func list(filter: DiaryFilter) async throws -> [DiaryEntry]
    func list(tag: String?) async throws -> [DiaryEntry]
    func recent(limit: Int) async throws -> [DiaryEntry]
    func detail(id: Int) async throws -> DiaryEntry
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState
    func tags(query: String, mine: Bool) async throws -> [DiaryTagSuggestion]
    func tags(query: String) async throws -> [DiaryTagSuggestion]
    func allTags(mine: Bool) async throws -> [DiaryTagSuggestion]
}

extension DiaryRepository {
    func list(filter: DiaryFilter) async throws -> [DiaryEntry] {
        try await list(tag: filter.tag)
    }

    func list() async throws -> [DiaryEntry] {
        try await list(tag: nil)
    }

    func recent(limit: Int) async throws -> [DiaryEntry] {
        guard limit > 0 else { return [] }
        return Array(try await list().prefix(limit))
    }

    func tags(query: String, mine: Bool) async throws -> [DiaryTagSuggestion] {
        try await tags(query: query)
    }

    func allTags(mine: Bool) async throws -> [DiaryTagSuggestion] {
        try await tags(query: "", mine: mine)
    }
}

struct DiaryFilter: Equatable {
    var tag: String? = nil
    var itemId: Int? = nil
    var hasReview = false
    var liked = false
}

protocol ProfileRepository {
    func me() async throws -> UserProfile
    func likedMedia() async throws -> [MediaSummary]
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String?
    func deleteAvatar() async throws -> String?
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences
    func changePassword(_ request: PasswordChangeRequest) async throws
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?]
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?]
}

extension ProfileRepository {
    func likedMedia() async throws -> [MediaSummary] {
        fatalError("Not implemented")
    }
}

protocol ListRepository {
    func list(membershipFor ref: MediaRef?) async throws -> [CustomListSummary]
    func list() async throws -> [CustomListSummary]
    func detail(id: Int) async throws -> CustomListDetail
    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary
    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail
    func delete(id: Int) async throws
    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary
    func removeItem(listId: Int, itemId: Int) async throws
    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail
}

extension ListRepository {
    func list() async throws -> [CustomListSummary] {
        try await list(membershipFor: nil)
    }
}

protocol ImportRepository {
    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse
    func queueStoryGraphImport(
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
    let lists: ListRepository
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
            lists: APIListRepository(client: client),
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

    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse {
        let request = HallOfFameItemWriteRequest(ref: ref)
        if liked {
            return try await client.post("/me/liked-media/", body: request, authenticated: true)
        }
        return try await client.delete("/me/liked-media/", body: request, authenticated: true)
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

    func detail(ref: MediaRef) async throws -> TrackingState {
        try await client.get(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/",
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
        try await list(filter: DiaryFilter(tag: tag))
    }

    func list(filter: DiaryFilter) async throws -> [DiaryEntry] {
        let tag = filter.tag?.trimmingCharacters(in: .whitespacesAndNewlines)
        var baseQuery = tag.map { $0.isEmpty ? [] : [URLQueryItem(name: "tag", value: $0)] } ?? []
        if let itemId = filter.itemId {
            baseQuery.append(URLQueryItem(name: "item_id", value: String(itemId)))
        }
        if filter.hasReview {
            baseQuery.append(URLQueryItem(name: "has_review", value: "true"))
        }
        if filter.liked {
            baseQuery.append(URLQueryItem(name: "liked", value: "true"))
        }
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

    func recent(limit: Int) async throws -> [DiaryEntry] {
        guard limit > 0 else { return [] }
        let response: PagedResponse<DiaryEntry> = try await client.get("/diary/", authenticated: true)
        return Array(response.results.prefix(limit))
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
        try await tags(query: query, mine: false)
    }

    func tags(query: String, mine: Bool) async throws -> [DiaryTagSuggestion] {
        var queryItems = query.isEmpty ? [] : [URLQueryItem(name: "q", value: query)]
        if mine {
            queryItems.append(URLQueryItem(name: "mine", value: "true"))
        }
        let response: DiaryTagSuggestionsResponse = try await client.get(
            "/diary/tags/",
            query: queryItems,
            authenticated: true
        )
        return response.results
    }

    func allTags(mine: Bool) async throws -> [DiaryTagSuggestion] {
        var queryItems = [URLQueryItem(name: "all", value: "true")]
        if mine {
            queryItems.append(URLQueryItem(name: "mine", value: "true"))
        }
        let response: DiaryTagSuggestionsResponse = try await client.get(
            "/diary/tags/",
            query: queryItems,
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

    func likedMedia() async throws -> [MediaSummary] {
        var page: String?
        var media: [MediaSummary] = []

        repeat {
            let query = page.map { [URLQueryItem(name: "page", value: $0)] } ?? []
            let response: PagedResponse<MediaSummary> = try await client.get(
                "/me/liked-media/",
                query: query,
                authenticated: true
            )
            media += response.results
            page = APIPageCursor.nextPage(from: response.next)
        } while page != nil

        return media
    }

    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile {
        try await client.patch("/me/", body: request, authenticated: true)
    }

    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? {
        let response: AvatarUploadResponse = try await client.uploadMultipart(
            "/me/avatar/",
            formFields: [:],
            fileFieldName: "avatar",
            fileName: fileName,
            fileData: imageData,
            mimeType: mimeType,
            authenticated: true
        )
        return response.avatarUrl
    }

    func deleteAvatar() async throws -> String? {
        let response: AvatarUploadResponse = try await client.delete("/me/avatar/", authenticated: true)
        return response.avatarUrl
    }

    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences {
        try await client.patch("/me/preferences/", body: request, authenticated: true)
    }

    func changePassword(_ request: PasswordChangeRequest) async throws {
        let _: EmptyResponse = try await client.post("/me/password/", body: request, authenticated: true)
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

struct APIListRepository: ListRepository {
    let client: APIClient

    func list(membershipFor ref: MediaRef? = nil) async throws -> [CustomListSummary] {
        var query: [URLQueryItem] = []
        if let ref {
            query.append(URLQueryItem(name: "ref[source]", value: ref.source))
            query.append(URLQueryItem(name: "ref[media_type]", value: ref.mediaType))
            query.append(URLQueryItem(name: "ref[media_id]", value: ref.mediaId))
            if let seasonNumber = ref.seasonNumber {
                query.append(URLQueryItem(name: "ref[season_number]", value: String(seasonNumber)))
            }
            if let episodeNumber = ref.episodeNumber {
                query.append(URLQueryItem(name: "ref[episode_number]", value: String(episodeNumber)))
            }
        }
        let response: PagedResponse<CustomListSummary> = try await client.get(
            "/lists/",
            query: query,
            authenticated: true
        )
        return response.results
    }

    func detail(id: Int) async throws -> CustomListDetail {
        try await client.get("/lists/\(id)/", authenticated: true)
    }

    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary {
        try await client.post("/lists/", body: request, authenticated: true)
    }

    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail {
        try await client.patch("/lists/\(id)/", body: request, authenticated: true)
    }

    func delete(id: Int) async throws {
        let _: EmptyResponse = try await client.delete("/lists/\(id)/", authenticated: true)
    }

    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary {
        let response: ListItemWriteResponse = try await client.post(
            "/lists/\(listId)/items/",
            body: ListItemWriteRequest(ref: ref),
            authenticated: true
        )
        return response.item
    }

    func removeItem(listId: Int, itemId: Int) async throws {
        let _: EmptyResponse = try await client.delete("/lists/\(listId)/items/\(itemId)/", authenticated: true)
    }

    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail {
        try await client.patch(
            "/lists/\(listId)/items/reorder/",
            body: ListItemsReorderRequest(itemIds: itemIds),
            authenticated: true
        )
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

    func queueStoryGraphImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)? = nil
    ) async throws -> ImportQueueResponse {
        try await client.uploadMultipart(
            "/imports/storygraph/",
            formFields: ["mode": mode.rawValue],
            fileFieldName: "file",
            fileName: fileName,
            fileData: fileData,
            mimeType: "text/csv",
            authenticated: true,
            progressHandler: progressHandler
        )
    }

    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus {
        try await client.get("/imports/tasks/\(taskId)/", authenticated: true)
    }
}
