import SwiftUI

@MainActor
@Observable
final class MediaDetailViewModel {
    var detail: MediaDetail?
    var reviews: [MediaReview] = []
    var tracking: TrackingState?
    var selectedStatus = "Planning"
    var ratingText = ""
    var progressText = ""
    var notesText = ""
    var isLoading = false
    var isLoadingReviews = false
    var isSaving = false
    var errorMessage: String?
    var reviewsErrorMessage: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loaded = try await mediaRepository.detail(ref: ref)
            detail = loaded
            reviews = loaded.reviews ?? []
            selectedStatus = loaded.userState?.status ?? "Planning"
            ratingText = loaded.userState?.rating ?? ""
            await loadReviews()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func loadReviews() async {
        isLoadingReviews = true
        reviewsErrorMessage = nil
        defer { isLoadingReviews = false }

        do {
            reviews = try await mediaRepository.reviews(ref: ref)
        } catch {
            reviewsErrorMessage = error.localizedDescription
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
                    progress: progress,
                    notes: notesText.isEmpty ? nil : notesText
                )
            )
            if let tracking {
                selectedStatus = tracking.status ?? selectedStatus
                ratingText = tracking.rating ?? ratingText
                notesText = tracking.notes ?? notesText
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

    func toggleLike(for review: MediaReview) async {
        do {
            let state = try await diaryRepository.setLike(entryId: review.id, liked: !review.viewerHasLiked)
            guard let index = reviews.firstIndex(where: { $0.id == review.id }) else { return }
            reviews[index].viewerHasLiked = state.liked
            reviews[index].likeCount = state.likeCount
        } catch {
            reviewsErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private enum MediaDetailSheet: Identifiable {
    case tracking

    var id: String { "tracking" }
}

private struct TopSafeAreaInsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

struct MediaDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaDetailViewModel
    @State private var presentedSheet: MediaDetailSheet?
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var edgeDragOffset: CGFloat = 0

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: MediaDetailViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                Group {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 520)
                    } else if let detail = viewModel.detail {
                        VStack(spacing: 0) {
                            hero(detail)
                                .padding(.top, -topSafeAreaInset)
                            content(detail)
                        }
                    } else if let error = viewModel.errorMessage {
                        ContentUnavailableView("Could not load media", systemImage: "exclamationmark.triangle", description: Text(error))
                            .foregroundStyle(.white)
                            .padding()
                    }
                }
                .padding(.bottom, 116)
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)

            topButtons
                .padding(.horizontal, 16)
                .padding(.top, topSafeAreaInset + 6)

