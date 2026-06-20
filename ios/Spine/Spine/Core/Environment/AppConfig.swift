import Foundation

/// Central configuration for API connectivity.
enum AppConfig {
    static let apiPrefix = "/api/v1"

    /// Permanent API host (Cloudflare named tunnel). Set once; never use trycloudflare.com.
    /// Example after setup: https://api.yourdomain.com
    static let productionAPIBaseURL = "https://api.spine-api.com"

    /// Override in the Xcode scheme with SPINE_API_BASE_URL when local development needs localhost.
    static var apiBaseURL: URL {
        if let override = ProcessInfo.processInfo.environment["SPINE_API_BASE_URL"],
           !override.isEmpty,
           let url = URL(string: override) {
            return url
        }

        guard productionAPIBaseURL != "https://REPLACE-WITH-YOUR-HOSTNAME",
              let url = URL(string: productionAPIBaseURL) else {
            fatalError("Set AppConfig.productionAPIBaseURL to your permanent Cloudflare hostname.")
        }
        return url
    }
}
