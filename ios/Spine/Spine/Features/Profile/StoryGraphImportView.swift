import SwiftUI
import UniformTypeIdentifiers

struct StoryGraphImportView: View {
    @State private var mode: ImportMode = .new
    @State private var isFileImporterPresented = false
    @State private var isOverwriteConfirmationPresented = false
    @State private var isUploadScreenPresented = false

    let coordinator: StoryGraphImportCoordinator

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
                Text("Export your library from StoryGraph, then upload the .csv file here.")
                Link("Open StoryGraph", destination: URL(string: "https://app.thestorygraph.com/")!)
            }

            Section("Import Mode") {
                Picker("Import Mode", selection: $mode) {
                    ForEach(ImportMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .disabled(isBusy)

                Text(modeDetail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section {
                Button {
                    chooseFile()
                } label: {
                    Label("Choose StoryGraph CSV", systemImage: "doc.text")
                }
                .disabled(isBusy)
            }
        }
        .navigationTitle("StoryGraph Import")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog(
            "This replaces existing book tracking and book diary entries before importing. This can't be undone.",
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
            allowedContentTypes: [.commaSeparatedText, UTType(filenameExtension: "csv")!],
            allowsMultipleSelection: false,
            onCompletion: handleFileImporterResult
        )
        .fullScreenCover(isPresented: $isUploadScreenPresented) {
            StoryGraphImportUploadView(
                coordinator: coordinator,
                onDone: { isUploadScreenPresented = false }
            )
        }
    }

    private var modeDetail: String {
        switch mode {
        case .new:
            "Import books and dated diary entries that aren't already in Spine."
        case .overwrite:
            "Delete your existing book tracking and book diary entries, then import everything from this file."
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

private struct MockImportRepository: ImportRepository {
    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse {
        fatalError("Not used")
    }

    func queueStoryGraphImport(
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
            taskName: "Import from StoryGraph",
            status: "SUCCESS",
            dateCreated: nil,
            dateDone: nil,
            result: "Imported 12 books."
        )
    }
}

#Preview {
    NavigationStack {
        StoryGraphImportView(
            coordinator: StoryGraphImportCoordinator(importRepository: MockImportRepository())
        )
    }
}
