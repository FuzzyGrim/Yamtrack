import SwiftUI

@MainActor
@Observable
final class BackdropPickerViewModel {
    var backdrops: [PosterOption] = []
    var selectedLanguage = "all"
    var selectedBackdropURL: String?
    var isLoading = false
    var isSaving = false
    var errorMessage: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private let onSaved: (BackdropSaveResponse) -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (BackdropSaveResponse) -> Void
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
        self.onSaved = onSaved
    }

    var languageOptions: [PosterLanguageOption] {
        var options = [PosterLanguageOption(id: "all", title: "All Languages")]
        let languages = Set(backdrops.compactMap(\.language))
        if languages.contains("en") {
            options.append(PosterLanguageOption(id: "en", title: "English"))
        }
        options += languages
            .filter { $0 != "en" }
            .sorted()
            .map { PosterLanguageOption(id: $0, title: PosterPickerViewModel.languageName(for: $0)) }
        if backdrops.contains(where: { $0.language == nil }) {
            options.append(PosterLanguageOption(id: "none", title: "No Language"))
        }
        return options
    }

    var filteredBackdrops: [PosterOption] {
        let filtered: [PosterOption]
        switch selectedLanguage {
        case "all":
            filtered = backdrops
        case "none":
            filtered = backdrops.filter { $0.language == nil }
        default:
            filtered = backdrops.filter { $0.language == selectedLanguage }
        }
        guard let selectedBackdropURL,
              let selected = backdrops.first(where: { $0.url == selectedBackdropURL }),
              !filtered.contains(selected) else {
            return filtered
        }
        return [selected] + filtered
    }

    var canSave: Bool {
        selectedBackdropURL != nil && !isSaving
    }

    func load() async {
        guard backdrops.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            backdrops = try await mediaRepository.backdrops(ref: ref).pinningCurrentFirst()
            selectedBackdropURL = backdrops.first(where: \.isSelected)?.url ?? backdrops.first?.url
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save() async {
        guard let selectedBackdropURL, !isSaving else { return }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let response = try await mediaRepository.saveBackdrop(ref: ref, backdropURL: selectedBackdropURL)
            onSaved(response)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private extension Array where Element == PosterOption {
    func pinningCurrentFirst() -> [PosterOption] {
        guard let selectedIndex = firstIndex(where: \.isSelected), selectedIndex != startIndex else { return self }
        var options = self
        let selected = options.remove(at: selectedIndex)
        options.insert(selected, at: startIndex)
        return options
    }
}

struct BackdropPickerView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: BackdropPickerViewModel

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (BackdropSaveResponse) -> Void
    ) {
        _viewModel = State(initialValue: BackdropPickerViewModel(
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
                    } else if let error = viewModel.errorMessage, viewModel.backdrops.isEmpty {
                        ContentUnavailableView("Could not load backdrops", systemImage: "exclamationmark.triangle", description: Text(error))
                            .foregroundStyle(.white)
                            .padding()
                    } else if viewModel.backdrops.isEmpty {
                        ContentUnavailableView("No backdrops found", systemImage: "photo.on.rectangle")
                            .foregroundStyle(.white)
                            .padding()
                    } else {
                        backdropGrid
                    }
                }
            }
            .navigationTitle("Customize Backdrop")
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

    private var backdropGrid: some View {
        VStack(spacing: 14) {
            Picker("Language", selection: $viewModel.selectedLanguage) {
                ForEach(viewModel.languageOptions) { option in
                    Text(option.title).tag(option.id)
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red.opacity(0.9))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
            }

            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 12)], spacing: 12) {
                    ForEach(viewModel.filteredBackdrops) { backdrop in
                        BackdropOptionCell(
                            backdrop: backdrop,
                            isSelected: viewModel.selectedBackdropURL == backdrop.url
                        ) {
                            viewModel.selectedBackdropURL = backdrop.url
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

private struct BackdropOptionCell: View {
    let backdrop: PosterOption
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack(alignment: .topLeading) {
                AsyncImage(url: URL(string: backdrop.thumbnailUrl ?? backdrop.url)) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                    default:
                        Color.gray.opacity(0.18)
                    }
                }
                .aspectRatio(16.0 / 9.0, contentMode: .fill)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay {
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(isSelected ? .white : .clear, lineWidth: 3)
                }

                if backdrop.isSelected {
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
        .accessibilityLabel(backdrop.isSelected ? "Current backdrop" : "Backdrop option")
    }
}
