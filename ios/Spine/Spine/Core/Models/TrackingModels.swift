import Foundation

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
}

struct ProgressState: Codable, Equatable {
    let kind: String
    let value: Decimal?
    let max: Decimal?
    let unit: String
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