            MediaDetailBottomBar()
                .padding(.horizontal, 18)
                .padding(.bottom, 8)
                .frame(maxHeight: .infinity, alignment: .bottom)
        }
        .toolbar(.hidden, for: .tabBar)
        .navigationBarBackButtonHidden()
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: TopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(TopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
        .sheet(item: $presentedSheet) { sheet in
            switch sheet {
            case .tracking:
                TrackingEditSheet(viewModel: viewModel)
            }
        }
        .task {
            if viewModel.detail == nil {
                await viewModel.load()
            }
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

    private var topButtons: some View {
        HStack {
            CircleIconButton(systemName: "chevron.left", label: "Back") {
                dismiss()
            }
            Spacer()
            CircleIconButton(systemName: "ellipsis", label: "More") {}
        }
    }

    private func hero(_ detail: MediaDetail) -> some View {
        ZStack(alignment: .bottom) {
            HeroArtwork(detail: detail)
                .frame(height: MediaDetailLayout.heroHeight)

            VStack(spacing: 0) {
                PosterImage(urlString: detail.customPosterUrl ?? detail.imageUrl, title: detail.title)
                    .frame(width: MediaDetailLayout.heroPosterWidth)
                    .shadow(color: .black.opacity(0.48), radius: 22, y: 12)
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 16)

                HStack(alignment: .bottom, spacing: 12) {
                    VStack(alignment: .leading, spacing: 11) {
                        Text(detail.title)
                            .font(.system(size: 22, weight: .heavy))
                            .foregroundStyle(.white)
                            .lineLimit(2)
                            .minimumScaleFactor(0.82)

                        if let byline = byline(detail) {
                            Text(byline)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.62))
                                .lineLimit(1)
                        }

                        genreChips(detail)
                        RatingChipRow(chips: ratingChips(detail))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    ActionRail(
                        isTracked: currentStatus(detail) != nil,
                        onTrack: { presentedSheet = .tracking }
                    )
                    .padding(.bottom, 1)
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 18)
        }
    }

    private func content(_ detail: MediaDetail) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            TrackingSummarySection(detail: detail, tracking: viewModel.tracking, userState: detail.userState)
            SynopsisCard(text: synopsisPreview(detail))
            SpineRatingDistributionSection(community: detail.community)
            MediaFactsSection(rows: detailRows(detail))
            CreditSection(title: creditTitle(detail), people: primaryCredits(detail))
            SeasonsSection(seasons: detail.seasons ?? [])
            EpisodesSection(episodes: detail.episodes ?? [])
            ReviewsSection(reviews: viewModel.reviews, isLoading: viewModel.isLoadingReviews, error: viewModel.reviewsErrorMessage)
            RecommendationsSection(sections: detail.relatedSections ?? [])
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
    }

    private func genreChips(_ detail: MediaDetail) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(primaryChips(detail), id: \.self) { chip in
                    Text(chip)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(1)
                        .padding(.horizontal, 11)
                        .padding(.vertical, 8)
                        .background(.white.opacity(0.12), in: Capsule())
                }
            }
        }
    }

    private func primaryChips(_ detail: MediaDetail) -> [String] {
        var chips = [mediaTypeChipLabel(detail.ref.mediaType), year(detail)].compactMap { $0 }
        if detail.ref.mediaType == "movie", let runtime = detailString(detail, "runtime"), !runtime.isEmpty {
            chips.append(runtime)
        }
        if let contentRating = contentRating(detail) {
            chips.append(contentRating)
        }
        chips += detailArray(detail, "genres")
        return Array(chips.prefix(6))
    }

    private func contentRating(_ detail: MediaDetail) -> String? {
        guard ["movie", "tv"].contains(detail.ref.mediaType) else { return nil }
        guard let rating = detailString(detail, "rating"), !rating.isEmpty else { return nil }
        return rating
    }

    private func mediaTypeChipLabel(_ mediaType: String) -> String {
        switch mediaType {
        case "tv":
            "TV"
        default:
            mediaType.capitalized
        }
    }

    private func currentStatus(_ detail: MediaDetail) -> String? {
        viewModel.tracking?.status ?? detail.userState?.status
    }

    private func currentRating(_ detail: MediaDetail) -> String? {
        viewModel.tracking?.rating ?? detail.userState?.rating
    }

    private func ratingChips(_ detail: MediaDetail) -> [RatingChip] {
        var chips: [RatingChip] = []
        if let rating = detail.community?.averageRating, !rating.isEmpty {
            chips.append(RatingChip(source: "SP", value: rating, assetName: nil))
        }
        for rating in detail.externalRatings ?? [] where !rating.value.isEmpty {
            if detail.ref.mediaType == "movie", rating.source.lowercased() == "tmdb" {
                continue
            }
            chips.append(RatingChip(
                source: rating.source.ratingAbbreviation,
                value: rating.value,
                assetName: rating.source.ratingAssetName
            ))
        }
        if let rating = currentRating(detail), !rating.isEmpty {
            chips.append(RatingChip(source: "You", value: rating, assetName: nil))
        }
        return chips
    }

    private func byline(_ detail: MediaDetail) -> String? {
        if let author = authors(detail).first {
            return author
        }
        for key in ["director", "creator", "developer"] {
            if let value = detailString(detail, key) {
                return value
            }
        }
        return detail.subtitle
    }

    private func synopsisPreview(_ detail: MediaDetail) -> String {
        detail.displaySynopsis ?? "No synopsis available yet."
    }

    private func detailRows(_ detail: MediaDetail) -> [DetailFactRow] {
        let mediaType = detail.ref.mediaType
        var rows = [
            DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)),
            DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
            DetailFactRow(label: "Certification", value: detailString(detail, "rating")),
            DetailFactRow(label: "Pages", value: detailString(detail, "number_of_pages") ?? detailString(detail, "pages")),
            DetailFactRow(label: "Publish Date", value: detailString(detail, "publish_date") ?? detailString(detail, "published_date")),
            DetailFactRow(label: "Developer", value: detailString(detail, "developer")),
            DetailFactRow(label: "Creator", value: detailString(detail, "creator")),
            DetailFactRow(label: "Director", value: detailString(detail, "director")),
        ]
        for (label, key) in [
            ("Authors", "authors"),
            ("Genres", "genres"),
            ("Studios", "studios"),
            ("Country", "country"),
            ("Languages", "languages"),
            ("Platforms", "platforms"),
            ("Companies", "companies"),
            ("Publishers", "publishers"),
            ("ISBN", "isbn"),
        ] {
            let values = detailArray(detail, key)
            if !values.isEmpty {
                rows.append(DetailFactRow(label: label, value: values.joined(separator: ", ")))
            }
        }
        if mediaType == "tv" || mediaType == "season", let count = detailString(detail, "episodes") {
            rows.append(DetailFactRow(label: "Episodes", value: count))
        }
        return rows
        .filter { $0.value?.isEmpty == false }
    }

    private func creditTitle(_ detail: MediaDetail) -> String {
        detail.ref.mediaType == "book" ? "Authors" : "Cast & Crew"
    }

    private func primaryCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        if detail.ref.mediaType == "book" {
            return authors(detail).map { CreditDisplay(name: $0, subtitle: "Author", imageUrl: nil) }
        }
        return ((detail.cast ?? []) + (detail.crew ?? [])).map {
            CreditDisplay(name: $0.name, subtitle: $0.character ?? $0.role, imageUrl: $0.imageUrl)
        }
    }

    private func year(_ detail: MediaDetail) -> String? {
        let value = detail.releaseDate ?? detailString(detail, "release_date") ?? detailString(detail, "first_air_date") ?? detailString(detail, "publish_date")
        guard let value, value.count >= 4 else { return nil }
        return String(value.prefix(4))
    }

    private func authors(_ detail: MediaDetail) -> [String] {
        detailArray(detail, "authors")
    }

    private func releaseDate(_ detail: MediaDetail) -> String? {
        detail.releaseDate
            ?? detailString(detail, "release_date")
            ?? detailString(detail, "first_air_date")
            ?? detailString(detail, "start_date")
            ?? detailString(detail, "publish_date")
            ?? detailString(detail, "published_date")
    }

    private func formattedReleaseDate(_ detail: MediaDetail) -> String? {
        guard let raw = releaseDate(detail) else { return nil }
        return Self.longDateFormatter.string(from: raw) ?? raw
    }

    private static let longDateFormatter: LongDateFormatter = LongDateFormatter()

    private func detailString(_ detail: MediaDetail, _ key: String) -> String? {
        detail.details?[key]?.displayString
    }

    private func detailArray(_ detail: MediaDetail, _ key: String) -> [String] {
        detail.details?[key]?.displayStrings ?? []
    }
}

