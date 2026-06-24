import SwiftUI

@MainActor
@Observable
final class DiaryViewModel {
    var entries: [DiaryEntry] = []
    var isLoading = false
    var errorMessage: String?

    private let diaryRepository: DiaryRepository
    private let filter: DiaryFilter?
    private let onUnauthorized: () -> Void

    init(diaryRepository: DiaryRepository, filter: DiaryFilter? = nil, onUnauthorized: @escaping () -> Void) {
        self.diaryRepository = diaryRepository
        self.filter = filter
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            if let filter {
                entries = try await diaryRepository.list(filter: filter)
            } else {
                entries = try await diaryRepository.list()
            }
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct MediaDiaryView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryViewModel

    private let title: String
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        title: String,
        itemId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        selectedTab: AppTab = .diary,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.title = title
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: DiaryViewModel(
            diaryRepository: diaryRepository,
            filter: DiaryFilter(itemId: itemId),
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SpinePageBackground()

                ScrollView(showsIndicators: false) {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        header

                        if viewModel.isLoading {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage {
                            DiaryStateCard(
                                title: "Could not load logs",
                                systemImage: "exclamationmark.triangle",
                                message: error
                            )
                        } else if viewModel.entries.isEmpty {
                            DiaryStateCard(
                                title: "No logs",
                                systemImage: "calendar",
                                message: "Logs for this media will appear here."
                            )
                        } else {
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
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 18)
                    .padding(.bottom, 28)
                }
                .refreshable {
                    await viewModel.load()
                }
            }
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbar(.hidden, for: .tabBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white)
                }
            }
            .task {
                await viewModel.load()
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Task { await viewModel.load() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .storygraphImportDidSucceed)) { _ in
                Task { await viewModel.load() }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.system(size: 32, weight: .black))
                .foregroundStyle(.white)

            Text("Your logs for this media.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
        }
        .padding(.bottom, 14)
    }
}

struct DiaryView: View {
    @State private var viewModel: DiaryViewModel

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        selectedTab: AppTab = .diary,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: DiaryViewModel(diaryRepository: diaryRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SpinePageBackground()

                ScrollView(showsIndicators: false) {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        header

                        if viewModel.isLoading {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage {
                            DiaryStateCard(
                                title: "Could not load diary",
                                systemImage: "exclamationmark.triangle",
                                message: error
                            )
                        } else if viewModel.entries.isEmpty {
                            DiaryStateCard(
                                title: "No diary entries",
                                systemImage: "calendar",
                                message: "Logs you create from media pages will appear here."
                            )
                        } else {
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
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 18)
                    .padding(.bottom, 28)
                }
                .refreshable {
                    await viewModel.load()
                }
            }
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                await viewModel.load()
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Task { await viewModel.load() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .storygraphImportDidSucceed)) { _ in
                Task { await viewModel.load() }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Diary")
                .font(.system(size: 32, weight: .black))
                .foregroundStyle(.white)

            Text("Your logged watches, reads, plays, and reviews.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
        }
        .padding(.bottom, 14)
    }
}
