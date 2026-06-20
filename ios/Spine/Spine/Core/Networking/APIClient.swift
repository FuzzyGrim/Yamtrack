import Foundation

struct APIClient: Sendable {
    static let defaultSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 15
        return URLSession(configuration: config)
    }()

    let baseURL: URL
    let tokenProvider: KeychainTokenStore
    let session: URLSession

    init(
        baseURL: URL = AppConfig.apiBaseURL,
        tokenProvider: KeychainTokenStore = .shared,
        session: URLSession = APIClient.defaultSession
    ) {
        self.baseURL = baseURL
        self.tokenProvider = tokenProvider
        self.session = session
    }

    func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], authenticated: Bool = false) async throws -> T {
        try await request(path: path, method: "GET", query: query, body: Optional<Data>.none, authenticated: authenticated)
    }

    func post<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        authenticated: Bool = false
    ) async throws -> Response {
        let data = try JSONEncoder.api.encode(body)
        return try await request(path: path, method: "POST", query: [], body: data, authenticated: authenticated)
    }

    func put<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        authenticated: Bool = false
    ) async throws -> Response {
        let data = try JSONEncoder.api.encode(body)
        return try await request(path: path, method: "PUT", query: [], body: data, authenticated: authenticated)
    }

    func patch<Body: Encodable, Response: Decodable>(
        _ path: String,
        body: Body,
        authenticated: Bool = false
    ) async throws -> Response {
        let data = try JSONEncoder.api.encode(body)
        return try await request(path: path, method: "PATCH", query: [], body: data, authenticated: authenticated)
    }

    func delete<T: Decodable>(_ path: String, query: [URLQueryItem] = [], authenticated: Bool = false) async throws -> T {
        try await request(path: path, method: "DELETE", query: query, body: Optional<Data>.none, authenticated: authenticated)
    }

    private func request<T: Decodable>(
        path: String,
        method: String,
        query: [URLQueryItem],
        body: Data?,
        authenticated: Bool
    ) async throws -> T {
        let url = try endpointURL(path: path, query: query)

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if authenticated, let token = tokenProvider.accessToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.network(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        if http.statusCode == 401 {
            tokenProvider.clear()
            throw APIError.unauthorized
        }

        guard (200 ... 299).contains(http.statusCode) else {
            let message = String(data: data, encoding: .utf8)
            throw APIError.httpStatus(http.statusCode, message)
        }

        do {
            if data.isEmpty, T.self == EmptyResponse.self {
                return EmptyResponse() as! T
            }
            return try JSONDecoder.api.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    private func endpointURL(path: String, query: [URLQueryItem]) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }

        let basePath = components.path.trimmedPathSlashes
        let apiPrefix = AppConfig.apiPrefix.trimmedPathSlashes
        let requestPath = path.trimmedPathSlashes
        var resolvedPath = "/" + [basePath, apiPrefix, requestPath]
            .filter { !$0.isEmpty }
            .joined(separator: "/")
        if path.hasSuffix("/"), !resolvedPath.hasSuffix("/") {
            resolvedPath += "/"
        }
        components.path = resolvedPath

        if !query.isEmpty {
            components.queryItems = query
        }
        guard let url = components.url else {
            throw APIError.invalidURL
        }
        return url
    }
}

private extension String {
    var trimmedPathSlashes: String {
        trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}

extension JSONDecoder {
    static let api: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()
}

extension JSONEncoder {
    static let api: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()
}
