import Foundation

enum ProgressUpdateMode: String, CaseIterable, Identifiable {
    case pages
    case percentage

    var id: String { rawValue }

    var title: String {
        switch self {
        case .pages: "Pages"
        case .percentage: "Percent"
        }
    }

    var apiValue: String {
        switch self {
        case .pages: "pages"
        case .percentage: "percentage"
        }
    }

    var unit: String {
        switch self {
        case .pages: "page"
        case .percentage: "percent"
        }
    }
}

struct TrackingState: Codable, Equatable {
    let trackingId: Int
    let status: String?
    let rating: String?
    let progress: ProgressState?
    let latestProgressChange: ProgressChangeState?
    let repeats: Int?
    let startDate: String?
    let endDate: String?
    let notes: String?
    let updatedAt: String?

    init(
        trackingId: Int,
        status: String?,
        rating: String?,
        progress: ProgressState?,
        latestProgressChange: ProgressChangeState? = nil,
        repeats: Int?,
        startDate: String?,
        endDate: String?,
        notes: String?,
        updatedAt: String?
    ) {
        self.trackingId = trackingId
        self.status = status
        self.rating = rating
        self.progress = progress
        self.latestProgressChange = latestProgressChange
        self.repeats = repeats
        self.startDate = startDate
        self.endDate = endDate
        self.notes = notes
        self.updatedAt = updatedAt
    }

    func replacingProgress(_ progress: ProgressState?) -> TrackingState {
        TrackingState(
            trackingId: trackingId,
            status: status,
            rating: rating,
            progress: progress,
            latestProgressChange: latestProgressChange,
            repeats: repeats,
            startDate: startDate,
            endDate: endDate,
            notes: notes,
            updatedAt: updatedAt
        )
    }

    func homeProgressText(preferredMode: ProgressUpdateMode?) -> String {
        if let changeText = latestProgressChange?.compactDisplayText(preferredMode: preferredMode) {
            return changeText
        }
        if let progressText = progress?.compactDisplayText(preferredMode: preferredMode) {
            return progressText
        }
        return status == "In progress" ? "Started" : status ?? "In progress"
    }
}

struct ProgressChangeState: Codable, Equatable {
    let id: Int
    let previous: ProgressState
    let current: ProgressState
    let createdAt: String?
}

struct ProgressChangeDisplay: Equatable {
    let previous: String
    let current: String
}

extension ProgressChangeState {
    func compactDisplayParts(preferredMode: ProgressUpdateMode?) -> ProgressChangeDisplay? {
        guard
            let previousText = previous.compactDisplayText(preferredMode: preferredMode),
            let currentText = current.compactDisplayText(preferredMode: preferredMode),
            previousText != currentText
        else {
            return nil
        }
        return ProgressChangeDisplay(previous: previousText, current: currentText)
    }

    func compactDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        guard let parts = compactDisplayParts(preferredMode: preferredMode) else {
            return nil
        }
        return "\(parts.previous) → \(parts.current)"
    }

    func compactDeltaText(preferredMode: ProgressUpdateMode?) -> String? {
        let mode = preferredMode ?? current.mode
        guard
            let previousValue = previous.value(in: mode),
            let currentValue = current.value(in: mode)
        else {
            return nil
        }

        let delta = currentValue - previousValue
        guard delta != 0 else { return nil }

        let prefix = delta > 0 ? "+" : ""
        if mode == .percentage {
            return "\(prefix)\(delta)%"
        }
        let unit = abs(delta) == 1 ? current.unit : current.pluralizedUnit
        return "\(prefix)\(delta) \(unit)"
    }
}

struct ProgressState: Codable, Hashable {
    let kind: String
    let value: Decimal?
    let max: Decimal?
    let unit: String
}

extension ProgressState {
    var compactDisplayText: String? {
        compactDisplayText(preferredMode: nil)
    }

    var detailDisplayText: String? {
        detailDisplayText(preferredMode: nil)
    }

