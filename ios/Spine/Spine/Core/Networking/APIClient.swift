import Foundation

struct APIClient: Sendable {
    static let defaultSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 15
        return URLSession(configuration: config)
    }()

    static let uploadSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 300
        return URLSession(configuration: config)
    }()

    let baseURL: URL
    let tokenProvider: KeychainTokenStore
    let session: URLSession
    let multipartSession: URLSession

    init(
        baseURL: URL = AppConfig.apiBaseURL,
        tokenProvider: KeychainTokenStore = .shared,
        session: URLSession = APIClient.defaultSession,
        multipartSession: URLSession = APIClient.uploadSession
    ) {
        self.baseURL = baseURL
        self.tokenProvider = tokenProvider
        self.session = session
        self.multipartSession = multipartSession
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

    func uploadMultipart<Response: Decodable>(
        _ path: String,
        formFields: [String: String],
        fileFieldName: String,
        fileName: String,
        fileData: Data,
        mimeType: String,
        authenticated: Bool = true,
        progressHandler: (@MainActor @Sendable (Double) -> Void)? = nil
    ) async throws -> Response {
        let boundary = "Boundary-\(UUID().uuidString)"
        let url = try endpointURL(path: path, query: [])
        let bodyURL = try MultipartFormData.writeBodyFile(
            boundary: boundary,
            fields: formFields,
            fileFieldName: fileFieldName,
            fileName: fileName,
            fileData: fileData,
            mimeType: mimeType
        )
        defer { try? FileManager.default.removeItem(at: bodyURL) }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        if authenticated, let token = tokenProvider.accessToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (data, response): (Data, URLResponse)
        do {
            let delegate = progressHandler.map(MultipartUploadProgressDelegate.init(progressHandler:))
            (data, response) = try await multipartSession.upload(for: request, fromFile: bodyURL, delegate: delegate)
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
            return try JSONDecoder.api.decode(Response.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
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

enum MultipartFormData {
    static func body(
        boundary: String,
        fields: [String: String],
        fileFieldName: String,
        fileName: String,
        fileData: Data,
        mimeType: String
    ) -> Data {
        var data = Data()
        let lineBreak = "\r\n"

        for (name, value) in fields.sorted(by: { $0.key < $1.key }) {
            data.appendString("--\(boundary)\(lineBreak)")
            data.appendString("Content-Disposition: form-data; name=\"\(name.multipartEscaped)\"\(lineBreak)\(lineBreak)")
            data.appendString("\(value)\(lineBreak)")
        }

        data.appendString("--\(boundary)\(lineBreak)")
        data.appendString("Content-Disposition: form-data; name=\"\(fileFieldName.multipartEscaped)\"; filename=\"\(fileName.multipartEscaped)\"\(lineBreak)")
        data.appendString("Content-Type: \(mimeType)\(lineBreak)\(lineBreak)")
        data.append(fileData)
        data.appendString(lineBreak)
        data.appendString("--\(boundary)--\(lineBreak)")
        return data
    }

    static func writeBodyFile(
        boundary: String,
        fields: [String: String],
        fileFieldName: String,
        fileName: String,
        fileData: Data,
        mimeType: String
    ) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("spine-multipart-\(UUID().uuidString)")
            .appendingPathExtension("body")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }

        let lineBreak = "\r\n"
        for (name, value) in fields.sorted(by: { $0.key < $1.key }) {
            handle.write(Data("--\(boundary)\(lineBreak)".utf8))
            handle.write(Data("Content-Disposition: form-data; name=\"\(name.multipartEscaped)\"\(lineBreak)\(lineBreak)".utf8))
            handle.write(Data("\(value)\(lineBreak)".utf8))
        }

        handle.write(Data("--\(boundary)\(lineBreak)".utf8))
        handle.write(Data("Content-Disposition: form-data; name=\"\(fileFieldName.multipartEscaped)\"; filename=\"\(fileName.multipartEscaped)\"\(lineBreak)".utf8))
        handle.write(Data("Content-Type: \(mimeType)\(lineBreak)\(lineBreak)".utf8))
        handle.write(fileData)
        handle.write(Data(lineBreak.utf8))
        handle.write(Data("--\(boundary)--\(lineBreak)".utf8))
        return url
    }
}

private final class MultipartUploadProgressDelegate: NSObject, URLSessionTaskDelegate {
    let progressHandler: @MainActor @Sendable (Double) -> Void

    init(progressHandler: @escaping @MainActor @Sendable (Double) -> Void) {
        self.progressHandler = progressHandler
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didSendBodyData bytesSent: Int64,
        totalBytesSent: Int64,
        totalBytesExpectedToSend: Int64
    ) {
        guard totalBytesExpectedToSend > 0 else { return }
        let progress = min(1, max(0, Double(totalBytesSent) / Double(totalBytesExpectedToSend)))
        Task { @MainActor [progressHandler] in
            progressHandler(progress)
        }
    }
}

private extension String {
    var multipartEscaped: String {
        replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\r", with: "")
            .replacingOccurrences(of: "\n", with: "")
    }
}

private extension Data {
    mutating func appendString(_ string: String) {
        append(Data(string.utf8))
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
