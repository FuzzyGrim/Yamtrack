import Foundation

enum InProgressLibraryLoader {
    static let status = "In progress"

    static func mediaTypes(from profile: UserProfile?) -> [String] {
        let enabledTypes = profile?.preferences.enabledMediaTypes ?? []
        let baseTypes = enabledTypes.isEmpty ? APIConstants.fallbackMediaTypes : enabledTypes
        return baseTypes.compactMap { type in
            switch type {
            case "episode":
                nil
            case "tv":
                "season"
            default:
                type
            }
        }
    }

    static func load(
        profile: UserProfile?,
        trackingRepository: TrackingRepository,
        limit: Int
    ) async throws -> [LibraryItem] {
        try await load(
            mediaTypes: mediaTypes(from: profile),
            trackingRepository: trackingRepository,
            limit: limit
        )
    }

    static func load(
        mediaTypes: [String],
        trackingRepository: TrackingRepository,
        limit: Int
    ) async throws -> [LibraryItem] {
        guard limit > 0 else { return [] }

        var mergedItems: [LibraryItem] = []
        for mediaType in mediaTypes {
            let response = try await trackingRepository.list(
                mediaType: mediaType,
                page: nil,
                status: status
            )
            mergedItems += response.results
        }

        return limitedSortedItems(mergedItems, limit: limit)
    }

    static func limitedSortedItems(_ items: [LibraryItem], limit: Int) -> [LibraryItem] {
        guard limit > 0 else { return [] }
        return Array(items.sorted(by: inProgressSort).prefix(limit))
    }

    private nonisolated static func inProgressSort(_ lhs: LibraryItem, _ rhs: LibraryItem) -> Bool {
        let leftDate = date(from: lhs.tracking.updatedAt)
        let rightDate = date(from: rhs.tracking.updatedAt)
        switch (leftDate, rightDate) {
        case let (left?, right?):
            return left > right
        case (.some, .none):
            return true
        case (.none, .some):
            return false
        case (.none, .none):
            return lhs.media.title.localizedCaseInsensitiveCompare(rhs.media.title) == .orderedAscending
        }
    }

    private nonisolated static func date(from value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        return fractionalFormatter.date(from: value) ?? standardFormatter.date(from: value)
    }

    private nonisolated static var fractionalFormatter: ISO8601DateFormatter {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }

    private nonisolated static var standardFormatter: ISO8601DateFormatter {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }
}