private enum MediaDetailLayout {
    static let heroPosterWidth: CGFloat = 191
    static let heroHeight: CGFloat = 535
    static let ratingBadgeSize: CGFloat = 24
    static let ratingPillVerticalPadding: CGFloat = 6
    static var ratingPillHeight: CGFloat { ratingBadgeSize + ratingPillVerticalPadding * 2 }
}

private enum SpinePalette {
    static let pageBackground = Color(red: 0.07, green: 0.07, blue: 0.065)
}

private struct SpinePageBackground: View {
    var body: some View {
        SpinePalette.pageBackground
            .ignoresSafeArea()
    }
}

private struct CircleIconButton: View {
    let systemName: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

private struct HeroArtwork: View {
    let detail: MediaDetail

    var body: some View {
        ZStack {
            SpinePalette.pageBackground

            GeometryReader { proxy in
                AsyncImage(url: artworkURL) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        accentColor
                            .frame(width: proxy.size.width, height: proxy.size.height)
                    }
                }
                .blur(radius: blurRadius, opaque: true)
                .scaleEffect(scale)
                .brightness(0.02)
                .saturation(usesPosterFallback ? 1.18 : 1.34)
                .frame(width: proxy.size.width, height: proxy.size.height)
            }

            accentColor
                .opacity(0.07)
                .blendMode(.softLight)

