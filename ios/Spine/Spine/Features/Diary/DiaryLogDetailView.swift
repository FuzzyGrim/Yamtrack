import SwiftUI

@MainActor
@Observable
final class DiaryLogDetailViewModel {
    var entry: DiaryEntry?
    var mediaDetail: MediaDetail?
    var isLoading = false
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
    @State private var edgeDragOffset: CGFloat = 0

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        entryId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        selectedTab: AppTab = .diary,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
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
        VStack(alignment: .leading, spacing: 24) {
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

                    let parts = titleParts(entry.media.title)
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
            .accessibilityLabel("View \(entry.media.title)")
            .accessibilityAddTraits(.isButton)
            .padding(.horizontal, 16)
            .padding(.bottom, 22)
        }
    }

    private func logSection(_ entry: DiaryEntry) -> some View {
        VStack(alignment: .leading, spacing: 18) {
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
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .padding(.horizontal, 16)
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
        var parts = [titleParts(entry.media.title).year, DiaryLogFormat.year(viewModel.mediaDetail?.releaseDate), detailString(viewModel.mediaDetail, "runtime")]
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
        return detail?.customBackdropUrl ?? detail?.backdropUrl
    }

    private var usesBackdrop: Bool {
        backdropURL != nil
    }
}

private struct DiaryLogTagsView: View {
    let tags: [String]
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
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
