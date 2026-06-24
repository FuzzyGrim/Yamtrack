import SwiftUI

@MainActor
@Observable
final class MediaDetailViewModel {
    var detail: MediaDetail?
    var reviews: [MediaReview] = []
    var tracking: TrackingState?
    var isLoading = false
    var isLoadingReviews = false
    var isSavingQuickAction = false
    var isSavingProgress = false
    var isSavingLike = false
    var errorMessage: String?
    var reviewsErrorMessage: String?
    var quickActionErrorMessage: String?
    var progressErrorMessage: String?
    var likeErrorMessage: String?

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
        tracking = nil
        defer { isLoading = false }

        do {
            let loaded = try await mediaRepository.detail(ref: ref)
            detail = loaded
            reviews = loaded.reviews ?? []
            await loadTrackingIfNeeded(for: loaded)
            await loadReviews()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func loadTrackingIfNeeded(for detail: MediaDetail) async {
        guard detail.userState?.isTracked == true || detail.userState?.status != nil else { return }
        do {
            tracking = try await trackingRepository.detail(ref: detail.ref)
        } catch APIError.httpStatus(404, _) {
            tracking = nil
        } catch {
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

    func toggleMediaLike(for detail: MediaDetail) async -> Bool {
        guard !isSavingLike else { return false }
        isSavingLike = true
        likeErrorMessage = nil
        defer { isSavingLike = false }

        let next = !(detail.userState?.hasLiked ?? false)
        self.detail = detail.replacingHasLiked(next)
        do {
            let response = try await mediaRepository.setLiked(ref: detail.ref, liked: next)
            self.detail = self.detail?.replacingHasLiked(response.liked)
            return true
        } catch {
            self.detail = detail
            likeErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func performQuickAction(_ action: MediaDetailQuickAction, for detail: MediaDetail, completedAt: Date = Date()) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }

        do {
            let state: TrackingState
            switch action {
            case .currently:
                state = try await trackingRepository.update(
                    ref: detail.ref,
                    request: TrackingWriteRequest(status: "In progress")
                )
            case .finished:
                if detail.ref.mediaType == "book" {
                    state = try await trackingRepository.completeBook(
                        source: detail.ref.source,
                        mediaId: detail.ref.mediaId,
                        completedAt: completedAt
                    )
                } else {
                    state = try await trackingRepository.consume(ref: detail.ref, consumedAt: completedAt)
                }
            case .stopped:
                state = try await trackingRepository.update(
                    ref: detail.ref,
                    request: TrackingWriteRequest(status: "Dropped")
                )
            }
            tracking = state
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func saveProgress(_ request: ProgressUpdateSaveRequest, for detail: MediaDetail) async -> Bool {
        guard !isSavingProgress else { return false }
        isSavingProgress = true
        progressErrorMessage = nil
        defer { isSavingProgress = false }

        do {
            var state: TrackingState
            if detail.ref.mediaType == "book" {
                state = try await trackingRepository.updateBookProgress(
                    source: detail.ref.source,
                    mediaId: detail.ref.mediaId,
                    progressType: request.mode.apiValue,
                    value: Decimal(request.value),
                    notes: ""
                )
                state = state.replacingProgress(progressState(for: request, detail: detail, fallback: state.progress))
            } else {
                state = try await trackingRepository.update(
                    ref: detail.ref,
                    request: TrackingWriteRequest(
                        status: "In progress",
                        progress: request.value
                    )
                )
                state = state.replacingProgress(progressState(for: request, detail: detail, fallback: state.progress))
            }
            ProgressDisplayPreferences.setMode(request.mode, for: detail.ref)
            tracking = state
            return true
        } catch {
            progressErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    private func progressState(
        for request: ProgressUpdateSaveRequest,
        detail: MediaDetail,
        fallback: ProgressState?
    ) -> ProgressState {
        let max: Decimal?
        switch request.mode {
        case .percentage:
            max = Decimal(100)
        case .pages:
            max = detail.progressTotalPages.map { Decimal($0) } ?? fallback?.max
        }
        return ProgressState(
            kind: request.mode.apiValue,
            value: Decimal(request.value),
            max: max,
            unit: request.mode.unit
        )
    }

    func applyPosterSave(_ response: PosterSaveResponse) {
        detail = detail?.replacingPoster(with: response)
    }

    func applyBackdropSave(_ response: BackdropSaveResponse) {
        detail = detail?.replacingBackdrop(with: response)
    }
}

enum MediaDetailQuickAction {
    case currently
    case finished
    case stopped
}

private enum MediaDetailSheet: Identifiable {
    case posterMenu
    case bookGameActions
    case addToList

    var id: String {
        switch self {
        case .posterMenu: "posterMenu"
        case .bookGameActions: "bookGameActions"
        case .addToList: "addToList"
        }
    }
}

private struct PresentedDiaryEntry: Identifiable {
    let id: Int
}

private struct PresentedMediaDiary: Identifiable {
    let detail: MediaDetail

    var id: String { detail.ref.id }
    var title: String { "\(detail.title) Logs" }
}

enum MediaArtworkCustomization {
    static func supportsPoster(source: String, mediaType: String) -> Bool {
        if source == "tmdb", ["movie", "tv"].contains(mediaType) {
            return true
        }
        return mediaType == "book" && ["openlibrary", "hardcover"].contains(source)
    }

    static func supportsBackdrop(source: String, mediaType: String) -> Bool {
        source == "tmdb" && ["movie", "tv"].contains(mediaType)
    }
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
    @State private var presentedRef: MediaRef?
    @State private var presentedDiaryEntry: PresentedDiaryEntry?
    @State private var presentedMediaDiary: PresentedMediaDiary?
    @State private var isPosterPickerPresented = false
    @State private var isBackdropPickerPresented = false
    @State private var isLogPresented = false
    @State private var progressUpdateDetail: MediaDetail?
    @State private var isQuickActionAlertPresented = false
    @State private var isLikeAlertPresented = false
    @State private var showsTitleLogo = true
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var edgeDragOffset: CGFloat = 0

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
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

            if progressUpdateDetail == nil {
                MediaDetailBottomBar(selectedTab: selectedTab, onSelectTab: navigateToTab)
                    .padding(.horizontal, 18)
                    .padding(.bottom, 8)
                    .frame(maxHeight: .infinity, alignment: .bottom)
            }
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
        .overlay {
            if let detail = progressUpdateDetail {
                ProgressUpdateSheet(
                    detail: detail,
                    progress: currentProgress(detail),
                    isSaving: viewModel.isSavingProgress,
                    errorMessage: viewModel.progressErrorMessage,
                    onSave: { request in
                        await viewModel.saveProgress(request, for: detail)
                    },
                    onDismiss: {
                        progressUpdateDetail = nil
                    },
                    onLogFinished: {
                        progressUpdateDetail = nil
                        isLogPresented = true
                    }
                )
            }
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: TopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(TopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
        .sheet(item: $presentedSheet) { sheet in
            switch sheet {
            case .posterMenu:
                PosterMenuSheet(
                    posterLabel: canCustomizeBackdrop(viewModel.detail) ? "Customize Poster" : "Customize Cover",
                    showsBackdropOption: canCustomizeBackdrop(viewModel.detail),
                    onAddToList: {
                        presentedSheet = .addToList
                    },
                    onCustomizePoster: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isPosterPickerPresented = true
                        }
                    },
                    onCustomizeBackdrop: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isBackdropPickerPresented = true
                        }
                    }
                )
                .presentationDetents([.height(canCustomizeBackdrop(viewModel.detail) ? 284 : 216)])
                .presentationDragIndicator(.visible)
            case .bookGameActions:
                if let detail = viewModel.detail {
                    BookGameActionSheet(
                        mediaType: detail.ref.mediaType,
                        isInProgress: currentStatus(detail) == "In progress",
                        isSaving: viewModel.isSavingQuickAction,
                        errorMessage: viewModel.quickActionErrorMessage,
                        onAction: { action in
                            await performQuickAction(action, for: detail, dismissSheet: true)
                        },
                        onUpdateProgress: {
                            openProgressUpdate(for: detail)
                        },
                        onLog: {
                            presentedSheet = nil
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                                isLogPresented = true
                            }
                        }
                    )
                    .presentationDetents([.height(224)])
                    .presentationDragIndicator(.visible)
                }
            case .addToList:
                if let detail = viewModel.detail {
                    AddToListSheet(
                        ref: detail.ref,
                        listRepository: listRepository,
                        onUnauthorized: onUnauthorized
                    )
                }
            }
        }
        .alert("Tracking Update Failed", isPresented: $isQuickActionAlertPresented) {
            Button("OK") {
                viewModel.quickActionErrorMessage = nil
            }
        } message: {
            Text(viewModel.quickActionErrorMessage ?? "")
        }
        .alert("Like Update Failed", isPresented: $isLikeAlertPresented) {
            Button("OK") {
                viewModel.likeErrorMessage = nil
            }
        } message: {
            Text(viewModel.likeErrorMessage ?? "")
        }
        .fullScreenCover(isPresented: $isBackdropPickerPresented) {
            if let detail = viewModel.detail {
                BackdropPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                ) { response in
                    viewModel.applyBackdropSave(response)
                    presentedSheet = nil
                    isBackdropPickerPresented = false
                }
            }
        }
        .fullScreenCover(isPresented: $isLogPresented) {
            if let detail = viewModel.detail {
                MediaLogView(
                    detail: detail,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    onUnauthorized: onUnauthorized
                ) {
                    Task {
                        await viewModel.load()
                    }
                }
            }
        }
        .fullScreenCover(isPresented: $isPosterPickerPresented) {
            if let detail = viewModel.detail {
                PosterPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    title: isBook(detail) ? "Customize Cover" : "Customize Poster",
                    showsLanguageFilter: !isBook(detail),
                    contentMode: isBook(detail) ? .fit : .fill,
                    onUnauthorized: onUnauthorized
                ) { response in
                    viewModel.applyPosterSave(response)
                    presentedSheet = nil
                    isPosterPickerPresented = false
                }
            }
        }
        .fullScreenCover(item: $presentedRef) { ref in
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
        .fullScreenCover(item: $presentedDiaryEntry) { entry in
            DiaryLogDetailNavigationCover(
                entryId: entry.id,
                diaryRepository: diaryRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedMediaDiary) { diary in
            if let itemId = diary.detail.ref.itemId {
                MediaDiaryView(
                    title: diary.title,
                    itemId: itemId,
                    posterURL: diary.detail.displayPosterURL,
                    posterOrientation: diary.detail.posterOrientation,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .task {
            if viewModel.detail == nil {
                await viewModel.load()
            }
        }
        .onChange(of: viewModel.detail?.id) {
            showsTitleLogo = true
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
            if viewModel.detail != nil {
                CircleIconButton(systemName: "ellipsis", label: "More") {
                    if canCustomizePoster(viewModel.detail) {
                        presentedSheet = .posterMenu
                    } else {
                        presentedSheet = .addToList
                    }
                }
            }
        }
    }

    private func canCustomizePoster(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsPoster(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func canCustomizeBackdrop(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsBackdrop(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func isBook(_ detail: MediaDetail) -> Bool {
        detail.ref.mediaType == "book"
    }

    private func usesBookGameActions(_ detail: MediaDetail) -> Bool {
        ["book", "game"].contains(detail.ref.mediaType)
    }

    private func trackAction(for detail: MediaDetail) {
        if usesBookGameActions(detail) {
            presentedSheet = .bookGameActions
        } else {
            isLogPresented = true
        }
    }

    private func eyeAction(for detail: MediaDetail) {
        guard usesBookGameActions(detail) else { return }
        Task {
            await performQuickAction(.finished, for: detail, dismissSheet: false)
        }
    }

    private func performQuickAction(_ action: MediaDetailQuickAction, for detail: MediaDetail, dismissSheet: Bool) async {
        if await viewModel.performQuickAction(action, for: detail) {
            if dismissSheet {
                presentedSheet = nil
            }
            await viewModel.load()
        } else if !dismissSheet {
            isQuickActionAlertPresented = true
        }
    }

    private func likeAction(for detail: MediaDetail) {
        Task {
            let succeeded = await viewModel.toggleMediaLike(for: detail)
            if !succeeded {
                isLikeAlertPresented = true
            }
        }
    }

    private func openProgressUpdate(for detail: MediaDetail) {
        viewModel.progressErrorMessage = nil
        presentedSheet = nil
        progressUpdateDetail = detail
    }

    private func navigateToTab(_ tab: AppTab) {
        dismiss()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            onSelectTab(tab)
        }
    }

    private func isShowingTitleLogo(_ detail: MediaDetail) -> Bool {
        showsTitleLogo && supportsTitleLogo(detail)
    }

    private func hero(_ detail: MediaDetail) -> some View {
        ZStack(alignment: .top) {
            HeroArtwork(detail: detail)
                .frame(height: heroHeight(for: detail))

            if let backdropURL = backdropURLString(for: detail) {
                BackdropArtwork(urlString: backdropURL)
                    .frame(height: topSafeAreaInset + MediaDetailLayout.backdropHeight)
            }

            heroHeader(detail)
            .padding(.horizontal, 14)
            .padding(.bottom, 18)
            .padding(.top, topSafeAreaInset + heroPosterTopOffset(for: detail))
            .frame(minHeight: heroHeight(for: detail), alignment: .top)
        }
    }

    @ViewBuilder
    private func heroHeader(_ detail: MediaDetail) -> some View {
        if backdropURLString(for: detail) != nil {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 11) {
                    MediaTitleDisplay(
                        detail: detail,
                        title: displayTitle(detail),
                        showsLogo: $showsTitleLogo,
                        font: .system(size: 32, weight: .black),
                        lineLimit: 3,
                        minimumScaleFactor: 0.72,
                        maxLogoHeight: 44
                    )

                    if let byline = byline(detail) {
                        Text(byline)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.62))
                            .lineLimit(2)
                            .multilineTextAlignment(isShowingTitleLogo(detail) ? .center : .leading)
                            .frame(
                                maxWidth: .infinity,
                                alignment: isShowingTitleLogo(detail) ? .center : .leading
                            )
                    }

                    genreChips(detail, wrapsAfterThird: true)
                    RatingChipRow(chips: ratingChips(detail), stacked: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 10)

                VStack(spacing: 14) {
                    MediaArtwork(
                        url: detail.displayPosterURL,
                        title: detail.title,
                        slot: .hero,
                        mediaType: detail.ref.mediaType,
                        orientation: detail.posterOrientation
                    )
                        .shadow(color: .black.opacity(0.48), radius: 22, y: 12)

                    ActionRail(
                        isTracked: currentStatus(detail) != nil,
                        isLiked: detail.userState?.hasLiked ?? false,
                        isHorizontal: true,
                        trackLabel: usesBookGameActions(detail) ? "Track" : nil,
                        eyeLabel: usesBookGameActions(detail) ? bookGameCopy(for: detail.ref.mediaType).finished : nil,
                        isEyeLoading: usesBookGameActions(detail) && viewModel.isSavingQuickAction,
                        isLikeLoading: viewModel.isSavingLike,
                        onTrack: { trackAction(for: detail) },
                        onLike: { likeAction(for: detail) },
                        onEye: { eyeAction(for: detail) }
                    )
                }
                .frame(maxWidth: .infinity, alignment: .center)
            }
        } else {
            VStack(spacing: 0) {
                MediaArtwork(
                    url: detail.displayPosterURL,
                    title: detail.title,
                    slot: .hero,
                    mediaType: detail.ref.mediaType,
                    orientation: detail.posterOrientation
                )
                    .shadow(color: .black.opacity(0.48), radius: 22, y: 12)
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 16)

                HStack(alignment: .bottom, spacing: 12) {
                    VStack(alignment: .leading, spacing: 11) {
                        MediaTitleDisplay(
                            detail: detail,
                            title: displayTitle(detail),
                            showsLogo: $showsTitleLogo,
                            font: .system(size: 33, weight: .heavy),
                            lineLimit: nil,
                            minimumScaleFactor: 0.66,
                            maxLogoHeight: 48
                        )

                        if let byline = byline(detail) {
                            Text(byline)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.62))
                                .lineLimit(1)
                                .multilineTextAlignment(isShowingTitleLogo(detail) ? .center : .leading)
                                .frame(
                                    maxWidth: .infinity,
                                    alignment: isShowingTitleLogo(detail) ? .center : .leading
                                )
                        }

                        genreChips(detail, wrapsAfterThird: false)
                        RatingChipRow(chips: ratingChips(detail), stacked: false)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    ActionRail(
                        isTracked: currentStatus(detail) != nil,
                        isLiked: detail.userState?.hasLiked ?? false,
                        isHorizontal: false,
                        trackLabel: usesBookGameActions(detail) ? "Track" : nil,
                        eyeLabel: usesBookGameActions(detail) ? bookGameCopy(for: detail.ref.mediaType).finished : nil,
                        isEyeLoading: usesBookGameActions(detail) && viewModel.isSavingQuickAction,
                        isLikeLoading: viewModel.isSavingLike,
                        onTrack: { trackAction(for: detail) },
                        onLike: { likeAction(for: detail) },
                        onEye: { eyeAction(for: detail) }
                    )
                }
            }
        }
    }

    private func backdropURLString(for detail: MediaDetail) -> String? {
        return detail.customBackdropUrl ?? detail.backdropUrl
    }

    private func heroHeight(for detail: MediaDetail) -> CGFloat {
        if backdropURLString(for: detail) != nil {
            return MediaDetailLayout.heroHeight + MediaDetailLayout.backdropTopSpacing
        }
        return MediaDetailLayout.legacyHeroHeight
    }

    private func heroPosterTopOffset(for detail: MediaDetail) -> CGFloat {
        MediaDetailLayout.heroPosterTopOffset + (backdropURLString(for: detail) == nil ? 0 : MediaDetailLayout.backdropTopSpacing)
    }

    private func content(_ detail: MediaDetail) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            trackingSummarySection(detail)
            SynopsisCard(text: synopsisPreview(detail))
            SpineRatingDistributionSection(community: detail.community)

            if detail.ref.mediaType == "tv" {
                seasonsSection(detail)
                CreditSection(title: creditTitle(detail), people: primaryCredits(detail))
            } else {
                CreditSection(title: creditTitle(detail), people: primaryCredits(detail))
                seasonsSection(detail)
            }

            MediaFactsSection(rows: detailRows(detail))
            EpisodesSection(episodes: detail.episodes ?? [])
            ReviewsSection(reviews: viewModel.reviews, isLoading: viewModel.isLoadingReviews, error: viewModel.reviewsErrorMessage)
            RecommendationsSection(sections: relatedSections(detail)) { item in
                presentedRef = item.ref
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
    }

    @ViewBuilder
    private func trackingSummarySection(_ detail: MediaDetail) -> some View {
        TrackingSummarySection(
            detail: detail,
            tracking: viewModel.tracking,
            userState: detail.userState,
            onOpenDiaryEntry: {
                Task {
                    await openTrackingDiary(for: detail)
                }
            },
            onUpdateProgress: {
                openProgressUpdate(for: detail)
            }
        )
    }

    private func openTrackingDiary(for detail: MediaDetail) async {
        if isMultipleLogMedia(detail), detail.ref.itemId != nil {
            presentedMediaDiary = PresentedMediaDiary(detail: detail)
            return
        }

        if let entryId = detail.userState?.diaryEntryId {
            presentedDiaryEntry = PresentedDiaryEntry(id: entryId)
            return
        }

        do {
            if let entry = try await diaryRepository.list().first(where: { matches($0.media.ref, detail.ref) }) {
                presentedDiaryEntry = PresentedDiaryEntry(id: entry.id)
            }
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func matches(_ lhs: MediaRef, _ rhs: MediaRef) -> Bool {
        lhs.source == rhs.source
            && lhs.mediaType == rhs.mediaType
            && lhs.mediaId == rhs.mediaId
            && lhs.seasonNumber == rhs.seasonNumber
            && lhs.episodeNumber == rhs.episodeNumber
    }

    private func isMultipleLogMedia(_ detail: MediaDetail) -> Bool {
        (detail.userState?.diaryCount ?? 0) > 1
    }

    private func seasonsSection(_ detail: MediaDetail) -> some View {
        SeasonsSection(seasons: detail.seasons ?? []) { season in
            presentedRef = MediaRef(
                itemId: nil,
                source: detail.ref.source,
                mediaType: "season",
                mediaId: detail.ref.mediaId,
                seasonNumber: season.seasonNumber,
                episodeNumber: nil
            )
        }
    }

    private func genreChips(_ detail: MediaDetail, wrapsAfterThird: Bool) -> some View {
        let chips = primaryChips(detail)

        if !wrapsAfterThird {
            return AnyView(
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(chips.prefix(6)), id: \.self) { chip in
                            genreChip(chip)
                        }
                    }
                }
                .mask(alignment: .trailing) {
                    LinearGradient(
                        stops: [
                            .init(color: .black, location: 0),
                            .init(color: .black, location: 0.88),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                }
            )
        }

        return AnyView(
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    ForEach(Array(chips.prefix(3)), id: \.self) { chip in
                        genreChip(chip)
                    }
                }

                if chips.count > 3 {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(Array(chips.dropFirst(3)), id: \.self) { chip in
                                genreChip(chip)
                            }
                        }
                    }
                    .mask(alignment: .trailing) {
                        LinearGradient(
                            stops: [
                                .init(color: .black, location: 0),
                                .init(color: .black, location: 0.88),
                                .init(color: .clear, location: 1),
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    }
                }
            }
        )
    }

    private func genreChip(_ chip: String) -> some View {
        Text(chip)
            .font(.system(size: 11, weight: .bold))
            .foregroundStyle(.white.opacity(0.82))
            .lineLimit(1)
            .padding(.horizontal, 11)
            .frame(height: MediaDetailLayout.genrePillHeight)
            .background(.white.opacity(0.12), in: Capsule())
    }

    private func primaryChips(_ detail: MediaDetail) -> [String] {
        var chips = [mediaTypeChipLabel(detail.ref.mediaType), year(detail)].compactMap { $0 }
        if let seasonLabel = seasonChipLabel(detail) {
            chips.append(seasonLabel)
        }
        if ["movie", "tv"].contains(detail.ref.mediaType), let runtime = detailString(detail, "runtime"), !runtime.isEmpty {
            chips.append(runtime)
        }
        if let contentRating = contentRating(detail) {
            chips.append(contentRating)
        }
        if ["tv", "anime"].contains(detail.ref.mediaType), let episodes = detailString(detail, "episodes") {
            chips.append("\(episodes) episodes")
        }
        if detail.ref.mediaType == "book", let pages = detailString(detail, "number_of_pages") ?? detailString(detail, "pages") {
            chips.append("\(pages) pages")
        }
        if detail.ref.mediaType == "anime", let format = detailString(detail, "format") {
            chips.append(format)
        }
        if detail.ref.mediaType == "game", let platform = detailArray(detail, "platforms").first {
            chips.append(platform)
        }
        chips += detailArray(detail, "genres")

        var seen = Set<String>()
        let uniqueChips = chips.filter { chip in
            seen.insert(chip.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()).inserted
        }
        return uniqueChips
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
        case "season":
            "TV"
        default:
            mediaType.capitalized
        }
    }

    private func displayTitle(_ detail: MediaDetail) -> String {
        guard let seasonNumber = detail.ref.seasonNumber, detail.ref.mediaType == "season" else { return detail.title }
        return "\(detail.title) S\(seasonNumber)"
    }

    private func seasonChipLabel(_ detail: MediaDetail) -> String? {
        guard let seasonNumber = detail.ref.seasonNumber, detail.ref.mediaType == "season" else { return nil }
        return "Season \(seasonNumber)"
    }

    private func currentStatus(_ detail: MediaDetail) -> String? {
        viewModel.tracking?.status ?? detail.userState?.status
    }

    private func currentRating(_ detail: MediaDetail) -> String? {
        viewModel.tracking?.rating ?? detail.userState?.rating
    }

    private func currentProgress(_ detail: MediaDetail) -> ProgressState? {
        viewModel.tracking?.progress ?? detail.userState?.progress
    }

    private func ratingChips(_ detail: MediaDetail) -> [RatingChip] {
        var chips: [RatingChip] = []
        if let rating = detail.community?.averageRating, !rating.isEmpty {
            chips.append(RatingChip(
                source: "SP",
                value: "\(rating.starRatingValue)/5",
                assetName: nil,
                voteCount: detail.community?.ratingCount,
                voteCountLabel: "ratings"
            ))
        }
        for rating in sortedExternalRatings(detail.externalRatings ?? []) where !rating.value.isEmpty {
            if rating.source.lowercased() == "spine" {
                continue
            }
            if ["movie", "tv", "season"].contains(detail.ref.mediaType), rating.source.lowercased() == "tmdb" {
                continue
            }
            chips.append(RatingChip(
                source: rating.source.ratingAbbreviation,
                value: rating.displayValue,
                assetName: rating.ratingAssetName,
                voteCount: rating.voteCount,
                voteCountLabel: rating.source.ratingCountLabel
            ))
        }
        if let rating = currentRating(detail), !rating.isEmpty {
            chips.append(RatingChip(source: "You", value: rating.starRatingLabel, assetName: nil))
        }
        return chips
    }

    private func sortedExternalRatings(_ ratings: [ExternalRating]) -> [ExternalRating] {
        ratings.enumerated().sorted { lhs, rhs in
            let lhsOrder = externalRatingOrder(lhs.element.source)
            let rhsOrder = externalRatingOrder(rhs.element.source)
            return lhsOrder == rhsOrder ? lhs.offset < rhs.offset : lhsOrder < rhsOrder
        }.map {
            $0.element
        }
    }

    private func externalRatingOrder(_ source: String) -> Int {
        switch source.lowercased() {
        case "letterboxd": 0
        case "rotten tomatoes": 1
        case "imdb": 2
        default: 3
        }
    }

    private func byline(_ detail: MediaDetail) -> String? {
        if let author = authors(detail).first {
            return author
        }
        for key in ["director", "creator", "developer", "publisher"] {
            if let value = detailString(detail, key) {
                return value
            }
        }
        if let person = detailArray(detail, "people").first {
            return person
        }
        return detail.subtitle
    }

    private func synopsisPreview(_ detail: MediaDetail) -> String {
        detail.displaySynopsis ?? "No synopsis available yet."
    }

    private func detailRows(_ detail: MediaDetail) -> [DetailFactRow] {
        let mediaType = detail.ref.mediaType
        var rows: [DetailFactRow] = [
            DetailFactRow(label: "Status", value: detailString(detail, "status")),
            DetailFactRow(label: "Format", value: detailString(detail, "format")),
        ]

        switch mediaType {
        case "movie":
            rows += [
                DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Certification", value: detailString(detail, "rating")),
                DetailFactRow(label: "Director", value: detailString(detail, "director")),
                DetailFactRow(label: "Box Office", value: moneyString(detail, "revenue")),
            ]
        case "tv", "season":
            rows += [
                DetailFactRow(label: "First Aired", value: formattedDate(detailString(detail, "first_air_date") ?? detail.releaseDate)),
                DetailFactRow(label: "Last Aired", value: formattedDate(detailString(detail, "last_air_date"))),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Certification", value: detailString(detail, "rating")),
                DetailFactRow(label: "Seasons", value: detailString(detail, "seasons")),
                DetailFactRow(label: "Episodes", value: detailString(detail, "episodes")),
                DetailFactRow(label: "Creator", value: detailString(detail, "creator")),
            ]
        case "anime":
            rows += [
                DetailFactRow(label: "Episodes", value: detailString(detail, "episodes")),
                DetailFactRow(label: "Aired", value: detailString(detail, "season")),
                DetailFactRow(label: "Broadcast", value: detailString(detail, "broadcast")),
                DetailFactRow(label: "Source", value: detailString(detail, "source")),
            ]
        case "manga":
            rows += [
                DetailFactRow(label: "Chapters", value: detailString(detail, "number_of_chapters")),
                DetailFactRow(label: "Latest Chapter", value: detailString(detail, "latest_chapter_translated")),
                DetailFactRow(label: "Year", value: detailString(detail, "year")),
            ]
        case "game":
            rows += [
                DetailFactRow(label: "Developer", value: detailString(detail, "developer")),
                DetailFactRow(label: "Themes", value: detailArray(detail, "themes").joinedOrNil),
                DetailFactRow(label: "Time to Beat", value: timeToBeatString(detail)),
            ]
        case "comic":
            rows += [
                DetailFactRow(label: "Publisher", value: detailString(detail, "publisher")),
                DetailFactRow(label: "Issues", value: detailString(detail, "issues_count")),
                DetailFactRow(label: "Last Issue", value: lastIssueString(detail)),
            ]
        case "book":
            rows += [
                DetailFactRow(label: "Pages", value: detailString(detail, "number_of_pages") ?? detailString(detail, "pages")),
                DetailFactRow(label: "Publish Date", value: formattedDate(detailString(detail, "publish_date") ?? detailString(detail, "published_date") ?? detailString(detail, "release_date"))),
                DetailFactRow(label: "Physical Format", value: detailString(detail, "physical_format")),
            ]
        default:
            rows.append(DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)))
        }

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
            if mediaType == "book", key == "authors" {
                continue
            }
            let values = detailArray(detail, key)
            if !values.isEmpty {
                rows.append(DetailFactRow(label: label, value: values.joined(separator: ", ")))
            }
        }
        return rows
        .filter { $0.value?.isEmpty == false }
    }

    private func creditTitle(_ detail: MediaDetail) -> String {
        switch detail.ref.mediaType {
        case "book":
            "Authors"
        case "comic":
            "People"
        default:
            "Cast & Crew"
        }
    }

    private func primaryCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        if detail.ref.mediaType == "book" {
            return authors(detail).map { CreditDisplay(name: $0, subtitle: "Author", imageUrl: nil) }
        }
        let credits = ((detail.cast ?? []) + (detail.crew ?? [])).map {
            CreditDisplay(name: $0.name, subtitle: $0.character ?? $0.role, imageUrl: $0.imageUrl)
        }
        if credits.isEmpty, detail.ref.mediaType == "comic" {
            return detailArray(detail, "people").map { CreditDisplay(name: $0, subtitle: nil, imageUrl: nil) }
        }
        return credits
    }

    private func year(_ detail: MediaDetail) -> String? {
        if detail.ref.mediaType == "tv" {
            let start = detailString(detail, "first_air_date") ?? detail.releaseDate
            let end = detailString(detail, "last_air_date")
            guard let startYear = start?.yearPrefix else { return nil }
            let status = detailString(detail, "status")?.lowercased()
            if let status, ["ended", "canceled", "cancelled"].contains(status), let endYear = end?.yearPrefix, endYear != startYear {
                return "\(startYear)-\(endYear)"
            }
            return startYear
        }
        let value = detail.releaseDate ?? detailString(detail, "release_date") ?? detailString(detail, "first_air_date") ?? detailString(detail, "start_date") ?? detailString(detail, "publish_date")
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
        formattedDate(releaseDate(detail))
    }

    private func formattedDate(_ raw: String?) -> String? {
        guard let raw else { return nil }
        return Self.longDateFormatter.string(from: raw) ?? raw
    }

    private func moneyString(_ detail: MediaDetail, _ key: String) -> String? {
        guard let value = detail.details?[key]?.numberValue, value > 0 else { return detailString(detail, key) }
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        formatter.maximumFractionDigits = 0
        formatter.usesGroupingSeparator = true
        return formatter.string(from: NSNumber(value: value))
    }

    private func timeToBeatString(_ detail: MediaDetail) -> String? {
        guard let object = detail.details?["time_to_beat"]?.objectValue else { return detailString(detail, "time_to_beat") }
        let keys = [
            ("normally", "Main"),
            ("hastily", "Rush"),
            ("completely", "Complete"),
        ]
        let parts = keys.compactMap { key, label -> String? in
            guard let value = object[key]?.numberValue, value > 0 else { return nil }
            return "\(label) \(Int(value / 3600))h"
        }
        return parts.joinedOrNil
    }

    private func lastIssueString(_ detail: MediaDetail) -> String? {
        let name = detailString(detail, "last_issue_name")
        let number = detailString(detail, "last_issue_number")
        return [name, number.map { "#\($0)" }].compactMap { $0 }.joined(separator: " ").nilIfEmpty
    }

    private static let longDateFormatter: LongDateFormatter = LongDateFormatter()

    private func detailString(_ detail: MediaDetail, _ key: String) -> String? {
        detail.details?[key]?.displayString
    }

    private func detailArray(_ detail: MediaDetail, _ key: String) -> [String] {
        detail.details?[key]?.displayStrings ?? []
    }

    private func relatedSections(_ detail: MediaDetail) -> [RelatedMediaSection] {
        let sections: [RelatedMediaSection]
        if let relatedSections = detail.relatedSections, !relatedSections.isEmpty {
            sections = relatedSections
        } else if let related = detail.related {
            sections = related.compactMap { key, value in
                guard key != "seasons", key != "all_related",
                      let values = value.arrayValue else { return nil }
                let items = values.compactMap { rawRelatedSummary($0, parent: detail) }
                guard !items.isEmpty else { return nil }
                let id = detail.ref.mediaType == "movie" && key != "recommendations" ? "collection" : key
                let title = key.replacingOccurrences(of: "_", with: " ").capitalized
                return RelatedMediaSection(id: id, title: title, items: items)
            }
        } else {
            return []
        }
        return sections.filter { $0.id != "all_related" }
    }

    private func rawRelatedSummary(_ value: JSONValue, parent: MediaDetail) -> MediaSummary? {
        guard let object = value.objectValue else { return nil }
        let source = object["source"]?.displayString ?? parent.ref.source
        let mediaType = object["media_type"]?.displayString ?? parent.ref.mediaType
        guard let mediaId = object["media_id"]?.displayString ?? object["id"]?.displayString,
              let title = object["title"]?.displayString ?? object["name"]?.displayString else { return nil }
        return MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: source,
                mediaType: mediaType,
                mediaId: mediaId,
                seasonNumber: object["season_number"]?.intValue,
                episodeNumber: object["episode_number"]?.intValue
            ),
            title: title,
            subtitle: object["year"]?.displayString,
            overview: object["overview"]?.displayString,
            imageUrl: object["image_url"]?.displayString ?? object["image"]?.displayString,
            posterUrl: object["poster_url"]?.displayString,
            customPosterUrl: object["custom_poster_url"]?.displayString,
            posterOrientation: PosterOrientation(rawValue: object["poster_orientation"]?.displayString ?? "") ?? .unknown,
            posterAccentColor: object["poster_accent_color"]?.displayString,
            releaseDate: object["release_date"]?.displayString ?? object["first_air_date"]?.displayString,
            defaultSource: source,
            userState: nil
        )
    }
}

