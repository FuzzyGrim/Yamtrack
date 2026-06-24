import SwiftUI

@MainActor
@Observable
final class HomeViewModel {
    var profile: UserProfile?
    var inProgressItems: [LibraryItem] = []
    var recentEntries: [DiaryEntry] = []
    var isLoading = false
    var isLoadingInProgress = false
    var isLoadingActivity = false
    var profileErrorMessage: String?
    var inProgressErrorMessage: String?
    var activityErrorMessage: String?

    private let profileRepository: ProfileRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        profileErrorMessage = nil
        inProgressErrorMessage = nil
        activityErrorMessage = nil
        defer { isLoading = false }

        do {
            profile = try await profileRepository.me()
        } catch {
            profileErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return
        }

        await loadInProgress()
        await loadActivity()
    }

    func reload() async {
        await load()
    }

    func loadInProgress() async {
        let mediaTypes = InProgressLibraryLoader.mediaTypes(from: profile)
        guard !mediaTypes.isEmpty else {
            inProgressItems = []
            return
        }

        isLoadingInProgress = true
        inProgressErrorMessage = nil
        defer { isLoadingInProgress = false }

        do {
            inProgressItems = try await InProgressLibraryLoader.load(
                mediaTypes: mediaTypes,
                trackingRepository: trackingRepository,
                limit: 10
            )
        } catch {
            inProgressItems = []
            inProgressErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func loadActivity() async {
        isLoadingActivity = true
        activityErrorMessage = nil
        defer { isLoadingActivity = false }

        do {
            recentEntries = try await diaryRepository.recent(limit: 6)
        } catch {
            recentEntries = []
            activityErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

struct HomeView: View {
    private static let headerAvatarSize: CGFloat = 58

    @State private var viewModel: HomeViewModel
    @State private var selectedRef: MediaRef?
    @State private var selectedEntry: DiaryEntry?

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: HomeViewModel(
            profileRepository: profileRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SpinePageBackground()

                ScrollView(showsIndicators: false) {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        header
                        content
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 18)
                    .padding(.bottom, 92)
                }
                .refreshable {
                    await viewModel.reload()
                }
            }
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                if viewModel.profile == nil {
                    await viewModel.load()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Task { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .profileDidUpdate)) { notification in
                if let profile = notification.userInfo?["profile"] as? UserProfile {
                    viewModel.profile = profile
                    Task { await viewModel.loadInProgress() }
                } else {
                    Task { await viewModel.reload() }
                }
            }
            .onChange(of: selectedRef) { oldValue, newValue in
                if oldValue != nil, newValue == nil {
                    Task { await viewModel.loadInProgress() }
                }
            }
            .fullScreenCover(item: $selectedRef) { ref in
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
            .fullScreenCover(item: $selectedEntry) { entry in
                DiaryLogDetailView(
                    entryId: entry.id,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                onSelectTab(.profile)
            } label: {
                HomeAvatar(profile: viewModel.profile, size: Self.headerAvatarSize)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Profile")

            VStack(alignment: .leading, spacing: 4) {
                Text("Home")
                    .font(.system(size: 32, weight: .black))
                    .foregroundStyle(.white)

                if let profile = viewModel.profile {
                    Text("@\(profile.username)")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white.opacity(0.54))
                }
            }

            Spacer()

            Button {
                onSelectTab(.search)
            } label: {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 17, weight: .black))
                    .foregroundStyle(.white)
                    .frame(width: 42, height: 42)
                    .background(.white.opacity(0.10), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Search")
        }
    }

    @ViewBuilder
    private var content: some View {
        if let error = viewModel.profileErrorMessage, viewModel.profile == nil {
            HomeStateCard(
                title: "Could not load home",
                systemImage: "exclamationmark.triangle",
                message: error
            )
        } else if viewModel.isLoading, viewModel.profile == nil {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 420)
        } else {
            inProgressSection
            activitySection
            socialPlaceholders
        }
    }

    private var inProgressSection: some View {
        HomeSection(title: "In Progress") {
            if viewModel.isLoadingInProgress {
                HomeInProgressSkeleton()
            } else if let error = viewModel.inProgressErrorMessage {
                HomeStateCard(title: "Could not load progress", systemImage: "exclamationmark.triangle", message: error)
            } else if viewModel.inProgressItems.isEmpty {
                HomeStateCard(title: "Nothing in progress", systemImage: "play.circle", message: "Current watches, reads, and plays will appear here.")
            } else {
                HomeInProgressRail(items: viewModel.inProgressItems) { item in
                    selectedRef = item.media.ref
                }
            }
        }
    }

    private var activitySection: some View {
        HomeSection(title: "Activity") {
            if viewModel.isLoadingActivity {
                HomeActivitySkeleton()
            } else if let error = viewModel.activityErrorMessage {
                HomeStateCard(title: "Could not load activity", systemImage: "exclamationmark.triangle", message: error)
            } else if viewModel.recentEntries.isEmpty {
                HomeStateCard(title: "No activity yet", systemImage: "person.2", message: "Diary logs will appear here.")
            } else {
                LazyVStack(spacing: 10) {
                    ForEach(viewModel.recentEntries) { entry in
                        Button {
                            selectedEntry = entry
                        } label: {
                            HomeActivityCard(entry: entry)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private var socialPlaceholders: some View {
        VStack(alignment: .leading, spacing: 18) {
            HomeSection(title: "Friend Feed") {
                HomePlaceholderCard(title: "No friend activity yet", systemName: "person.2.fill", message: "Followed users' logs and ratings will appear here.")
            }

            HomeSection(title: "People to Follow") {
                HomePlaceholderCard(title: "No suggestions yet", systemName: "person.badge.plus", message: "Suggested people will appear here.")
            }

            HomeSection(title: "Community Lists") {
                HomePlaceholderCard(title: "No community lists yet", systemName: "list.bullet.rectangle", message: "Public lists will appear here.")
            }
        }
    }
}

private struct HomeAvatar: View {
    let profile: UserProfile?
    var size: CGFloat = 42

    var body: some View {
        AsyncImage(url: URL(string: profile?.avatarUrl ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.crop.circle.fill")
                    .resizable()
                    .foregroundStyle(.white.opacity(0.42))
                    .padding(4)
            }
        }
        .frame(width: size, height: size)
        .background(.white.opacity(0.10), in: Circle())
        .clipShape(Circle())
        .overlay {
            Circle().stroke(.white.opacity(0.12), lineWidth: 1)
        }
    }
}

private struct HomeSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title.uppercased())
                .font(.system(size: 13, weight: .black))
                .foregroundStyle(.white.opacity(0.58))
                .tracking(0.8)

            content()
        }
    }
}

private struct HomeInProgressRail: View {
    let items: [LibraryItem]
    let action: (LibraryItem) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            LazyHStack(alignment: .top, spacing: 12) {
                ForEach(items) { item in
                    HomeInProgressCard(item: item) {
                        action(item)
                    }
                }
            }
            .padding(.trailing, 14)
        }
    }
}

private struct HomeInProgressCard: View {
    let item: LibraryItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                MediaArtwork(
                    url: item.media.displayPosterURL,
                    title: item.media.title,
                    slot: .carousel,
                    mediaType: item.media.ref.mediaType,
                    orientation: item.media.posterOrientation
                )
                .shadow(color: .black.opacity(0.30), radius: 10, y: 5)

                VStack(alignment: .leading, spacing: 3) {
                    Text(item.media.title)
                        .font(.system(size: 12, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                        .frame(height: 32, alignment: .topLeading)

                    Text(metadataText)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white.opacity(0.54))
                        .lineLimit(1)
                }
            }
            .frame(width: PosterSlot.carousel.size.width, alignment: .leading)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("View \(item.media.title)")
    }

    private var metadataText: String {
        item.tracking.progress?.compactDisplayText(preferredMode: ProgressDisplayPreferences.mode(for: item.media.ref)) ?? item.tracking.status ?? "In progress"
    }
}

private struct HomeActivityCard: View {
    let entry: DiaryEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                HomeUserAvatar(user: entry.user)

                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.user.displayName)
                        .font(.system(size: 14, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(1)

                    Text("logged \(mediaTypePhrase)")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white.opacity(0.52))
                        .lineLimit(1)
                }

                Spacer(minLength: 0)

                if let date = DiaryDateFormatter.exactDate(from: entry.consumedAt ?? entry.createdAt) {
                    Text(date)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white.opacity(0.42))
                        .lineLimit(1)
                        .minimumScaleFactor(0.82)
                }
            }

            HStack(alignment: .top, spacing: 12) {
                MediaArtwork(
                    url: entry.media.displayPosterURL,
                    title: entry.media.title,
                    slot: .profileRow,
                    mediaType: entry.media.ref.mediaType,
                    orientation: entry.media.posterOrientation
                )
                .shadow(color: .black.opacity(0.24), radius: 8, y: 4)

                VStack(alignment: .leading, spacing: 7) {
                    Text(entry.media.title)
                        .font(.system(size: 16, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(2)

                    if let reviewTitle = clean(entry.reviewTitle) {
                        Text(reviewTitle)
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.white.opacity(0.78))
                            .lineLimit(2)
                    }

                    if entry.containsSpoilers {
                        Label("Contains spoilers", systemImage: "eye.slash")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(.white.opacity(0.56))
                    } else if let review = clean(entry.review) {
                        Text(review)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(.white.opacity(0.58))
                            .lineLimit(3)
                    }

                    metadata
                }
            }
        }
        .padding(12)
        .background(.white.opacity(0.065), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(0.08), lineWidth: 1)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var metadata: some View {
        HStack(spacing: 6) {
            if let rating = clean(entry.rating) {
                HomeChip(text: rating, systemName: "star.fill")
            }

            if entry.isRewatch {
                HomeChip(text: "Rewatch", systemName: "arrow.clockwise")
            }

            ForEach(entry.tags.prefix(2), id: \.self) { tag in
                HomeChip(text: tag)
            }

            if entry.likeCount > 0 {
                HomeChip(text: entry.likeCount.formatted(), systemName: "heart.fill")
            }
        }
    }

    private var mediaTypePhrase: String {
        switch entry.media.ref.mediaType {
        case "movie":
            "a movie"
        case "tv":
            "a TV show"
        case "season":
            "a season"
        case "episode":
            "an episode"
        case "anime":
            "an anime"
        case "manga":
            "a manga"
        case "game":
            "a game"
        case "book":
            "a book"
        case "comic":
            "a comic"
        case "boardgame":
            "a board game"
        default:
            "media"
        }
    }

    private func clean(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

private struct HomeUserAvatar: View {
    let user: UserSummary

    var body: some View {
        AsyncImage(url: URL(string: user.avatarUrl ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.fill")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white.opacity(0.54))
            }
        }
        .frame(width: 32, height: 32)
        .background(.white.opacity(0.10), in: Circle())
        .clipShape(Circle())
    }
}

private struct HomeChip: View {
    let text: String
    var systemName: String?

    var body: some View {
        Label {
            Text(text)
                .lineLimit(1)
        } icon: {
            if let systemName {
                Image(systemName: systemName)
                    .font(.system(size: 9, weight: .black))
            }
        }
        .font(.system(size: 10, weight: .bold))
        .foregroundStyle(.white.opacity(0.78))
        .padding(.horizontal, 7)
        .frame(height: 21)
        .background(.white.opacity(0.11), in: Capsule())
    }
}

private struct HomePlaceholderCard: View {
    let title: String
    let systemName: String
    let message: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: systemName)
                .font(.system(size: 18, weight: .black))
                .foregroundStyle(.white.opacity(0.62))
                .frame(width: 36, height: 36)
                .background(.white.opacity(0.08), in: Circle())

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 14, weight: .heavy))
                    .foregroundStyle(.white)

                Text(message)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.52))
                    .lineLimit(2)
            }

            Spacer(minLength: 0)
        }
        .padding(13)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(0.08), lineWidth: 1)
        }
    }
}

