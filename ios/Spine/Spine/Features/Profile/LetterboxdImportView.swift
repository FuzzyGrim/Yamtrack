import SwiftUI
import UniformTypeIdentifiers

@MainActor
@Observable
final class LetterboxdImportViewModel {
    enum ImportState: Equatable {
        case idle
        case uploading
        case processing(String)
        case success(String)
        case failed(String)
    }

    var mode: ImportMode = .new
    var state: ImportState = .idle
    var isFileImporterPresented = false
    var isOverwriteConfirmationPresented = false

    private let importRepository: ImportRepository
    private let onUnauthorized: () -> Void

    init(importRepository: ImportRepository, onUnauthorized: @escaping () -> Void = {}) {
        self.importRepository = importRepository
        self.onUnauthorized = onUnauthorized
    }

    var isBusy: Bool {
        switch state {
        case .uploading, .processing:
            true
        case .idle, .success, .failed:
            false
        }
    }

    func chooseFile() {
        if mode == .overwrite {
            isOverwriteConfirmationPresented = true
        } else {
            isFileImporterPresented = true
        }
    }

    func confirmOverwrite() {
        isOverwriteConfirmationPresented = false
        isFileImporterPresented = true
    }

    func handleFileImporterResult(_ result: Result<[URL], Error>) {
        switch result {
        case let .success(urls):
            guard let url = urls.first else { return }
            Task { await importFile(at: url) }
        case let .failure(error):
            state = .failed(error.localizedDescription)
        }
    }

    func importFile(at url: URL) async {
        guard url.pathExtension.lowercased() == "zip" else {
            state = .failed("Please upload the .zip file from Letterboxd, not the extracted folder.")
            return
        }

        state = .uploading
        do {
            let (data, fileName) = try await Self.readFile(url)
            let response = try await importRepository.queueLetterboxdImport(
                fileData: data,
                fileName: fileName,
                mode: mode
            )
            state = .processing("Import queued. Waiting for results...")
            try await poll(taskId: response.taskId)
        } catch {
            state = .failed(error.localizedDescription)
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func poll(taskId: String) async throws {
        let startedAt = Date()

        while Date().timeIntervalSince(startedAt) < 600 {
            try await Task.sleep(for: .seconds(2))
            let task = try await importRepository.importTaskStatus(taskId: taskId)

            switch task.status.uppercased() {
            case "SUCCESS":
                state = .success(task.result?.trimmedNonEmpty ?? "Letterboxd import complete.")
                return
            case "FAILURE":
                state = .failed(task.result?.trimmedNonEmpty ?? "Letterboxd import failed.")
                return
            default:
                state = .processing("Import status: \(task.status)")
            }
        }

        state = .failed("Import is still processing. Check again later or retry after confirming the worker is running.")
    }

    private nonisolated static func readFile(_ url: URL) async throws -> (Data, String) {
        try await Task.detached(priority: .userInitiated) {
            let didStartAccessing = url.startAccessingSecurityScopedResource()
            defer {
                if didStartAccessing {
                    url.stopAccessingSecurityScopedResource()
                }
            }

            let trimmedFileName = url.lastPathComponent.trimmingCharacters(in: .whitespacesAndNewlines)
            let fileName = trimmedFileName.isEmpty ? "letterboxd.zip" : trimmedFileName
            return (try Data(contentsOf: url), fileName)
        }.value
    }
}

struct LetterboxdImportView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: LetterboxdImportViewModel

    init(importRepository: ImportRepository, onUnauthorized: @escaping () -> Void = {}) {
        _viewModel = State(initialValue: LetterboxdImportViewModel(importRepository: importRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        List {
            Section {
                Text("Export your data from Letterboxd settings, then upload the .zip file here.")
                Link("Open Letterboxd Data Settings", destination: URL(string: "https://letterboxd.com/settings/data/")!)
            }

            Section("Import Mode") {
                Picker("Import Mode", selection: $viewModel.mode) {
                    ForEach(ImportMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .disabled(viewModel.isBusy)

                Text(viewModel.mode.detail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section {
                Button {
                    viewModel.chooseFile()
                } label: {
                    Label("Choose Letterboxd Export", systemImage: "doc.zipper")
                }
                .disabled(viewModel.isBusy)

                switch viewModel.state {
                case .idle:
                    EmptyView()
                case .uploading:
                    statusRow("Uploading Letterboxd export...")
                case let .processing(message):
                    statusRow(message)
                case let .success(message):
                    resultView(message: message, isSuccess: true)
                case let .failed(message):
                    resultView(message: message, isSuccess: false)
                }
            }

            if case .success = viewModel.state {
                Section {
                    Button("Done") {
                        dismiss()
                    }
                    Text("Pull to refresh profile after import.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Letterboxd Import")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog(
            "This will remove your current movie tracking and Letterboxd-imported lists before importing. This can't be undone.",
            isPresented: $viewModel.isOverwriteConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("Replace Existing", role: .destructive) {
                viewModel.confirmOverwrite()
            }
            Button("Cancel", role: .cancel) {}
        }
        .fileImporter(
            isPresented: $viewModel.isFileImporterPresented,
            allowedContentTypes: [.zip],
            allowsMultipleSelection: false,
            onCompletion: viewModel.handleFileImporterResult
        )
    }

    private func statusRow(_ message: String) -> some View {
        HStack(spacing: 12) {
            ProgressView()
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private func resultView(message: String, isSuccess: Bool) -> some View {
        Label {
            Text(message)
                .font(.subheadline)
        } icon: {
            Image(systemName: isSuccess ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(isSuccess ? .green : .red)
        }
    }
}

private extension ImportMode {
    var title: String {
        switch self {
        case .new:
            "Add new only"
        case .overwrite:
            "Replace existing"
        }
    }

    var detail: String {
        switch self {
        case .new:
            "Import movies, diary entries, lists, and likes that aren't already in Spine."
        case .overwrite:
            "Delete your existing imported movies, diary entries, and Letterboxd lists, then import everything from this file."
        }
    }
}

private extension String {
    var trimmedNonEmpty: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

private struct MockImportRepository: ImportRepository {
    func queueLetterboxdImport(fileData: Data, fileName: String, mode: ImportMode) async throws -> ImportQueueResponse {
        ImportQueueResponse(taskId: "preview-task", status: "queued")
    }

    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus {
        ImportTaskStatus(
            taskId: taskId,
            taskName: "Import from Letterboxd",
            status: "SUCCESS",
            dateCreated: nil,
            dateDone: nil,
            result: "Imported 12 movies."
        )
    }
}

#Preview {
    NavigationStack {
        LetterboxdImportView(importRepository: MockImportRepository())
    }
}