private enum MediaDetailLayout {
    static let heroPosterWidth: CGFloat = 191
    static let heroHeight: CGFloat = 455
    static let legacyHeroHeight: CGFloat = 535
    static let heroPosterTopOffset: CGFloat = 108
    static let backdropTopSpacing: CGFloat = 137.5
    static let backdropHeight: CGFloat = 352.34375
    static let genrePillHeight: CGFloat = 31
    static let ratingBadgeSize: CGFloat = 24
    static let ratingPillVerticalPadding: CGFloat = 6
    static var ratingPillHeight: CGFloat { ratingBadgeSize + ratingPillVerticalPadding * 2 }
    static let recommendationPosterSize = CGSize(width: 100, height: 150)
    static let recommendationCardHeight: CGFloat = 190
    static let seasonPosterSize = CGSize(width: 90, height: 135)
    static let castImageSize: CGFloat = 84
    static let castCardWidth: CGFloat = 126
}

private enum SpinePalette {
    static let pageBackground = Color(red: 0.07, green: 0.07, blue: 0.065)
}

struct SpinePageBackground: View {
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

private func bookGameCopy(for mediaType: String) -> (currently: String, finished: String, stopped: String) {
    mediaType == "book"
        ? ("Currently Reading", "Finished Reading", "Stopped Reading")
        : ("Currently Playing", "Finished Playing", "Stopped Playing")
}

private struct PosterMenuSheet: View {
    let posterLabel: String
    let showsBackdropOption: Bool
    let onAddToList: () -> Void
    let onCustomizePoster: () -> Void
    let onCustomizeBackdrop: () -> Void

