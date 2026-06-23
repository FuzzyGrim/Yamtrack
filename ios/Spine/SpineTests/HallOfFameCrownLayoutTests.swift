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

    func testCrownLayoutShowsSevenSlots() {
        let placements = HallOfFameCrownLayout.placements(
            count: 7,
            cardSize: CGSize(width: 50, height: 75),
            avatarDiameter: 128
        )

        XCTAssertEqual(placements.count, 7)
    }

    func testFavoriteSlotsSortOrderPreserved() {
        let hof: [String: MediaSummary?] = [
            "comic": nil,
            "book": nil,
            "movie": nil,
            "anime": nil,
            "game": nil,
            "tv": nil,
            "manga": nil,
        ]

        XCTAssertEqual(
            ProfileFavorites.slots(from: hof).map(\.id),
            ["movie", "tv", "anime", "manga", "game", "book", "comic"]
        )
    }

    func testFavoriteSlotsIncludeDefaultEmptyChoicesWhenAPIOnlySendsFilledItems() {
        let movie = media(index: 1, mediaType: "movie")
        let book = media(index: 2, mediaType: "book")
        let slots = ProfileFavorites.slots(from: [
            "movie": movie,
            "book": book,
        ])

        XCTAssertEqual(slots.map(\.id), ["movie", "tv", "anime", "manga", "game", "book", "comic"])
        XCTAssertEqual(slots.compactMap(\.item).map(\.id), [movie.id, book.id])
    }

    func testFavoriteSlotsPreserveEmptyAndFilledState() {
        let movie = media(index: 1, mediaType: "movie")
        let slots = ProfileFavorites.slots(from: [
            "movie": movie,
            "tv": nil,
        ])

        XCTAssertEqual(slots.count, 7)
        XCTAssertEqual(slots[0].id, "movie")
        XCTAssertEqual(slots[0].item?.id, movie.id)
        XCTAssertEqual(slots[1].id, "tv")
        XCTAssertNil(slots[1].item)
    }

    func testCrownLayoutUsesGeometricArcForSevenSlots() {
        let placements = HallOfFameCrownLayout.placements(
            count: 7,
            cardSize: CGSize(width: 54, height: 81),
            avatarDiameter: 128
        )

        XCTAssertEqual(placements[3].x, 0, accuracy: 0.001)
        XCTAssertEqual(placements[3].rotation.degrees, 0, accuracy: 0.001)
        XCTAssertEqual(placements[0].x, -placements[6].x, accuracy: 0.001)
        XCTAssertEqual(placements[0].y, placements[6].y, accuracy: 0.001)
        XCTAssertEqual(placements[0].rotation.degrees, -placements[6].rotation.degrees, accuracy: 0.001)
    }

    func testCrownLayoutRaisesCenterCardAboveAvatar() {
        let placements = HallOfFameCrownLayout.placements(
            count: 7,
            cardSize: CGSize(width: 54, height: 81),
            avatarDiameter: 128
        )

        XCTAssertLessThan(placements[3].y, -96)
    }

    private func media(index: Int, mediaType: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: index,
                source: "test",
                mediaType: mediaType,
                mediaId: "\(index)",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Favorite \(index)",
            posterOrientation: .portrait
        )
    }
}
