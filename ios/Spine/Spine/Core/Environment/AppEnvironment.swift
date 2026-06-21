import Foundation

enum AppEnvironment {
    case live
    case mock

    static var current: AppEnvironment {
        ProcessInfo.processInfo.environment["SPINE_APP_ENV"] == "mock" ? .mock : .live
    }

    var apiClient: APIClient {
        switch self {
        case .live:
            APIClient(baseURL: AppConfig.apiBaseURL, tokenProvider: KeychainTokenStore.shared)
        case .mock:
            APIClient(baseURL: AppConfig.apiBaseURL, tokenProvider: KeychainTokenStore.shared)
        }
    }
}