    var body: some View {
        VStack(spacing: 10) {
            Capsule()
                .fill(.secondary.opacity(0.35))
                .frame(width: 38, height: 5)
                .padding(.top, 8)

            Button(action: onAddToList) {
                Label("Add to List", systemImage: "list.bullet.rectangle")
                    .font(.system(size: 17, weight: .semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .frame(height: 54)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)

            Button(action: onCustomizePoster) {
                Label(posterLabel, systemImage: "photo.on.rectangle.angled")
                    .font(.system(size: 17, weight: .semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .frame(height: 54)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)

            if showsBackdropOption {
                Button(action: onCustomizeBackdrop) {
                    Label("Customize Backdrop", systemImage: "photo.on.rectangle")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }
        }
        .presentationBackground(.regularMaterial)
    }
}

private struct BookGameActionSheet: View {
    let mediaType: String
    let isInProgress: Bool
    let isSaving: Bool
    let errorMessage: String?
    let onAction: (MediaDetailQuickAction) async -> Void
    let onUpdateProgress: () -> Void
    let onLog: () -> Void

    var body: some View {
        let copy = bookGameCopy(for: mediaType)

        VStack(spacing: 16) {
            HStack(spacing: 12) {
                if isInProgress {
                    progressButton
                } else {
                    actionButton(title: copy.currently, systemName: "play.fill", action: .currently)
                }
                actionButton(title: copy.finished, systemName: "checkmark", action: .finished)
                actionButton(title: copy.stopped, systemName: "xmark", action: .stopped)
                logButton
            }
            .padding(.horizontal, 16)
            .padding(.top, 36)

            if let errorMessage {
                Text(errorMessage)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.red.opacity(0.92))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
            }
        }
        .presentationBackground(.regularMaterial)
    }

    private func actionButton(title: String, systemName: String, action: MediaDetailQuickAction) -> some View {
        Button {
            Task {
                await onAction(action)
            }
        } label: {
            actionLabel(title: title, systemName: systemName)
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
    }

    private var progressButton: some View {
        Button(action: onUpdateProgress) {
            actionLabel(title: "Update Progress", systemName: "slider.horizontal.3")
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
    }

    private var logButton: some View {
        Button(action: onLog) {
            actionLabel(title: "Log", systemName: "square.and.pencil")
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
    }

    private func actionLabel(title: String, systemName: String) -> some View {
        VStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(.white.opacity(0.055))
                    .overlay {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(.white.opacity(0.13), lineWidth: 1.25)
                    }
                if isSaving {
                    ProgressView()
                        .tint(.white)
                } else {
                    Image(systemName: systemName)
                        .font(.system(size: 30, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.94))
                }
            }
            .frame(height: 86)
            .shadow(color: .black.opacity(0.18), radius: 10, y: 6)

            Text(title)
                .font(.system(size: 14, weight: .heavy))
                .foregroundStyle(.white.opacity(0.92))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.82)
                .frame(height: 36, alignment: .top)
        }
        .frame(maxWidth: .infinity)
    }
}

@MainActor
@Observable
private final class AddToListViewModel {
    var lists: [CustomListSummary] = []
    var ref: MediaRef
    var isLoading = false
    var loadingListID: Int?
    var errorMessage: String?

    private let listRepository: ListRepository
    private let onUnauthorized: () -> Void

    init(ref: MediaRef, listRepository: ListRepository, onUnauthorized: @escaping () -> Void) {
        self.ref = ref
        self.listRepository = listRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            lists = try await listRepository.list(membershipFor: ref)
        } catch {
            handle(error)
        }
    }

    func toggle(_ list: CustomListSummary) async {
        guard loadingListID == nil else { return }
        loadingListID = list.id
        errorMessage = nil
        defer { loadingListID = nil }

        do {
            if list.hasItem == true {
                guard let itemId = ref.itemId else {
                    await load()
                    return
                }
                try await listRepository.removeItem(listId: list.id, itemId: itemId)
            } else {
                let item = try await listRepository.addItem(listId: list.id, ref: ref)
                ref = item.ref
            }
            await load()
        } catch {
            handle(error)
        }
    }

    func createAndAdd(name: String) async -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        loadingListID = -1
        errorMessage = nil
        defer { loadingListID = nil }

        do {
            let list = try await listRepository.create(CustomListWriteRequest(
                name: trimmed,
                description: "",
                visibility: "private",
                isRanked: false
            ))
            let item = try await listRepository.addItem(listId: list.id, ref: ref)
            ref = item.ref
            await load()
            return true
        } catch {
            handle(error)
            return false
        }
    }

    private func handle(_ error: Error) {
        errorMessage = error.localizedDescription
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

private struct AddToListSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: AddToListViewModel
    @State private var searchText = ""
    @State private var newListName = ""
    @State private var isCreating = false

    init(ref: MediaRef, listRepository: ListRepository, onUnauthorized: @escaping () -> Void) {
        _viewModel = State(initialValue: AddToListViewModel(
            ref: ref,
            listRepository: listRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            List {
                if viewModel.isLoading {
                    HStack {
                        Spacer()
                        ProgressView()
                        Spacer()
                    }
                } else if let error = viewModel.errorMessage {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                } else if filteredLists.isEmpty {
                    ContentUnavailableView("No Lists", systemImage: "list.bullet.rectangle")
                        .listRowBackground(Color.clear)
                } else {
                    ForEach(filteredLists) { list in
                        Button {
                            Task {
                                await viewModel.toggle(list)
                            }
                        } label: {
                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(list.name)
                                        .font(.system(size: 16, weight: .semibold))
                                    Text("\(list.itemsCount.formatted()) items")
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if viewModel.loadingListID == list.id {
                                    ProgressView()
                                } else if list.hasItem == true {
                                    Image(systemName: "checkmark")
                                        .font(.system(size: 16, weight: .bold))
                                        .foregroundStyle(.green)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                        .disabled(viewModel.loadingListID != nil)
                    }
                }

                Section {
                    if isCreating {
                        HStack {
                            TextField("New list name", text: $newListName)
                            Button("Create") {
                                Task {
                                    if await viewModel.createAndAdd(name: newListName) {
                                        newListName = ""
                                        isCreating = false
                                    }
                                }
                            }
                            .disabled(newListName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.loadingListID != nil)
                        }
                    } else {
                        Button {
                            isCreating = true
                        } label: {
                            Label("Create new list...", systemImage: "plus")
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .navigationTitle("Add to List")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $searchText, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search lists")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .task {
                if viewModel.lists.isEmpty {
                    await viewModel.load()
                }
            }
        }
    }

    private var filteredLists: [CustomListSummary] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return viewModel.lists }
        return viewModel.lists.filter { $0.name.localizedCaseInsensitiveContains(query) }
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
        return URL(string: detail.customBackdropUrl ?? detail.backdropUrl ?? detail.displayPosterURL ?? "")
    }

    private var usesPosterFallback: Bool {
        detail.backdropUrl == nil && detail.customBackdropUrl == nil && detail.displayPosterURL != nil
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

private struct BackdropArtwork: View {
    let urlString: String

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                AsyncImage(url: URL(string: urlString)) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        Color.clear
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.42), location: 0),
                        .init(color: .black.opacity(0.18), location: 0.36),
                        .init(color: .clear, location: 0.72),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .mask(
                LinearGradient(
                    stops: [
                        .init(color: .white, location: 0),
                        .init(color: .white, location: 0.52),
                        .init(color: .white.opacity(0.35), location: 0.78),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
        }
        .clipped()
    }
}

private struct ActionRail: View {
    private static let buttonSize: CGFloat = 44
    private static let buttonSpacing: CGFloat = 10

    let isTracked: Bool
    let isLiked: Bool
    let isHorizontal: Bool
    var trackLabel: String?
    var eyeLabel: String?
    var isEyeLoading = false
    var isLikeLoading = false
    let onTrack: () -> Void
    let onLike: () -> Void
    var onEye: () -> Void = {}

    var body: some View {
        if isHorizontal {
            HStack(spacing: Self.buttonSpacing) {
                railButton(
                    systemName: "plus",
                    label: trackLabel ?? (isTracked ? "Edit tracking" : "Log"),
                    filled: true,
                    usesLargePlus: true,
                    action: onTrack
                )
                railButton(
                    systemName: isLiked ? "heart.fill" : "heart",
                    label: isLiked ? "Unlike" : "Like",
                    filled: true,
                    usesLargePlus: false,
                    isLoading: isLikeLoading,
                    action: onLike
                )
                railButton(
                    systemName: "eye",
                    label: eyeLabel ?? "Mark as watched",
                    filled: true,
                    usesLargePlus: false,
                    isLoading: isEyeLoading,
                    action: onEye
                )
            }
        } else {
            VStack(spacing: Self.buttonSpacing) {
                railButton(
                    systemName: isLiked ? "heart.fill" : "heart",
                    label: isLiked ? "Unlike" : "Like",
                    filled: true,
                    usesLargePlus: false,
                    isLoading: isLikeLoading,
                    action: onLike
                )
                railButton(
                    systemName: "eye",
                    label: eyeLabel ?? "Mark as watched",
                    filled: true,
                    usesLargePlus: false,
                    isLoading: isEyeLoading,
                    action: onEye
                )
                railButton(
                    systemName: "plus",
                    label: trackLabel ?? (isTracked ? "Edit tracking" : "Log"),
                    filled: true,
                    usesLargePlus: false,
                    action: onTrack
                )
            }
        }
    }

    private func railButton(
        systemName: String,
        label: String,
        filled: Bool,
        usesLargePlus: Bool,
        isLoading: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(filled ? .white.opacity(0.92) : .black.opacity(0.34))
                if isLoading {
                    ProgressView()
                        .tint(.black)
                } else {
                    Image(systemName: systemName)
                        .font(.system(size: usesLargePlus ? 25 : 17, weight: usesLargePlus ? .semibold : .bold))
                        .foregroundStyle(railIconColor(systemName: systemName, filled: filled))
                }
            }
            .frame(width: Self.buttonSize, height: Self.buttonSize)
            .shadow(color: usesLargePlus ? .white.opacity(0.22) : .clear, radius: 10, y: 2)
        }
        .buttonStyle(.plain)
        .disabled(isLoading)
        .accessibilityLabel(label)
    }

    private func railIconColor(systemName: String, filled: Bool) -> Color {
        guard filled else { return .white }
        if systemName == "heart.fill" { return .pink }
        return Color.black.opacity(0.82)
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
    let voteCount: Int?
    let voteCountLabel: String?

    init(source: String, value: String, assetName: String?, voteCount: Int? = nil, voteCountLabel: String? = nil) {
        self.source = source
        self.value = value
        self.assetName = assetName
        self.voteCount = voteCount
        self.voteCountLabel = voteCountLabel
    }
}

private struct RatingChipRow: View {
    let chips: [RatingChip]
    let stacked: Bool

    var body: some View {
        if !chips.isEmpty {
            if stacked {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(chips, id: \.self) { chip in
                        ratingChip(chip)
                    }
                }
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(chips, id: \.self) { chip in
                            ratingChip(chip)
                        }
                    }
                }
            }
        }
    }

    private func ratingChip(_ chip: RatingChip) -> some View {
        HStack(spacing: 6) {
            RatingSourceBadge(chip: chip)
            VStack(alignment: .leading, spacing: 1) {
                Text(chip.value)
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white)
                if let voteCount = chip.voteCount, voteCount > 0 {
                    Text("\(voteCount.formatted(.number.notation(.compactName))) \(chip.voteCountLabel ?? "votes")")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.58))
                        .lineLimit(1)
                }
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
        .background(.white.opacity(0.12), in: Capsule())
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
        case "RatingRottenTomatoesCertifiedFresh":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.045)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingHardcover":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .offset(y: 3)
                .clipped()
        case "RatingIGDB":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .scaleEffect(1.55)
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
    let onOpenDiaryEntry: () -> Void
    let onUpdateProgress: () -> Void

    var body: some View {
        if hasState {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Your Tracking")
                HStack(alignment: .top, spacing: 10) {
                    MediaArtwork(
                        url: detail.displayPosterURL,
                        title: detail.title,
                        slot: .libraryRow,
                        mediaType: detail.ref.mediaType,
                        orientation: detail.posterOrientation
                    )
                    .onTapGesture(perform: onOpenDiaryEntry)
                    .accessibilityLabel("View diary log for \(detail.title)")
                    .accessibilityAddTraits(.isButton)

                    VStack(alignment: .leading, spacing: 4) {
                        if let status {
                            Text(status)
                                .font(.system(size: 14, weight: .heavy))
                                .foregroundStyle(.white)
                                .onTapGesture {
                                    if hasMultipleLogs {
                                        onOpenDiaryEntry()
                                    }
                                }
                                .accessibilityAddTraits(hasMultipleLogs ? .isButton : [])
                            if showsUpdateProgressButton {
                                Button(action: onUpdateProgress) {
                                    Text("Update Progress")
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundStyle(.white.opacity(0.82))
                                        .padding(.horizontal, 11)
                                        .frame(height: 24)
                                        .background(.white.opacity(0.12), in: Capsule())
                                }
                                .buttonStyle(.plain)
                                .padding(.top, 3)
                            }
                        }
                        ForEach(lines, id: \.self) { line in
                            Text(line)
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(.white.opacity(0.76))
                                .onTapGesture {
                                    if line == logLine {
                                        onOpenDiaryEntry()
                                    }
                                }
                                .accessibilityAddTraits(line == logLine ? .isButton : [])
                        }
                    }

                    Spacer()
                }
            }
        }
    }

    private var status: String? { tracking?.status ?? userState?.status }
    private var hasState: Bool { status != nil || !lines.isEmpty }
    private var showsUpdateProgressButton: Bool {
        status == "In progress" && ["book", "game"].contains(detail.ref.mediaType)
    }
    private var hasMultipleLogs: Bool {
        (userState?.diaryCount ?? 0) > 1
    }
    private var logLine: String? {
        guard hasMultipleLogs, let diaryCount = userState?.diaryCount else { return nil }
        return "\(diaryCount) logs"
    }

    private var lines: [String] {
        var values: [String] = []
        if status == "In progress",
           detail.ref.mediaType != "movie",
            let progressText = (tracking?.progress ?? userState?.progress)?.detailDisplayText(preferredMode: ProgressDisplayPreferences.mode(for: detail.ref)) {
            values.append(progressText)
        }
        if let logLine {
            values.append(logLine)
        }
        if let rating = userState?.diaryRating ?? tracking?.rating ?? userState?.rating {
            values.append("Rated \(rating.starRatingLabel)")
        }
        if !hasMultipleLogs, let consumedAt = userState?.diaryConsumedAt {
            values.append("Logged \(consumedAt.shortDateLabel)")
        }
        if detail.ref.mediaType != "movie" {
            if let startDate = tracking?.startDate {
                values.append("Started \(startDate.longDateLabel)")
            }
        }
        return values
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
        .system(size: 14, weight: .semibold)
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
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.62))
                                .frame(width: 104, alignment: .leading)
                            Text(row.value ?? "")
                                .font(.system(size: 14, weight: .heavy))
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
                .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 16))
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
                        Label(average.starRatingLabel, systemImage: "star.fill")
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
            .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 16))
        }
    }

    private var buckets: [RatingDistributionBucket] {
        let rawBuckets = community?.ratingDistribution ?? []
        guard !rawBuckets.isEmpty else { return [] }
        let counts = Dictionary(grouping: rawBuckets, by: { $0.rating.starRatingStep })
            .mapValues { $0.reduce(0) { $0 + $1.count } }
        return (1...10).map { step in
            RatingDistributionBucket(rating: String.starRatingLabel(forStep: step), count: counts[step, default: 0])
        }
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
                    HStack(alignment: .top, spacing: 12) {
                        ForEach(people) { person in
                            VStack(spacing: 10) {
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
                                .frame(width: MediaDetailLayout.castImageSize, height: MediaDetailLayout.castImageSize)
                                .clipShape(Circle())

                                Text(person.name)
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundStyle(.white)
                                    .multilineTextAlignment(.center)
                                    .lineLimit(2)
                                    .minimumScaleFactor(0.86)
                                if let subtitle = person.subtitle, !subtitle.isEmpty {
                                    Text(subtitle)
                                        .font(.system(size: 13, weight: .regular))
                                        .foregroundStyle(.white.opacity(0.84))
                                        .multilineTextAlignment(.center)
                                        .lineLimit(3)
                                        .minimumScaleFactor(0.82)
                                }
                            }
                            .frame(width: MediaDetailLayout.castCardWidth, alignment: .top)
                        }
                    }
                }
            }
        }
    }
}

