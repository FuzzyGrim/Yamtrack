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
    private let listRepository: ListRepository
    private let mediaLensStore: MediaLensStore
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    @MainActor
    init(
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository? = nil,
        mediaLensStore: MediaLensStore? = nil,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .search,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository ?? AppRepositories.current().lists
        self.mediaLensStore = mediaLensStore ?? MediaLensStore()
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        SearchViewContainer(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            listRepository: listRepository,
            mediaLensStore: mediaLensStore,
            currentUserId: currentUserId,
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
    let listRepository: ListRepository
    let mediaLensStore: MediaLensStore
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        SearchViewContent(
            mediaRepository: mediaRepository,
            mediaLensStore: mediaLensStore,
            onUnauthorized: onUnauthorized,
            onSelect: { selectedRef = $0 }
        )
        .fullScreenCover(item: $selectedRef, onDismiss: { selectedRef = nil }) { ref in
            MediaDetailCover(
                ref: ref,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                currentUserId: currentUserId,
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
    let listRepository: ListRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        MediaDetailView(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            listRepository: listRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }
}

private struct SearchViewContent: View {
    @State private var viewModel: SearchViewModel
    @State private var isMediaLensExpanded = false
    @State private var draftText = ""
    @AppStorage("recentMedia") private var recentMediaData = "[]"

    let mediaLensStore: MediaLensStore
    let onSelect: (MediaRef) -> Void

    init(mediaRepository: MediaRepository, mediaLensStore: MediaLensStore, onUnauthorized: @escaping () -> Void, onSelect: @escaping (MediaRef) -> Void) {
        _viewModel = State(initialValue: SearchViewModel(mediaRepository: mediaRepository, onUnauthorized: onUnauthorized))
        self.mediaLensStore = mediaLensStore
        self.onSelect = onSelect
    }

    var body: some View {
        ZStack {
            NavigationStack {
                VStack(spacing: 0) {
                    MediaSearchBar(
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
