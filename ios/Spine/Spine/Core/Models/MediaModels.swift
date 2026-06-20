import Foundation

struct MediaRef: Codable, Hashable, Identifiable {
    let itemId: Int?
    let source: String
    let mediaType: String
    let mediaId: String
    let seasonNumber: Int?
    let episodeNumber: Int?

    var id: String {
        [
            source,
            mediaType,
            mediaId,
            seasonNumber.map(String.init) ?? "_",
            episodeNumber.map(String.init) ?? "_",
        ].joined(separator: ":")
    }
}

struct MediaSummary: Codable, Identifiable, Hashable {
    let ref: MediaRef
    let title: String
    let subtitle: String?
    let overview: String?
    let imageUrl: String?
    let posterAccentColor: String?
    let releaseDate: String?
    let defaultSource: String?
    var userState: UserMediaState?

    var id: String { ref.id }
}

struct MediaDetail: Codable, Identifiable {
    let ref: MediaRef
    let title: String
    let subtitle: String?
    let overview: String?
    let imageUrl: String?
    let posterAccentColor: String?
    let releaseDate: String?
    let defaultSource: String?
    let userState: UserMediaState?
    let backdropUrl: String?
    let details: [String: JSONValue]?
    let related: [String: JSONValue]?
    let providers: JSONValue?
    let community: CommunityStats?

    var id: String { ref.id }
}

struct UserMediaState: Codable, Hashable {
    let isTracked: Bool
    let trackingId: Int?
    let status: String?
    let rating: String?
    let inLists: [Int]
}

struct CommunityStats: Codable {
    let averageRating: String?
    let ratingCount: Int
    let diaryCount: Int
    let reviewCount: Int
    let likedCount: Int
}

enum JSONValue: Codable, Hashable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value.")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .string(value):
            try container.encode(value)
        case let .number(value):
            try container.encode(value)
        case let .bool(value):
            try container.encode(value)
        case let .object(value):
            try container.encode(value)
        case let .array(value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }
}
