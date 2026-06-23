import SwiftUI

struct RecentSearch: Codable, Equatable, Hashable {
    let text: String
    let mediaType: String

    func matches(_ other: RecentSearch) -> Bool {
        text.caseInsensitiveCompare(other.text) == .orderedSame && mediaType == other.mediaType
    }

    static func decodeList(from string: String, fallbackMediaType: String) -> [RecentSearch] {
        guard let data = string.data(using: .utf8) else { return [] }
        if let searches = try? JSONDecoder().decode([RecentSearch].self, from: data) {
            return searches
        }
        if let legacy = try? JSONDecoder().decode([String].self, from: data) {
            return legacy.map { RecentSearch(text: $0, mediaType: fallbackMediaType) }
        }
        return []
    }
}

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
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        selectedTab: AppTab = .search,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        SearchViewContainer(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
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
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
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
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
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
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        MediaDetailView(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }
}

private struct SearchViewContent: View {
    @State private var viewModel: SearchViewModel
    @State private var mediaLensStore = MediaLensStore()
    @State private var isMediaLensExpanded = false
    @State private var draftText = ""
    @AppStorage("recentMedia") private var recentMediaData = "[]"

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
                        query: viewModel.query,
                        selectedMediaType: mediaLensStore.selectedMediaType,
                        results: viewModel.results,
                        isLoading: viewModel.isLoading,
                        errorMessage: viewModel.errorMessage,
                        recentMedia: recentMedia,
                        onRecentMedia: { media in
                            saveRecentMedia(media)
                            onSelect(media.ref)
                        },
                        onSelect: { media in
                            saveRecentMedia(media)
                            onSelect(media.ref)
                        }
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
                .task(id: draftText) {
                    try? await Task.sleep(for: .milliseconds(300))
                    guard !Task.isCancelled else { return }
                    await viewModel.search(draftText, mediaType: mediaLensStore.selectedMediaType)
                }
            }
            .background(Color.black)
        }
    }

    private var recentMedia: [MediaSummary] {
        RecentMedia.decodeList(from: recentMediaData)
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
        let type = mediaType ?? mediaLensStore.selectedMediaType
        Task { await viewModel.search(trimmed, mediaType: type) }
    }

    private func searchCurrentQuery(mediaType: String) async {
        let trimmed = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        await viewModel.search(trimmed, mediaType: mediaType)
    }

    private func validateSelectedMediaType() {
        guard !viewModel.mediaTypes.contains(mediaLensStore.selectedMediaType) else { return }
        mediaLensStore.setMediaType(viewModel.mediaTypes.first ?? "movie")
    }

    private func saveRecentMedia(_ media: MediaSummary) {
        var mediaItems = recentMedia.filter { $0.ref != media.ref }
        mediaItems.insert(media, at: 0)
        mediaItems = Array(mediaItems.prefix(8))
        if let data = try? JSONEncoder().encode(mediaItems),
           let string = String(data: data, encoding: .utf8) {
            recentMediaData = string
        }
    }
}

private enum RecentMedia {
    static func decodeList(from string: String) -> [MediaSummary] {
        (try? JSONDecoder().decode([MediaSummary].self, from: Data(string.utf8))) ?? []
    }
}

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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let query: String
    let selectedMediaType: String
    let results: [MediaSummary]
    let isLoading: Bool
    let errorMessage: String?
    let recentMedia: [MediaSummary]
    let onRecentMedia: (MediaSummary) -> Void
    let onSelect: (MediaSummary) -> Void

    var body: some View {
        ZStack {
            Color.black

            content
                .transition(.opacity.combined(with: .scale(scale: 0.98)))
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: contentState)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: resultIDs)
    }

    @ViewBuilder
    private var content: some View {
        if let error = errorMessage {
            ContentUnavailableView("Search failed", systemImage: "exclamationmark.triangle", description: Text(error))
        } else if results.isEmpty {
            SearchEmptyState(
                query: query,
                selectedMediaType: selectedMediaType,
                isLoading: isLoading,
                recentMedia: recentMedia,
                onRecentMedia: onRecentMedia
            )
        } else {
            SearchResultsList(results: results, onSelect: onSelect)
        }
    }

    private var contentState: String {
        if errorMessage != nil { return "error" }
        if !results.isEmpty { return "results" }
        if isLoading { return "loading-empty" }
        return query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "empty" : "no-results"
    }

    private var resultIDs: [String] {
        results.map(\.id)
    }
}

private struct SearchEmptyState: View {
    let query: String
    let selectedMediaType: String
    let isLoading: Bool
    let recentMedia: [MediaSummary]
    let onRecentMedia: (MediaSummary) -> Void

    var body: some View {
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isLoading {
            SearchNoResultsState(query: query, selectedMediaType: selectedMediaType)
        } else if recentMedia.isEmpty {
            ContentUnavailableView("Search Spine", systemImage: "magnifyingglass", description: Text("Enter a title to find media."))
        } else {
            List {
                Section("Recently Opened") {
                    ForEach(recentMedia) { media in
                        Button {
                            onRecentMedia(media)
                        } label: {
                            SearchResultRow(result: media, usesDiarySize: true)
                        }
                        .buttonStyle(.plain)
                        .listRowInsets(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 12))
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
        }
    }
}

private struct SearchNoResultsState: View {
    let query: String
    let selectedMediaType: String

    var body: some View {
        ContentUnavailableView(
            "No \(MediaTypeTheme.theme(for: selectedMediaType).displayName.lowercased()) found",
            systemImage: "magnifyingglass",
            description: Text("No matches for \"\(query.trimmingCharacters(in: .whitespacesAndNewlines))\". Try another title or media type.")
        )
    }
}

private struct SearchResultsList: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let results: [MediaSummary]
    let onSelect: (MediaSummary) -> Void

    var body: some View {
        List {
            ForEach(results) { result in
                Button {
                    onSelect(result)
                } label: {
                    SearchResultRow(result: result)
                }
                .buttonStyle(.plain)
                .listRowInsets(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 12))
                .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Color.black)
        .scrollDismissesKeyboard(.interactively)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: results.map(\.id))
    }
}

private struct SearchResultRow: View {
    let result: MediaSummary
    var usesDiarySize = false

    var body: some View {
        HStack(spacing: 14) {
            MediaArtwork(
                url: result.displayPosterURL,
                title: result.title,
                slot: usesDiarySize ? .diaryRow : .searchRow,
                mediaType: result.ref.mediaType,
                orientation: result.posterOrientation
            )
            .scaleEffect(usesDiarySize ? 0.75 : 1)
            .frame(width: usesDiarySize ? 42 : nil, height: usesDiarySize ? 63 : nil)

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
        let text = [result.subtitle, formattedReleaseDate]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        return text.isEmpty ? nil : text
    }

    private var formattedReleaseDate: String? {
        guard let releaseDate = result.releaseDate else { return nil }
        return SearchDateFormatter.string(from: releaseDate) ?? releaseDate
    }
}

private enum SearchDateFormatter {
    private static let input: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let output: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMMM d, yyyy"
        return formatter
    }()

    static func string(from raw: String) -> String? {
        let trimmed = String(raw.trimmingCharacters(in: .whitespacesAndNewlines).prefix(10))
        guard let date = input.date(from: trimmed) else { return nil }
        return output.string(from: date)
    }
}
