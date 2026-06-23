import SwiftUI

@MainActor
@Observable
final class ProfileViewModel {
    var profile: UserProfile?
    var recentEntries: [DiaryEntry] = []
    var isLoading = false
    var errorMessage: String?
    var activityErrorMessage: String?

    private let profileRepository: ProfileRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(profileRepository: ProfileRepository, diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.profileRepository = profileRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        activityErrorMessage = nil
        defer { isLoading = false }

        do {
            profile = try await profileRepository.me()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return
        }

        do {
            recentEntries = Array(try await diaryRepository.list().prefix(5))
        } catch {
            activityErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct ProfileView: View {
    @State private var viewModel: ProfileViewModel
    @State private var selectedRef: MediaRef?
    @State private var isSettingsPresented = false

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let importCoordinator: LetterboxdImportCoordinator
    private let onLogout: () -> Void
    private let onOpenDiary: () -> Void
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        importCoordinator: LetterboxdImportCoordinator,
        onLogout: @escaping () -> Void,
        onOpenDiary: @escaping () -> Void,
        onUnauthorized: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: ProfileViewModel(
            profileRepository: profileRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.importCoordinator = importCoordinator
        self.onLogout = onLogout
        self.onOpenDiary = onOpenDiary
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        NavigationStack {
            ZStack(alignment: .top) {
                SpinePageBackground()

                ScrollView(showsIndicators: false) {
                    Group {
                        if viewModel.isLoading {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 520)
                        } else if let error = viewModel.errorMessage {
                            ContentUnavailableView("Could not load profile", systemImage: "exclamationmark.triangle", description: Text(error))
                                .foregroundStyle(.white)
                                .padding(.top, 120)
                        } else if let profile = viewModel.profile {
                            VStack(alignment: .leading, spacing: 24) {
                                hero(profile)
                                activitySection
                            }
                            .padding(.horizontal, 16)
                            .padding(.top, 28)
                            .padding(.bottom, 100)
                        }
                    }
                }
                .refreshable {
                    await viewModel.load()
                }

                settingsButton
                    .padding(.top, 16)
                    .padding(.trailing, 16)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(.hidden, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                if viewModel.profile == nil {
                    await viewModel.load()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Task { await viewModel.load() }
            }
            .fullScreenCover(item: $selectedRef) { ref in
                MediaDetailView(
                    ref: ref,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    onUnauthorized: onUnauthorized
                )
            }
            .sheet(isPresented: $isSettingsPresented) {
                ProfileSettingsSheet(
                    profile: viewModel.profile,
                    importCoordinator: importCoordinator,
                    onLogout: onLogout,
                )
            }
        }
    }

    private var settingsButton: some View {
        Button {
            isSettingsPresented = true
        } label: {
            Image(systemName: "gearshape.fill")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Settings")
    }

    private func hero(_ profile: UserProfile) -> some View {
        let allSlots = favoriteSlots(from: profile.hof)

        return VStack(spacing: 6) {
            VStack(spacing: 8) {
                ZStack {
                    HallOfFameCrownView(slots: allSlots) { item in
                        selectedRef = item.ref
                    }

                    avatar(profile)
                }
                .frame(height: 232)
                .padding(.top, 8)

                if allSlots.isEmpty {
                    Text("No favorites yet")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.4))
                }
            }

            VStack(spacing: 6) {
                Text(profile.displayName)
                    .font(.system(size: 34, weight: .black))
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.72)

                HStack(spacing: 8) {
                    Text("@\(profile.username)")
                    if profile.isPrivate {
                        Label("Private", systemImage: "lock.fill")
                            .labelStyle(.titleAndIcon)
                    }
                }
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.62))
            }

            if let bio = profile.bio?.trimmedNonEmpty {
                Text(bio)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(.white.opacity(0.78))
                    .multilineTextAlignment(.center)
                    .lineLimit(4)
                    .padding(.horizontal, 10)
            }

            if let location = profile.location?.trimmedNonEmpty {
                Label(location, systemImage: "mappin.and.ellipse")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
            }

            statsGrid(profile.counts)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 4)
        .padding(.vertical, 12)
    }

    private func avatar(_ profile: UserProfile) -> some View {
        AsyncImage(url: URL(string: profile.avatarUrl ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.crop.circle.fill")
                    .resizable()
                    .foregroundStyle(.white.opacity(0.36))
                    .padding(10)
            }
        }
        .frame(width: 128, height: 128)
        .background(.white.opacity(0.08), in: Circle())
        .clipShape(Circle())
        .overlay {
            Circle()
                .stroke(.white.opacity(0.18), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.44), radius: 22, y: 12)
        .accessibilityLabel(profile.displayName)
    }

    private func statsGrid(_ counts: ProfileCounts) -> some View {
        HStack(spacing: 8) {
            ProfileStatChip(value: counts.diaryEntries, title: "Logs", systemName: "calendar")
            ProfileStatChip(value: counts.followers, title: "Followers", systemName: "person.2")
            ProfileStatChip(value: counts.following, title: "Following", systemName: "person.crop.circle.badge.checkmark")
            ProfileStatChip(value: counts.lists, title: "Lists", systemName: "list.bullet.rectangle")
        }
    }

