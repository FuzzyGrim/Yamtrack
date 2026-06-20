import Foundation

enum AppEnvironment {
    case live
    case mock

    static let current: AppEnvironment = .live

    var apiClient: APIClient {
        switch self {
        case .live:
            APIClient(baseURL: AppConfig.apiBaseURL, tokenProvider: KeychainTokenStore.shared)
        case .mock:
            APIClient(baseURL: AppConfig.apiBaseURL, tokenProvider: KeychainTokenStore.shared)
        }
    }
}
