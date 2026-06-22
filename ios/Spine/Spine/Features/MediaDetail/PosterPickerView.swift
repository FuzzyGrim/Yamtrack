import SwiftUI

@MainActor
@Observable
final class PosterPickerViewModel {
    var posters: [PosterOption] = []
    var selectedLanguage = "en"
    var selectedPosterURL: String?
    var isLoading = false
    var isSaving = false
    var errorMessage: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private let onSaved: (PosterSaveResponse) -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (PosterSaveResponse) -> Void
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
        self.onSaved = onSaved
    }

    var languageOptions: [PosterLanguageOption] {
        var options = [PosterLanguageOption(id: "all", title: "All Languages")]
        let languages = Set(posters.compactMap(\.language))
        if languages.contains("en") {
            options.append(PosterLanguageOption(id: "en", title: "English"))
        }
        options += languages
            .filter { $0 != "en" }
            .sorted()
            .map { PosterLanguageOption(id: $0, title: Self.languageName(for: $0)) }
        if posters.contains(where: { $0.language == nil }) {
            options.append(PosterLanguageOption(id: "none", title: "No Language"))
        }
        return options
    }

    var filteredPosters: [PosterOption] {
        let filtered: [PosterOption]
        switch selectedLanguage {
        case "all":
            filtered = posters
        case "none":
            filtered = posters.filter { $0.language == nil }
        default:
            filtered = posters.filter { $0.language == selectedLanguage }
        }
        guard let selectedPosterURL,
              let selected = posters.first(where: { $0.url == selectedPosterURL }),
              !filtered.contains(selected) else {
            return filtered
        }
        return [selected] + filtered
    }

    var canSave: Bool {
        selectedPosterURL != nil && !isSaving
    }

    func load() async {
        guard posters.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            posters = try await mediaRepository.posters(ref: ref)
            selectedPosterURL = posters.first(where: \.isSelected)?.url ?? posters.first?.url
            if !posters.contains(where: { $0.language == selectedLanguage }) {
                selectedLanguage = languageOptions.contains(where: { $0.id == "en" }) ? "en" : "all"
            }
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save() async {
        guard let selectedPosterURL, !isSaving else { return }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let response = try await mediaRepository.savePoster(ref: ref, posterURL: selectedPosterURL)
            onSaved(response)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    static func languageName(for code: String) -> String {
        Locale.current.localizedString(forLanguageCode: code) ?? code.uppercased()
    }
}

struct PosterLanguageOption: Identifiable, Hashable {
    let id: String
    let title: String
}

struct PosterPickerView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: PosterPickerViewModel
    let title: String
    let showsLanguageFilter: Bool
    let contentMode: PosterContentMode

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        title: String = "Customize Poster",
        showsLanguageFilter: Bool = true,
        contentMode: PosterContentMode = .fill,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (PosterSaveResponse) -> Void
    ) {
        self.title = title
        self.showsLanguageFilter = showsLanguageFilter
        self.contentMode = contentMode
        _viewModel = State(initialValue: PosterPickerViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized,
            onSaved: onSaved
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SpinePageBackground()

                Group {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                    } else if let error = viewModel.errorMessage, viewModel.posters.isEmpty {
                        ContentUnavailableView("Could not load posters", systemImage: "exclamationmark.triangle", description: Text(error))
                            .foregroundStyle(.white)
                            .padding()
                    } else if viewModel.posters.isEmpty {
                        ContentUnavailableView("No posters found", systemImage: "photo.on.rectangle")
                            .foregroundStyle(.white)
                            .padding()
                    } else {
                        posterGrid
                    }
                }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            await viewModel.save()
                            if viewModel.errorMessage == nil {
                                dismiss()
                            }
                        }
                    } label: {
                        if viewModel.isSaving {
                            ProgressView()
                        } else {
                            Text("Save")
                        }
                    }
                    .disabled(!viewModel.canSave)
                }
            }
            .task {
                await viewModel.load()
            }
        }
    }

    private var posterGrid: some View {
        VStack(spacing: 14) {
            if showsLanguageFilter {
                Picker("Language", selection: $viewModel.selectedLanguage) {
                    ForEach(viewModel.languageOptions) { option in
                        Text(option.title).tag(option.id)
                    }
                }
                .pickerStyle(.menu)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red.opacity(0.9))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
            }

            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 12)], spacing: 12) {
                    ForEach(viewModel.filteredPosters) { poster in
                        PosterOptionCell(
                            poster: poster,
                            isSelected: viewModel.selectedPosterURL == poster.url,
                            contentMode: contentMode
                        ) {
                            viewModel.selectedPosterURL = poster.url
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
        }
        .padding(.top, 12)
    }
}

private struct PosterOptionCell: View {
    let poster: PosterOption
    let isSelected: Bool
    let contentMode: PosterContentMode
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack(alignment: .topLeading) {
                MediaArtwork(
                    url: poster.thumbnailUrl ?? poster.url,
                    title: "Poster option",
                    slot: .pickerGrid,
                    contentMode: contentMode
                )
                    .overlay {
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(isSelected ? .white : .clear, lineWidth: 3)
                    }

                if poster.isSelected {
                    Text("Current")
                        .font(.caption2.weight(.heavy))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 4)
                        .background(.green, in: Capsule())
                        .padding(6)
                }

                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.title2)
                        .foregroundStyle(.white)
                        .shadow(radius: 4)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                        .padding(7)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(poster.isSelected ? "Current poster" : "Poster option")
    }
}

#Preview("Book Cover Picker") {
    PosterPickerView(
        ref: MediaRef(
            itemId: nil,
            source: "openlibrary",
            mediaType: "book",
            mediaId: "OL7353617M",
            seasonNumber: nil,
            episodeNumber: nil
        ),
        mediaRepository: PreviewPosterRepository(),
        title: "Customize Cover",
        showsLanguageFilter: false,
        contentMode: .fit,
        onUnauthorized: {}
    ) { _ in }
}

private struct PreviewPosterRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }

    func posters(ref: MediaRef) async throws -> [PosterOption] {
        [
            PosterOption(
                url: "https://covers.openlibrary.org/b/id/6979861-L.jpg",
                thumbnailUrl: "https://covers.openlibrary.org/b/id/6979861-M.jpg",
                width: 0,
                height: 0,
                aspectRatio: 0.667,
                voteAverage: 0,
                voteCount: 0,
                language: nil,
                isOriginal: true,
                isSelected: true
            ),
            PosterOption(
                url: "https://covers.openlibrary.org/b/id/10521270-L.jpg",
                thumbnailUrl: "https://covers.openlibrary.org/b/id/10521270-M.jpg",
                width: 0,
                height: 0,
                aspectRatio: 0.667,
                voteAverage: 0,
                voteCount: 0,
                language: nil,
                isOriginal: false,
                isSelected: false
            ),
        ]
    }

    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse {
        PosterSaveResponse(posterUrl: posterURL, customPosterUrl: posterURL, posterAccentColor: nil)
    }

    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
}