    func compactDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        if preferredMode == .percentage, let value = value(in: .percentage) {
            guard value > 0 else { return nil }
            return "\(Self.display(Decimal(value)))%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            guard value > 0 else { return nil }
            let maxText = max.map { "/\(Self.display($0))" } ?? ""
            let unitText = value == 1 && max == nil ? "page" : "pages"
            return "\(value)\(maxText) \(unitText)"
        }
        guard let value else { return nil }
        guard value > 0 else { return nil }
        if isPercentage {
            return "\(Self.display(value))%"
        }
        let maxText = max.map { "/\(Self.display($0))" } ?? ""
        return "\(Self.display(value))\(maxText) \(pluralizedUnit(for: max ?? value))"
    }

    func detailDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        if preferredMode == .percentage, let value = value(in: .percentage) {
            guard value > 0 else { return nil }
            return "\(value)%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            guard value > 0 else { return nil }
            if let max {
                return "\(value) of \(Self.display(max)) pages"
            }
            return "\(value) pages"
        }
        guard let value else { return nil }
        guard value > 0 else { return nil }
        if isPercentage {
            return "\(Self.display(value))%"
        }
        if let max {
            return "\(Self.display(value)) of \(Self.display(max)) \(pluralizedUnit(for: max))"
        }
        return "\(Self.display(value)) \(pluralizedUnit(for: value))"
    }

    var mode: ProgressUpdateMode {
        let kind = kind.lowercased()
        let unit = unit.lowercased()
        if kind.contains("percent") || unit.contains("percent") || unit == "%" {
            return .percentage
        }
        return .pages
    }

    func value(in requestedMode: ProgressUpdateMode) -> Int? {
        guard let value else { return nil }
        let intValue = Int(NSDecimalNumber(decimal: value).doubleValue.rounded())
        guard mode != requestedMode else { return intValue }
        guard let maxValue = max.map({ NSDecimalNumber(decimal: $0).doubleValue }), maxValue > 0 else {
            if requestedMode == .percentage, isMinuteProgress, (0...100).contains(intValue) {
                return intValue
            }
            return nil
        }
        switch (mode, requestedMode) {
        case (.pages, .percentage):
            return Int((Double(intValue) / maxValue * 100).rounded())
        case (.percentage, .pages):
            return Int((Double(intValue) / 100 * maxValue).rounded())
        default:
            return intValue
        }
    }

    private var isPercentage: Bool {
        mode == .percentage
    }

    private var isMinuteProgress: Bool {
        let unit = unit.lowercased()
        return unit == "min" || unit.contains("minute")
    }

    private func pluralizedUnit(for value: Decimal) -> String {
        if value == 1 || unit.hasSuffix("s") {
            return unit
        }
        return "\(unit)s"
    }

    var pluralizedUnit: String {
        unit.hasSuffix("s") ? unit : "\(unit)s"
    }

    private static func display(_ value: Decimal) -> String {
        NSDecimalNumber(decimal: value).stringValue
    }
}

enum ProgressDisplayPreferences {
    private static let prefix = "progress.display.mode."

    static func mode(for ref: MediaRef) -> ProgressUpdateMode? {
        UserDefaults.standard.string(forKey: key(for: ref)).flatMap(ProgressUpdateMode.init(rawValue:))
    }

    static func setMode(_ mode: ProgressUpdateMode, for ref: MediaRef) {
        UserDefaults.standard.set(mode.rawValue, forKey: key(for: ref))
    }

    static func removeMode(for ref: MediaRef) {
        UserDefaults.standard.removeObject(forKey: key(for: ref))
    }

    private static func key(for ref: MediaRef) -> String {
        "\(prefix)\(ref.id)"
    }
}

struct TrackingWriteRequest: Encodable {
    let status: String?
    let rating: Decimal?
    let progress: Int?
    let notes: String?

    init(status: String? = nil, rating: Decimal? = nil, progress: Int? = nil, notes: String? = nil) {
        self.status = status
        self.rating = rating
        self.progress = progress
        self.notes = notes
    }
}

struct TrackingConsumeRequest: Encodable {
    let consumedAt: Date?
}

struct BookProgressRequest: Encodable {
    let progressType: String
    let value: Decimal
    let notes: String
}

struct BookCompleteRequest: Encodable {
    let completedAt: Date?
}

struct LibraryItem: Decodable, Identifiable {
    let media: MediaSummary
    let tracking: TrackingState

    var id: String { media.id }
}