private struct SeasonsSection: View {
    let seasons: [SeasonSummary]
    let onSelect: (SeasonSummary) -> Void

    var body: some View {
        if !seasons.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Seasons")
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 12) {
                        ForEach(seasons) { season in
                            Button {
                                onSelect(season)
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    MediaArtwork(
                                        url: season.imageUrl,
                                        title: season.title,
                                        slot: .seasonCard,
                                        mediaType: "season"
                                    )
                                    Text(season.title)
                                        .font(.system(size: 12, weight: .heavy))
                                        .foregroundStyle(.white)
                                        .lineLimit(2)
                                    if let count = season.episodeCount {
                                        Text("\(count) episodes")
                                            .font(.system(size: 11, weight: .semibold))
                                            .foregroundStyle(.white.opacity(0.55))
                                            .lineLimit(1)
                                    }
                                }
                                .frame(width: MediaDetailLayout.seasonPosterSize.width, alignment: .topLeading)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Open \(season.title)")
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
                            if episode.imageUrl != nil {
                                MediaArtwork(
                                    url: episode.imageUrl,
                                    title: episode.title,
                                    slot: .episodeStill,
                                    mediaType: "episode"
                                )
                            }
                            VStack(alignment: .leading, spacing: 4) {
                                Text(episode.title)
                                    .font(.system(size: 14, weight: .heavy))
                                    .foregroundStyle(.white)
                                let metadata = [episode.airDate?.longDateLabel, episode.runtime, episode.rating?.oneDecimalLabel].compactMap { $0 }.joined(separator: " - ")
                                if !metadata.isEmpty {
                                    Text(metadata)
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundStyle(.white.opacity(0.56))
                                }
                                if let overview = episode.overview, !overview.isEmpty {
                                    Text(overview)
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundStyle(.white.opacity(0.7))
                                        .lineLimit(3)
                                }
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
                                    Label(rating.starRatingLabel, systemImage: "star.fill")
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
    let onSelect: (MediaSummary) -> Void

    var body: some View {
        ForEach(sections.filter { !$0.items.isEmpty }) { section in
            VStack(alignment: .leading, spacing: 18) {
                SectionLabel(title: section.title)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(section.items) { item in
                            Button {
                                onSelect(item)
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    MediaArtwork(
                                        url: item.displayPosterURL,
                                        title: item.title,
                                        slot: .carousel,
                                        mediaType: item.ref.mediaType,
                                        orientation: item.posterOrientation
                                    )
                                    Text(item.title)
                                        .font(.system(size: 12, weight: .heavy))
                                        .foregroundStyle(.white)
                                        .lineLimit(2)
                                        .frame(height: 32, alignment: .topLeading)
                                }
                                .frame(width: MediaDetailLayout.recommendationPosterSize.width, height: MediaDetailLayout.recommendationCardHeight, alignment: .topLeading)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Open \(item.title)")
                        }
                    }
                }
            }
        }
    }
}

private struct MediaDetailBottomBar: View {
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void

