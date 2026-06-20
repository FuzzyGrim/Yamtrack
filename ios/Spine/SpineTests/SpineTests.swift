import XCTest
@testable import Spine

final class SpineTests: XCTestCase {
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
            XCTAssertEqual(request.url?.path, "/root/api/v1/health/")
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
            XCTAssertEqual(request.url?.path, "/api/v1/auth/login/")
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
        profile: FakeProfileRepository()
    )
}

private struct FakeMediaRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
}

private struct FakeTrackingRepository: TrackingRepository {
    func list(mediaType: String) async throws -> [Spine.LibraryItem] { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
}

private struct FakeDiaryRepository: DiaryRepository {
    func list() async throws -> [DiaryEntry] { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
}

private struct FakeProfileRepository: ProfileRepository {
    func me() async throws -> UserProfile { fatalError("Not used") }
}
