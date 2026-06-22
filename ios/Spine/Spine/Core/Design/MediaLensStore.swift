import Foundation

@MainActor
@Observable
final class MediaLensStore {
    static let persistenceKey = "selectedMediaType"

    private let defaults: UserDefaults
    var selectedMediaType: String

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        selectedMediaType = defaults.string(forKey: Self.persistenceKey) ?? "movie"
    }

    func theme(for slug: String) -> MediaTypeTheme {
        MediaTypeTheme.theme(for: slug)
    }

    func setMediaType(_ slug: String) {
        guard selectedMediaType != slug else { return }
        selectedMediaType = slug
        defaults.set(slug, forKey: Self.persistenceKey)
    }
}
