import SwiftUI

struct MediaArtwork: View {
    let url: String?
    let title: String
    let slot: PosterSlot
    var mediaType: String?
    var orientation: PosterOrientation?
    var contentMode: PosterContentMode = .fill

    var body: some View {
        Group {
            if let imageURL {
                AsyncImage(url: imageURL) { phase in
                    artwork(for: phase)
                }
            } else {
                placeholder
            }
        }
        .frame(width: slot.size.width, height: slot.size.height)
        .clipShape(RoundedRectangle(cornerRadius: slot.cornerRadius, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: slot.cornerRadius, style: .continuous))
        .accessibilityLabel(title)
    }

    @ViewBuilder
    private func artwork(for phase: AsyncImagePhase) -> some View {
        switch phase {
        case let .success(image):
            image
                .resizable()
                .modifier(ArtworkScaleModifier(contentMode: contentMode))
                .frame(width: slot.size.width, height: slot.size.height)
                .clipped()
        case .empty:
            ProgressView()
        default:
            placeholder
        }
    }

    private var imageURL: URL? {
        guard let url, !url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return URL(string: url)
    }

    private var placeholder: some View {
        let theme = MediaTypeTheme.theme(for: mediaType ?? "unknown")
        return ZStack {
            LinearGradient(
                colors: theme.gradientColors.map { $0.opacity(0.8) },
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .overlay(.quaternary.opacity(0.28))

            MediaTypeGlyph(theme: theme, size: slot.glyphSize)
                .opacity(0.8)
        }
        .frame(width: slot.size.width, height: slot.size.height)
    }
}

private struct ArtworkScaleModifier: ViewModifier {
    let contentMode: PosterContentMode

    @ViewBuilder
    func body(content: Content) -> some View {
        switch contentMode {
        case .fill:
            content.scaledToFill()
        case .fit:
            content.scaledToFit()
        }
    }
}

#Preview("Media Artwork") {
    HStack(alignment: .top, spacing: 14) {
        MediaArtwork(
            url: "https://image.tmdb.org/t/p/w500/8kOWDBK6XlPUzckuHDo3wwVRFwt.jpg",
            title: "Portrait",
            slot: .carousel,
            mediaType: "movie",
            orientation: .portrait
        )
        MediaArtwork(
            url: "https://image.tmdb.org/t/p/original/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
            title: "Landscape",
            slot: .carousel,
            mediaType: "tv",
            orientation: .landscape
        )
        MediaArtwork(url: nil, title: "Missing", slot: .carousel, mediaType: "book")
    }
    .padding()
    .background(Color.black)
}
