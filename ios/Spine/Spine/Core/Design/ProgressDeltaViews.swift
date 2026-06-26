import SwiftUI

struct ProgressDeltaInlineView: View {
    let delta: ProgressChangeDisplay

    var body: some View {
        HStack(spacing: 4) {
            Text(delta.previous)
            Image(systemName: "arrow.right")
                .font(.system(size: 9, weight: .black))
            Text(delta.current)
        }
        .font(.system(size: 11, weight: .bold))
        .foregroundStyle(.white.opacity(0.54))
        .lineLimit(1)
        .minimumScaleFactor(0.65)
        .accessibilityLabel("\(delta.previous) to \(delta.current)")
    }
}

struct ProgressDeltaChipView: View {
    let delta: ProgressChangeDisplay

    var body: some View {
        HStack(spacing: 7) {
            Text(delta.previous)
            Image(systemName: "arrow.right")
                .font(.system(size: 9, weight: .black))
            Text(delta.current)
        }
        .font(.system(size: 10, weight: .bold))
        .foregroundStyle(.white.opacity(0.78))
        .padding(.horizontal, 10)
        .frame(height: 21)
        .background(.white.opacity(0.11), in: Capsule())
        .accessibilityLabel("\(delta.previous) to \(delta.current)")
    }
}