            LinearGradient(
                stops: [
                    .init(color: .black.opacity(0.22), location: 0),
                    .init(color: .black.opacity(0.08), location: 0.14),
                    .init(color: .clear, location: 0.38),
                ],
                startPoint: .top,
                endPoint: .bottom
            )

            LinearGradient(
                stops: [
                    .init(color: .clear, location: 0.48),
                    .init(color: SpinePalette.pageBackground.opacity(0.12), location: 0.62),
                    .init(color: SpinePalette.pageBackground.opacity(0.45), location: 0.78),
                    .init(color: SpinePalette.pageBackground.opacity(0.82), location: 0.9),
                    .init(color: SpinePalette.pageBackground, location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )

            RadialGradient(
                colors: [.clear, .black.opacity(0.1)],
                center: .center,
                startRadius: 60,
                endRadius: 360
            )
            .blendMode(.multiply)
        }
        .clipped()
    }

    private var artworkURL: URL? {
        URL(string: detail.backdropUrl ?? detail.imageUrl ?? "")
    }

    private var usesPosterFallback: Bool {
        detail.backdropUrl == nil && detail.imageUrl != nil
    }

    private var blurRadius: CGFloat {
        usesPosterFallback ? 30 : 22
    }

    private var scale: CGFloat {
        usesPosterFallback ? 1.28 : 1.2
    }

    private var accentColor: Color {
        Color(hex: detail.posterAccentColor) ?? SpinePalette.pageBackground
    }
}

private struct ActionRail: View {
    private static let iconFont = Font.system(size: 24, weight: .semibold)

    let isTracked: Bool
    let onTrack: () -> Void

    var body: some View {
        VStack(alignment: .trailing, spacing: 18) {
            Image(systemName: "eye")
                .font(Self.iconFont)
                .accessibilityLabel("Mark as watched")

            Image(systemName: "heart")
                .font(Self.iconFont)

            Button(action: onTrack) {
                HStack(spacing: 6) {
                    Text("LOG")
                        .font(.system(size: 12, weight: .heavy))
                    Image(systemName: isTracked ? "checkmark" : "plus")
                        .font(.system(size: 12, weight: .bold))
                }
                .foregroundStyle(SpinePalette.pageBackground)
                .frame(height: MediaDetailLayout.ratingBadgeSize)
                .padding(.horizontal, 9)
                .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
                .background(.white.opacity(0.92), in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(isTracked ? "Edit tracking" : "Log")
        }
        .foregroundStyle(.white.opacity(0.92))
        .shadow(color: .black.opacity(0.3), radius: 8, y: 3)
    }
}

private struct SectionLabel: View {
    let title: String

    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 11, weight: .heavy))
            .foregroundStyle(.white.opacity(0.48))
            .tracking(0)
    }
}

private struct RatingChip: Hashable {
    let source: String
    let value: String
    let assetName: String?
}

private struct RatingChipRow: View {
    let chips: [RatingChip]

    var body: some View {
        if !chips.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(chips, id: \.self) { chip in
                        HStack(spacing: 6) {
                            RatingSourceBadge(chip: chip)
                            Text(chip.value)
                                .font(.system(size: 12, weight: .heavy))
                                .foregroundStyle(.white)
                        }
                        .padding(.horizontal, 9)
                        .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
                        .background(.white.opacity(0.12), in: Capsule())
                    }
                }
            }
        }
    }
}

private struct RatingSourceBadge: View {
    let chip: RatingChip