    var body: some View {
        HStack(spacing: 8) {
            HStack(spacing: 0) {
                BottomBarItem(title: "Home", systemName: "house.fill", isSelected: selectedTab == .home) {
                    onSelectTab(.home)
                }
                BottomBarItem(title: "Library", systemName: "books.vertical.fill", isSelected: selectedTab == .library) {
                    onSelectTab(.library)
                }
                BottomBarItem(title: "Community", systemName: "person.2.fill", isSelected: selectedTab == .profile) {
                    onSelectTab(.profile)
                }
            }
            .padding(5)
            .background(.black.opacity(0.74), in: Capsule())
            .overlay {
                Capsule().stroke(.white.opacity(0.06))
            }

            Button {
                onSelectTab(.search)
            } label: {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 24, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 58, height: 58)
                    .background(.black.opacity(0.82), in: Circle())
                    .overlay {
                        Circle().stroke(.white.opacity(0.06))
                    }
                }
            .buttonStyle(.plain)
            .accessibilityLabel("Search")
        }
    }
}

private struct BottomBarItem: View {
    let title: String
    let systemName: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
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
        .buttonStyle(.plain)
        .accessibilityLabel(title)
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

    var numberValue: Double? {
        switch self {
        case let .number(value):
            value
        case let .string(value):
            Double(value)
        default:
            nil
        }
    }

