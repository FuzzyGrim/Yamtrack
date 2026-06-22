import SwiftUI

enum MediaLogMode: String, CaseIterable, Identifiable {
    case finished
    case progress

    var id: String { rawValue }
}

@MainActor
@Observable
final class MediaLogViewModel {
    let detail: MediaDetail
    var mode: MediaLogMode = .finished
    var selectedSeasonNumber: Int?
    var consumedAt = Date()
    var ratingSteps = 0
    var review = ""
    var tags: [String] = []
    var tagQuery = ""
    var tagSuggestions: [DiaryTagSuggestion] = []
    var containsSpoilers = false
    var liked = false
    var isRepeat = false
    var progressText = ""
    var progressType = "pages"
    var isLoadingTags = false
    var isSaving = false
    var errorMessage: String?

    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void
    private let onSaved: () -> Void

    init(
        detail: MediaDetail,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping () -> Void
    ) {
        self.detail = detail
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
        self.onSaved = onSaved
    }

    var supportsProgress: Bool {
        ["book", "manga", "comic", "game", "boardgame"].contains(detail.ref.mediaType)
    }

    var supportsSeasonLogging: Bool {
        detail.ref.mediaType == "tv" && !(detail.seasons ?? []).isEmpty
    }

    var selectedRef: MediaRef {
        guard let selectedSeasonNumber else { return detail.ref }
        return MediaRef(
            itemId: nil,
            source: detail.ref.source,
            mediaType: "season",
            mediaId: detail.ref.mediaId,
            seasonNumber: selectedSeasonNumber,
            episodeNumber: nil
        )
    }

    var selectedTitle: String {
        guard let selectedSeasonNumber else { return detail.title }
        return "\(detail.title) Season \(selectedSeasonNumber)"
    }

    var repeatLabel: String {
        switch detail.ref.mediaType {
        case "book", "manga", "comic":
            "Reread"
        case "game", "boardgame":
            "Replay"
        default:
            "Rewatch"
        }
    }

    var primaryActionTitle: String {
        switch selectedRef.mediaType {
        case "book", "manga", "comic":
            "Log Read"
        case "game", "boardgame":
            "Log Play"
        case "season":
            "Log Season"
        case "tv":
            "Log Show"
        default:
            "Log Watched"
        }
    }

    var markOnlyTitle: String {
        switch selectedRef.mediaType {
        case "book", "manga", "comic":
            "Mark Read"
        case "game", "boardgame":
            "Mark Played"
        case "season":
            "Mark Season Watched"
        default:
            "Mark Watched"
        }
    }

    var progressUnit: String {
        switch detail.ref.mediaType {
        case "book":
            progressType == "percentage" ? "percent" : "pages"
        case "manga":
            "chapters"
        case "comic":
            "issues"
        case "game", "boardgame":
            "progress"
        default:
            "progress"
        }
    }

    var progressPlaceholder: String {
        if let maxProgress {
            return "0-\(maxProgress)"
        }
        return detail.ref.mediaType == "book" && progressType == "percentage" ? "0-100" : "Progress"
    }

    var maxProgress: Int? {
        switch detail.ref.mediaType {
        case "book":
            return detail.detailInt("number_of_pages") ?? detail.detailInt("pages") ?? detail.detailInt("total_pages")
        case "manga":
            return detail.detailInt("number_of_chapters") ?? detail.detailInt("chapters")
        case "comic":
            return detail.detailInt("issues_count") ?? detail.detailInt("issues")
        default:
            return nil
        }
    }

    static func ratingDecimal(for steps: Int) -> Decimal? {
        guard steps > 0 else { return nil }
        return Decimal(steps)
    }

