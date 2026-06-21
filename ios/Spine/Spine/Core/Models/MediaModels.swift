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

struct MediaDetail: Decodable, Identifiable {
    let ref: MediaRef
    let title: String
    let subtitle: String?
    let overview: String?
    let synopsis: String?
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
    let externalRatings: [ExternalRating]?
    let reviews: [MediaReview]?
    let cast: [CreditPerson]?
    let crew: [CreditPerson]?
    let relatedSections: [RelatedMediaSection]?
    let episodes: [EpisodeSummary]?
    let seasons: [SeasonSummary]?
    let customPosterUrl: String?

    var id: String { ref.id }

    var displaySynopsis: String? {
        let placeholder = "No synopsis available."
        let candidates = [
            overview,
            synopsis,
            details?["synopsis"]?.stringValue,
            details?["overview"]?.stringValue,
            details?["description"]?.stringValue,
        ]
        for candidate in candidates {
            guard let text = candidate?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !text.isEmpty,
                  text != placeholder else { continue }
            return text
        }
        return nil
    }
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
    let ratingDistribution: [RatingDistributionBucket]

    enum CodingKeys: String, CodingKey {
        case averageRating
        case ratingCount
        case diaryCount
        case reviewCount
        case likedCount
        case ratingDistribution
    }

    init(
        averageRating: String?,
        ratingCount: Int,
        diaryCount: Int,
        reviewCount: Int,
        likedCount: Int,
        ratingDistribution: [RatingDistributionBucket] = []
    ) {
        self.averageRating = averageRating
        self.ratingCount = ratingCount
        self.diaryCount = diaryCount
        self.reviewCount = reviewCount
        self.likedCount = likedCount
        self.ratingDistribution = ratingDistribution
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        averageRating = try container.decodeIfPresent(String.self, forKey: .averageRating)
        ratingCount = try container.decode(Int.self, forKey: .ratingCount)
        diaryCount = try container.decode(Int.self, forKey: .diaryCount)
        reviewCount = try container.decode(Int.self, forKey: .reviewCount)
        likedCount = try container.decode(Int.self, forKey: .likedCount)
        ratingDistribution = try container.decodeIfPresent([RatingDistributionBucket].self, forKey: .ratingDistribution) ?? []
    }
}

struct RatingDistributionBucket: Codable, Hashable {
    let rating: String
    let count: Int
}

struct ExternalRating: Codable, Identifiable, Hashable {
    let source: String
    let value: String
    let voteCount: Int?
    let maxValue: String?

    var id: String { source }
}

struct MediaReview: Codable, Identifiable, Hashable {
    let id: Int
    let user: UserSummary
    let rating: String?
    let reviewTitle: String?
    let review: String
    let containsSpoilers: Bool
    var likeCount: Int
    var viewerHasLiked: Bool
    let consumedAt: String?
    let createdAt: String?
}

struct LikeState: Codable, Equatable {
    let liked: Bool
    let likeCount: Int
}

struct CreditPerson: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let role: String?
    let character: String?
    let imageUrl: String?
}

struct RelatedMediaSection: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let items: [MediaSummary]
}

struct EpisodeSummary: Codable, Identifiable, Hashable {
    let episodeNumber: Int
    let title: String
    let overview: String?
    let airDate: String?
    let runtime: String?
    let imageUrl: String?
    let rating: String?

    var id: Int { episodeNumber }
}

struct SeasonSummary: Codable, Identifiable, Hashable {
    let seasonNumber: Int
    let title: String
    let episodeCount: Int?
    let imageUrl: String?
    let releaseDate: String?

    var id: Int { seasonNumber }
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

extension JSONValue {
    var stringValue: String? {
        if case let .string(value) = self {
            return value
        }
        return nil
    }
}
