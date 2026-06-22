import SwiftUI

@MainActor
@Observable
final class LibraryViewModel {
    var mediaType = "movie"
    var mediaTypes = APIConstants.fallbackMediaTypes
    var items: [LibraryItem] = []
    var isLoading = false
    var errorMessage: String?

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let onUnauthorized: () -> Void

    init(mediaRepository: MediaRepository, trackingRepository: TrackingRepository, onUnauthorized: @escaping () -> Void) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
    }

    func loadMeta() async {
        do {
            let meta = try await mediaRepository.meta()
            mediaTypes = meta.mediaTypes.filter { $0 != "episode" }
        } catch {
            mediaTypes = APIConstants.fallbackMediaTypes
        }
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            items = try await trackingRepository.list(mediaType: mediaType)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct LibraryView: View {
    @State private var viewModel: LibraryViewModel

    init(mediaRepository: MediaRepository, trackingRepository: TrackingRepository, onUnauthorized: @escaping () -> Void = {}) {
        _viewModel = State(initialValue: LibraryViewModel(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("Media Type", selection: $viewModel.mediaType) {
                        ForEach(viewModel.mediaTypes, id: \.self) { type in
                            Text(type.capitalized).tag(type)
                        }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: viewModel.mediaType) { _, _ in
                        Task { await viewModel.load() }
                    }
                }

                if viewModel.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                } else if let error = viewModel.errorMessage {
                    Text(error)
                        .foregroundStyle(.red)
                } else if viewModel.items.isEmpty {
                    ContentUnavailableView("No tracked items", systemImage: "books.vertical")
                } else {
                    ForEach(viewModel.items) { item in
                        HStack(spacing: 12) {
                            MediaArtwork(
                                url: item.media.displayPosterURL,
                                title: item.media.title,
                                slot: .libraryRow,
                                mediaType: item.media.ref.mediaType,
                                orientation: item.media.posterOrientation
                            )
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.media.title)
                                    .font(.headline)
                                Text(item.tracking.status ?? "Tracked")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                HStack {
                                    if let rating = item.tracking.rating {
                                        Label(rating, systemImage: "star.fill")
                                    }
                                    if let progress = item.tracking.progress {
                                        Text(progressText(progress))
                                    }
                                }
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Library")
            .toolbar {
                Button {
                    Task { await viewModel.load() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            .task {
                await viewModel.loadMeta()
                await viewModel.load()
            }
        }
    }

    private func progressText(_ progress: ProgressState) -> String {
        let value = progress.value.map { NSDecimalNumber(decimal: $0).stringValue } ?? "0"
        let max = progress.max.map { "/\(NSDecimalNumber(decimal: $0).stringValue)" } ?? ""
        return "\(value)\(max) \(progress.unit)"
    }
}
