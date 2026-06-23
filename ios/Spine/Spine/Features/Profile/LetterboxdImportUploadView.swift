import SwiftUI
import UIKit

struct LetterboxdImportUploadView: View {
    let coordinator: LetterboxdImportCoordinator
    let onDone: () -> Void

    var body: some View {
        ZStack {
            SpinePageBackground()

            VStack(spacing: 28) {
                Spacer(minLength: 24)

                content
                    .frame(maxWidth: 420)
                    .padding(.horizontal, 24)

                Spacer(minLength: 24)
            }
        }
        .interactiveDismissDisabled(coordinator.isUploadBlockingDismiss)
        .onAppear(perform: updateIdleTimer)
        .onChange(of: coordinator.phase) {
            updateIdleTimer()
        }
        .onDisappear {
            UIApplication.shared.isIdleTimerDisabled = false
        }
    }

    @ViewBuilder
    private var content: some View {
        switch coordinator.phase {
        case let .uploading(fileName, progress):
            uploadContent(fileName: fileName, progress: progress)
        case let .processing(_, statusLabel, _):
            processingContent(statusLabel: statusLabel)
        case let .succeeded(message):
            resultContent(
                systemName: "checkmark.circle.fill",
                tint: .green,
                title: "Import complete",
                message: message
            )
        case let .failed(message):
            failureContent(message: message)
        case .idle:
            resultContent(
                systemName: "square.and.arrow.down",
                tint: .white.opacity(0.7),
                title: "Letterboxd Import",
                message: "Choose a Letterboxd export to start."
            )
        }
    }

    private func uploadContent(fileName: String, progress: Double) -> some View {
        VStack(spacing: 18) {
            Image(systemName: "arrow.up.doc.fill")
                .font(.system(size: 58, weight: .bold))
                .foregroundStyle(.white)

            Text("Uploading your Letterboxd export")
                .font(.system(size: 31, weight: .black))
                .foregroundStyle(.white)
                .multilineTextAlignment(.center)
                .lineLimit(3)
                .minimumScaleFactor(0.72)

            Text("Keep Spine open until this finishes. Don't lock your phone or switch apps.")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white.opacity(0.68))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 8) {
                ProgressView(value: progress)
                    .tint(.green)

                HStack {
                    Text("\(Int((progress * 100).rounded()))%")
                    Spacer()
                    Text(fileDetail(fileName))
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white.opacity(0.58))
            }
            .padding(.top, 8)
        }
    }

    private func processingContent(statusLabel: String) -> some View {
        VStack(spacing: 18) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 64, weight: .bold))
                .foregroundStyle(.green)

            Text("Upload complete")
                .font(.system(size: 34, weight: .black))
                .foregroundStyle(.white)

            Text("You can leave now")
                .font(.system(size: 22, weight: .black))
                .foregroundStyle(.white)

            VStack(spacing: 10) {
                Text("Your import is running on our servers. This usually takes 10-20 minutes for large libraries.")
                Text("We'll keep checking in the background. You can see status in Settings > Import.")
                Label(statusLabel, systemImage: "clock.arrow.circlepath")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.white.opacity(0.62))
            }
            .font(.system(size: 16, weight: .semibold))
            .foregroundStyle(.white.opacity(0.72))
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)

            primaryButton("Done", action: onDone)
                .padding(.top, 8)
        }
    }

    private func resultContent(systemName: String, tint: Color, title: String, message: String) -> some View {
        VStack(spacing: 18) {
            Image(systemName: systemName)
                .font(.system(size: 64, weight: .bold))
                .foregroundStyle(tint)

            Text(title)
                .font(.system(size: 34, weight: .black))
                .foregroundStyle(.white)
                .multilineTextAlignment(.center)

            Text(message)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white.opacity(0.72))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            primaryButton("Done", action: onDone)
                .padding(.top, 8)
        }
    }

    private func failureContent(message: String) -> some View {
        VStack(spacing: 18) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 58, weight: .bold))
                .foregroundStyle(.red)

            Text("Import needs attention")
                .font(.system(size: 32, weight: .black))
                .foregroundStyle(.white)
                .multilineTextAlignment(.center)

            Text(message)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white.opacity(0.72))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 12) {
                primaryButton("Try Again") {
                    coordinator.retryLastImport()
                }

                Button("Cancel") {
                    coordinator.cancelUploadFailure()
                    onDone()
                }
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(.white.opacity(0.74))
            }
            .padding(.top, 8)
        }
    }

    private func primaryButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 17, weight: .black))
                .foregroundStyle(.black)
                .frame(maxWidth: .infinity)
                .frame(height: 52)
                .background(.green, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private func fileDetail(_ fileName: String) -> String {
        guard let size = coordinator.uploadFileSize else { return fileName }
        return "\(fileName) - \(ByteCountFormatter.string(fromByteCount: size, countStyle: .file))"
    }

    private func updateIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = coordinator.isUploadBlockingDismiss
    }
}

#Preview("Uploading") {
    let coordinator = LetterboxdImportCoordinator(importRepository: MockUploadImportRepository())
    coordinator.phase = .uploading(fileName: "letterboxd-export.zip", progress: 0.42)
    coordinator.uploadFileSize = 24_000_000
    return LetterboxdImportUploadView(coordinator: coordinator, onDone: {})
}

private struct MockUploadImportRepository: ImportRepository {
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
        ImportTaskStatus(taskId: taskId, taskName: nil, status: "PENDING", dateCreated: nil, dateDone: nil, result: nil)
    }
}
