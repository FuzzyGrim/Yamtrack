import Foundation

struct EmptyResponse: Codable, Equatable {}

struct PagedResponse<T: Decodable>: Decodable {
    let count: Int
    let next: String?
    let previous: String?
    let results: [T]
}

struct MetaResponse: Decodable {
    let version: String
    let mediaTypes: [String]
    let sources: [String: [String]]
    let statusChoices: [String]
    let sourceChoices: [String]
    let dateFormats: [PreferenceChoice]?
    let timeFormats: [PreferenceChoice]?
    let weekStartDays: [PreferenceChoice]?
    let quickWatchDates: [PreferenceChoice]?

    var settingsOptions: SettingsOptions? {
        guard let dateFormats, let timeFormats, let weekStartDays, let quickWatchDates else {
            return nil
        }
        return SettingsOptions(
            dateFormats: dateFormats,
            timeFormats: timeFormats,
            weekStartDays: weekStartDays,
            quickWatchDates: quickWatchDates
        )
    }
}

enum APIConstants {
    static let fallbackMediaTypes = ["movie", "tv", "anime", "manga", "game", "book", "comic", "boardgame"]
    static let statusChoices = ["Completed", "In progress", "Planning", "Paused", "Dropped"]
    static let visibilityChoices = ["public", "followers", "private"]
}
