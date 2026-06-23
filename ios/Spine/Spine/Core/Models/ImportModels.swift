import Foundation

enum ImportMode: String, Codable, CaseIterable, Identifiable {
    case new
    case overwrite

    var id: String { rawValue }
}

struct ImportQueueResponse: Decodable, Equatable {
    let taskId: String
    let status: String
}

struct ImportTaskStatus: Decodable, Equatable {
    let taskId: String
    let taskName: String?
    let status: String
    let dateCreated: String?
    let dateDone: String?
    let result: String?
}
