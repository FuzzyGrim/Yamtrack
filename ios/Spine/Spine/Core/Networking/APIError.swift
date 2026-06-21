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
            Self.userFacingHTTPMessage(statusCode: code, body: message)
        case let .decoding(error):
            "Could not read server response: \(error.localizedDescription)"
        case let .network(error):
            Self.networkMessage(for: error)
        }
    }

    private static func userFacingHTTPMessage(statusCode: Int, body: String?) -> String {
        let host = AppConfig.apiBaseURL.host ?? "the API"

        if let body, !body.isEmpty {
            if let data = body.data(using: .utf8),
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let errors = json["errors"] as? [[String: Any]],
               let first = errors.first {
                let detail = (first["detail"] as? String) ?? (first["message"] as? String) ?? ""
                let code = first["code"] as? Int
                if detail.contains("Cloudflare Tunnel") || code == 1033 || statusCode == 530 {
                    return tunnelUnavailableMessage(host: host)
                }
                if !detail.isEmpty {
                    return detail
                }
            }

            let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.contains("error code: 1033") || trimmed == "error code: 1033" {
                return tunnelUnavailableMessage(host: host)
            }

            if trimmed.contains("<html") || trimmed.contains("<!DOCTYPE") {
                return serverUnavailableMessage(host: host, statusCode: statusCode)
            }

            if trimmed.count <= 180, !trimmed.hasPrefix("{") {
                return trimmed
            }
        }

        if statusCode == 502 || statusCode == 503 || statusCode == 530 {
            return tunnelUnavailableMessage(host: host)
        }

        return "Request to \(host) failed (HTTP \(statusCode))."
    }

    private static func tunnelUnavailableMessage(host: String) -> String {
        """
        Can't reach \(host). Start the Spine backend on your Mac, then start the Cloudflare tunnel (cloudflared). On a physical iPhone, localhost won't work — the tunnel or your Mac's LAN IP must be reachable.
        """
    }

    private static func serverUnavailableMessage(host: String, statusCode: Int) -> String {
        "The server at \(host) returned HTTP \(statusCode). Check that the backend is running."
    }

    private static func networkMessage(for error: Error) -> String {
        let host = AppConfig.apiBaseURL.host ?? "the API"

        guard let urlError = error as? URLError else {
            return "Network error: \(error.localizedDescription)"
        }

        switch urlError.code {
        case .timedOut:
            return "Timed out reaching \(host)."
        case .notConnectedToInternet, .cannotConnectToHost, .networkConnectionLost:
            return tunnelUnavailableMessage(host: host)
        default:
            return urlError.localizedDescription
        }
    }
}
