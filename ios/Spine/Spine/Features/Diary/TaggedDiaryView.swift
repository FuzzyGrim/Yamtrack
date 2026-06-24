import SwiftUI

enum TaggedDiaryTab: String, CaseIterable, Identifiable {
    case diary = "Diary"
    case grid = "Grid"

    var id: String { rawValue }
}

struct TaggedDiaryMedia: Identifiable {
    let entry: DiaryEntry

    var id: String { entry.media.ref.id }
    var media: DiaryMedia { entry.media }
}

@MainActor
@Observable
final class TaggedDiaryViewModel {
    var entries: [DiaryEntry] = []
    var isLoading = false
    var errorMessage: String?
    var selectedTab: TaggedDiaryTab = .diary

    private let tag: String
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(tag: String, diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.tag = tag.trimmingCharacters(in: .whitespacesAndNewlines)
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    var media: [TaggedDiaryMedia] {
        Self.uniqueMedia(from: entries)
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            entries = try await diaryRepository.list(tag: tag)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    static func uniqueMedia(from entries: [DiaryEntry]) -> [TaggedDiaryMedia] {
        var seen = Set<String>()
        return entries.compactMap { entry in
            guard seen.insert(entry.media.ref.id).inserted else { return nil }
            return TaggedDiaryMedia(entry: entry)
        }
    }
}

struct TaggedDiaryView: View {
    @State private var viewModel: TaggedDiaryViewModel

    private let tag: String
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        tag: String,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        selectedTab: AppTab = .diary,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        let trimmedTag = tag.trimmingCharacters(in: .whitespacesAndNewlines)
        self.tag = trimmedTag
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: TaggedDiaryViewModel(
            tag: trimmedTag,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 16) {
                    Picker("View", selection: $viewModel.selectedTab) {
                        ForEach(TaggedDiaryTab.allCases) { tab in
                            Text(tab.rawValue).tag(tab)
                        }
                    }
                    .pickerStyle(.segmented)

                    content
                }
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle(tag)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.entries.isEmpty {
                await viewModel.load()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
            Task { await viewModel.load() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .storygraphImportDidSucceed)) { _ in
            Task { await viewModel.load() }
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 320)
        } else if let error = viewModel.errorMessage {
            DiaryStateCard(
                title: "Could not load tag",
                systemImage: "exclamationmark.triangle",
                message: error
            )
        } else if viewModel.entries.isEmpty {
            DiaryStateCard(
                title: "No logs for \(tag)",
                systemImage: "tag",
                message: "Diary logs with this tag will appear here."
            )
        } else {
            switch viewModel.selectedTab {
            case .diary:
                DiaryEntryList(entries: viewModel.entries) { entry in
                    DiaryLogDetailView(
                        entryId: entry.id,
                        diaryRepository: diaryRepository,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                }
            case .grid:
                mediaGrid
            }
        }
    }

    private var mediaGrid: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
            ForEach(viewModel.media) { item in
                NavigationLink {
                    MediaDetailView(
                        ref: item.media.ref,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    MediaArtwork(
                        url: item.media.displayPosterURL,
                        title: item.media.title,
                        slot: .tagGrid,
                        mediaType: item.media.ref.mediaType,
                        orientation: item.media.posterOrientation
                    )
                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(item.media.title)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
