import Foundation

struct UserSummary: Codable, Identifiable, Hashable {
    let id: Int
    let username: String
    let displayName: String
    let avatarUrl: String?
}

struct UserProfile: Codable, Identifiable {
    let id: Int
    let username: String
    let displayName: String
    let email: String?
    let bio: String?
    let pronouns: String?
    let location: String?
    let avatarUrl: String?
    let isPrivate: Bool
    let viewerRelationship: ViewerRelationship
    let counts: ProfileCounts
    let hof: [String: MediaSummary?]
    let preferences: UserPreferences
}

extension UserProfile {
    func replacingHallOfFame(_ hof: [String: MediaSummary?]) -> UserProfile {
        UserProfile(
            id: id,
            username: username,
            displayName: displayName,
            email: email,
            bio: bio,
            pronouns: pronouns,
            location: location,
            avatarUrl: avatarUrl,
            isPrivate: isPrivate,
            viewerRelationship: viewerRelationship,
            counts: counts,
            hof: hof,
            preferences: preferences
        )
    }

    func replacingPreferences(_ preferences: UserPreferences) -> UserProfile {
        UserProfile(
            id: id,
            username: username,
            displayName: displayName,
            email: email,
            bio: bio,
            pronouns: pronouns,
            location: location,
            avatarUrl: avatarUrl,
            isPrivate: isPrivate,
            viewerRelationship: viewerRelationship,
            counts: counts,
            hof: hof,
            preferences: preferences
        )
    }

    func replacingAvatarUrl(_ avatarUrl: String?) -> UserProfile {
        UserProfile(
            id: id,
            username: username,
            displayName: displayName,
            email: email,
            bio: bio,
            pronouns: pronouns,
            location: location,
            avatarUrl: avatarUrl,
            isPrivate: isPrivate,
            viewerRelationship: viewerRelationship,
            counts: counts,
            hof: hof,
            preferences: preferences
        )
    }
}

struct ViewerRelationship: Codable {
    let following: Bool
    let followedBy: Bool
    let requested: Bool
    let blocked: Bool
}

struct ProfileCounts: Codable {
    let followers: Int
    let following: Int
    let diaryEntries: Int
    let lists: Int
    let libraryItems: Int
    let reviews: Int
    let plannedItems: Int
    let likedItems: Int
    let tags: Int

    init(
        followers: Int,
        following: Int,
        diaryEntries: Int,
        lists: Int,
        libraryItems: Int = 0,
        reviews: Int = 0,
        plannedItems: Int = 0,
        likedItems: Int = 0,
        tags: Int = 0
    ) {
        self.followers = followers
        self.following = following
        self.diaryEntries = diaryEntries
        self.lists = lists
        self.libraryItems = libraryItems
        self.reviews = reviews
        self.plannedItems = plannedItems
        self.likedItems = likedItems
        self.tags = tags
    }

    enum CodingKeys: String, CodingKey {
        case followers
        case following
        case diaryEntries
        case lists
        case libraryItems
        case reviews
        case plannedItems
        case likedItems
        case tags
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        followers = try container.decodeIfPresent(Int.self, forKey: .followers) ?? 0
        following = try container.decodeIfPresent(Int.self, forKey: .following) ?? 0
        diaryEntries = try container.decodeIfPresent(Int.self, forKey: .diaryEntries) ?? 0
        lists = try container.decodeIfPresent(Int.self, forKey: .lists) ?? 0
        libraryItems = try container.decodeIfPresent(Int.self, forKey: .libraryItems) ?? 0
        reviews = try container.decodeIfPresent(Int.self, forKey: .reviews) ?? 0
        plannedItems = try container.decodeIfPresent(Int.self, forKey: .plannedItems) ?? 0
        likedItems = try container.decodeIfPresent(Int.self, forKey: .likedItems) ?? 0
        tags = try container.decodeIfPresent(Int.self, forKey: .tags) ?? 0
    }
}

struct UserPreferences: Codable {
    let enabledMediaTypes: [String]
    let dateFormat: String?
    let timeFormat: String?
    let weekStartDay: String?
    let quickWatchDate: String?
    let releaseNotificationsEnabled: Bool
    let dailyDigestEnabled: Bool
}

struct ProfileUpdateRequest: Encodable, Equatable {
    var username: String?
    var displayName: String?
    var bio: String?
    var pronouns: String?
    var location: String?
    var isPrivate: Bool?
}

struct PreferencesUpdateRequest: Encodable, Equatable {
    var enabledMediaTypes: [String]?
    var dateFormat: String?
    var timeFormat: String?
    var weekStartDay: String?
    var quickWatchDate: String?
    var releaseNotificationsEnabled: Bool?
    var dailyDigestEnabled: Bool?
}

struct AvatarUploadResponse: Decodable {
    let avatarUrl: String?
}

struct PasswordChangeRequest: Encodable, Equatable {
    let oldPassword: String
    let newPassword: String
    let newPasswordConfirm: String
}

struct PreferenceChoice: Decodable, Identifiable, Equatable {
    let value: String
    let label: String

    var id: String { value }
}

struct SettingsOptions: Decodable, Equatable {
    let dateFormats: [PreferenceChoice]
    let timeFormats: [PreferenceChoice]
    let weekStartDays: [PreferenceChoice]
    let quickWatchDates: [PreferenceChoice]
}

extension Notification.Name {
    static let profileDidUpdate = Notification.Name("profileDidUpdate")
}