    var body: some View {
        Group {
            if let assetName = chip.assetName {
                ratingLogo(assetName: assetName)
            } else {
                Text(chip.source)
                    .font(.system(size: 10, weight: .black))
                    .foregroundStyle(.black)
            }
        }
        .frame(width: MediaDetailLayout.ratingBadgeSize, height: MediaDetailLayout.ratingBadgeSize)
        .background(chip.assetName == "RatingMAL" ? .clear : .white, in: Circle())
        .clipShape(Circle())
    }

    @ViewBuilder
    private func ratingLogo(assetName: String) -> some View {
        switch assetName {
        case "RatingIMDb":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.15)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingLetterboxd":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.1)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingMAL":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .clipped()
        default:
            Image(assetName)
                .resizable()
                .scaledToFit()
                .padding(3)
        }
    }
}

private struct TrackingSummarySection: View {
    let detail: MediaDetail
    let tracking: TrackingState?
    let userState: UserMediaState?

    var body: some View {
        if hasState {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Your Tracking")
                HStack(alignment: .top, spacing: 10) {
                    PosterImage(urlString: detail.customPosterUrl ?? detail.imageUrl, title: detail.title)
                        .frame(width: 58)
                        .clipShape(RoundedRectangle(cornerRadius: 2))

                    VStack(alignment: .leading, spacing: 4) {
                        if let status {
                            Text(status)
                                .font(.system(size: 14, weight: .heavy))
                                .foregroundStyle(.white)
                        }
                        ForEach(lines, id: \.self) { line in
                            Text(line)
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(.white.opacity(0.76))
                        }
                    }

                    Spacer()
                }
            }
        }
    }

    private var status: String? { tracking?.status ?? userState?.status }
    private var hasState: Bool { status != nil || !lines.isEmpty }

    private var lines: [String] {
        var values: [String] = []
        if let progress = tracking?.progress, let value = progress.value {
            if let max = progress.max {
                values.append("\(display(value)) of \(display(max)) \(progress.unit)\(max == 1 ? "" : "s")")
            } else {
                values.append("\(display(value)) \(progress.unit)\(value == 1 ? "" : "s")")
            }
        }
        if let rating = tracking?.rating ?? userState?.rating {
            values.append("Rated \(rating)")
        }
        if let startDate = tracking?.startDate {
            values.append("Started \(startDate)")
        }
        if let endDate = tracking?.endDate {
            values.append("Completed \(endDate)")
        }
        return values
    }

    private func display(_ value: Decimal) -> String {
        NSDecimalNumber(decimal: value).stringValue
    }
}

private struct SynopsisCard: View {
    let text: String
    @State private var isExpanded = false
    @State private var truncatedHeight: CGFloat = 0
    @State private var fullHeight: CGFloat = 0

    private var canExpand: Bool {
        fullHeight > truncatedHeight + 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(text)
                .font(synopsisFont)
                .foregroundStyle(.white.opacity(0.9))
                .lineSpacing(2)
                .lineLimit(isExpanded ? nil : 3)
                .background {
                    Text(text)
                        .font(synopsisFont)
                        .lineSpacing(2)
                        .lineLimit(3)
                        .background {
                            GeometryReader { proxy in
                                Color.clear.preference(key: SynopsisTruncatedHeightKey.self, value: proxy.size.height)
                            }
                        }
                        .hidden()
                }
                .background {
                    Text(text)
                        .font(synopsisFont)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                        .background {
                            GeometryReader { proxy in
                                Color.clear.preference(key: SynopsisFullHeightKey.self, value: proxy.size.height)
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
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 8))
        .onPreferenceChange(SynopsisTruncatedHeightKey.self) { truncatedHeight = $0 }
        .onPreferenceChange(SynopsisFullHeightKey.self) { fullHeight = $0 }
    }

    private var synopsisFont: Font {
        .system(size: 14, weight: .semibold, design: .serif)
    }
}

private struct SynopsisTruncatedHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct SynopsisFullHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct DetailFactRow: Identifiable {
    let label: String
    let value: String?

