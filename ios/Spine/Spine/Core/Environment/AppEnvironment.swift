import Foundation

enum AppEnvironment {
    static var apiClient: APIClient {
        APIClient(baseURL: AppConfig.apiBaseURL, tokenProvider: KeychainTokenStore.shared)
    }
}
