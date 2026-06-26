import Foundation

struct DiaryEntry: Codable, Identifiable {
    let id: Int
    let user: UserSummary
    let media: DiaryMedia
    let consumedAt: String?
    let rating: String?
    let reviewTitle: String?
    let review: String?
    let containsSpoilers: Bool
    let liked: Bool
    let isRewatch: Bool
    let tags: [String]
    let visibility: String
    let likeCount: Int
    let viewerHasLiked: Bool
    let createdAt: String?
    let updatedAt: String?
}

struct DiaryMedia: Codable {
    let ref: MediaRef
    let title: String
    let imageUrl: String?
    let posterUrl: String?
    let posterOrientation: PosterOrientation?

    var displayPosterURL: String? {
        posterUrl ?? imageUrl
    }

    enum CodingKeys: String, CodingKey {
        case ref
        case title
        case imageUrl
        case posterUrl
        case posterOrientation
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        ref = try container.decode(MediaRef.self, forKey: .ref)
        title = try container.decode(String.self, forKey: .title)
        self.imageUrl = imageUrl
        posterUrl = try container.decodeIfPresent(String.self, forKey: .posterUrl) ?? imageUrl
        posterOrientation = try container.decodeIfPresent(PosterOrientation.self, forKey: .posterOrientation)
    }
}

struct DiaryEntryWriteRequest: Encodable {
    let ref: MediaRef
    let consumedAt: Date?
    let rating: Decimal?
    let review: String
    let reviewTitle: String
    let liked: Bool
    let isRewatch: Bool
    let autoMarkConsumed: Bool
    let containsSpoilers: Bool
    let visibility: String
    let tags: [String]
}

struct DiaryEntryUpdateRequest: Encodable {
    let consumedAt: Date?
    let rating: Decimal?
    let review: String?
    let reviewTitle: String?
    let tags: [String]?
    let liked: Bool?
    let isRewatch: Bool?
    let containsSpoilers: Bool?
    let visibility: String?
}

struct DiaryTagSuggestion: Codable, Hashable {
    let name: String
    let usageCount: Int
}

struct DiaryTagSuggestionsResponse: Codable {
    let results: [DiaryTagSuggestion]
}

struct ActivityItem: Codable, Identifiable {
    let id: Int
    let type: String
    let createdAt: String?
    let actor: UserSummary
    let media: MediaSummary?
    let object: ActivityObject
}

struct ActivityObject: Codable {
    let type: String
    let id: Int
    let previous: ProgressState?
    let current: ProgressState?
    let rating: String?
    let name: String?
}

struct ActivityCursorResponse: Codable {
    let nextCursor: String?
    let previousCursor: String?
    let results: [ActivityItem]
}
