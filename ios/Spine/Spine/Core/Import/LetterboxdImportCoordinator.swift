import Foundation

enum LetterboxdImportJobPhase: Equatable {
    case idle
    case uploading(fileName: String, progress: Double)
    case processing(taskId: String, statusLabel: String, startedAt: Date)
    case succeeded(message: String)
    case failed(message: String)
}

extension Notification.Name {
    static let letterboxdImportDidSucceed = Notification.Name("letterboxdImportDidSucceed")
}

@MainActor
@Observable
final class LetterboxdImportCoordinator {
    var phase: LetterboxdImportJobPhase = .idle
    var uploadFileSize: Int64?
    var lastFileName: String?
    var isCheckingStatus = false

    @ObservationIgnored var onUnauthorized: (() -> Void)?

    @ObservationIgnored private let importRepository: ImportRepository
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let pollInterval: Duration
    @ObservationIgnored private let timeout: TimeInterval
    @ObservationIgnored private var uploadTask: Task<Void, Never>?
    @ObservationIgnored private var pollingTask: Task<Void, Never>?
    @ObservationIgnored private var lastFileURL: URL?
    @ObservationIgnored private var lastMode: ImportMode = .new

    init(
        importRepository: ImportRepository,
        defaults: UserDefaults = .standard,
        pollInterval: Duration = .seconds(2),
        timeout: TimeInterval = 30 * 60
    ) {
        self.importRepository = importRepository
        self.defaults = defaults
        self.pollInterval = pollInterval
        self.timeout = timeout
    }

    var isUploadBlockingDismiss: Bool {
        if case .uploading = phase { return true }
        return false
    }

    var hasProcessingJob: Bool {
        if case .processing = phase { return true }
        return false
    }

    var canCheckStatus: Bool {
        persistedJob != nil || currentProcessingJob != nil
    }

    func startImport(fileURL: URL, mode: ImportMode) {
        uploadTask?.cancel()
        pollingTask?.cancel()
        lastFileURL = fileURL
        lastMode = mode
        uploadTask = Task { [weak self] in
            await self?.runImport(fileURL: fileURL, mode: mode)
        }
    }

    func retryLastImport() {
        guard let lastFileURL else { return }
        startImport(fileURL: lastFileURL, mode: lastMode)
    }

    func cancelUploadFailure() {
        uploadTask?.cancel()
        clearPersistedJob()
        uploadFileSize = nil
        phase = .idle
    }

    func resumeIfNeeded() {
        if case let .processing(taskId, _, startedAt) = phase, pollingTask == nil {
            startPolling(taskId: taskId, startedAt: startedAt)
            return
        }

        guard let job = persistedJob else { return }
        guard !hasProcessingJob else { return }

        if Date().timeIntervalSince(job.startedAt) >= timeout {
            phase = .failed(message: Self.timeoutMessage)
            return
        }

        phase = .processing(taskId: job.taskId, statusLabel: Self.statusLabel(for: "PENDING"), startedAt: job.startedAt)
        startPolling(taskId: job.taskId, startedAt: job.startedAt)
    }

    func checkStatusOnce() {
        guard let job = persistedJob ?? currentProcessingJob else { return }
        Task {
            isCheckingStatus = true
            defer { isCheckingStatus = false }
            do {
                let task = try await importRepository.importTaskStatus(taskId: job.taskId)
                handle(task: task, taskId: job.taskId, startedAt: job.startedAt)
            } catch {
                phase = .failed(message: error.localizedDescription)
                if case APIError.unauthorized = error {
                    onUnauthorized?()
                }
            }
        }
    }

    func clearFinishedJob() {
        pollingTask?.cancel()
        uploadTask?.cancel()
        clearPersistedJob()
        uploadFileSize = nil
        lastFileName = nil
        phase = .idle
    }

    private func runImport(fileURL: URL, mode: ImportMode) async {
        guard fileURL.pathExtension.lowercased() == "zip" else {
            phase = .failed(message: "Please upload the .zip file from Letterboxd, not the extracted folder.")
            return
        }

        do {
            let file = try await Self.readFile(fileURL)
            lastFileName = file.name
            uploadFileSize = file.size
            phase = .uploading(fileName: file.name, progress: 0)

            let response = try await importRepository.queueLetterboxdImport(
                fileData: file.data,
                fileName: file.name,
                mode: mode,
                progressHandler: { [weak self] progress in
                    guard let self else { return }
                    self.phase = .uploading(fileName: file.name, progress: progress)
                }
            )

            let startedAt = Date()
            persist(taskId: response.taskId, mode: mode, startedAt: startedAt)
            phase = .processing(taskId: response.taskId, statusLabel: Self.statusLabel(for: response.status), startedAt: startedAt)
            startPolling(taskId: response.taskId, startedAt: startedAt)
        } catch {
            phase = .failed(message: error.localizedDescription)
            if case APIError.unauthorized = error {
                onUnauthorized?()
            }
        }
    }