private struct HomeStateCard: View {
    let title: String
    let systemImage: String
    let message: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.system(size: 24, weight: .bold))
                .foregroundStyle(.white.opacity(0.64))

            Text(title)
                .font(.system(size: 16, weight: .heavy))
                .foregroundStyle(.white)

            Text(message)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.54))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(22)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(0.08), lineWidth: 1)
        }
    }
}

private struct HomeInProgressSkeleton: View {
    var body: some View {
        HStack(spacing: 12) {
            ForEach(0 ..< 3, id: \.self) { _ in
                VStack(alignment: .leading, spacing: 8) {
                    RoundedRectangle(cornerRadius: PosterSlot.carousel.cornerRadius, style: .continuous)
                        .fill(.white.opacity(0.08))
                        .frame(width: PosterSlot.carousel.size.width, height: PosterSlot.carousel.size.height)

                    RoundedRectangle(cornerRadius: 4)
                        .fill(.white.opacity(0.08))
                        .frame(width: 86, height: 12)

                    RoundedRectangle(cornerRadius: 4)
                        .fill(.white.opacity(0.06))
                        .frame(width: 54, height: 10)
                }
            }
        }
        .redacted(reason: .placeholder)
    }
}

private struct HomeActivitySkeleton: View {
    var body: some View {
        VStack(spacing: 10) {
            ForEach(0 ..< 3, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(.white.opacity(0.07))
                    .frame(height: 126)
            }
        }
        .redacted(reason: .placeholder)
    }
}