    var intValue: Int? {
        numberValue.map(Int.init)
    }

    var objectValue: [String: JSONValue]? {
        if case let .object(value) = self {
            return value
        }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case let .array(value) = self {
            return value
        }
        return nil
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

func supportsTitleLogo(_ detail: MediaDetail) -> Bool {
    detail.ref.source == "tmdb" && ["movie", "tv"].contains(detail.ref.mediaType) && detail.logoUrl != nil
}

private struct MediaTitleDisplay: View {
    let detail: MediaDetail
    let title: String
    @Binding var showsLogo: Bool
    let font: Font
    let lineLimit: Int?
    let minimumScaleFactor: CGFloat
    let maxLogoHeight: CGFloat

    private var canToggle: Bool {
        supportsTitleLogo(detail)
    }

    var body: some View {
        Group {
            if canToggle, showsLogo, let logoUrl = detail.logoUrl, let url = URL(string: logoUrl) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        TitleLogoLayout(maxLogoHeight: maxLogoHeight, aspectRatio: aspectRatio) {
                            image
                                .resizable()
                                .scaledToFit()
                        }
                    case .failure:
                        titleText
                    default:
                        Color.clear
                            .frame(height: maxLogoHeight)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .center)
            } else {
                titleText
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            guard canToggle else { return }
            withAnimation(.easeInOut(duration: 0.2)) {
                showsLogo.toggle()
            }
        }
        .accessibilityLabel(title)
        .accessibilityHint(canToggle ? "Double tap to switch between logo and text title" : "")
        .accessibilityAddTraits(canToggle ? .isButton : [])
    }

    private var titleText: some View {
        Text(title)
            .font(font)
            .foregroundStyle(.white)
            .lineLimit(lineLimit)
            .minimumScaleFactor(minimumScaleFactor)
    }

    private var aspectRatio: CGFloat? {
        if let ratio = detail.logoAspectRatio, ratio > 0 {
            return CGFloat(ratio)
        }
        if let width = detail.logoWidth, let height = detail.logoHeight, height > 0 {
            return CGFloat(width) / CGFloat(height)
        }
        return nil
    }
}

private struct TitleLogoLayout: Layout {
    let maxLogoHeight: CGFloat
    let aspectRatio: CGFloat?

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        return CGSize(width: width, height: logoSize(for: width).height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let subview = subviews.first else { return }
        let size = logoSize(for: bounds.width)
        let origin = CGPoint(
            x: bounds.midX - size.width / 2,
            y: bounds.minY
        )
        subview.place(
            at: origin,
            proposal: ProposedViewSize(size)
        )
    }

