import Foundation

struct CustomListSummary: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let slug: String
    let description: String
    let visibility: String
    let isRanked: Bool
    let hasItem: Bool?
    let owner: UserSummary
    let imageUrl: String?
    let previewItems: [MediaSummary]?
    let itemsCount: Int
    let updatedAt: String?
    let likeCount: Int

    init(
        id: Int,
        name: String,
        slug: String,
        description: String,
        visibility: String,
        isRanked: Bool = false,
        hasItem: Bool? = nil,
        owner: UserSummary,
        imageUrl: String? = nil,
        previewItems: [MediaSummary]? = nil,
        itemsCount: Int,
        updatedAt: String? = nil,
        likeCount: Int
    ) {
        self.id = id
        self.name = name
        self.slug = slug
        self.description = description
        self.visibility = visibility
        self.isRanked = isRanked
        self.hasItem = hasItem
        self.owner = owner
        self.imageUrl = imageUrl
        self.previewItems = previewItems
        self.itemsCount = itemsCount
        self.updatedAt = updatedAt
        self.likeCount = likeCount
    }

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case slug
        case description
        case visibility
        case isRanked
        case hasItem
        case owner
        case imageUrl
        case previewItems
        case itemsCount
        case updatedAt
        case likeCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        slug = try container.decode(String.self, forKey: .slug)
        description = try container.decode(String.self, forKey: .description)
        visibility = try container.decode(String.self, forKey: .visibility)
        isRanked = try container.decodeIfPresent(Bool.self, forKey: .isRanked) ?? false
        hasItem = try container.decodeIfPresent(Bool.self, forKey: .hasItem)
        owner = try container.decode(UserSummary.self, forKey: .owner)
        imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        previewItems = try container.decodeIfPresent([MediaSummary].self, forKey: .previewItems)
        itemsCount = try container.decode(Int.self, forKey: .itemsCount)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        likeCount = try container.decode(Int.self, forKey: .likeCount)
    }
}

struct CustomListDetail: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let slug: String
    let description: String
    let visibility: String
    let isRanked: Bool
    let owner: UserSummary
    let imageUrl: String?
    let itemsCount: Int
    let updatedAt: String?
    let likeCount: Int
    let items: [MediaSummary]

    init(
        id: Int,
        name: String,
        slug: String,
        description: String,
        visibility: String,
        isRanked: Bool = false,
        owner: UserSummary,
        imageUrl: String? = nil,
        itemsCount: Int,
        updatedAt: String? = nil,
        likeCount: Int,
        items: [MediaSummary]
    ) {
        self.id = id
        self.name = name
        self.slug = slug
        self.description = description
        self.visibility = visibility
        self.isRanked = isRanked
        self.owner = owner
        self.imageUrl = imageUrl
        self.itemsCount = itemsCount
        self.updatedAt = updatedAt
        self.likeCount = likeCount
        self.items = items
    }

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case slug
        case description
        case visibility
        case isRanked
        case owner
        case imageUrl
        case itemsCount
        case updatedAt
        case likeCount
        case items
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        slug = try container.decode(String.self, forKey: .slug)
        description = try container.decode(String.self, forKey: .description)
        visibility = try container.decode(String.self, forKey: .visibility)
        isRanked = try container.decodeIfPresent(Bool.self, forKey: .isRanked) ?? false
        owner = try container.decode(UserSummary.self, forKey: .owner)
        imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        itemsCount = try container.decode(Int.self, forKey: .itemsCount)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        likeCount = try container.decode(Int.self, forKey: .likeCount)
        items = try container.decode([MediaSummary].self, forKey: .items)
    }
}

struct CustomListWriteRequest: Encodable {
    var name: String?
    var description: String?
    var visibility: String?
    var isRanked: Bool?
}

struct ListItemWriteRequest: Encodable {
    let ref: MediaRef
}

struct ListItemWriteResponse: Decodable {
    let item: MediaSummary
}

struct ListItemsReorderRequest: Encodable {
    let itemIds: [Int]
}
