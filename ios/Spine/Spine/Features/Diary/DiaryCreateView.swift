import SwiftUI

@MainActor
@Observable
final class DiaryCreateViewModel {
    var mediaType = "movie"
    var mediaTypes = APIConstants.fallbackMediaTypes
    var query = ""
    var results: [MediaSummary] = []
    var selectedMedia: MediaSummary?
    var consumedAt = Date()
    var ratingText = ""
    var reviewTitle = ""
    var review = ""
    var tagsText = ""
    var visibility = "public"
    var containsSpoilers = false
    var isRewatch = false
    var isSearching = false
    var isSaving = false
    var errorMessage: String?

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(diaryRepository: DiaryRepository, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
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

    func search() async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        isSearching = true
        errorMessage = nil
        defer { isSearching = false }

        do {
            results = try await mediaRepository.search(query: trimmed, mediaType: mediaType)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func create() async -> Bool {
        guard let selectedMedia else {
            errorMessage = "Choose media for the diary entry."
            return false
        }

        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let tags = tagsText
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            let request = DiaryEntryWriteRequest(
                ref: selectedMedia.ref,
                consumedAt: consumedAt,
                rating: Decimal(string: ratingText),
                review: review,
                reviewTitle: reviewTitle,
                liked: false,
                isRewatch: isRewatch,
                autoMarkConsumed: true,
                containsSpoilers: containsSpoilers,
                visibility: visibility,
                tags: tags
            )
            _ = try await diaryRepository.create(request)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }
}

struct DiaryCreateView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryCreateViewModel
    let onCreated: () -> Void

    init(diaryRepository: DiaryRepository, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void = {}, onCreated: @escaping () -> Void) {
        _viewModel = State(initialValue: DiaryCreateViewModel(
            diaryRepository: diaryRepository,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
        self.onCreated = onCreated
    }

    var body: some View {
        NavigationStack {
            Form {
                mediaSearchSection
                entrySection
                if let error = viewModel.errorMessage {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("New Diary Entry")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            if await viewModel.create() {
                                onCreated()
                            }
                        }
                    } label: {
                        if viewModel.isSaving {
                            ProgressView()
                        } else {
                            Text("Save")
                        }
                    }
                    .disabled(viewModel.selectedMedia == nil || viewModel.isSaving)
                }
            }
            .task {
                await viewModel.loadMeta()
            }
        }
    }

    private var mediaSearchSection: some View {
        Section("Media") {
            Picker("Type", selection: $viewModel.mediaType) {
                ForEach(viewModel.mediaTypes, id: \.self) { type in
                    Text(type.capitalized).tag(type)
                }
            }

            HStack {
                TextField("Search title", text: $viewModel.query)
                    .textInputAutocapitalization(.never)
                Button {
                    Task { await viewModel.search() }
                } label: {
                    Label("Search", systemImage: "magnifyingglass")
                }
                .labelStyle(.iconOnly)
            }

            if viewModel.isSearching {
                ProgressView()
            }

            if let selected = viewModel.selectedMedia {
                Label(selected.title, systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            }

            ForEach(viewModel.results.prefix(6)) { result in
                Button {
                    viewModel.selectedMedia = result
                } label: {
                    HStack {
                        MediaArtwork(
                            url: result.displayPosterURL,
                            title: result.title,
                            slot: .searchRow,
                            mediaType: result.ref.mediaType,
                            orientation: result.posterOrientation
                        )
                        VStack(alignment: .leading) {
                            Text(result.title)
                            if let subtitle = result.subtitle ?? result.releaseDate {
                                Text(subtitle)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var entrySection: some View {
        Section("Entry") {
            DatePicker("Consumed", selection: $viewModel.consumedAt, displayedComponents: [.date, .hourAndMinute])
            TextField("Rating 0-10", text: $viewModel.ratingText)
                .keyboardType(.decimalPad)
            TextField("Review title", text: $viewModel.reviewTitle)
            TextField("Review", text: $viewModel.review, axis: .vertical)
                .lineLimit(4...8)
            TextField("Tags, comma separated", text: $viewModel.tagsText)
            Picker("Visibility", selection: $viewModel.visibility) {
                ForEach(APIConstants.visibilityChoices, id: \.self) { value in
                    Text(value.capitalized).tag(value)
                }
            }
            Toggle("Contains spoilers", isOn: $viewModel.containsSpoilers)
            Toggle("Rewatch", isOn: $viewModel.isRewatch)
        }
    }
}
