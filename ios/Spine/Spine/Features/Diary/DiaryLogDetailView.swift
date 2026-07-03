import SwiftUI

extension Notification.Name {
    static let diaryEntriesDidChange = Notification.Name("diaryEntriesDidChange")
}

@MainActor
@Observable
final class DiaryLogDetailViewModel {
    var entry: DiaryEntry?
    var mediaDetail: MediaDetail?
    var isLoading = false
    var isDeleting = false
    var errorMessage: String?
    var isReviewRevealed = false

    private let entryId: Int
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        entryId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.entryId = entryId
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loadedEntry = try await diaryRepository.detail(id: entryId)
            entry = loadedEntry
            await loadMediaDetail(for: loadedEntry.media.ref)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save(_ request: DiaryEntryUpdateRequest) async -> Bool {
        guard let entry else { return false }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            self.entry = try await diaryRepository.update(id: entry.id, request: request)
            if let ref = self.entry?.media.ref {
                await loadMediaDetail(for: ref)
            }
            NotificationCenter.default.post(name: .diaryEntriesDidChange, object: nil)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func delete() async -> Bool {
        guard let entry else { return false }
        isDeleting = true
        errorMessage = nil
        defer { isDeleting = false }

        do {
            try await diaryRepository.delete(id: entry.id)
            NotificationCenter.default.post(name: .diaryEntriesDidChange, object: nil)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    private func loadMediaDetail(for ref: MediaRef) async {
        do {
            mediaDetail = try await mediaRepository.detail(ref: ref)
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct DiaryLogDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryLogDetailViewModel
    @State private var presentedRef: MediaRef?
    @State private var isEditing = false
    @State private var isDeleteConfirmationPresented = false
    @State private var edgeDragOffset: CGFloat = 0

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        entryId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .diary,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: DiaryLogDetailViewModel(
            entryId: entryId,
            diaryRepository: diaryRepository,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                Group {
                    if viewModel.isLoading, viewModel.entry == nil {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 520)
                    } else if let entry = viewModel.entry {
                        content(entry)
                    } else if let error = viewModel.errorMessage {
                        ContentUnavailableView("Could not load log", systemImage: "exclamationmark.triangle", description: Text(error))
                            .foregroundStyle(.white)
                            .padding()
                    }
                }
                .padding(.bottom, 38)
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)

            HStack {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 17, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 38, height: 38)
                        .background(.black.opacity(0.34), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Back")

                Spacer()

                if canManageEntry {
                    Button("Edit") {
                        isEditing = true
                    }
                    .font(.system(size: 14, weight: .heavy))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 13)
                    .frame(height: 38)
                    .background(.black.opacity(0.34), in: Capsule())
                    .buttonStyle(.plain)

                    Button(role: .destructive) {
                        isDeleteConfirmationPresented = true
                    } label: {
                        Image(systemName: "trash")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(.white)
                            .frame(width: 38, height: 38)
                            .background(.black.opacity(0.34), in: Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Delete")
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
        }
        .navigationBarBackButtonHidden()
        .toolbar(.hidden, for: .tabBar)
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .task {
            if viewModel.entry == nil {
                await viewModel.load()
            }
        }
        .fullScreenCover(item: $presentedRef) { ref in
            mediaDestination(ref)
        }
        .sheet(isPresented: $isEditing) {
            if let entry = viewModel.entry {
                DiaryLogEditSheet(entry: entry, diaryRepository: diaryRepository) { request in
                    await viewModel.save(request)
                }
            }
        }
        .confirmationDialog("Delete this diary entry?", isPresented: $isDeleteConfirmationPresented, titleVisibility: .visible) {
            Button("Delete Entry", role: .destructive) {
                Task {
                    if await viewModel.delete() {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This cannot be undone.")
        }
    }

    private var canManageEntry: Bool {
        guard let entry = viewModel.entry, let currentUserId else { return false }
        return entry.user.id == currentUserId
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }

    private func content(_ entry: DiaryEntry) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            hero(entry)
            logSection(entry)
        }
    }

    private func hero(_ entry: DiaryEntry) -> some View {
        ZStack(alignment: .bottomLeading) {
            DiaryLogHeroArtwork(entry: entry, detail: viewModel.mediaDetail)
                .frame(height: 420)

            HStack(alignment: .bottom, spacing: 14) {
                MediaArtwork(
                    url: viewModel.mediaDetail?.displayPosterURL ?? entry.media.displayPosterURL,
                    title: entry.media.title,
                    slot: .hero,
                    mediaType: entry.media.ref.mediaType,
                    orientation: viewModel.mediaDetail?.posterOrientation ?? entry.media.posterOrientation
                )
                .shadow(color: .black.opacity(0.48), radius: 22, y: 12)

                VStack(alignment: .leading, spacing: 10) {
                    Text(mediaTypeLabel(entry.media.ref.mediaType))
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.56))
                        .textCase(.uppercase)

                    if let logged = DiaryLogFormat.dateLabel(entry.consumedAt) {
                        Text("Logged \(logged)")
                            .font(.system(size: 13, weight: .heavy))
                            .foregroundStyle(.white.opacity(0.72))

                        if let age = DiaryLogFormat.ageLabel(entry.consumedAt) {
                            Text(age)
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.58))
                        }
                    }

                    let parts = titleParts(entry.media.displayTitle)
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Text(parts.title)
                            .font(.system(size: 31, weight: .black))
                            .foregroundStyle(.white)
                            .lineLimit(4)
                            .minimumScaleFactor(0.72)

                        if entry.isRewatch {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 17.25, weight: .bold))
                                .foregroundStyle(.white.opacity(0.74))
                                .accessibilityLabel("Rewatch")
                        }
                    }

                    if let metadata = heroMetadata(entry) {
                        Text(metadata)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.66))
                            .lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .onTapGesture { presentedRef = entry.media.ref }
            .accessibilityLabel("View \(entry.media.displayTitle)")
            .accessibilityAddTraits(.isButton)
            .padding(.horizontal, 16)
            .padding(.bottom, 22)
        }
    }

