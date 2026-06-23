import SwiftUI

struct HallOfFameCrownView: View {
    let items: [MediaSummary]
    let overflowCount: Int
    let onTap: (MediaSummary) -> Void

    @State private var crownRevealed = false

    private let avatarDiameter: CGFloat = 128
    private let borderOpacity = 0.14

    var body: some View {
        let visibleItems = Array(items.prefix(5))
        let cardSize = HallOfFameCrownLayout.cardSize(for: visibleItems.count)
        let placements = HallOfFameCrownLayout.placements(
            count: visibleItems.count,
            cardSize: cardSize,
            avatarDiameter: avatarDiameter
        )

        ZStack {
            ForEach(placements, id: \.index) { placement in
                let item = visibleItems[placement.index]

                Button {
                    onTap(item)
                } label: {
                    MediaArtwork(
                        url: item.displayPosterURL,
                        title: item.title,
                        slot: .hofCrown,
                        mediaType: item.ref.mediaType,
                        orientation: item.posterOrientation
                    )
                    .scaleEffect(cardSize.width / PosterSlot.hofCrown.size.width)
                    .frame(width: cardSize.width, height: cardSize.height)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .overlay {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .stroke(.white.opacity(borderOpacity), lineWidth: 1)
                    }
                    .shadow(color: .black.opacity(0.42), radius: 12, y: 8)
                }
                .buttonStyle(.plain)
                .scaleEffect(placement.scale, anchor: .bottom)
                .rotationEffect(crownRevealed ? placement.rotation : .zero, anchor: .bottom)
                .offset(
                    x: placement.x,
                    y: crownRevealed ? placement.y : placement.y + 20
                )
                .opacity(crownRevealed ? 1 : 0)
                .zIndex(placement.zIndex)
                .animation(
                    .spring(response: 0.45, dampingFraction: 0.78)
                        .delay(Double(placement.index) * 0.04),
                    value: crownRevealed
                )
                .accessibilityLabel("Hall of Fame, \(item.title)")
            }
        }
        .frame(width: 230, height: 132)
        .onAppear {
            crownRevealed = true
        }
    }
}

#Preview("0 filled") {
    HallOfFameCrownPreview(count: 0)
}

#Preview("1 filled") {
    HallOfFameCrownPreview(count: 1)
}

#Preview("3 filled") {
    HallOfFameCrownPreview(count: 3)
}

#Preview("5 filled") {
    HallOfFameCrownPreview(count: 5)
}

#Preview("7 filled") {
    HallOfFameCrownPreview(count: 7)
}

private struct HallOfFameCrownPreview: View {
    let count: Int

    private var items: [MediaSummary] {
        (0..<count).map { index in
            MediaSummary(
                ref: MediaRef(
                    itemId: index,
                    source: "preview",
                    mediaType: ["movie", "tv", "anime", "manga", "game", "book", "comic"][index % 7],
                    mediaId: "\(index)",
                    seasonNumber: nil,
                    episodeNumber: nil
                ),
                title: "Favorite \(index + 1)",
                posterUrl: nil,
                posterOrientation: .portrait
            )
        }
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ZStack {
                HallOfFameCrownView(items: Array(items.prefix(5)), overflowCount: max(0, count - 5)) { _ in }

                Circle()
                    .fill(.black.opacity(0.82))
                    .frame(width: 128, height: 128)
                    .overlay {
                        Circle()
                            .stroke(.white.opacity(0.18), lineWidth: 1)
                    }
                    .shadow(color: .black.opacity(0.44), radius: 22, y: 12)
                    .overlay(alignment: .bottomTrailing) {
                        if count > 5 {
                            Text("+\(count - 5)")
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 8)
                                .frame(height: 24)
                                .background(.black.opacity(0.72), in: Capsule())
                                .overlay {
                                    Capsule()
                                        .stroke(.white.opacity(0.18), lineWidth: 1)
                                }
                                .offset(x: 4, y: -8)
                                .accessibilityLabel("\(count - 5) more Hall of Fame items")
                        }
                    }
            }
        }
        .frame(width: 320, height: 240)
    }
}
