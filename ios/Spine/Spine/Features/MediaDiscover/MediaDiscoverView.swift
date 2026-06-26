import SwiftUI

@MainActor
@Observable
final class MediaDiscoverViewModel {
    var results: [MediaSummary] = []
    var isLoading = false
    var isLoadingNextPage = false
    var errorMessage: String?
    var totalCount = 0

    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private var request: MediaDiscoverRequest
    private var nextPage: String?

    init(request: MediaDiscoverRequest, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.request = request
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    var hasMorePages: Bool {
        nextPage != nil
    }

    func loadNextPageIfNeeded(currentItem: MediaSummary) async {
        guard shouldLoadNextPage(currentItem: currentItem) else { return }
        await loadNextPage()
    }

    func load() async {
        guard !isLoading else { return }
        request.page = nil
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let response = try await mediaRepository.discover(request)
            results = response.results
            totalCount = response.count
            nextPage = Self.page(from: response.next)
        } catch {
            results = []
            totalCount = 0
            nextPage = nil
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func loadNextPage() async {
        guard let nextPage, !isLoadingNextPage else { return }
        request.page = nextPage
        isLoadingNextPage = true
        defer { isLoadingNextPage = false }

        do {
            let response = try await mediaRepository.discover(request)
            let existing = Set(results.map(\.id))
            results += response.results.filter { !existing.contains($0.id) }
            totalCount = response.count
            self.nextPage = Self.page(from: response.next)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func shouldLoadNextPage(currentItem: MediaSummary) -> Bool {
        guard nextPage != nil,
              !isLoading,
              !isLoadingNextPage,
              let index = results.firstIndex(where: { $0.id == currentItem.id }) else { return false }
        return index >= results.count - 12
    }

    static func page(from urlString: String?) -> String? {
        guard let urlString,
              let components = URLComponents(string: urlString) else { return nil }
        return components.queryItems?.first(where: { $0.name == "page" })?.value
    }
}

struct MediaDiscoverView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaDiscoverViewModel
    @State private var selectedRef: MediaRef?
    @State private var edgeDragOffset: CGFloat = 0

    private let request: MediaDiscoverRequest
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        request: MediaDiscoverRequest,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.request = request
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: MediaDiscoverViewModel(
            request: request,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack(alignment: .topLeading) {
                Color.black.ignoresSafeArea()

                if viewModel.isLoading {
                    ProgressView()
                        .tint(.white)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = viewModel.errorMessage, viewModel.results.isEmpty {
                    ContentUnavailableView("Could not load media", systemImage: "exclamationmark.triangle", description: Text(error))
                } else if viewModel.results.isEmpty {
                    ContentUnavailableView("No results", systemImage: "square.grid.2x2", description: Text("Try another media detail pill."))
                } else {
                    ScrollView {
                        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
                            ForEach(viewModel.results) { media in
                                Button {
                                    selectedRef = media.ref
                                } label: {
                                    MediaArtwork(
                                        url: media.displayPosterURL,
                                        title: media.title,
                                        slot: .tagGrid,
                                        mediaType: media.ref.mediaType,
                                        orientation: media.posterOrientation
                                    )
                                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("View \(media.title)")
                                .task {
                                    await viewModel.loadNextPageIfNeeded(currentItem: media)
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.top, 14)
                    }
                }
            }
            .navigationTitle(request.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    MediaDiscoverBackButton {
                        dismiss()
                    }
                }
            }
            .task {
                if viewModel.results.isEmpty {
                    await viewModel.load()
                }
            }
        }
        .background(Color.black)
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .fullScreenCover(item: $selectedRef, onDismiss: { selectedRef = nil }) { ref in
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

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }
}

private struct MediaDiscoverBackButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Back")
    }
}