    var id: String { label }
}

private struct MediaFactsSection: View {
    let rows: [DetailFactRow]

    var body: some View {
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 18) {
                SectionLabel(title: "Details")

                VStack(spacing: 0) {
                    ForEach(rows) { row in
                        HStack(alignment: .top) {
                            Text(row.label)
                                .font(.system(size: 13, weight: .semibold, design: .serif))
                                .foregroundStyle(.white.opacity(0.62))
                                .frame(width: 104, alignment: .leading)
                            Text(row.value ?? "")
                                .font(.system(size: 14, weight: .heavy, design: .serif))
                                .foregroundStyle(.white)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 12)

                        if row.id != rows.last?.id {
                            Divider().overlay(.white.opacity(0.05))
                        }
                    }
                }
                .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 4))
            }
        }
    }
}

private struct SpineRatingDistributionSection: View {
    let community: CommunityStats?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionLabel(title: "Spine Ratings")
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    if let average = community?.averageRating {
                        Label(average, systemImage: "star.fill")
                            .font(.system(size: 18, weight: .heavy))
                            .foregroundStyle(.white)
                    }
                    Spacer()
                    Text("\((community?.ratingCount ?? 0).formatted()) ratings")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white.opacity(0.52))
                }

                if buckets.isEmpty {
                    Text("No Spine ratings yet.")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.58))
                } else {
                    HStack(alignment: .bottom, spacing: 8) {
                        ForEach(buckets, id: \.rating) { bucket in
                            VStack(spacing: 6) {
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(.white.opacity(0.82))
                                    .frame(width: 18, height: max(4, CGFloat(bucket.count) / CGFloat(maxCount) * 70))
                                Text(bucket.rating)
                                    .font(.system(size: 9, weight: .heavy))
                                    .foregroundStyle(.white.opacity(0.58))
                            }
                            .frame(maxWidth: .infinity)
                        }
                    }
                    .frame(height: 96, alignment: .bottom)
                }
            }
            .padding(14)
            .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 4))
        }
    }

    private var buckets: [RatingDistributionBucket] {
        community?.ratingDistribution ?? []
    }

    private var maxCount: Int {
        max(buckets.map(\.count).max() ?? 1, 1)
    }
}

private struct CreditDisplay: Identifiable {
    let name: String
    let subtitle: String?
    let imageUrl: String?

    var id: String { "\(name):\(subtitle ?? "")" }
}

private struct CreditSection: View {
    let title: String
    let people: [CreditDisplay]

    var body: some View {
        if !people.isEmpty {
            VStack(alignment: .leading, spacing: 18) {
                SectionLabel(title: title)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(people) { person in
                            VStack(alignment: .leading, spacing: 8) {
                                AsyncImage(url: URL(string: person.imageUrl ?? "")) { phase in
                                    switch phase {
                                    case let .success(image):
                                        image.resizable().scaledToFill()
                                    default:
                                        Circle()
                                            .fill(.white.opacity(0.12))
                                            .overlay {
                                                Image(systemName: "person.fill")
                                                    .foregroundStyle(.white.opacity(0.7))
                                            }
                                    }
                                }
                                .frame(width: 58, height: 58)
                                .clipShape(Circle())

                                Text(person.name)
                                    .font(.system(size: 13, weight: .heavy))
                                    .foregroundStyle(.white)
                                    .lineLimit(2)
                                if let subtitle = person.subtitle, !subtitle.isEmpty {
                                    Text(subtitle)
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundStyle(.white.opacity(0.62))
                                        .lineLimit(1)
                                }
                            }
                            .frame(width: 118, alignment: .topLeading)
                        }
                    }
                }
            }
        }
    }
}

private struct SeasonsSection: View {
    let seasons: [SeasonSummary]

