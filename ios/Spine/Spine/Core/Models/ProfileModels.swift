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