    private var activitySection: some View {
        ProfileSection(title: "Recent Activity", actionTitle: "Diary", action: onOpenDiary) {
            if let activityError = viewModel.activityErrorMessage {
                EmptyProfileCard(title: activityError, systemName: "exclamationmark.triangle")
            } else if viewModel.recentEntries.isEmpty {
                EmptyProfileCard(title: "No diary activity yet", systemName: "calendar")
            } else {
                RecentActivityRail(entries: viewModel.recentEntries) { entry in
                    selectedRef = entry.media.ref
                }
            }
        }
    }

    private func favoriteSlots(from hof: [String: MediaSummary?]) -> [FavoriteSlot] {
        ProfileFavorites.slots(from: hof)
    }

    private func favoriteSlotRank(_ key: String) -> Int {
        ProfileFavorites.rank(key)
    }
}

struct ProfileFavorites {
    private static let defaultSlotKeys = ["movie", "tv", "anime", "manga", "game", "book", "comic"]

    static func slots(from hof: [String: MediaSummary?]) -> [FavoriteSlot] {
        let keys = (defaultSlotKeys + hof.keys.filter { !defaultSlotKeys.contains($0) })
        return keys.sorted { lhs, rhs in
            let leftRank = rank(lhs)
            let rightRank = rank(rhs)
            return leftRank == rightRank ? lhs < rhs : leftRank < rightRank
        }.map { key in
            FavoriteSlot(id: key, title: key.profileSlotTitle, item: hof[key] ?? nil)
        }
    }

    static func rank(_ key: String) -> Int {
        let normalized = key.lowercased()
        let order = ["movie", "tv", "anime", "manga", "game", "book", "comic", "boardgame"]
        return order.firstIndex { normalized.contains($0) } ?? order.count
    }
}

struct FavoriteSlot: Identifiable {
    let id: String
    let title: String
    let item: MediaSummary?
}

private struct ProfileStatChip: View {
    let value: Int
    let title: String
    let systemName: String

    var body: some View {
        VStack(spacing: 5) {
            Image(systemName: systemName)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(.white.opacity(0.62))

            Text(value.formatted())
                .font(.system(size: 18, weight: .black))
                .foregroundStyle(.white)
                .lineLimit(1)
                .minimumScaleFactor(0.6)

            Text(title)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.white.opacity(0.52))
                .lineLimit(1)
                .minimumScaleFactor(0.68)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 70)
        .background(.black.opacity(0.24), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct ProfileSection<Content: View>: View {
    let title: String
    let actionTitle: String?
    let action: (() -> Void)?
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(title.uppercased())
                    .font(.system(size: 13, weight: .black))
                    .foregroundStyle(.white.opacity(0.58))
                    .tracking(0.8)
                Spacer()
                if let actionTitle, let action {
                    Button(actionTitle, action: action)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white)
                        .buttonStyle(.plain)
                }
            }

            content()
        }
    }
}

private struct RecentActivityRail: View {
    let entries: [DiaryEntry]
    let action: (DiaryEntry) -> Void

    var body: some View {
        GeometryReader { proxy in
            let spacing: CGFloat = 10
            let itemWidth: CGFloat = 62
            let visibleCount = max(3, min(5, Int((proxy.size.width + spacing) / (itemWidth + spacing))))
            HStack(alignment: .top, spacing: spacing) {
                ForEach(Array(entries.prefix(visibleCount))) { entry in
                    RecentActivityPoster(entry: entry) {
                        action(entry)
                    }
                    .frame(width: itemWidth)
                }
                Spacer(minLength: 0)
            }
        }
        .frame(height: 104)
    }
}

private struct RecentActivityPoster: View {
    let entry: DiaryEntry
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 7) {
                MediaArtwork(
                    url: entry.media.displayPosterURL,
                    title: entry.media.title,
                    slot: .diaryRow,
                    mediaType: entry.media.ref.mediaType,
                    orientation: entry.media.posterOrientation
                )

                ProfileStarRating(rating: entry.rating)
            }
        }
        .buttonStyle(.plain)
    }
}

private struct ProfileStarRating: View {
    let rating: String?

    var body: some View {
        HStack(spacing: 1) {
            ForEach(Array(symbolNames.enumerated()), id: \.offset) { _, symbolName in
                Image(systemName: symbolName)
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(.yellow.opacity(0.92))
            }
        }
        .frame(width: 56, height: 10, alignment: .leading)
        .accessibilityHidden(value == nil)
        .accessibilityLabel(value.map { "Rating \($0) out of 5 stars" } ?? "")
    }

    private var value: Double? {
        guard let rating, let raw = Double(rating) else { return nil }
        return raw / 2
    }

