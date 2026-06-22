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
                .fill(.white)
                .frame(width: size * 1.25, height: size)
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
            CGPoint(x: 0.02, y: 0.48),
            CGPoint(x: 0.20, y: 0.35),
            CGPoint(x: 0.12, y: 0.06),
            CGPoint(x: 0.34, y: 0.20),
            CGPoint(x: 0.42, y: 0.00),
            CGPoint(x: 0.52, y: 0.22),
            CGPoint(x: 0.70, y: 0.05),
            CGPoint(x: 0.66, y: 0.34),
            CGPoint(x: 0.98, y: 0.30),
            CGPoint(x: 0.76, y: 0.52),
            CGPoint(x: 0.94, y: 0.78),
            CGPoint(x: 0.62, y: 0.70),
            CGPoint(x: 0.56, y: 1.00),
            CGPoint(x: 0.46, y: 0.74),
            CGPoint(x: 0.28, y: 0.96),
            CGPoint(x: 0.32, y: 0.66),
            CGPoint(x: 0.04, y: 0.72),
            CGPoint(x: 0.22, y: 0.56)
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
