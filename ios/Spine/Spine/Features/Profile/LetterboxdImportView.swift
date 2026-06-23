import SwiftUI
import UniformTypeIdentifiers

struct LetterboxdImportView: View {
    @State private var mode: ImportMode = .new
    @State private var isFileImporterPresented = false
    @State private var isOverwriteConfirmationPresented = false
    @State private var isUploadScreenPresented = false

    let coordinator: LetterboxdImportCoordinator

    private var isBusy: Bool {
        switch coordinator.phase {
        case .uploading, .processing:
            true
        case .idle, .succeeded, .failed:
            false
        }
    }

    var body: some View {
        List {
            Section {
                Text("Export your data from Letterboxd settings, then upload the .zip file here.")
                Link("Open Letterboxd Data Settings", destination: URL(string: "https://letterboxd.com/settings/data/")!)
            }

            Section("Import Mode") {
                Picker("Import Mode", selection: $mode) {
                    ForEach(ImportMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .disabled(isBusy)

                Text(mode.detail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section {
                Button {
                    chooseFile()
                } label: {
                    Label("Choose Letterboxd Export", systemImage: "doc.zipper")
                }
                .disabled(isBusy)
            }
        }
        .navigationTitle("Letterboxd Import")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog(
            "This will remove your current movie tracking and Letterboxd-imported lists before importing. This can't be undone.",
            isPresented: $isOverwriteConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("Replace Existing", role: .destructive) {
                isOverwriteConfirmationPresented = false
                isFileImporterPresented = true
            }
            Button("Cancel", role: .cancel) {}
        }
        .fileImporter(
            isPresented: $isFileImporterPresented,
            allowedContentTypes: [.zip],
            allowsMultipleSelection: false,
            onCompletion: handleFileImporterResult
        )
        .fullScreenCover(isPresented: $isUploadScreenPresented) {
            LetterboxdImportUploadView(
                coordinator: coordinator,
                onDone: { isUploadScreenPresented = false }
            )
        }
    }

    private func chooseFile() {
        if mode == .overwrite {
            isOverwriteConfirmationPresented = true
        } else {
            isFileImporterPresented = true
        }
    }

    private func handleFileImporterResult(_ result: Result<[URL], Error>) {
        switch result {
        case let .success(urls):
            guard let url = urls.first else { return }
            isUploadScreenPresented = true
            coordinator.startImport(fileURL: url, mode: mode)
        case let .failure(error):
            isUploadScreenPresented = true
            coordinator.phase = .failed(message: error.localizedDescription)
        }
    }
}

extension ImportMode {
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

private struct MockImportRepository: ImportRepository {
    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        progressHandler?(1)
        return ImportQueueResponse(taskId: "preview-task", status: "queued")
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
        LetterboxdImportView(
            coordinator: LetterboxdImportCoordinator(importRepository: MockImportRepository())
        )
    }
}