    private var symbolNames: [String] {
        guard let value else { return [] }
        let fullStars = Int(value.rounded(.down))
        let hasHalfStar = value - Double(fullStars) >= 0.5
        return Array(repeating: "star.fill", count: fullStars)
            + (hasHalfStar ? ["star.leadinghalf.filled"] : [])
    }
}

private struct EmptyProfileCard: View {
    let title: String
    let systemName: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemName)
                .font(.system(size: 15, weight: .bold))
            Text(title)
                .font(.system(size: 14, weight: .semibold))
                .lineLimit(3)
        }
        .foregroundStyle(.white.opacity(0.48))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct ProfileSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var isImportStatusPresented = false

    let profile: UserProfile?
    let importCoordinator: LetterboxdImportCoordinator
    let onLogout: () -> Void

    var body: some View {
        NavigationStack {
            List {
                if let profile {
                    Section("Account") {
                        LabeledContent("Name", value: profile.displayName)
                        LabeledContent("Username", value: "@\(profile.username)")
                        if let email = profile.email?.trimmedNonEmpty {
                            LabeledContent("Email", value: email)
                        }
                        LabeledContent("Privacy", value: profile.isPrivate ? "Private" : "Public")
                    }

                    Section("Preferences") {
                        LabeledContent("Media Types", value: profile.preferences.enabledMediaTypes.map(\.profileSlotTitle).joined(separator: ", "))
                        LabeledContent("Release Notifications", value: profile.preferences.releaseNotificationsEnabled ? "On" : "Off")
                        LabeledContent("Daily Digest", value: profile.preferences.dailyDigestEnabled ? "On" : "Off")
                        if let dateFormat = profile.preferences.dateFormat?.trimmedNonEmpty {
                            LabeledContent("Date Format", value: dateFormat)
                        }
                        if let weekStartDay = profile.preferences.weekStartDay?.trimmedNonEmpty {
                            LabeledContent("Week Starts", value: weekStartDay.profileSlotTitle)
                        }
                    }
                }

                Section("Import") {
                    importSectionContent
                }

                Section("App") {
                    LabeledContent("API Base URL", value: AppConfig.apiBaseURL.absoluteString)
                    LabeledContent("API Prefix", value: AppConfig.apiPrefix)
                }

                Section {
                    Text("Profile editing, avatar upload, Hall of Fame editing, and preference saves need the v2 API contract before they become editable here.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Button(role: .destructive) {
                        dismiss()
                        onLogout()
                    } label: {
                        Label("Logout", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .fullScreenCover(isPresented: $isImportStatusPresented) {
                LetterboxdImportUploadView(
                    coordinator: importCoordinator,
                    onDone: { isImportStatusPresented = false }
                )
            }
        }
    }

    @ViewBuilder
    private var importSectionContent: some View {
        switch importCoordinator.phase {
        case .idle:
            NavigationLink {
                LetterboxdImportView(coordinator: importCoordinator)
            } label: {
                Label("Import from Letterboxd", systemImage: "square.and.arrow.down")
            }
        case let .uploading(_, progress):
            Button {
                isImportStatusPresented = true
            } label: {
                HStack(spacing: 12) {
                    ProgressView(value: progress)
                        .frame(width: 44)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Uploading...")
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        case let .processing(_, statusLabel, _):
            Button {
                isImportStatusPresented = true
            } label: {
                HStack(spacing: 12) {
                    ProgressView()
                    VStack(alignment: .leading, spacing: 3) {
                        Text(statusLabel)
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            checkStatusButton
        case let .succeeded(message):
            importResultRow(systemName: "checkmark.circle.fill", tint: .green, message: message)
            Button("Dismiss") {
                importCoordinator.clearFinishedJob()
            }
        case let .failed(message):
            importResultRow(systemName: "exclamationmark.triangle.fill", tint: .red, message: message)
            if importCoordinator.canCheckStatus {
                checkStatusButton
            }
            NavigationLink {
                LetterboxdImportView(coordinator: importCoordinator)
            } label: {
                Label("Try Again", systemImage: "arrow.clockwise")
            }
            Button("Dismiss") {
                importCoordinator.clearFinishedJob()
            }
        }
    }

    private var checkStatusButton: some View {
        Button {
            importCoordinator.checkStatusOnce()
        } label: {
            if importCoordinator.isCheckingStatus {
                Label("Checking Status", systemImage: "clock.arrow.circlepath")
            } else {
                Label("Check Status", systemImage: "arrow.clockwise")
            }
        }
        .disabled(importCoordinator.isCheckingStatus)
    }

    private func importResultRow(systemName: String, tint: Color, message: String) -> some View {
        Label {
            Text(message)
                .lineLimit(2)
        } icon: {
            Image(systemName: systemName)
                .foregroundStyle(tint)
        }
    }
}

private extension String {
    var trimmedNonEmpty: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    var profileSlotTitle: String {
        replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .split(separator: " ")
            .map { $0.capitalized }
            .joined(separator: " ")
    }
}
