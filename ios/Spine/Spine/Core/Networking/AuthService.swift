import Foundation

struct AuthService {
    let client: APIClient

    func login(usernameOrEmail: String, password: String) async throws -> AuthUser {
        let response: AuthTokenResponse = try await client.post(
            "/auth/login/",
            body: LoginRequest(usernameOrEmail: usernameOrEmail, password: password)
        )
        storeTokens(from: response)
        return response.user
    }

    func register(username: String, email: String, password: String) async throws -> AuthUser {
        let response: AuthTokenResponse = try await client.post(
            "/auth/register/",
            body: RegisterRequest(
                username: username,
                email: email,
                password: password,
                passwordConfirm: password
            )
        )
        storeTokens(from: response)
        return response.user
    }

    func refresh() async throws {
        guard let refreshToken = client.tokenProvider.refreshToken else {
            throw APIError.unauthorized
        }
        let response: AuthRefreshResponse = try await client.post(
            "/auth/refresh/",
            body: RefreshRequest(refresh: refreshToken)
        )
        client.tokenProvider.accessToken = response.access
        if let refresh = response.refresh {
            client.tokenProvider.refreshToken = refresh
        }
    }

    func logout() async {
        if let refreshToken = client.tokenProvider.refreshToken {
            let _: EmptyResponse? = try? await client.post(
                "/auth/logout/",
                body: LogoutRequest(refresh: refreshToken),
                authenticated: true
            )
        }
        client.tokenProvider.clear()
    }

    private func storeTokens(from response: AuthTokenResponse) {
        client.tokenProvider.accessToken = response.access
        client.tokenProvider.refreshToken = response.refresh
    }
}

struct HealthService {
    let client: APIClient

    func check() async throws -> HealthResponse {
        try await client.get("/health/")
    }
}
