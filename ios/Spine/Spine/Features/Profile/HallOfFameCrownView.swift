import SwiftUI

struct HallOfFameCrownView: View {
    let slots: [FavoriteSlot]
    var savingSlotIDs: Set<String> = []
    let onTap: (MediaSummary) -> Void
    var onEmptyTap: (FavoriteSlot) -> Void = { _ in }
    var onFilledLongPress: (FavoriteSlot) -> Void = { _ in }

    @State private var crownRevealed = false

    private let avatarDiameter: CGFloat = 128
    private let borderOpacity = 0.14

    var body: some View {
        let cardSize = HallOfFameCrownLayout.cardSize(for: slots.count)
        let placements = HallOfFameCrownLayout.placements(
            count: slots.count,
            cardSize: cardSize,
            avatarDiameter: avatarDiameter
        )

        ZStack {
            ForEach(placements, id: \.index) { placement in
                let slot = slots[placement.index]

                Group {
                    if let item = slot.item {
                        Button {
                            onTap(item)
                        } label: {
                            HallOfFameCrownFilledCard(item: item, cardSize: cardSize, borderOpacity: borderOpacity)
                        }
                        .buttonStyle(.plain)
                        .onLongPressGesture {
                            onFilledLongPress(slot)
                        }
                        .accessibilityLabel("Hall of Fame, \(item.title)")
                    } else {
                        Button {
                            onEmptyTap(slot)
                        } label: {
                            HallOfFameCrownEmptyShell(cardSize: cardSize)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Add Hall of Fame \(slot.title)")
                    }

                    if savingSlotIDs.contains(slot.id) {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(.black.opacity(0.52))
                            .frame(width: cardSize.width, height: cardSize.height)
                            .overlay {
                                ProgressView()
                                    .tint(.white)
                            }
                    }
                }
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
            }
        }
        .frame(width: 340, height: 210)
        .onAppear {
            crownRevealed = true
        }
    }
}

private struct HallOfFameCrownFilledCard: View {
    let item: MediaSummary
    let cardSize: CGSize
    let borderOpacity: Double

    var body: some View {
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
}

private struct HallOfFameCrownEmptyShell: View {
    let cardSize: CGSize

    var body: some View {
        RoundedRectangle(cornerRadius: 8, style: .continuous)
            .fill(.white.opacity(0.04))
            .frame(width: cardSize.width, height: cardSize.height)
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(.white.opacity(0.10), lineWidth: 1)
            }
            .overlay {
                Image(systemName: "plus")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.26))
            }
            .opacity(0.88)
            .shadow(color: .black.opacity(0.16), radius: 8, y: 5)
    }
}

#Preview("0 slots") {
    HallOfFameCrownPreview(slots: [])
}

#Preview("7 empty shells") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "anime", "manga", "game", "book", "comic"], filledIndexes: []))
}

#Preview("2 filled, 5 empty") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "anime", "manga", "game", "book", "comic"], filledIndexes: [0, 5]))
}

#Preview("5 filled") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(filledIndexes: [0, 1, 2, 3, 4]))
}

#Preview("7 slots, 3 filled") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "anime", "manga", "game", "book", "comic"], filledIndexes: [0, 2, 5]))
}

private struct HallOfFameCrownPreview: View {
    let slots: [FavoriteSlot]

    static func slots(keys: [String] = ["movie", "tv", "anime", "manga", "game"], filledIndexes: Set<Int>) -> [FavoriteSlot] {
        keys.enumerated().map { index, key in
            FavoriteSlot(id: key, title: title(for: key), item: filledIndexes.contains(index) ? media(index: index, mediaType: key) : nil)
        }
    }

    private static func title(for key: String) -> String {
        key.replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .split(separator: " ")
            .map { $0.capitalized }
            .joined(separator: " ")
    }

    private static func media(index: Int, mediaType: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: index,
                source: "preview",
                mediaType: mediaType,
                mediaId: "\(index)",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Favorite \(index + 1)",
            posterUrl: nil,
            posterOrientation: .portrait
        )
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ZStack {
                HallOfFameCrownView(slots: slots) { _ in }

                Circle()
                    .fill(.black.opacity(0.82))
                    .frame(width: 128, height: 128)
                    .overlay {
                        Circle()
                            .stroke(.white.opacity(0.18), lineWidth: 1)
                    }
                    .shadow(color: .black.opacity(0.44), radius: 22, y: 12)
            }
        }
        .frame(width: 390, height: 280)
    }
}