    private func logSection(_ entry: DiaryEntry) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            if hasRatingOrLike(entry) {
                ratingLikeLine(entry)
            }

            if let title = clean(entry.reviewTitle) {
                Text(title)
                    .font(.system(size: 21, weight: .heavy))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
            }

            reviewBody(entry)

            if !entry.tags.isEmpty {
                DiaryLogTagsView(
                    tags: entry.tags,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .padding(.horizontal, 16)
    }

    private func hasRatingOrLike(_ entry: DiaryEntry) -> Bool {
        clean(entry.rating) != nil || entry.liked
    }

    private func ratingLikeLine(_ entry: DiaryEntry) -> some View {
        HStack(spacing: 10) {
            if let rating = clean(entry.rating) {
                DiaryStarRating(rating: rating, fontSize: 17)
            }

            if entry.liked {
                Image(systemName: "heart.fill")
                    .font(.system(size: 17, weight: .bold))
                    .foregroundStyle(.pink)
                    .accessibilityLabel("Liked")
            }
        }
    }

    @ViewBuilder
    private func reviewBody(_ entry: DiaryEntry) -> some View {
        if let review = clean(entry.review) {
            if entry.containsSpoilers && !viewModel.isReviewRevealed {
                Button {
                    viewModel.isReviewRevealed = true
                } label: {
                    Label("Reveal spoiler review", systemImage: "eye.slash")
                        .font(.system(size: 14, weight: .heavy))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 18)
                        .background(.white.opacity(0.09), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
                .buttonStyle(.plain)
            } else {
                Text(review)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(.white.opacity(0.78))
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            Text("No review text.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.5))
        }
    }

    private func mediaDestination(_ ref: MediaRef) -> some View {
        MediaDetailView(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }

    private func heroMetadata(_ entry: DiaryEntry) -> String? {
        var parts = [titleParts(entry.media.displayTitle).year, DiaryLogFormat.year(viewModel.mediaDetail?.releaseDate), detailString(viewModel.mediaDetail, "runtime")]
            .compactMap { clean($0) }
        parts += detailArray(viewModel.mediaDetail, "genres").prefix(2)
        var seen = Set<String>()
        parts = parts.filter { seen.insert($0.lowercased()).inserted }
        return parts.isEmpty ? nil : parts.joined(separator: " - ")
    }

    private func detailString(_ detail: MediaDetail?, _ key: String) -> String? {
        guard let value = detail?.details?[key] else { return nil }
        switch value {
        case let .string(string):
            return clean(string)
        case let .number(number):
            return number.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(number))" : "\(number)"
        case let .bool(bool):
            return bool ? "Yes" : "No"
        default:
            return nil
        }
    }

    private func detailArray(_ detail: MediaDetail?, _ key: String) -> [String] {
        guard case let .array(values)? = detail?.details?[key] else { return [] }
        return values.compactMap { value in
            if case let .string(string) = value {
                return clean(string)
            }
            return nil
        }
    }

    private func mediaTypeLabel(_ mediaType: String) -> String {
        mediaType == "tv" ? "TV" : mediaType.capitalized
    }

    private func titleParts(_ title: String) -> (title: String, year: String?) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasSuffix(")") else { return (trimmed, nil) }
        let suffix = String(trimmed.suffix(6))
        guard suffix.first == "(", suffix.last == ")" else { return (trimmed, nil) }
        let year = String(suffix.dropFirst().dropLast())
        guard year.count == 4, year.allSatisfy(\.isNumber) else { return (trimmed, nil) }
        return (String(trimmed.dropLast(7)), year)
    }

    private func clean(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct DiaryLogDetailNavigationCover: View {
    let entryId: Int
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        NavigationStack {
            DiaryLogDetailView(
                entryId: entryId,
                diaryRepository: diaryRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
    }
}

private struct DiaryLogHeroArtwork: View {
    let entry: DiaryEntry
    let detail: MediaDetail?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color(red: 0.07, green: 0.07, blue: 0.065)

                AsyncImage(url: artworkURL) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .blur(radius: usesBackdrop ? 16 : 28)
                            .scaleEffect(usesBackdrop ? 1.08 : 1.24)
                            .opacity(0.62)
                    default:
                        LinearGradient(
                            colors: MediaTypeTheme.theme(for: entry.media.ref.mediaType).gradientColors,
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                        .opacity(0.28)
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.12), location: 0),
                        .init(color: .black.opacity(0.3), location: 0.42),
                        .init(color: Color(red: 0.07, green: 0.07, blue: 0.065).opacity(0.78), location: 0.78),
                        .init(color: Color(red: 0.07, green: 0.07, blue: 0.065), location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
        }
        .clipped()
    }

    private var artworkURL: URL? {
        URL(string: backdropURL ?? detail?.displayPosterURL ?? entry.media.displayPosterURL ?? "")
    }

    private var backdropURL: String? {
        guard ["movie", "tv"].contains(entry.media.ref.mediaType) else { return nil }
        return detail?.displayBackdropURL
    }

    private var usesBackdrop: Bool {
        backdropURL != nil
    }
}

@MainActor
@Observable
private final class DiaryLogEditViewModel {
    let entry: DiaryEntry
    var consumedAt: Date
    var ratingSteps: Int
    var reviewTitle: String
    var review: String
    var tags: [String]
    var tagQuery = ""
    var tagSuggestions: [DiaryTagSuggestion] = []
    var liked: Bool
    var isRewatch: Bool
    var containsSpoilers: Bool
    var visibility: String
    var isLoadingTags = false
    var isSaving = false
    var errorMessage: String?

    private let diaryRepository: DiaryRepository

    init(entry: DiaryEntry, diaryRepository: DiaryRepository) {
        self.entry = entry
        self.diaryRepository = diaryRepository
        consumedAt = ISO8601DateFormatter().date(from: entry.consumedAt ?? "") ?? Date()
        ratingSteps = entry.rating.flatMap { Decimal(string: $0).map { NSDecimalNumber(decimal: $0).intValue } } ?? 0
        reviewTitle = entry.reviewTitle ?? ""
        review = entry.review ?? ""
        tags = entry.tags
        liked = entry.liked
        isRewatch = entry.isRewatch
        containsSpoilers = entry.containsSpoilers
        visibility = entry.visibility
    }

    var repeatLabel: String {
        switch entry.media.ref.mediaType {
        case "book", "manga", "comic":
            "Reread"
        case "game", "boardgame":
            "Replay"
        default:
            "Rewatch"
        }
    }

    func ratingLabel() -> String {
        guard ratingSteps > 0 else { return "No rating" }
        let stars = Double(ratingSteps) / 2
        return stars.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(stars))/5" : "\(stars)/5"
    }

    func setRating(locationX: CGFloat, width: CGFloat) {
        guard width > 0 else { return }
        let clamped = min(max(locationX, 0), width)
        ratingSteps = max(1, min(10, Int(ceil((clamped / width) * 10))))
    }

    func loadTags() async {
        isLoadingTags = true
        defer { isLoadingTags = false }
        do {
            tagSuggestions = try await diaryRepository.tags(query: tagQuery)
        } catch {
            tagSuggestions = []
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

    func request() -> DiaryEntryUpdateRequest {
        DiaryEntryUpdateRequest(
            consumedAt: consumedAt,
            rating: ratingSteps > 0 ? Decimal(ratingSteps) : nil,
            review: review,
            reviewTitle: reviewTitle,
            tags: tags,
            liked: liked,
            isRewatch: isRewatch,
            containsSpoilers: containsSpoilers,
            visibility: visibility
        )
    }
}

private struct DiaryLogEditSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryLogEditViewModel
    let onSave: (DiaryEntryUpdateRequest) async -> Bool

    init(entry: DiaryEntry, diaryRepository: DiaryRepository, onSave: @escaping (DiaryEntryUpdateRequest) async -> Bool) {
        _viewModel = State(initialValue: DiaryLogEditViewModel(entry: entry, diaryRepository: diaryRepository))
        self.onSave = onSave
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    fields
                    tagEditor
                    if let error = viewModel.errorMessage {
                        Text(error)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.red.opacity(0.92))
                    }
                    saveButton
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
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 38, height: 38)
                        .background(.white.opacity(0.12), in: Circle())
                }
                .buttonStyle(.plain)
                Spacer()
            }

            HStack(alignment: .bottom, spacing: 14) {
                MediaArtwork(
                    url: viewModel.entry.media.displayPosterURL,
                    title: viewModel.entry.media.title,
                    slot: .logSheet,
                    mediaType: viewModel.entry.media.ref.mediaType,
                    orientation: viewModel.entry.media.posterOrientation
                )
                .shadow(color: .black.opacity(0.42), radius: 16, y: 8)

                VStack(alignment: .leading, spacing: 8) {
                    Text("Edit Log")
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.56))
                        .textCase(.uppercase)
                    Text(viewModel.entry.media.displayTitle)
                        .font(.system(size: 28, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(3)
                        .minimumScaleFactor(0.78)
                }
            }
        }
    }

    private var fields: some View {
        VStack(alignment: .leading, spacing: 16) {
            fieldGroup {
                DatePicker("Date", selection: $viewModel.consumedAt, displayedComponents: [.date])
                    .datePickerStyle(.compact)
                    .colorScheme(.dark)
                ratingPicker
            }
            fieldGroup {
                TextField("Review title", text: $viewModel.reviewTitle)
                Divider().background(.white.opacity(0.12))
                TextField("Review", text: $viewModel.review, axis: .vertical)
                    .lineLimit(5...9)
            }
            fieldGroup {
                Picker("Visibility", selection: $viewModel.visibility) {
                    ForEach(APIConstants.visibilityChoices, id: \.self) { value in
                        Text(value.capitalized).tag(value)
                    }
                }
                Toggle("Contains spoilers", isOn: $viewModel.containsSpoilers)
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
                    .gesture(DragGesture(minimumDistance: 0).onChanged { value in
                        viewModel.setRating(locationX: value.location.x, width: proxy.size.width)
                    })
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
                compactIconButton(systemName: viewModel.liked ? "heart.fill" : "heart", title: "Like", isSelected: viewModel.liked) {
                    viewModel.liked.toggle()
                }
                compactIconButton(systemName: "arrow.clockwise.circle", title: viewModel.repeatLabel, isSelected: viewModel.isRewatch) {
                    viewModel.isRewatch.toggle()
                }
            }

            Text(viewModel.ratingLabel())
                .font(.system(size: 13, weight: .heavy))
                .foregroundStyle(.white.opacity(0.62))
        }
    }

    private func starSystemName(_ star: Int) -> String {
        if viewModel.ratingSteps >= star * 2 { return "star.fill" }
        if viewModel.ratingSteps == star * 2 - 1 { return "star.leadinghalf.filled" }
        return "star"
    }

    private var tagEditor: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !viewModel.tags.isEmpty {
                FlowLayout(spacing: 8) {
                    ForEach(viewModel.tags, id: \.self) { tag in
                        Button { viewModel.removeTag(tag) } label: {
                            Label(tag, systemImage: "xmark")
                                .font(.system(size: 12, weight: .bold))
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

    private var saveButton: some View {
        Button {
            Task {
                viewModel.isSaving = true
                defer { viewModel.isSaving = false }
                if await onSave(viewModel.request()) {
                    dismiss()
                }
            }
        } label: {
            HStack {
                Spacer()
                if viewModel.isSaving {
                    ProgressView().tint(.black)
                } else {
                    Text("Save Changes").font(.system(size: 16, weight: .heavy))
                }
                Spacer()
            }
            .foregroundStyle(.black)
            .frame(height: 54)
            .background(.white.opacity(0.94), in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(viewModel.isSaving)
    }

    private func compactIconButton(systemName: String, title: String, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(isSelected ? (systemName == "heart.fill" ? .pink : .black) : .white)
                .frame(width: 42, height: 42)
                .background(isSelected ? .white.opacity(0.94) : .white.opacity(0.12), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
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

private struct DiaryLogTagsView: View {
    let tags: [String]
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    @State private var isExpanded = false
    @State private var fullHeight: CGFloat = 0

    private let rowHeight: CGFloat = 30
    private let spacing: CGFloat = 8

    private var collapsedHeight: CGFloat {
        rowHeight * 3 + spacing * 2
    }

    private var canExpand: Bool {
        fullHeight > collapsedHeight + 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            tagLinks
                .frame(maxHeight: isExpanded ? nil : collapsedHeight, alignment: .top)
                .clipped()
                .background {
                    tagChips
                        .fixedSize(horizontal: false, vertical: true)
                        .background {
                            GeometryReader { proxy in
                                Color.clear.preference(key: DiaryLogTagsFullHeightKey.self, value: proxy.size.height)
                            }
                        }
                        .hidden()
                }

            if canExpand {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                } label: {
                    Label(isExpanded ? "READ LESS" : "READ MORE", systemImage: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.62))
                }
                .buttonStyle(.plain)
            }
        }
        .onPreferenceChange(DiaryLogTagsFullHeightKey.self) { fullHeight = $0 }
    }

    private var tagLinks: some View {
        FlowLayout(spacing: spacing) {
            ForEach(tags, id: \.self) { tag in
                NavigationLink {
                    TaggedDiaryView(
                        tag: tag,
                        diaryRepository: diaryRepository,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        currentUserId: currentUserId,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    DiaryLogChip(text: tag, systemImage: "tag")
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var tagChips: some View {
        FlowLayout(spacing: spacing) {
            ForEach(tags, id: \.self) { tag in
                DiaryLogChip(text: tag, systemImage: "tag")
            }
        }
    }
}

private struct DiaryLogTagsFullHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct DiaryLogChip: View {
    let text: String
    let systemImage: String?

    var body: some View {
        Label {
            Text(text)
                .lineLimit(1)
        } icon: {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 10, weight: .black))
            }
        }
        .font(.system(size: 12, weight: .heavy))
        .foregroundStyle(.white.opacity(0.82))
        .padding(.horizontal, 10)
        .frame(height: 30)
        .background(.white.opacity(0.11), in: Capsule())
    }
}

enum DiaryLogFormat {
    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let fallbackISOFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let dateOnlyFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let displayDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale.current
        formatter.dateFormat = "MMM d, yyyy"
        return formatter
    }()

    static func dateLabel(_ rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return displayDateFormatter.string(from: date)
    }

    static func ageLabel(_ rawValue: String?, now: Date = Date(), calendar: Calendar = .current) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        let start = calendar.startOfDay(for: date)
        let end = calendar.startOfDay(for: now)
        guard start < end else { return nil }

        let components = calendar.dateComponents([.year, .month, .day], from: start, to: end)
        let parts = [
            componentLabel(components.year ?? 0, "year"),
            componentLabel(components.month ?? 0, "month"),
            componentLabel(components.day ?? 0, "day"),
        ].compactMap { $0 }

        return "\(joinedWithAnd(parts)) ago"
    }

    static func year(_ rawValue: String?) -> String? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 4 else { return nil }
        return String(trimmed.prefix(4))
    }

    static func starRatingLabel(_ rating: String) -> String {
        guard let raw = Double(rating) else { return rating }
        let stars = raw / 2
        let value = stars.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(stars))" : String(format: "%.1f", stars)
        return "\(value)/5"
    }

    private static func date(from rawValue: String?) -> Date? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let dateOnly = String(trimmed.prefix(10))
        return dateOnlyFormatter.date(from: dateOnly)
            ?? isoFormatter.date(from: trimmed)
            ?? fallbackISOFormatter.date(from: trimmed)
    }

    private static func componentLabel(_ value: Int, _ unit: String) -> String? {
        guard value > 0 else { return nil }
        return "\(value) \(unit)\(value == 1 ? "" : "s")"
    }

    private static func joinedWithAnd(_ parts: [String]) -> String {
        guard parts.count > 2 else { return parts.joined(separator: " and ") }
        return "\(parts.dropLast().joined(separator: ", ")) and \(parts[parts.count - 1])"
    }
}
