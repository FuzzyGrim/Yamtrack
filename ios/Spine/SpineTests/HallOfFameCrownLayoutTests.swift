import SwiftUI
import XCTest
@testable import Spine

final class HallOfFameCrownLayoutTests: XCTestCase {
    func testCrownLayoutReturnsEmptyForZeroCount() {
        let placements = HallOfFameCrownLayout.placements(
            count: 0,
            cardSize: CGSize(width: 50, height: 75),
            avatarDiameter: 128
        )

        XCTAssertEqual(placements, [])
    }

    func testCrownLayoutSymmetricForTwoCards() {
        let placements = HallOfFameCrownLayout.placements(
            count: 2,
            cardSize: CGSize(width: 60, height: 90),
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 2)
        XCTAssertEqual(placements[0].x, -placements[1].x, accuracy: 0.001)
        XCTAssertEqual(placements[0].rotation.degrees, -placements[1].rotation.degrees, accuracy: 0.001)
    }

    func testCrownLayoutCenterCardUprightForThree() {
        let placements = HallOfFameCrownLayout.placements(
            count: 3,
            cardSize: CGSize(width: 58, height: 87),
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 3)
        XCTAssertEqual(placements[1].x, 0, accuracy: 0.001)
        XCTAssertEqual(placements[1].rotation.degrees, 0, accuracy: 0.001)
    }

    func testCrownLayoutCountNeverExceedsFive() {
        let placements = HallOfFameCrownLayout.placements(
            count: 7,
            cardSize: CGSize(width: 50, height: 75),
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 5)
    }

    func testFavoriteSlotsSortOrderPreserved() {
        let hof: [String: MediaSummary?] = [
            "comic": nil,
            "movie_secondary": nil,
            "book": nil,
            "movie_primary": nil,
            "anime": nil,
            "game": nil,
            "tv": nil,
            "manga": nil,
        ]

        XCTAssertEqual(
            ProfileFavorites.slots(from: hof).map(\.id),
            ["movie_primary", "movie_secondary", "tv", "anime", "manga", "game", "book", "comic"]
        )
    }
}
