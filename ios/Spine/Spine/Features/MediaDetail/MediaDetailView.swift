import SwiftUI

@MainActor
@Observable
final class MediaDetailViewModel {
    var detail: MediaDetail?
    var tracking: TrackingState?
    var selectedStatus = "Planning"
    var ratingText = ""
    var progressText = ""
    var isLoading = false
    var isSaving = false
    var errorMessage: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let onUnauthorized: () -> Void

    init(ref: MediaRef, mediaRepository: MediaRepository, trackingRepository: TrackingRepository, onUnauthorized: @escaping () -> Void) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loaded = try await mediaRepository.detail(ref: ref)
            detail = loaded
            selectedStatus = loaded.userState?.status ?? "Planning"
            ratingText = loaded.userState?.rating ?? ""
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func saveTracking() async {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let rating = Decimal(string: ratingText)
            let progress = Int(progressText)
            tracking = try await trackingRepository.update(
                ref: ref,
                request: TrackingWriteRequest(
                    status: selectedStatus,
                    rating: rating,
                    progress: progress
                )
            )
            if let tracking {
                selectedStatus = tracking.status ?? selectedStatus
                ratingText = tracking.rating ?? ratingText
                if let value = tracking.progress?.value {
                    progressText = NSDecimalNumber(decimal: value).stringValue
                }
            }
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct MediaDetailView: View {
    @State private var viewModel: MediaDetailViewModel

    init(ref: MediaRef, mediaRepository: MediaRepository, trackingRepository: TrackingRepository, onUnauthorized: @escaping () -> Void = {}) {
        _viewModel = State(initialValue: MediaDetailViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ScrollView {
            if viewModel.isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, minHeight: 320)
            } else if let detail = viewModel.detail {
                VStack(alignment: .leading, spacing: 20) {
                    header(detail)
                    trackingControls
                    metadata(detail)
                }
                .padding()
            } else if let error = viewModel.errorMessage {
                ContentUnavailableView("Could not load media", systemImage: "exclamationmark.triangle", description: Text(error))
                    .padding()
            }
        }
        .navigationTitle(viewModel.detail?.title ?? "Media")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            if viewModel.detail == nil {
                await viewModel.load()
            }
        }
    }

    private func header(_ detail: MediaDetail) -> some View {
        HStack(alignment: .top, spacing: 16) {
            PosterImage(urlString: detail.imageUrl, title: detail.title)
                .frame(width: 132)

            VStack(alignment: .leading, spacing: 8) {
                Text(detail.title)
                    .font(.title2.weight(.bold))
                if let subtitle = detail.subtitle ?? detail.releaseDate {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Text(detail.ref.mediaType.capitalized)
                    .font(.caption.weight(.medium))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.quaternary, in: Capsule())
            }
            Spacer()
        }
    }

    private var trackingControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Tracking")
                .font(.headline)

            Picker("Status", selection: $viewModel.selectedStatus) {
                ForEach(APIConstants.statusChoices, id: \.self) { status in
                    Text(status).tag(status)
                }
            }

            TextField("Rating 0-10", text: $viewModel.ratingText)
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)

            TextField("Progress", text: $viewModel.progressText)
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)

            if let tracking = viewModel.tracking {
                Text("Saved \(tracking.status ?? "tracking")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if let state = viewModel.detail?.userState, state.isTracked {
                Text("Currently \(state.status ?? "tracked")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            Button {
                Task { await viewModel.saveTracking() }
            } label: {
                if viewModel.isSaving {
                    ProgressView()
                } else {
                    Label("Save Tracking", systemImage: "checkmark.circle")
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(viewModel.isSaving)
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private func metadata(_ detail: MediaDetail) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if let overview = detail.overview, !overview.isEmpty {
                Text("Overview")
                    .font(.headline)
                Text(overview)
                    .font(.body)
            }

            if let community = detail.community {
                Text("Community")
                    .font(.headline)
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                    GridRow {
                        Text("Average")
                        Text(community.averageRating ?? "-")
                    }
                    GridRow {
                        Text("Diary")
                        Text("\(community.diaryCount)")
                    }
                    GridRow {
                        Text("Reviews")
                        Text("\(community.reviewCount)")
                    }
                }
                .font(.subheadline)
                .foregroundStyle(.secondary)
            }
        }
    }
}
