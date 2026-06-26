import SwiftUI

struct MediaSearchBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var isFocused: Bool
    @Binding var text: String
    @Binding var selectedMediaType: String
    @Binding var isLensExpanded: Bool
    let availableTypes: [String]
    var placeholderPrefix = "Search"
    var horizontalPadding: CGFloat = 16
    let onLensTap: () -> Void
    let onLensSelect: (String) -> Void
    let onSearch: (String) -> Void
    let onClear: () -> Void

    var body: some View {
        ZStack {
            if isLensExpanded {
                MediaSearchLensRail(
                    selectedType: $selectedMediaType,
                    availableTypes: availableTypes,
                    onSelect: selectLens
                )
                .transition(.opacity)
            } else {
                searchControls
                    .transition(.opacity)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, isLensExpanded ? 12 : 3)
        .frame(minHeight: isLensExpanded ? 92 : 44)
        .background(.ultraThinMaterial, in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(0.10), lineWidth: 1)
        }
        .padding(.horizontal, horizontalPadding)
        .padding(.top, 2)
        .padding(.bottom, 5)
        .animation(reduceMotion ? nil : .spring(response: 0.36, dampingFraction: 0.84), value: isLensExpanded)
        .onChange(of: isLensExpanded) {
            if isLensExpanded {
                isFocused = false
            }
        }
    }

    private var searchControls: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)

            TextField(searchPlaceholder, text: $text)
                .focused($isFocused)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .onSubmit(performSearch)

            MediaLensChip(selectedType: selectedMediaType, size: 24, symbolSize: 12, onTap: onLensTap)
                .padding(.horizontal, 2)

            Button(action: clearOrDismiss) {
                Image(systemName: "xmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(text.isEmpty ? "Dismiss search" : "Clear search")
        }
    }

    private var searchPlaceholder: String {
        "\(placeholderPrefix) \(MediaTypeTheme.theme(for: selectedMediaType).displayName.lowercased())"
    }

    private func performSearch() {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        onSearch(trimmed)
    }

    private func clearOrDismiss() {
        if text.isEmpty {
            isFocused = false
        } else {
            text = ""
            onClear()
        }
    }

    private func selectLens(_ type: String) {
        selectedMediaType = type
        onLensSelect(type)
        isLensExpanded = false
    }
}

private struct MediaSearchLensRail: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var selectedType: String
    let availableTypes: [String]
    let onSelect: (String) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 12) {
                    ForEach(availableTypes, id: \.self) { type in
                        MediaSearchLensOrb(
                            type: type,
                            isSelected: selectedType == type,
                            onTap: {
                                onSelect(type)
                            }
                        )
                        .id(type)
                    }
                }
                .scrollTargetLayout()
                .padding(.horizontal, 2)
                .padding(.vertical, 10)
            }
            .frame(height: 68)
            .scrollTargetBehavior(.viewAligned)
            .onAppear {
                proxy.scrollTo(selectedType, anchor: .center)
            }
            .onChange(of: selectedType) {
                guard !reduceMotion else { return }
                withAnimation(.spring(response: 0.30, dampingFraction: 0.82)) {
                    proxy.scrollTo(selectedType, anchor: .center)
                }
            }
        }
        .accessibilityLabel("Media type picker")
    }
}

private struct MediaSearchLensOrb: View {
    let type: String
    let isSelected: Bool
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
