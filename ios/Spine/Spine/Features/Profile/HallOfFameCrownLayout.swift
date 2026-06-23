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
        let count = max(count, 0)
        guard count > 0 else { return [] }

        let maxAngle = maxAngle(for: count)
        let angles: [Double]
        if count == 1 {
            angles = [0]
        } else {
            angles = (0..<count).map { index in
                let progress = Double(index) / Double(count - 1)
                return -maxAngle + progress * maxAngle * 2
            }
        }
        let radiusX = avatarDiameter * 0.82
        let radiusY = avatarDiameter * 0.62
        let baseY = avatarDiameter * 0.12

        return angles.enumerated().map { index, degrees in
            let radians = degrees * .pi / 180
            let distanceFromCenter = abs(Double(index) - Double(count - 1) / 2)
            let normalizedDistance = count == 1 ? 0 : distanceFromCenter / (Double(count - 1) / 2)
            let scale = 1.04 - CGFloat(normalizedDistance) * 0.10
            let x = sin(radians) * radiusX
            let y = baseY - cos(radians) * radiusY
            let zIndex = 10 - normalizedDistance

            return HallOfFameCrownPlacement(
                index: index,
                x: x,
                y: y,
                rotation: .degrees(degrees * 0.58),
                scale: scale,
                zIndex: zIndex
            )
        }
    }

    static func cardSize(for count: Int) -> CGSize {
        switch max(count, 1) {
        case 1:
            CGSize(width: 64, height: 96)
        case 2:
            CGSize(width: 60, height: 90)
        case 3:
            CGSize(width: 58, height: 87)
        case 4:
            CGSize(width: 54, height: 81)
        case 5:
            CGSize(width: 50, height: 75)
        case 6, 7:
            CGSize(width: 46, height: 69)
        default:
            CGSize(width: 42, height: 63)
        }
    }

    private static func maxAngle(for count: Int) -> Double {
        switch count {
        case 1:
            0
        case 2:
            35
        case 3:
            48
        case 4:
            58
        case 5:
            66
        case 6:
            70
        default:
            72
        }
    }
}
