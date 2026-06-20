import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case httpStatus(Int, String?)
    case unauthorized
    case decoding(Error)
    case network(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "Invalid API URL."
        case .invalidResponse:
            "Unexpected server response."
        case .unauthorized:
            "Your session has expired. Sign in again."
        case let .httpStatus(code, message):
            message ?? "Request failed with status \(code)."
        case let .decoding(error):
            "Could not read server response: \(error.localizedDescription)"
        case let .network(error):
            Self.networkMessage(for: error)
        }
    }

    private static func networkMessage(for error: Error) -> String {
        guard let urlError = error as? URLError else {
            return "Network error: \(error.localizedDescription)"
        }

        switch urlError.code {
        case .timedOut:
            return "Timed out reaching the server at \(AppConfig.productionAPIBaseURL)."
        case .notConnectedToInternet, .cannotConnectToHost, .networkConnectionLost:
            return """
            Can't reach the API. Check that Docker and the Cloudflare tunnel are running on your Mac.
            """
        default:
            return urlError.localizedDescription
        }
    }
}
