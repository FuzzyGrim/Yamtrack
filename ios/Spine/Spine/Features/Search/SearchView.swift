import SwiftUI

@MainActor
@Observable
final class SearchViewModel {
    var query = ""
    var mediaType = "movie"
    var mediaTypes = APIConstants.fallbackMediaTypes
    var results: [MediaSummary] = []
    var isLoading = false
    var errorMessage: String?

    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func loadMeta() async {
        do {
            let meta = try await mediaRepository.meta()
            mediaTypes = meta.mediaTypes.filter { $0 != "episode" }
            if !mediaTypes.contains(mediaType) {
                mediaType = mediaTypes.first ?? "movie"
            }
        } catch {
            mediaTypes = APIConstants.fallbackMediaTypes
        }
    }

    func search() async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            results = []
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            results = try await mediaRepository.search(query: trimmed, mediaType: mediaType)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct SearchView: View {
    @State private var viewModel: SearchViewModel
    @State private var selectedRef: MediaRef?
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
        _viewModel = State(initialValue: SearchViewModel(mediaRepository: mediaRepository, onUnauthorized: onUnauthorized))
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                mediaTypePicker

                if viewModel.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = viewModel.errorMessage {
                    ContentUnavailableView("Search failed", systemImage: "exclamationmark.triangle", description: Text(error))
                } else if viewModel.results.isEmpty {
                    ContentUnavailableView("Search Spine", systemImage: "magnifyingglass", description: Text("Enter a title to find media."))
                } else {
                    resultGrid
                }
            }
            .navigationTitle("Search")
            .searchable(text: $viewModel.query, prompt: "Movie, show, book, game")
            .onSubmit(of: .search) {
                Task { await viewModel.search() }
            }
            .toolbar {
                Button {
                    Task { await viewModel.search() }
                } label: {
                    Label("Search", systemImage: "magnifyingglass")
                }
                .disabled(viewModel.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .task {
                await viewModel.loadMeta()
            }
            .fullScreenCover(item: $selectedRef) { ref in
                MediaDetailView(
                    ref: ref,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    onUnauthorized: onUnauthorized
                )
            }
        }
    }

    private var mediaTypePicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Picker("Media Type", selection: $viewModel.mediaType) {
                ForEach(viewModel.mediaTypes, id: \.self) { type in
                    Text(type.capitalized).tag(type)
                }
            }
            .pickerStyle(.segmented)
            .padding()
            .frame(minWidth: 360)
        }
        .background(.bar)
    }

    private var resultGrid: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 132), spacing: 16)], spacing: 20) {
                ForEach(viewModel.results) { result in
                    Button {
                        selectedRef = result.ref
                    } label: {
                        VStack(alignment: .leading, spacing: 8) {
                            PosterImage(urlString: result.imageUrl, title: result.title)
                            Text(result.title)
                                .font(.subheadline.weight(.semibold))
                                .lineLimit(2)
                                .foregroundStyle(.primary)
                            if let subtitle = result.subtitle ?? result.releaseDate {
                                Text(subtitle)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding()
        }
    }
}
