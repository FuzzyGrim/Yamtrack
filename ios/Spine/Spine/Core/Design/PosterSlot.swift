import SwiftUI

enum PosterSlot {
    case searchRow
    case libraryRow
    case diaryRow
    case profileRow
    case hero
    case carousel
    case seasonCard
    case logSheet
    case pickerGrid
    case tagGrid
    case episodeStill
    case hofCrown

    var size: CGSize {
        switch self {
        case .searchRow:
            CGSize(width: 54, height: 81)
        case .libraryRow, .diaryRow, .profileRow:
            CGSize(width: 56, height: 84)
        case .hero:
            CGSize(width: 191, height: 286.5)
        case .carousel:
            CGSize(width: 100, height: 150)
        case .seasonCard:
            CGSize(width: 90, height: 135)
        case .logSheet:
            CGSize(width: 106, height: 159)
        case .pickerGrid:
            CGSize(width: 104, height: 156)
        case .tagGrid:
            CGSize(width: 80, height: 120)
        case .episodeStill:
            CGSize(width: 72, height: 42)
        case .hofCrown:
            CGSize(width: 64, height: 96)
        }
    }

    var cornerRadius: CGFloat {
        switch self {
        case .episodeStill:
            4
        case .hero, .logSheet:
            10
        default:
            8
        }
    }

    var glyphSize: CGFloat {
        min(size.width, size.height) * 0.36
    }
}

enum PosterContentMode {
    case fill
    case fit
}