    private func logoSize(for availableWidth: CGFloat) -> CGSize {
        guard availableWidth > 0, let aspectRatio, aspectRatio > 0 else {
            return CGSize(width: availableWidth, height: maxLogoHeight)
        }

        let targetWidth = availableWidth * 0.82
        let neededHeight = targetWidth / aspectRatio
        let height = min(max(neededHeight, maxLogoHeight), maxLogoHeight * 1.45)
        return CGSize(width: min(availableWidth, height * aspectRatio), height: height)
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }

    var yearPrefix: String? {
        count >= 4 ? String(prefix(4)) : nil
    }

    var starRatingLabel: String {
        guard let raw = Double(self) else { return self }
        let stars = raw / 2
        return "\(Self.cleanRating(stars))/5"
    }

    var starRatingValue: String {
        guard let raw = Double(self) else { return self }
        return String(format: "%.1f", raw / 2)
    }

    var starRatingStep: Int {
        guard let raw = Double(self) else { return 0 }
        return min(max(Int(round(raw)), 1), 10)
    }

    var shortDateLabel: String {
        let trimmed = String(prefix(10))
        let input = DateFormatter()
        input.calendar = Calendar(identifier: .gregorian)
        input.locale = Locale(identifier: "en_US_POSIX")
        input.dateFormat = "yyyy-MM-dd"
        guard let date = input.date(from: trimmed) else { return trimmed }

        let output = DateFormatter()
        output.calendar = Calendar(identifier: .gregorian)
        output.locale = Locale.current
        output.dateFormat = "MMM d, yyyy"
        return output.string(from: date)
    }

