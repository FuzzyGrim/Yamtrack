import SwiftUI

@MainActor
@Observable
final class SearchViewModel {
    var query = ""
    var mediaTypes = APIConstants.fallbackMediaTypes
    var results: [MediaSummary] = []
    var isLoading = false
    var errorMessage: String?

    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private var searchID = 0

    init(mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    static func lensMediaTypes(from mediaTypes: [String]) -> [String] {
        let filtered = mediaTypes.filter { !["episode", "season"].contains($0) }
        return filtered.isEmpty ? APIConstants.fallbackMediaTypes : filtered
    }

    func loadMeta() async {
        do {
            let meta = try await mediaRepository.meta()
            mediaTypes = Self.lensMediaTypes(from: meta.mediaTypes)
        } catch {
            mediaTypes = Self.lensMediaTypes(from: APIConstants.fallbackMediaTypes)
        }
    }

    func clear() {
        searchID += 1
        query = ""
        results = []
        errorMessage = nil
        isLoading = false
    }

    func search(_ text: String? = nil, mediaType: String) async {
        let trimmed = (text ?? query).trimmingCharacters(in: .whitespacesAndNewlines)
        searchID += 1
        let currentSearchID = searchID
        query = trimmed

        guard !trimmed.isEmpty else {
            results = []
            return
        }

        isLoading = true
        errorMessage = nil
        defer {
            if currentSearchID == searchID {
                isLoading = false
            }
        }

        do {
            let found = try await mediaRepository.search(query: trimmed, mediaType: mediaType)
            guard currentSearchID == searchID else { return }
            results = found
        } catch {
            guard currentSearchID == searchID else { return }
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct SearchView: View {
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        SearchViewContainer(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
    }
}

/// Owns detail presentation so neither search typing nor results updates touch `MediaDetailView`.
private struct SearchViewContainer: View {
    @State private var selectedRef: MediaRef?

    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void

    var body: some View {
        SearchViewContent(
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized,
            onSelect: { selectedRef = $0 }
        )
        .fullScreenCover(item: $selectedRef) { ref in
            MediaDetailCover(
                ref: ref,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onUnauthorized: onUnauthorized
            )
        }
    }
}

/// Thin wrapper so `MediaDetailView` is only constructed when the cover is actually presented.
private struct MediaDetailCover: View {
    let ref: MediaRef
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void

    var body: some View {
        MediaDetailView(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
    }
}

private struct SearchViewContent: View {
    @State private var viewModel: SearchViewModel
    @State private var mediaLensStore = MediaLensStore()
    @State private var isMediaLensExpanded = false
    @State private var draftText = ""
    @AppStorage("recentSearches") private var recentSearchesData = "[]"

    let onSelect: (MediaRef) -> Void

    init(mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void, onSelect: @escaping (MediaRef) -> Void) {
        _viewModel = State(initialValue: SearchViewModel(mediaRepository: mediaRepository, onUnauthorized: onUnauthorized))
        self.onSelect = onSelect
    }

    var body: some View {
        ZStack {
            NavigationStack {
                VStack(spacing: 0) {
                    SearchInputBar(
                        text: $draftText,
                        selectedMediaType: selectedMediaTypeBinding,
                        isLensExpanded: $isMediaLensExpanded,
                        availableTypes: SearchViewModel.lensMediaTypes(from: viewModel.mediaTypes),
                        onLensTap: {
                            isMediaLensExpanded = true
                        },
                        onLensSelect: { selectedType in
                            Task {
                                await searchCurrentQuery(mediaType: selectedType)
                            }
                        },
                        onSearch: { text in
                            search(text)
                        },
                        onClear: {
                            viewModel.clear()
                        }
                    )

                    SearchResultsSection(
                        results: viewModel.results,
                        isLoading: viewModel.isLoading,
                        errorMessage: viewModel.errorMessage,
                        recentSearches: recentSearches,
                        onRecentSearch: { recentSearch in
                            mediaLensStore.setMediaType(recentSearch.mediaType)
                            draftText = recentSearch.text
                            search(recentSearch.text, mediaType: recentSearch.mediaType)
                        },
                        onSelect: onSelect
                    )
                    .blur(radius: isMediaLensExpanded ? 8 : 0)
                    .allowsHitTesting(!isMediaLensExpanded)
                    .overlay {
                        if isMediaLensExpanded {
                            Color.black.opacity(0.001)
                                .onTapGesture {
                                    isMediaLensExpanded = false
                                }
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.black)
                .mediaLensAtmosphere(theme: currentTheme)
                .navigationTitle("Search")
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(.hidden, for: .navigationBar)
                .toolbarColorScheme(.dark, for: .navigationBar)
                .task {
                    await viewModel.loadMeta()
                    validateSelectedMediaType()
                }
            }
            .background(Color.black)
        }
    }

    private var recentSearches: [RecentSearch] {
        RecentSearch.decodeList(from: recentSearchesData, fallbackMediaType: mediaLensStore.selectedMediaType)
    }

    private var currentTheme: MediaTypeTheme {
        mediaLensStore.theme(for: mediaLensStore.selectedMediaType)
    }

    private var selectedMediaTypeBinding: Binding<String> {
        Binding(
            get: { mediaLensStore.selectedMediaType },
            set: { mediaLensStore.setMediaType($0) }
        )
    }

    private func search(_ text: String, mediaType: String? = nil) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let type = mediaType ?? mediaLensStore.selectedMediaType
        saveRecentSearch(trimmed, mediaType: type)
        Task { await viewModel.search(trimmed, mediaType: type) }
    }

    private func searchCurrentQuery(mediaType: String) async {
        let trimmed = viewModel.query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        saveRecentSearch(trimmed, mediaType: mediaType)
        await viewModel.search(mediaType: mediaType)
    }

    private func validateSelectedMediaType() {
        guard !viewModel.mediaTypes.contains(mediaLensStore.selectedMediaType) else { return }
        mediaLensStore.setMediaType(viewModel.mediaTypes.first ?? "movie")
    }

    private func saveRecentSearch(_ text: String, mediaType: String) {
        let recent = RecentSearch(text: text, mediaType: mediaType)
        var searches = recentSearches.filter { !$0.matches(recent) }
        searches.insert(recent, at: 0)
        searches = Array(searches.prefix(8))
        if let data = try? JSONEncoder().encode(searches),
           let string = String(data: data, encoding: .utf8) {
            recentSearchesData = string
        }
    }
}

struct RecentSearch: Codable, Equatable, Identifiable {
    let text: String
    let mediaType: String

    var id: String {
        "\(mediaType):\(text.lowercased())"
    }

    static func decodeList(from string: String, fallbackMediaType: String) -> [RecentSearch] {
        let data = Data(string.utf8)
        if let searches = try? JSONDecoder().decode([RecentSearch].self, from: data) {
            return searches
        }
        let legacy = (try? JSONDecoder().decode([String].self, from: data)) ?? []
        return legacy.map { RecentSearch(text: $0, mediaType: fallbackMediaType) }
    }

    func matches(_ other: RecentSearch) -> Bool {
        mediaType == other.mediaType && text.localizedCaseInsensitiveCompare(other.text) == .orderedSame
    }
}

/// Keeps draft text out of the search model so keystrokes never invalidate results.
private struct SearchInputBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var isFocused: Bool
    @Binding var text: String
    @Binding var selectedMediaType: String
    @Binding var isLensExpanded: Bool
    let availableTypes: [String]
    let onLensTap: () -> Void
    let onLensSelect: (String) -> Void
    let onSearch: (String) -> Void
    let onClear: () -> Void

    var body: some View {
        ZStack {
            if isLensExpanded {
                SearchLensRail(
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
        .padding(.horizontal)
        .padding(.top, 2)
        .padding(.bottom, 10)
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
        "Search \(MediaTypeTheme.theme(for: selectedMediaType).displayName.lowercased())"
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

private struct SearchLensRail: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var selectedType: String
    let availableTypes: [String]
    let onSelect: (String) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 12) {
                    ForEach(availableTypes, id: \.self) { type in
                        SearchLensOrb(
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

private struct SearchLensOrb: View {
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

private struct SearchResultsSection: View {
    let results: [MediaSummary]
    let isLoading: Bool
    let errorMessage: String?
    let recentSearches: [RecentSearch]
    let onRecentSearch: (RecentSearch) -> Void
    let onSelect: (MediaRef) -> Void

    var body: some View {
        ZStack {
            Color.black

            if let error = errorMessage {
                ContentUnavailableView("Search failed", systemImage: "exclamationmark.triangle", description: Text(error))
            } else if results.isEmpty {
                SearchEmptyState(recentSearches: recentSearches, onRecentSearch: onRecentSearch)
            } else {
                SearchResultsList(results: results, onSelect: onSelect)
            }

            if isLoading {
                ProgressView()
                    .padding(24)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }
    }
}

private struct SearchEmptyState: View {
    let recentSearches: [RecentSearch]
    let onRecentSearch: (RecentSearch) -> Void

    var body: some View {
        if recentSearches.isEmpty {
            ContentUnavailableView("Search Spine", systemImage: "magnifyingglass", description: Text("Enter a title to find media."))
        } else {
            List {
                ForEach(recentSearches) { search in
                    Button {
                        onRecentSearch(search)
                    } label: {
                        RecentSearchRow(search: search)
                    }
                    .foregroundStyle(.primary)
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
        }
    }
}

private struct RecentSearchRow: View {
    let search: RecentSearch

    private var theme: MediaTypeTheme {
        MediaTypeTheme.theme(for: search.mediaType)
    }

    var body: some View {
        HStack {
            Text(search.text)

            Spacer()

            Text(theme.displayName)
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(.secondary.opacity(0.18), in: Capsule())
        }
    }
}

private struct SearchResultsList: View {
    let results: [MediaSummary]
    let onSelect: (MediaRef) -> Void

    var body: some View {
        List {
            ForEach(results) { result in
                Button {
                    onSelect(result.ref)
                } label: {
                    SearchResultRow(result: result)
                }
                .buttonStyle(.plain)
                .listRowInsets(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 12))
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Color.black)
        .scrollDismissesKeyboard(.interactively)
    }
}

private struct SearchResultRow: View {
    let result: MediaSummary

    var body: some View {
        HStack(spacing: 14) {
            PosterImage(urlString: result.imageUrl, title: result.title)
                .frame(width: 54, height: 81)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 4) {
                Text(result.title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                if let subtitle = subtitleText {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                if let overview = result.overview?.trimmingCharacters(in: .whitespacesAndNewlines),
                   !overview.isEmpty {
                    Text(overview)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .padding(.top, 2)
                }
            }

            Spacer(minLength: 8)

            Image(systemName: "chevron.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var subtitleText: String? {
        let text = [result.subtitle, result.releaseDate]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        return text.isEmpty ? nil : text
    }
}