    func ratingLabel(for steps: Int? = nil) -> String {
        let value = steps ?? ratingSteps
        guard value > 0 else { return "No rating" }
        let stars = Double(value) / 2
        return stars.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(stars))/5" : "\(stars)/5"
    }

    func setRating(star: Int, half: Bool) {
        let next = star * 2 - (half ? 1 : 0)
        ratingSteps = ratingSteps == next ? 0 : next
    }

    func setRating(locationX: CGFloat, width: CGFloat) {
        guard width > 0 else { return }
        let clamped = min(max(locationX, 0), width)
        let next = max(1, min(10, Int(ceil((clamped / width) * 10))))
        ratingSteps = next
    }

    func loadTags() async {
        isLoadingTags = true
        defer { isLoadingTags = false }
        do {
            tagSuggestions = try await diaryRepository.tags(query: tagQuery)
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func addTypedTag() {
        addTag(tagQuery)
        tagQuery = ""
    }

    func addTag(_ rawTag: String) {
        let tag = rawTag.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !tag.isEmpty, !tags.contains(where: { $0.caseInsensitiveCompare(tag) == .orderedSame }) else { return }
        tags.append(tag)
    }

    func removeTag(_ tag: String) {
        tags.removeAll { $0 == tag }
    }

    func save() async -> Bool {
        mode == .progress ? await saveProgressOnly() : await saveFinishedLog()
    }

    func markOnly() async -> Bool {
        await performSave {
            let ref = selectedRef
            if ref.mediaType == "season", let seasonNumber = ref.seasonNumber {
                _ = try await trackingRepository.watchSeason(source: ref.source, mediaId: ref.mediaId, seasonNumber: seasonNumber)
            } else if ref.mediaType == "book" {
                _ = try await trackingRepository.completeBook(source: ref.source, mediaId: ref.mediaId, completedAt: consumedAt)
            } else {
                _ = try await trackingRepository.consume(ref: ref, consumedAt: consumedAt)
            }
        }
    }

    private func saveFinishedLog() async -> Bool {
        await performSave {
            _ = try await diaryRepository.create(DiaryEntryWriteRequest(
                ref: selectedRef,
                consumedAt: consumedAt,
                rating: Self.ratingDecimal(for: ratingSteps),
                review: review,
                reviewTitle: "",
                liked: liked,
                isRewatch: isRepeat,
                autoMarkConsumed: true,
                containsSpoilers: containsSpoilers,
                visibility: "public",
                tags: tags
            ))
        }
    }

    private func saveProgressOnly() async -> Bool {
        await performSave {
            guard let value = Decimal(string: progressText.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                throw MediaLogError.invalidProgress
            }
            if detail.ref.mediaType == "book" {
                _ = try await trackingRepository.updateBookProgress(
                    source: detail.ref.source,
                    mediaId: detail.ref.mediaId,
                    progressType: progressType,
                    value: value,
                    notes: review
                )
            } else {
                _ = try await trackingRepository.update(
                    ref: detail.ref,
                    request: TrackingWriteRequest(
                        status: "In progress",
                        progress: NSDecimalNumber(decimal: value).intValue,
                        notes: review.isEmpty ? nil : review
                    )
                )
            }
        }
    }

    private func performSave(_ operation: () async throws -> Void) async -> Bool {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            try await operation()
            onSaved()
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

private enum MediaLogError: LocalizedError {
    case invalidProgress

    var errorDescription: String? {
        "Enter a valid progress value."
    }
}

struct MediaLogView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaLogViewModel

    init(
        detail: MediaDetail,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: MediaLogViewModel(
            detail: detail,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized,
            onSaved: onSaved
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 22) {
                    header
                    modePicker
                    if viewModel.mode == .progress {
                        progressFields
                    } else {
                        finishedFields
                    }
                    errorText
                    actions
                }
                .padding(.horizontal, 18)
                .padding(.top, 18)
                .padding(.bottom, 34)
            }
        }
        .task {
            await viewModel.loadTags()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 38, height: 38)
                        .background(.white.opacity(0.12), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Close")

                Spacer()
            }

            HStack(alignment: .bottom, spacing: 14) {
                PosterImage(urlString: viewModel.detail.customPosterUrl ?? viewModel.detail.imageUrl, title: viewModel.detail.title)
                    .frame(width: 106)
                    .shadow(color: .black.opacity(0.42), radius: 16, y: 8)

                VStack(alignment: .leading, spacing: 8) {
                    Text(viewModel.mode == .progress ? "Update Progress" : viewModel.primaryActionTitle)
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.56))
                        .textCase(.uppercase)

                    Text(viewModel.selectedTitle)
                        .font(.system(size: 28, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(3)
                        .minimumScaleFactor(0.78)

                    if let subtitle = viewModel.detail.subtitle ?? viewModel.detail.releaseDate {
                        Text(subtitle)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.58))
                            .lineLimit(1)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var modePicker: some View {
        VStack(spacing: 12) {
            if viewModel.supportsProgress {
                Picker("Log mode", selection: $viewModel.mode) {
                    Text("Finished").tag(MediaLogMode.finished)
                    Text("Progress").tag(MediaLogMode.progress)
                }
                .pickerStyle(.segmented)
            }

            if viewModel.supportsSeasonLogging {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        seasonChip(title: "Whole Show", seasonNumber: nil)
                        ForEach(viewModel.detail.seasons ?? []) { season in
                            seasonChip(title: "S\(season.seasonNumber)", seasonNumber: season.seasonNumber)
                        }
                    }
                }
            }
        }
    }

    private func seasonChip(title: String, seasonNumber: Int?) -> some View {
        Button {
            viewModel.selectedSeasonNumber = seasonNumber
        } label: {
            Text(title)
                .font(.system(size: 13, weight: .heavy))
                .foregroundStyle(viewModel.selectedSeasonNumber == seasonNumber ? .black : .white.opacity(0.82))
                .padding(.horizontal, 13)
                .frame(height: 34)
                .background(viewModel.selectedSeasonNumber == seasonNumber ? .white.opacity(0.92) : .white.opacity(0.12), in: Capsule())
        }
        .buttonStyle(.plain)
    }

    private var finishedFields: some View {
        VStack(alignment: .leading, spacing: 16) {
            fieldGroup {
                DatePicker("Date", selection: $viewModel.consumedAt, displayedComponents: [.date])
                    .datePickerStyle(.compact)
                    .colorScheme(.dark)
                ratingPicker
            }

            fieldGroup {
                TextField("Review", text: $viewModel.review, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(5...9)
            }

            tagEditor
            options
        }
    }

    private var progressFields: some View {
        VStack(alignment: .leading, spacing: 16) {
            fieldGroup {
                if viewModel.detail.ref.mediaType == "book" {
                    Picker("Progress Type", selection: $viewModel.progressType) {
                        Text("Pages").tag("pages")
                        Text("Percent").tag("percentage")
                    }
                    .pickerStyle(.segmented)
                }

                HStack {
                    TextField(viewModel.progressPlaceholder, text: $viewModel.progressText)
                        .keyboardType(.decimalPad)
                    Text(viewModel.progressUnit)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white.opacity(0.48))
                }

                Divider().background(.white.opacity(0.12))

                TextField("Notes", text: $viewModel.review, axis: .vertical)
                    .lineLimit(3...7)
            }
        }
    }

    private var ratingPicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                GeometryReader { proxy in
                    HStack(spacing: 6) {
                        ForEach(1...5, id: \.self) { star in
                            Image(systemName: starSystemName(star))
                                .font(.system(size: 32, weight: .bold))
                                .foregroundStyle(viewModel.ratingSteps >= star * 2 - 1 ? .yellow : .white.opacity(0.26))
                                .frame(width: 33)
                        }
                    }
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 0)
                            .onChanged { value in
                                viewModel.setRating(locationX: value.location.x, width: proxy.size.width)
                            }
                    )
                }
                .frame(width: 189, height: 42)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Rating")
                .accessibilityValue(viewModel.ratingLabel())
                .accessibilityAdjustableAction { direction in
                    switch direction {
                    case .increment:
                        viewModel.ratingSteps = min(10, viewModel.ratingSteps + 1)
                    case .decrement:
                        viewModel.ratingSteps = max(0, viewModel.ratingSteps - 1)
                    default:
                        break
                    }
                }

                Spacer(minLength: 0)

                compactIconButton(
                    systemName: viewModel.liked ? "heart.fill" : "heart",
                    title: "Like",
                    isSelected: viewModel.liked
                ) {
                    viewModel.liked.toggle()
                }

                compactIconButton(
                    systemName: "arrow.clockwise.circle",
                    title: viewModel.repeatLabel,
                    isSelected: viewModel.isRepeat
                ) {
                    viewModel.isRepeat.toggle()
                }
            }

            Text(viewModel.ratingLabel())
                .font(.system(size: 13, weight: .heavy))
                .foregroundStyle(.white.opacity(0.62))
        }
    }

    private func starSystemName(_ star: Int) -> String {
        if viewModel.ratingSteps >= star * 2 {
            return "star.fill"
        }
        if viewModel.ratingSteps == star * 2 - 1 {
            return "star.leadinghalf.filled"
        }
        return "star"
    }

    private var tagEditor: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !viewModel.tags.isEmpty {
                FlowLayout(spacing: 8) {
                    ForEach(viewModel.tags, id: \.self) { tag in
                        Button {
                            viewModel.removeTag(tag)
                        } label: {
                            Label(tag, systemImage: "xmark")
                                .font(.system(size: 12, weight: .bold))
                                .labelStyle(.titleAndIcon)
                                .foregroundStyle(.white)
                                .padding(.horizontal, 10)
                                .frame(height: 30)
                                .background(.white.opacity(0.13), in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            fieldGroup {
                HStack {
                    TextField("Tags", text: $viewModel.tagQuery)
                        .textInputAutocapitalization(.never)
                        .onSubmit { viewModel.addTypedTag() }
                        .task(id: viewModel.tagQuery) {
                            await viewModel.loadTags()
                        }
                    Button {
                        viewModel.addTypedTag()
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.system(size: 22, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.tagQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }

            if !viewModel.tagSuggestions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(viewModel.tagSuggestions, id: \.self) { suggestion in
                            Button {
                                viewModel.addTag(suggestion.name)
                                viewModel.tagQuery = ""
                            } label: {
                                Text(suggestion.name)
                                    .font(.system(size: 12, weight: .bold))
                                    .foregroundStyle(.white.opacity(0.84))
                                    .padding(.horizontal, 10)
                                    .frame(height: 30)
                                    .background(.white.opacity(0.09), in: Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    private var options: some View {
        fieldGroup {
            Toggle("Contains spoilers", isOn: $viewModel.containsSpoilers)
        }
    }

    private func compactIconButton(systemName: String, title: String, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(selectedIconColor(systemName: systemName, isSelected: isSelected))
                .frame(width: 42, height: 42)
                .background(isSelected ? .white.opacity(0.94) : .white.opacity(0.12), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private func selectedIconColor(systemName: String, isSelected: Bool) -> Color {
        guard isSelected else { return .white }
        return systemName == "heart.fill" ? .pink : .black
    }

    @ViewBuilder
    private var errorText: some View {
        if let error = viewModel.errorMessage {
            Text(error)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.red.opacity(0.92))
        }
    }

    private var actions: some View {
        VStack(spacing: 10) {
            Button {
                Task {
                    if await viewModel.save() {
                        dismiss()
                    }
                }
            } label: {
                saveLabel(viewModel.mode == .progress ? "Save Progress" : viewModel.primaryActionTitle)
            }
            .buttonStyle(.plain)
            .disabled(viewModel.isSaving)

            Button {
                Task {
                    if await viewModel.markOnly() {
                        dismiss()
                    }
                }
            } label: {
                Text(viewModel.markOnlyTitle)
                    .font(.system(size: 15, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.72))
                    .frame(maxWidth: .infinity)
                    .frame(height: 48)
                    .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .disabled(viewModel.isSaving)
        }
    }

    private func saveLabel(_ title: String) -> some View {
        HStack {
            Spacer()
            if viewModel.isSaving {
                ProgressView()
                    .tint(.black)
            } else {
                Text(title)
                    .font(.system(size: 16, weight: .heavy))
            }
            Spacer()
        }
        .foregroundStyle(.black)
        .frame(height: 54)
        .background(.white.opacity(0.94), in: RoundedRectangle(cornerRadius: 8))
    }

    private func fieldGroup<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            content()
        }
        .font(.system(size: 16, weight: .semibold))
        .foregroundStyle(.white)
        .tint(.white)
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .stroke(.white.opacity(0.06))
        }
    }
}

private struct FlowLayout: Layout {
    var spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }

        return CGSize(width: width, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > bounds.width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            subview.place(at: CGPoint(x: bounds.minX + x, y: bounds.minY + y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

private extension MediaDetail {
    func detailInt(_ key: String) -> Int? {
        details?[key]?.intValue
    }
}

private extension JSONValue {
    var intValue: Int? {
        switch self {
        case let .number(value):
            Int(value)
        case let .string(value):
            Int(value)
        default:
            nil
        }
    }
}
