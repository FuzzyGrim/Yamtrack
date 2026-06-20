import Foundation

struct HealthResponse: Decodable {
    let status: String
    let version: String
    let time: String
}
