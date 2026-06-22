import SwiftUI
import UIKit

struct MediaLensPicker: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var selectionNamespace

    @Binding var selectedType: String
    @Binding var isPresented: Bool

    let availableTypes: [String]
    let onSelect: ((String) -> Void)?

    var body: some View {
        ZStack(alignment: .top) {
            Color.black.opacity(0.001)
                .ignoresSafeArea()
                .onTapGesture {
                    dismiss()
                }

            lensWheel
                .padding(.top, 86)
                .transition(reduceMotion ? .opacity : .asymmetric(
                    insertion: .scale(scale: 0.88, anchor: .top).combined(with: .opacity),
                    removal: .scale(scale: 0.96, anchor: .top).combined(with: .opacity)
                ))
        }
        .animation(reduceMotion ? nil : .spring(response: 0.34, dampingFraction: 0.78), value: isPresented)
        .animation(reduceMotion ? nil : .spring(response: 0.30, dampingFraction: 0.76), value: selectedType)
    }

    private var lensWheel: some View {
        VStack(spacing: 8) {
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(spacing: 12) {
                        ForEach(availableTypes, id: \.self) { type in
                            MediaLensOrb(
                                type: type,
                                isSelected: selectedType == type,
                                namespace: selectionNamespace,
                                onTap: {
                                    select(type)
                                }
                            )
                            .id(type)
                        }
                    }
                    .scrollTargetLayout()
                    .padding(.horizontal, 18)
                    .padding(.vertical, 10)
                }
                .frame(height: 76)
                .scrollTargetBehavior(.viewAligned)
                .onAppear {
                    proxy.scrollTo(selectedType, anchor: .center)
                }
                .onChange(of: selectedType) {
                    guard !reduceMotion else { return }
                    withAnimation(.spring(response: 0.34, dampingFraction: 0.82)) {
                        proxy.scrollTo(selectedType, anchor: .center)
                    }
                }
            }

            Text(currentTheme.displayName)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.86))
                .padding(.horizontal, 11)
                .padding(.vertical, 5)
                .background(.white.opacity(0.08), in: Capsule())
                .overlay {
                    Capsule()
                        .stroke(currentTheme.accentColor.opacity(0.28), lineWidth: 1)
                }
                .transition(reduceMotion ? .opacity : .move(edge: .top).combined(with: .opacity))
                .id(currentTheme.slug)
        }
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background {
            Capsule()
                .fill(.ultraThinMaterial)
                .overlay {
                    Capsule()
                        .fill(
                            LinearGradient(
                                colors: [currentTheme.accentColor.opacity(0.16), .white.opacity(0.035)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                }
                .overlay {
                    Capsule()
                        .stroke(
                            LinearGradient(
                                colors: [currentTheme.accentColor.opacity(0.38), .white.opacity(0.10)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            lineWidth: 1
                        )
                }
        }
        .padding(.horizontal, 12)
    }

    private var currentTheme: MediaTypeTheme {
        MediaTypeTheme.theme(for: selectedType)
    }

    private func select(_ type: String) {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        selectedType = type
        onSelect?(type)
        dismiss()
    }

    private func dismiss() {
        if reduceMotion {
            isPresented = false
        } else {
            withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                isPresented = false
            }
        }
    }
}

private struct MediaLensOrb: View {
    let type: String
    let isSelected: Bool
    let namespace: Namespace.ID
    let onTap: () -> Void

    private var theme: MediaTypeTheme {
        MediaTypeTheme.theme(for: type)
    }

    var body: some View {
        Button(action: onTap) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: isSelected ? theme.gradientColors : [.white.opacity(0.14), .white.opacity(0.05)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .overlay {
                        Circle()
                            .stroke(isSelected ? theme.accentColor.opacity(0.72) : .white.opacity(0.14), lineWidth: 1)
                    }

                MediaTypeGlyph(theme: theme, size: isSelected ? 22 : 18)
            }
            .frame(width: isSelected ? 58 : 48, height: isSelected ? 58 : 48)
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .scaleEffect(isSelected ? 1.06 : 0.94)
        .accessibilityLabel(theme.displayName)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }
}

#Preview {
    @Previewable @State var selectedType = "movie"
    @Previewable @State var isPresented = true

    ZStack {
        Color.black.ignoresSafeArea()
        MediaLensPicker(
            selectedType: $selectedType,
            isPresented: $isPresented,
            availableTypes: APIConstants.fallbackMediaTypes.filter { !["episode", "season"].contains($0) },
            onSelect: nil
        )
    }
}
