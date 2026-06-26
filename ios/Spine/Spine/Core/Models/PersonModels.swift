import Foundation

struct PersonRef: Codable, Hashable, Identifiable {
    let source: String
    let id: String
}

struct PersonDetail: Decodable, Identifiable, Hashable {
    let id: String
    let source: String
    let name: String
    let biography: String?
    let profileUrl: String?
    let knownForDepartment: String?
    let birthDate: String?
    let deathDate: String?
    let placeOfBirth: String?
    let popularity: Double?
    let credits: PersonCredits

    var ref: PersonRef {
        PersonRef(source: source, id: id)
    }

    var filmography: [MediaSummary] {
        credits.cast
    }
}

struct PersonCredits: Decodable, Hashable {
    let cast: [MediaSummary]

    init(cast: [MediaSummary] = []) {
        self.cast = cast
    }
}