    var body: some View {
        if !seasons.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Seasons")
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(seasons) { season in
                            VStack(alignment: .leading, spacing: 8) {
                                PosterImage(urlString: season.imageUrl, title: season.title)
                                    .frame(width: 90)
                                Text(season.title)
                                    .font(.system(size: 12, weight: .heavy))
                                    .foregroundStyle(.white)
                                    .lineLimit(2)
                                if let count = season.episodeCount {
                                    Text("\(count) episodes")
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundStyle(.white.opacity(0.55))
                                }
                            }
                            .frame(width: 90, alignment: .topLeading)
                        }
                    }
                }
            }
        }
    }
}

private struct EpisodesSection: View {
    let episodes: [EpisodeSummary]

    var body: some View {
        if !episodes.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Episodes")
                VStack(spacing: 0) {
                    ForEach(episodes) { episode in
                        HStack(spacing: 10) {
                            Text("\(episode.episodeNumber)")
                                .font(.system(size: 12, weight: .heavy))
                                .foregroundStyle(.white.opacity(0.56))
                                .frame(width: 24)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(episode.title)
                                    .font(.system(size: 14, weight: .heavy))
                                    .foregroundStyle(.white)
                                Text([episode.airDate, episode.runtime, episode.rating].compactMap { $0 }.joined(separator: " - "))
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(.white.opacity(0.56))
                            }
                            Spacer()
                        }
                        .padding(12)
                        if episode.id != episodes.last?.id {
                            Divider().overlay(.white.opacity(0.05))
                        }
                    }
                }
                .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 4))
            }
        }
    }
}

private struct ReviewsSection: View {
    let reviews: [MediaReview]
    let isLoading: Bool
    let error: String?

    var body: some View {
        if isLoading || !reviews.isEmpty || error != nil {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Reviews")
                if isLoading {
                    ProgressView()
                        .tint(.white)
                        .frame(maxWidth: .infinity, minHeight: 80)
                } else {
                    ForEach(reviews.prefix(3)) { review in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text(review.user.displayName)
                                    .font(.system(size: 13, weight: .heavy))
                                    .foregroundStyle(.white)
                                Spacer()
                                if let rating = review.rating {
                                    Label(rating, systemImage: "star.fill")
                                        .font(.system(size: 11, weight: .heavy))
                                        .foregroundStyle(.white.opacity(0.85))
                                }
                            }
                            if let title = review.reviewTitle, !title.isEmpty {
                                Text(title)
                                    .font(.system(size: 14, weight: .heavy))
                                    .foregroundStyle(.white)
                            }
                            Text(review.containsSpoilers ? "Spoiler review" : review.review)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.68))
                                .lineLimit(4)
                        }
                        .padding(12)
                        .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 6))
                    }
                }
                if let error {
                    Text(error)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.red.opacity(0.8))
                }
            }
        }
    }
}

private struct RecommendationsSection: View {
    let sections: [RelatedMediaSection]

    var body: some View {
        ForEach(sections.filter { !$0.items.isEmpty }) { section in
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 4) {
                    SectionLabel(title: section.title)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.42))
                }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(section.items) { item in
                            VStack(alignment: .leading, spacing: 8) {
                                PosterImage(urlString: item.imageUrl, title: item.title)
                                    .frame(width: 100)
                                Text(item.title)
                                    .font(.system(size: 12, weight: .heavy))
                                    .foregroundStyle(.white)
                                    .lineLimit(2)
                            }
                            .frame(width: 100, alignment: .topLeading)
                        }
                    }
                }
            }
        }
    }
}

private struct MediaDetailBottomBar: View {
    var body: some View {
        HStack(spacing: 8) {
            HStack(spacing: 0) {
                BottomBarItem(title: "Home", systemName: "house.fill", isSelected: true)
                BottomBarItem(title: "Library", systemName: "books.vertical.fill", isSelected: false)
                BottomBarItem(title: "Community", systemName: "person.2.fill", isSelected: false)
            }
            .padding(5)
            .background(.black.opacity(0.74), in: Capsule())
            .overlay {
                Capsule().stroke(.white.opacity(0.06))
            }

            Image(systemName: "magnifyingglass")
                .font(.system(size: 24, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 58, height: 58)
                .background(.black.opacity(0.82), in: Circle())
                .overlay {
                    Circle().stroke(.white.opacity(0.06))
                }
        }
    }
}

private struct BottomBarItem: View {
    let title: String
    let systemName: String
    let isSelected: Bool

