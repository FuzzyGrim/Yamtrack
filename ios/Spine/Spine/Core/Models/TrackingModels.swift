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
    let repeats: Int?
    let startDate: String?
    let endDate: String?
    let notes: String?
    let updatedAt: String?

    func replacingProgress(_ progress: ProgressState?) -> TrackingState {
        TrackingState(
            trackingId: trackingId,
            status: status,
            rating: rating,
            progress: progress,
            repeats: repeats,
            startDate: startDate,
            endDate: endDate,
            notes: notes,
            updatedAt: updatedAt
        )
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
            return "\(Self.display(Decimal(value)))%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            let maxText = max.map { "/\(Self.display($0))" } ?? ""
            let unitText = value == 1 && max == nil ? "page" : "pages"
            return "\(value)\(maxText) \(unitText)"
        }
        guard let value else { return nil }
        if isPercentage {
            return "\(Self.display(value))%"
        }
        let maxText = max.map { "/\(Self.display($0))" } ?? ""
        return "\(Self.display(value))\(maxText) \(pluralizedUnit(for: max ?? value))"
    }

    func detailDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        if preferredMode == .percentage, let value = value(in: .percentage) {
            return "\(value)%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            if let max {
                return "\(value) of \(Self.display(max)) pages"
            }
            return "\(value) pages"
        }
        guard let value else { return nil }
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
