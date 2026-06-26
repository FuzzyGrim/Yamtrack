import XCTest
@testable import Spine

final class PersonDetailTests: XCTestCase {
    func testPersonDetailDecodesExpectedAPIShape() throws {
        let json = """
        {
          "id": "819",
          "source": "tmdb",
          "name": "Edward Norton",
          "biography": "An actor biography.",
          "profile_url": "https://image.tmdb.org/t/p/h632/profile.jpg",
          "known_for_department": "Acting",
          "birth_date": "1969-08-18",
          "death_date": null,
          "place_of_birth": "Boston, Massachusetts, USA",
          "popularity": 42.7,
          "credits": {
            "cast": [
              {
                "ref": {
                  "item_id": null,
                  "source": "tmdb",
                  "media_type": "movie",
                  "media_id": "550",
                  "season_number": null,
                  "episode_number": null
                },
                "title": "Fight Club",
                "subtitle": "1999",
                "overview": null,
                "image_url": "https://example.com/poster.jpg",
                "poster_url": "https://example.com/poster.jpg",
                "release_date": "1999-10-15",
                "default_source": "tmdb",
                "user_state": null
              }
            ]
          }
        }
        """

        let detail = try JSONDecoder.api.decode(PersonDetail.self, from: Data(json.utf8))

        XCTAssertEqual(detail.ref, PersonRef(source: "tmdb", id: "819"))
        XCTAssertEqual(detail.name, "Edward Norton")
        XCTAssertEqual(detail.profileUrl, "https://image.tmdb.org/t/p/h632/profile.jpg")
        XCTAssertEqual(detail.knownForDepartment, "Acting")
        XCTAssertEqual(detail.filmography.map(\.title), ["Fight Club"])
    }

    @MainActor
    func testPersonDetailViewModelLoadsAndDeduplicatesFilmography() async {
        let ref = PersonRef(source: "tmdb", id: "819")
        let repository = ScriptedPeopleRepository(result: .success(personDetail(filmography: [
            mediaSummary(id: "550", title: "Fight Club"),
            mediaSummary(id: "550", title: "Fight Club Duplicate"),
            mediaSummary(id: "680", title: "Pulp Fiction"),
        ])))
        let viewModel = PersonDetailViewModel(ref: ref, peopleRepository: repository, onUnauthorized: {})

        await viewModel.load()

        XCTAssertEqual(repository.requests, [ref])
        XCTAssertEqual(viewModel.detail?.name, "Edward Norton")
        XCTAssertEqual(viewModel.filmography.map(\.title), ["Fight Club", "Pulp Fiction"])
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.isLoading)
    }

    @MainActor
    func testPersonDetailViewModelSupportsEmptyFilmography() async {
        let repository = ScriptedPeopleRepository(result: .success(personDetail(filmography: [])))
        let viewModel = PersonDetailViewModel(
            ref: PersonRef(source: "tmdb", id: "819"),
            peopleRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertNotNil(viewModel.detail)
        XCTAssertEqual(viewModel.filmography, [])
        XCTAssertNil(viewModel.errorMessage)
    }

    @MainActor
    func testPersonDetailViewModelStoresError() async {
        let repository = ScriptedPeopleRepository(result: .failure(APIError.httpStatus(500, "Server error")))
        let viewModel = PersonDetailViewModel(
            ref: PersonRef(source: "tmdb", id: "819"),
            peopleRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertNil(viewModel.detail)
        XCTAssertEqual(viewModel.filmography, [])
        XCTAssertNotNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.isLoading)
    }

    @MainActor
    func testPersonDetailViewModelCallsUnauthorizedHandler() async {
        var didCallUnauthorized = false
        let repository = ScriptedPeopleRepository(result: .failure(APIError.unauthorized))
        let viewModel = PersonDetailViewModel(
            ref: PersonRef(source: "tmdb", id: "819"),
            peopleRepository: repository,
            onUnauthorized: { didCallUnauthorized = true }
        )

        await viewModel.load()

        XCTAssertTrue(didCallUnauthorized)
        XCTAssertNil(viewModel.detail)
    }

    private func personDetail(filmography: [MediaSummary]) -> PersonDetail {
        PersonDetail(
            id: "819",
            source: "tmdb",
            name: "Edward Norton",
            biography: "An actor biography.",
            profileUrl: "https://image.tmdb.org/t/p/h632/profile.jpg",
            knownForDepartment: "Acting",
            birthDate: "1969-08-18",
            deathDate: nil,
            placeOfBirth: "Boston",
            popularity: 42.7,
            credits: PersonCredits(cast: filmography)
        )
    }

    private func mediaSummary(id: String, title: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: "tmdb",
                mediaType: "movie",
                mediaId: id,
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: title,
            posterUrl: "https://example.com/\(id).jpg",
            defaultSource: "tmdb"
        )
    }
}

private final class ScriptedPeopleRepository: PeopleRepository {
    let result: Result<PersonDetail, Error>
    var requests: [PersonRef] = []

    init(result: Result<PersonDetail, Error>) {
        self.result = result
    }

    func detail(ref: PersonRef) async throws -> PersonDetail {
        requests.append(ref)
        return try result.get()
    }
}
