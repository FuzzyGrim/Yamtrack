import Foundation

struct CustomListSummary: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let slug: String
    let description: String
    let visibility: String
    let owner: UserSummary
    let imageUrl: String?
    let previewItems: [MediaSummary]?
    let itemsCount: Int
    let updatedAt: String?
    let likeCount: Int
}

struct CustomListDetail: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let slug: String
    let description: String
    let visibility: String
    let owner: UserSummary
    let imageUrl: String?
    let itemsCount: Int
    let updatedAt: String?
    let likeCount: Int
    let items: [MediaSummary]
}
