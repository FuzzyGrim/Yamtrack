import SwiftUI

@MainActor
@Observable
final class DiaryViewModel {
    var entries: [DiaryEntry] = []
    var isLoading = false
    var errorMessage: String?
    var isShowingCreate = false

    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            entries = try await diaryRepository.list()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct DiaryView: View {
    @State private var viewModel: DiaryViewModel
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(diaryRepository: DiaryRepository, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void = {}) {
        _viewModel = State(initialValue: DiaryViewModel(diaryRepository: diaryRepository, onUnauthorized: onUnauthorized))
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        NavigationStack {
            List {
                if viewModel.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                } else if let error = viewModel.errorMessage {
                    Text(error)
                        .foregroundStyle(.red)
                } else if viewModel.entries.isEmpty {
                    ContentUnavailableView("No diary entries", systemImage: "calendar")
                } else {
                    ForEach(viewModel.entries) { entry in
                        DiaryEntryRow(entry: entry)
                    }
                }
            }
            .navigationTitle("Diary")
            .toolbar {
                Button {
                    viewModel.isShowingCreate = true
                } label: {
                    Label("New Entry", systemImage: "plus")
                }
            }
            .sheet(isPresented: $viewModel.isShowingCreate) {
                DiaryCreateView(
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized,
                    onCreated: {
                        viewModel.isShowingCreate = false
                        Task { await viewModel.load() }
                    }
                )
            }
            .task {
                await viewModel.load()
            }
        }
    }
}

private struct DiaryEntryRow: View {
    let entry: DiaryEntry

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            PosterImage(urlString: entry.media.imageUrl, title: entry.media.title)
                .frame(width: 56)
            VStack(alignment: .leading, spacing: 5) {
                Text(entry.media.title)
                    .font(.headline)
                if let title = entry.reviewTitle, !title.isEmpty {
                    Text(title)
                        .font(.subheadline.weight(.medium))
                }
                if let review = entry.review, !review.isEmpty {
                    Text(review)
                        .font(.subheadline)
                        .lineLimit(3)
                }
                HStack {
                    if let rating = entry.rating {
                        Label(rating, systemImage: "star.fill")
                    }
                    Text(entry.visibility.capitalized)
                    if entry.containsSpoilers {
                        Label("Spoilers", systemImage: "eye.slash")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }
}
