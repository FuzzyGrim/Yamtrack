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

struct DiaryTagSuggestion: Codable, Hashable {
    let name: String
    let usageCount: Int
}

struct DiaryTagSuggestionsResponse: Codable {
    let results: [DiaryTagSuggestion]
}