    private func startPolling(taskId: String, startedAt: Date) {
        pollingTask?.cancel()
        pollingTask = Task { [weak self] in
            await self?.poll(taskId: taskId, startedAt: startedAt)
        }
    }

    private func poll(taskId: String, startedAt: Date) async {
        while !Task.isCancelled {
            if Date().timeIntervalSince(startedAt) >= timeout {
                phase = .failed(message: Self.timeoutMessage)
                pollingTask = nil
                return
            }

            do {
                let task = try await importRepository.importTaskStatus(taskId: taskId)
                if handle(task: task, taskId: taskId, startedAt: startedAt) {
                    pollingTask = nil
                    return
                }
            } catch {
                phase = .failed(message: error.localizedDescription)
                pollingTask = nil
                if case APIError.unauthorized = error {
                    onUnauthorized?()
                }
                return
            }

            do {
                try await Task.sleep(for: pollInterval)
            } catch {
                return
            }
        }
    }

    @discardableResult
    private func handle(task: ImportTaskStatus, taskId: String, startedAt: Date) -> Bool {
        switch task.status.uppercased() {
        case "SUCCESS":
            clearPersistedJob()
            phase = .succeeded(message: task.result?.trimmedNonEmpty ?? "Letterboxd import complete.")
            NotificationCenter.default.post(
                name: .letterboxdImportDidSucceed,
                object: self,
                userInfo: ["taskId": taskId],
            )
            return true
        case "FAILURE":
            clearPersistedJob()
            phase = .failed(message: task.result?.trimmedNonEmpty ?? "Letterboxd import failed.")
            return true
        default:
            if Date().timeIntervalSince(startedAt) >= timeout {
                phase = .failed(message: Self.timeoutMessage)
            } else {
                phase = .processing(taskId: taskId, statusLabel: Self.statusLabel(for: task.status), startedAt: startedAt)
            }
            return false
        }
    }

    private static func statusLabel(for status: String) -> String {
        switch status.uppercased() {
        case "PENDING", "QUEUED":
            "Queued..."
        case "STARTED":
            "Importing movies, diary, and lists..."
        case "SUCCESS":
            "Import complete."
        case "FAILURE":
            "Import failed."
        default:
            "Import status: \(status)"
        }
    }

    private static let timeoutMessage = "Import is taking longer than expected. Check back later - it may still complete on the server."

    private nonisolated static func readFile(_ url: URL) async throws -> (data: Data, name: String, size: Int64?) {
        try await Task.detached(priority: .userInitiated) {
            let didStartAccessing = url.startAccessingSecurityScopedResource()
            defer {
                if didStartAccessing {
                    url.stopAccessingSecurityScopedResource()
                }
            }

            let trimmedFileName = url.lastPathComponent.trimmingCharacters(in: .whitespacesAndNewlines)
            let fileName = trimmedFileName.isEmpty ? "letterboxd.zip" : trimmedFileName
            let values = try? url.resourceValues(forKeys: [.fileSizeKey])
            return (try Data(contentsOf: url), fileName, values?.fileSize.map(Int64.init))
        }.value
    }

    private var currentProcessingJob: PersistedJob? {
        if case let .processing(taskId, _, startedAt) = phase {
            return PersistedJob(taskId: taskId, mode: lastMode, startedAt: startedAt)
        }
        return nil
    }

    private var persistedJob: PersistedJob? {
        guard let taskId = defaults.string(forKey: Keys.taskId),
              let modeRaw = defaults.string(forKey: Keys.mode),
              let mode = ImportMode(rawValue: modeRaw)
        else { return nil }
        let startedAt = Date(timeIntervalSince1970: defaults.double(forKey: Keys.startedAt))
        return PersistedJob(taskId: taskId, mode: mode, startedAt: startedAt)
    }

    private func persist(taskId: String, mode: ImportMode, startedAt: Date) {
        defaults.set(taskId, forKey: Keys.taskId)
        defaults.set(mode.rawValue, forKey: Keys.mode)
        defaults.set(startedAt.timeIntervalSince1970, forKey: Keys.startedAt)
    }

    private func clearPersistedJob() {
        defaults.removeObject(forKey: Keys.taskId)
        defaults.removeObject(forKey: Keys.mode)
        defaults.removeObject(forKey: Keys.startedAt)
    }

    private struct PersistedJob {
        let taskId: String
        let mode: ImportMode
        let startedAt: Date
    }

    private enum Keys {
        static let taskId = "letterboxdImport.taskId"
        static let mode = "letterboxdImport.mode"
        static let startedAt = "letterboxdImport.startedAt"
    }
}

private extension String {
    var trimmedNonEmpty: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
