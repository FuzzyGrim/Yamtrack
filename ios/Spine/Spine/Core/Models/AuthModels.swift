import Foundation

struct AuthUser: Codable, Identifiable {
    let id: Int
    let username: String
    let displayName: String
    let isPrivate: Bool
}

struct AuthTokenResponse: Decodable {
    let access: String
    let refresh: String
    let user: AuthUser
}

struct AuthRefreshResponse: Decodable {
    let access: String
    let refresh: String?
}

struct LoginRequest: Encodable {
    let usernameOrEmail: String
    let password: String
}

struct RegisterRequest: Encodable {
    let username: String
    let email: String
    let password: String
    let passwordConfirm: String
}

struct RefreshRequest: Encodable {
    let refresh: String
}

struct LogoutRequest: Encodable {
    let refresh: String
}