    var body: some View {
        VStack(spacing: 3) {
            Image(systemName: systemName)
                .font(.system(size: 21, weight: .bold))
            Text(title)
                .font(.system(size: 9, weight: .heavy))
        }
        .foregroundStyle(.white)
        .frame(width: isSelected ? 88 : 76, height: 48)
        .background(isSelected ? Color.white.opacity(0.16) : .clear, in: Capsule())
    }
}

private struct TrackingEditSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Bindable var viewModel: MediaDetailViewModel

    var body: some View {
        NavigationStack {
            Form {
                Picker("Status", selection: $viewModel.selectedStatus) {
                    ForEach(APIConstants.statusChoices, id: \.self) { status in
                        Text(status).tag(status)
                    }
                }

                TextField("Rating 0-10", text: $viewModel.ratingText)
                    .keyboardType(.decimalPad)
                TextField("Progress", text: $viewModel.progressText)
                    .keyboardType(.numberPad)
                TextField("Notes", text: $viewModel.notesText, axis: .vertical)
                    .lineLimit(3...6)

                if let error = viewModel.errorMessage {
                    Text(error)
                        .foregroundStyle(.red)
                }
            }
            .navigationTitle("Track")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            await viewModel.saveTracking()
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
                    .disabled(viewModel.isSaving)
                }
            }
        }
    }
}

private extension JSONValue {
    var displayString: String? {
        switch self {
        case let .string(value):
            value
        case let .number(value):
            value.rounded() == value ? String(Int(value)) : String(value)
        case let .bool(value):
            value ? "Yes" : "No"
        case let .object(value):
            value["name"]?.displayString ?? value["provider_name"]?.displayString
        case .array, .null:
            nil
        }
    }

    var displayStrings: [String] {
        switch self {
        case let .array(values):
            values.flatMap(\.displayStrings)
        case let .object(value):
            if let string = displayString {
                [string]
            } else {
                value.values.flatMap(\.displayStrings)
            }
        default:
            displayString.map { [$0] } ?? []
        }
    }
}

private extension Color {
    init?(hex: String?) {
        guard let hex else { return nil }
        let value = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        guard value.count == 6, let int = Int(value, radix: 16) else { return nil }
        self.init(
            red: Double((int >> 16) & 0xFF) / 255,
            green: Double((int >> 8) & 0xFF) / 255,
            blue: Double(int & 0xFF) / 255
        )
    }
}

private extension String {
    var ratingAbbreviation: String {
        switch lowercased() {
        case "imdb":
            "IM"
        case "letterboxd":
            "LB"
        case "rotten tomatoes":
            "RT"
        case "tmdb":
            "TM"
        case "hardcover":
            "HC"
        case "igdb":
            "IG"
        case "mal":
            "MA"
        case "mangaupdates":
            "MU"
        case "openlibrary":
            "OL"
        default:
            String(prefix(2)).uppercased()
        }
    }

    var ratingAssetName: String? {
        switch lowercased() {
        case "imdb":
            "RatingIMDb"
        case "letterboxd":
            "RatingLetterboxd"
        case "rotten tomatoes":
            "RatingRottenTomatoes"
        case "mal", "myanimelist":
            "RatingMAL"
        default:
            nil
        }
    }
}

private struct LongDateFormatter {
    private let isoFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private let displayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale.current
        formatter.dateFormat = "MMMM d, yyyy"
        return formatter
    }()

    func string(from raw: String) -> String? {
        let trimmed = String(raw.prefix(10))
        guard let date = isoFormatter.date(from: trimmed) else { return nil }
        return displayFormatter.string(from: date)
    }
}

#Preview {
    MediaDetailView(
        ref: MockMediaFixtures.bookDetail.ref,
        mediaRepository: MockMediaRepository(),
        trackingRepository: MockTrackingRepository(),
        diaryRepository: MockDiaryRepository()
    )
}
