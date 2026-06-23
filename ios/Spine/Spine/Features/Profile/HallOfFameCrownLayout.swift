import SwiftUI

struct HallOfFameCrownPlacement: Equatable {
    let index: Int
    let x: CGFloat
    let y: CGFloat
    let rotation: Angle
    let scale: CGFloat
    let zIndex: Double
}

struct HallOfFameCrownLayout {
    static func placements(count: Int, cardSize: CGSize, avatarDiameter: CGFloat) -> [HallOfFameCrownPlacement] {
        let count = min(max(count, 0), 5)
        guard count > 0 else { return [] }

        let specs = Self.specs(for: count)
        let radius = avatarDiameter * specs.radiusMultiplier

        return specs.angles.enumerated().map { index, degrees in
            let radians = degrees * .pi / 180
            let distanceFromCenter = abs(Double(index) - Double(count - 1) / 2)
            let normalizedDistance = count == 1 ? 0 : distanceFromCenter / (Double(count - 1) / 2)
            let scale = specs.centerScale - CGFloat(normalizedDistance) * specs.edgeScaleDrop
            let x = sin(radians) * radius
            let y = specs.baseY - (1 - cos(radians)) * radius * 0.28
            let zIndex = 10 - normalizedDistance

            return HallOfFameCrownPlacement(
                index: index,
                x: x,
                y: y,
                rotation: .degrees(degrees),
                scale: scale,
                zIndex: zIndex
            )
        }
    }

    static func cardSize(for count: Int) -> CGSize {
        switch min(max(count, 1), 5) {
        case 1:
            CGSize(width: 64, height: 96)
        case 2:
            CGSize(width: 60, height: 90)
        case 3:
            CGSize(width: 58, height: 87)
        case 4:
            CGSize(width: 54, height: 81)
        default:
            CGSize(width: 50, height: 75)
        }
    }

    private static func specs(for count: Int) -> (angles: [Double], baseY: CGFloat, radiusMultiplier: CGFloat, centerScale: CGFloat, edgeScaleDrop: CGFloat) {
        switch count {
        case 1:
            ([0], -58, 1.06, 1, 0)
        case 2:
            ([-16, 16], -54, 1.2, 1, 0)
        case 3:
            ([-22, 0, 22], -58, 1.15, 1.05, 0.06)
        case 4:
            ([-28, -9, 9, 28], -56, 1.17, 1.02, 0.05)
        default:
            ([-32, -16, 0, 16, 32], -58, 1.16, 1.03, 0.08)
        }
    }
}
