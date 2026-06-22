import SwiftUI

struct MediaTypeTheme: Equatable {
    let slug: String
    let displayName: String
    let symbolName: String
    let accentColor = Color(red: 0.72, green: 0.74, blue: 0.78)
    let gradientColors = [Color(red: 0.31, green: 0.33, blue: 0.37), Color(red: 0.09, green: 0.10, blue: 0.12)]

    var symbolText: String? {
        slug == "anime" ? "オ" : nil
    }

    static func theme(for slug: String) -> MediaTypeTheme {
        let normalized = slug.lowercased()
        switch normalized {
        case "movie":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Movies",
                symbolName: "film"
            )
        case "tv":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "TV",
                symbolName: "tv"
            )
        case "anime":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Anime",
                symbolName: "sparkles"
            )
        case "manga":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Manga",
                symbolName: "book.closed"
            )
        case "game":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Games",
                symbolName: "gamecontroller.fill"
            )
        case "book":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Books",
                symbolName: "book.fill"
            )
        case "comic":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Comics",
                symbolName: "rectangle.3.group.bubble.left"
            )
        case "boardgame":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Board Games",
                symbolName: "dice.fill"
            )
        default:
            return MediaTypeTheme(
                slug: normalized,
                displayName: normalized.split(separator: "_").map { $0.capitalized }.joined(separator: " "),
                symbolName: "square.grid.2x2"
            )
        }
    }
}

struct MediaTypeGlyph: View {
    let theme: MediaTypeTheme
    let size: CGFloat

    var body: some View {
        if let symbolText = theme.symbolText {
            Text(symbolText)
                .font(.system(size: size, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
        } else if theme.slug == "comic" {
            ComicBurstShape()
                .stroke(.white, style: StrokeStyle(lineWidth: max(1.2, size * 0.08), lineCap: .round, lineJoin: .round))
                .frame(width: size * 1.35, height: size * 1.08)
        } else {
            Image(systemName: theme.symbolName)
                .symbolRenderingMode(.hierarchical)
                .font(.system(size: size, weight: .semibold))
                .foregroundStyle(.white, theme.accentColor)
        }
    }
}

private struct ComicBurstShape: Shape {
    func path(in rect: CGRect) -> Path {
        let points: [CGPoint] = [
            CGPoint(x: 0.03, y: 0.43),
            CGPoint(x: 0.21, y: 0.38),
            CGPoint(x: 0.14, y: 0.12),
            CGPoint(x: 0.33, y: 0.26),
            CGPoint(x: 0.40, y: 0.04),
            CGPoint(x: 0.50, y: 0.29),
            CGPoint(x: 0.68, y: 0.12),
            CGPoint(x: 0.63, y: 0.36),
            CGPoint(x: 0.94, y: 0.30),
            CGPoint(x: 0.75, y: 0.50),
            CGPoint(x: 0.92, y: 0.64),
            CGPoint(x: 0.69, y: 0.62),
            CGPoint(x: 0.72, y: 0.88),
            CGPoint(x: 0.54, y: 0.70),
            CGPoint(x: 0.48, y: 0.97),
            CGPoint(x: 0.40, y: 0.73),
            CGPoint(x: 0.29, y: 0.89),
            CGPoint(x: 0.26, y: 0.72),
            CGPoint(x: 0.08, y: 0.94),
            CGPoint(x: 0.20, y: 0.65),
            CGPoint(x: 0.04, y: 0.58),
            CGPoint(x: 0.21, y: 0.52)
        ]

        var path = Path()
        path.move(to: CGPoint(x: rect.minX + points[0].x * rect.width, y: rect.minY + points[0].y * rect.height))
        for point in points.dropFirst() {
            path.addLine(to: CGPoint(x: rect.minX + point.x * rect.width, y: rect.minY + point.y * rect.height))
        }
        path.closeSubpath()
        return path
    }
}
