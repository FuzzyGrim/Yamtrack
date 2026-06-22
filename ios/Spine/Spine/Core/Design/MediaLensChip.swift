import SwiftUI
import UIKit

// Usage (future screens):
// 1. Inject MediaLensStore or bind local mediaType
// 2. MediaLensChip + MediaLensPicker
// 3. .mediaLensAtmosphere(theme: store.theme(for: store.selectedMediaType))
// 4. Reload screen data on onChange(of: mediaType)
struct MediaLensChip: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @GestureState private var isPressed = false

    let selectedType: String
    var size: CGFloat = 44
    var symbolSize: CGFloat = 18
    let onTap: () -> Void

    private var theme: MediaTypeTheme {
        MediaTypeTheme.theme(for: selectedType)
    }

    var body: some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            onTap()
        } label: {
            ZStack {
                Circle()
                    .fill(.ultraThinMaterial)
                    .overlay {
                        Circle()
                            .fill(
                                LinearGradient(
                                    colors: [.white.opacity(0.24), .white.opacity(0.02)],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                    }
                    .overlay {
                        Circle()
                            .stroke(theme.accentColor.opacity(isPressed ? 0.72 : 0.38), lineWidth: 1)
                    }

                MediaTypeGlyph(theme: theme, size: symbolSize)
            }
            .frame(width: size, height: size)
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .scaleEffect(isPressed && !reduceMotion ? 0.92 : 1)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .updating($isPressed) { _, state, _ in
                    state = true
                }
        )
        .animation(reduceMotion ? nil : .spring(response: 0.24, dampingFraction: 0.72), value: isPressed)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.26), value: selectedType)
        .accessibilityLabel("Media type, \(theme.displayName)")
        .accessibilityAddTraits(.isButton)
    }
}

#Preview {
    ZStack {
        Color.black.ignoresSafeArea()
        MediaLensChip(selectedType: "movie") {}
    }
}