    var longDateLabel: String {
        LongDateFormatter().string(from: self) ?? self
    }

    var oneDecimalLabel: String {
        guard let value = Double(self) else { return self }
        return String(format: "%.1f", value)
    }

    static func starRatingLabel(forStep step: Int) -> String {
        cleanRating(Double(step) / 2)
    }

    private static func cleanRating(_ value: Double) -> String {
        value.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(value))" : String(format: "%.1f", value)
    }
}

private extension ExternalRating {
    var displayValue: String {
        let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if ["rotten tomatoes", "rottentomatoes"].contains(source.lowercased()) {
            return trimmedValue.hasSuffix("%") ? trimmedValue : "\(trimmedValue)%"
        }
        if source.lowercased() == "hardcover" {
            let displayRating = Double(trimmedValue).map { rawValue in
                let value = rawValue > 5 ? rawValue / 2 : rawValue
                return value.rounded() == value ? "\(Int(value))" : String(format: "%.1f", value)
            } ?? trimmedValue
            return "\(displayRating)/5"
        }
        if trimmedValue.contains("/") || trimmedValue.hasSuffix("%") {
            return trimmedValue
        }
        if let denominator = ratingDenominator ?? maxValue?.nilIfEmpty {
            return "\(trimmedValue)/\(denominator)"
        }
        return trimmedValue
    }

    private var ratingDenominator: String? {
        switch source.lowercased() {
        case "spine", "letterboxd", "hardcover":
            "5"
        case "imdb":
            "10"
        default:
            nil
        }
    }

    var ratingAssetName: String? {
        switch source.lowercased() {
        case "imdb":
            "RatingIMDb"
        case "letterboxd":
            "RatingLetterboxd"
        case "rotten tomatoes":
            rottenTomatoesAssetName
        case "mal", "myanimelist":
            "RatingMAL"
        case "hardcover":
            "RatingHardcover"
        case "igdb":
            "RatingIGDB"
        default:
            nil
        }
    }

    private var rottenTomatoesAssetName: String {
        // ponytail: API only sends RT score; use percent thresholds until it sends certification.
        guard let score = value.split(whereSeparator: { !$0.isNumber && $0 != "." }).first.flatMap({ Double($0) }) else {
            return "RatingRottenTomatoes"
        }
        if score <= 59 {
            return "RatingRottenTomatoesRotten"
        }
        if score >= 75 {
            return "RatingRottenTomatoesCertifiedFresh"
        }
        return "RatingRottenTomatoes"
    }
}

private extension String {

    var ratingCountLabel: String {
        switch lowercased() {
        case "letterboxd":
            "ratings"
        case "rotten tomatoes", "rottentomatoes":
            "reviews"
        default:
            "votes"
        }
    }

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
}

private extension Array where Element == String {
    var joinedOrNil: String? {
        let value = joined(separator: ", ")
        return value.isEmpty ? nil : value
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
