import SwiftUI

@MainActor
@Observable
final class ProfileViewModel {
    var profile: UserProfile?
    var recentEntries: [DiaryEntry] = []
    var inProgressItems: [LibraryItem] = []
    var isLoading = false
    var isLoadingInProgress = false
    var errorMessage: String?
    var activityErrorMessage: String?
    var inProgressErrorMessage: String?
    var hofErrorMessage: String?
    var savingHallOfFameSlots: Set<String> = []

    private let profileRepository: ProfileRepository
    private let diaryRepository: DiaryRepository
    private let trackingRepository: TrackingRepository
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        diaryRepository: DiaryRepository,
        trackingRepository: TrackingRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.diaryRepository = diaryRepository
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        activityErrorMessage = nil
        inProgressErrorMessage = nil
        inProgressItems = []
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

        await loadInProgressItems()
        await loadRecentActivity()
    }

    func reload() async {
        await load()
    }

    func isSavingHallOfFameSlot(_ mediaType: String) -> Bool {
        savingHallOfFameSlots.contains(mediaType)
    }

    private func loadRecentActivity() async {
        do {
            recentEntries = Array(try await diaryRepository.list().prefix(6))
        } catch {
            activityErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func loadInProgressItems() async {
        let mediaTypes = Self.inProgressMediaTypes(from: profile)
        guard !mediaTypes.isEmpty else {
            inProgressItems = []
            return
        }

        isLoadingInProgress = true
        defer { isLoadingInProgress = false }

        do {
            var mergedItems: [LibraryItem] = []
            for mediaType in mediaTypes {
                let response = try await trackingRepository.list(
                    mediaType: mediaType,
                    page: nil,
                    status: "In progress"
                )
                mergedItems += response.results
            }

            inProgressItems = Array(
                mergedItems
                    .sorted(by: Self.inProgressSort)
                    .prefix(8)
            )
        } catch {
            inProgressItems = []
            inProgressErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    static func inProgressMediaTypes(from profile: UserProfile?) -> [String] {
        let enabledTypes = profile?.preferences.enabledMediaTypes ?? []
        let baseTypes = enabledTypes.isEmpty ? APIConstants.fallbackMediaTypes : enabledTypes
        return baseTypes.compactMap { type in
            switch type {
            case "episode":
                return nil
            case "tv":
                return "season"
            default:
                return type
            }
        }
    }

    private static func inProgressSort(_ lhs: LibraryItem, _ rhs: LibraryItem) -> Bool {
        let leftDate = InProgressDateParser.date(from: lhs.tracking.updatedAt)
        let rightDate = InProgressDateParser.date(from: rhs.tracking.updatedAt)
        switch (leftDate, rightDate) {
        case let (left?, right?):
            return left > right
        case (.some, .none):
            return true
        case (.none, .some):
            return false
        case (.none, .none):
            return lhs.media.title.localizedCaseInsensitiveCompare(rhs.media.title) == .orderedAscending
        }
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }

    @discardableResult
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async -> Bool {
        guard !savingHallOfFameSlots.contains(mediaType) else { return false }
        savingHallOfFameSlots.insert(mediaType)
        hofErrorMessage = nil
        defer { savingHallOfFameSlots.remove(mediaType) }

        do {
            let hof = try await profileRepository.setHallOfFameItem(mediaType: mediaType, ref: ref)
            profile = profile?.replacingHallOfFame(hof)
            return true
        } catch {
            hofErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return false
        }
    }

    @discardableResult
    func clearHallOfFameItem(mediaType: String) async -> Bool {
        guard !savingHallOfFameSlots.contains(mediaType) else { return false }
        savingHallOfFameSlots.insert(mediaType)
        hofErrorMessage = nil
        defer { savingHallOfFameSlots.remove(mediaType) }

        do {
            let hof = try await profileRepository.clearHallOfFameItem(mediaType: mediaType)
            profile = profile?.replacingHallOfFame(hof)
            return true
        } catch {
            hofErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return false
        }
    }
}

struct ProfileView: View {
    @State private var viewModel: ProfileViewModel
    @State private var selectedRef: MediaRef?
    @State private var isSettingsPresented = false
    @State private var hofPickerSlot: FavoriteSlot?
    @State private var hofActionSlot: FavoriteSlot?

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let importCoordinator: LetterboxdImportCoordinator
    private let onLogout: () -> Void
    private let onOpenDiary: () -> Void
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        importCoordinator: LetterboxdImportCoordinator,
        onLogout: @escaping () -> Void,
        onOpenDiary: @escaping () -> Void,
        selectedTab: AppTab = .profile,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: ProfileViewModel(
            profileRepository: profileRepository,
            diaryRepository: diaryRepository,
            trackingRepository: trackingRepository,
            onUnauthorized: onUnauthorized
        ))
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.importCoordinator = importCoordinator
        self.onLogout = onLogout
        self.onOpenDiary = onOpenDiary
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        NavigationStack {
            profileContent
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(.hidden, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                if viewModel.profile == nil {
                    await viewModel.load()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Swift.Task<Void, Never> { await viewModel.reload() }
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
            .sheet(isPresented: $isSettingsPresented) {
                ProfileSettingsSheet(
                    profile: viewModel.profile,
                    importCoordinator: importCoordinator,
                    onLogout: onLogout,
                )
            }
            .sheet(item: $hofPickerSlot) { slot in
                HallOfFamePickerSheet(
                    slot: slot,
                    mediaRepository: mediaRepository,
                    isSaving: viewModel.isSavingHallOfFameSlot(slot.id),
                    errorMessage: viewModel.hofErrorMessage,
                    onSelect: { media in
                        await viewModel.setHallOfFameItem(mediaType: slot.id, ref: media.ref)
                    },
                    onClear: clearHallOfFameAction(for: slot),
                    onUnauthorized: onUnauthorized
                )
            }
            .confirmationDialog(
                hofActionSlot?.title ?? "Hall of Fame",
                isPresented: hofActionBinding,
                titleVisibility: .visible
            ) {
                if let slot = hofActionSlot {
                    if let item = slot.item {
                        Button("View \(item.title)") {
                            selectedRef = item.ref
                        }
                    }
                    Button("Change Favorite") {
                        hofPickerSlot = slot
                    }
                    if slot.item != nil {
                        Button("Remove Favorite", role: .destructive) {
                            Swift.Task<Void, Never> { await viewModel.clearHallOfFameItem(mediaType: slot.id) }
                        }
                    }
                }
            }
            .alert("Hall of Fame Update Failed", isPresented: hofErrorBinding) {
                Button("OK") {
                    viewModel.hofErrorMessage = nil
                }
            } message: {
                Text(viewModel.hofErrorMessage ?? "")
            }
        }
    }

    private var profileContent: some View {
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
                            inProgressSection
                            activitySection
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 28)
                        .padding(.bottom, 100)
                    }
                }
            }
            .refreshable {
                await viewModel.reload()
            }

            settingsButton
                .padding(.top, 16)
                .padding(.trailing, 16)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
        }
    }

    private func clearHallOfFameAction(for slot: FavoriteSlot) -> (() async -> Bool)? {
        guard slot.item != nil else { return nil }
        return {
            await viewModel.clearHallOfFameItem(mediaType: slot.id)
        }
    }

    private var hofErrorBinding: Binding<Bool> {
        Binding(
            get: { viewModel.hofErrorMessage != nil && hofPickerSlot == nil },
            set: { if !$0 { viewModel.hofErrorMessage = nil } }
        )
    }

    private var hofActionBinding: Binding<Bool> {
        Binding(
            get: { hofActionSlot != nil },
            set: { if !$0 { hofActionSlot = nil } }
        )
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
        let allSlots = favoriteSlots(from: profile)

        return VStack(spacing: 6) {
            VStack(spacing: 8) {
                ZStack(alignment: .bottom) {
                    HallOfFameCrownView(
                        slots: allSlots,
                        savingSlotIDs: viewModel.savingHallOfFameSlots
                    ) { slot in
                        hofPickerSlot = slot
                    } onEmptyTap: { slot in
                        hofPickerSlot = slot
                    } onFilledLongPress: { slot in
                        hofActionSlot = slot
                    }

                    avatar(profile)
                }
                .frame(height: 210)
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
        ProfileSection(title: "Recent Activity", action: onOpenDiary) {
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

    private var inProgressSection: some View {
        ProfileSection(title: "In Progress") {
            if viewModel.isLoadingInProgress {
                ProfileRailLoadingView()
            } else if let inProgressError = viewModel.inProgressErrorMessage {
                EmptyProfileCard(title: inProgressError, systemName: "exclamationmark.triangle")
            } else if viewModel.inProgressItems.isEmpty {
                EmptyProfileCard(title: "Nothing in progress yet", systemName: "play.circle")
            } else {
                InProgressRail(items: viewModel.inProgressItems) { item in
                    selectedRef = item.media.ref
                }
            }
        }
    }

    private func favoriteSlots(from profile: UserProfile) -> [FavoriteSlot] {
        ProfileFavorites.slots(from: profile.hof, enabledMediaTypes: profile.preferences.enabledMediaTypes)
    }

    private func favoriteSlotRank(_ key: String) -> Int {
        ProfileFavorites.rank(key)
    }
}

struct ProfileFavorites {
    private static let defaultSlotKeys = ["movie", "tv", "anime", "manga", "game", "book", "comic"]

    static func slots(from hof: [String: MediaSummary?], enabledMediaTypes: [String] = []) -> [FavoriteSlot] {
        let enabled = enabledMediaTypes.filter { defaultSlotKeys.contains($0) }
        let keys = enabled.isEmpty ? defaultSlotKeys : enabled
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

@MainActor
@Observable
private final class HallOfFamePickerViewModel {
    var query = ""
    var results: [MediaSummary] = []
    var isLoading = false
    var errorMessage: String?

    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func search(mediaType: String) async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            results = []
            errorMessage = nil
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            results = try await mediaRepository.search(query: trimmed, mediaType: mediaType)
        } catch is CancellationError {
            return
        } catch {
            results = []
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private struct HallOfFamePickerSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: HallOfFamePickerViewModel
    @State private var savingMediaID: String?
    @State private var isClearing = false

    let slot: FavoriteSlot
    let isSaving: Bool
    let errorMessage: String?
    let onSelect: (MediaSummary) async -> Bool
    let onClear: (() async -> Bool)?

    init(
        slot: FavoriteSlot,
        mediaRepository: MediaRepository,
        isSaving: Bool,
        errorMessage: String?,
        onSelect: @escaping (MediaSummary) async -> Bool,
        onClear: (() async -> Bool)?,
        onUnauthorized: @escaping () -> Void
    ) {
        self.slot = slot
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.onSelect = onSelect
        self.onClear = onClear
        _viewModel = State(initialValue: HallOfFamePickerViewModel(mediaRepository: mediaRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        NavigationStack {
            List {
                if let errorMessage {
                    Section {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                    }
                }

                if let current = slot.item {
                    Section("Current") {
                        HallOfFamePickerRow(media: current)
                        if onClear != nil {
                            Button(role: .destructive) {
                                Swift.Task<Void, Never> { await clear() }
                            } label: {
                                if isClearing {
                                    Label("Removing", systemImage: "clock")
                                } else {
                                    Label("Remove Favorite", systemImage: "trash")
                                }
                            }
                            .disabled(isSaving || isClearing)
                        }
                    }
                }

                Section("Results") {
                    if viewModel.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        ContentUnavailableView("Search \(slot.title)", systemImage: "magnifyingglass")
                            .listRowBackground(Color.clear)
                    } else if viewModel.isLoading {
                        HStack {
                            Spacer()
                            ProgressView()
                            Spacer()
                        }
                    } else if let error = viewModel.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                    } else if viewModel.results.isEmpty {
                        ContentUnavailableView("No Results", systemImage: "magnifyingglass")
                            .listRowBackground(Color.clear)
                    } else {
                        ForEach(viewModel.results) { media in
                            Button {
                                Swift.Task<Void, Never> { await select(media) }
                            } label: {
                                HStack(spacing: 10) {
                                    HallOfFamePickerRow(media: media)
                                    if savingMediaID == media.id {
                                        ProgressView()
                                    }
                                }
                            }
                            .buttonStyle(.plain)
                            .disabled(isSaving || savingMediaID != nil)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .navigationTitle("\(slot.title) Favorite")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $viewModel.query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search \(slot.title)")
            .task(id: viewModel.query) {
                try? await Task.sleep(for: .milliseconds(300))
                guard !Task.isCancelled else { return }
                await viewModel.search(mediaType: slot.id)
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }

    private func select(_ media: MediaSummary) async {
        savingMediaID = media.id
        let didSave = await onSelect(media)
        savingMediaID = nil
        if didSave {
            dismiss()
        }
    }

    private func clear() async {
        guard let onClear else { return }
        isClearing = true
        let didClear = await onClear()
        isClearing = false
        if didClear {
            dismiss()
        }
    }
}

private struct HallOfFamePickerRow: View {
    let media: MediaSummary

    var body: some View {
        HStack(spacing: 12) {
            MediaArtwork(
                url: media.displayPosterURL,
                title: media.title,
                slot: .searchRow,
                mediaType: media.ref.mediaType,
                orientation: media.posterOrientation
            )

            VStack(alignment: .leading, spacing: 4) {
                Text(media.title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                if let subtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 0)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var subtitle: String? {
        let text = [media.subtitle, media.releaseDate]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        return text.isEmpty ? nil : text
    }
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
    let action: (() -> Void)?
    @ViewBuilder let content: () -> Content

    init(
        title: String,
        action: (() -> Void)? = nil,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.title = title
        self.action = action
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                if let action {
                    Button(action: action) {
                        HStack(spacing: 5) {
                            sectionTitle
                            Image(systemName: "chevron.right")
                                .font(.system(size: 10, weight: .black))
                                .foregroundStyle(.white.opacity(0.58))
                        }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(title)
                } else {
                    sectionTitle
                }
                Spacer()
            }

            content()
        }
    }

    private var sectionTitle: some View {
        Text(title.uppercased())
            .font(.system(size: 13, weight: .black))
            .foregroundStyle(.white.opacity(0.58))
            .tracking(0.8)
    }
}

private struct RecentActivityRail: View {
    let entries: [DiaryEntry]
    let action: (DiaryEntry) -> Void

    var body: some View {
        GeometryReader { proxy in
            let itemWidth = PosterSlot.diaryRow.size.width
            let minimumSpacing: CGFloat = 4
            let maximumVisibleCount = min(6, entries.count)
            let visibleCount = max(1, min(maximumVisibleCount, Int((proxy.size.width + minimumSpacing) / (itemWidth + minimumSpacing))))
            let spacing = visibleCount > 1
                ? min(10, max(minimumSpacing, (proxy.size.width - itemWidth * CGFloat(visibleCount)) / CGFloat(visibleCount - 1)))
                : 0
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

private struct InProgressRail: View {
    let items: [LibraryItem]
    let action: (LibraryItem) -> Void

    var body: some View {
        GeometryReader { proxy in
            let spacing: CGFloat = 10
            let itemWidth: CGFloat = 62
            let visibleCount = max(3, min(5, Int((proxy.size.width + spacing) / (itemWidth + spacing))))
            HStack(alignment: .top, spacing: spacing) {
                ForEach(Array(items.prefix(visibleCount))) { item in
                    InProgressPoster(item: item) {
                        action(item)
                    }
                    .frame(width: itemWidth)
                }
                Spacer(minLength: 0)
            }
        }
        .frame(height: 104)
    }
}

private struct InProgressPoster: View {
    let item: LibraryItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            MediaArtwork(
                url: item.media.displayPosterURL,
                title: item.media.title,
                slot: .diaryRow,
                mediaType: item.media.ref.mediaType,
                orientation: item.media.posterOrientation
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("View \(item.media.title)")
    }
}

private struct ProfileRailLoadingView: View {
    var body: some View {
        HStack(spacing: 10) {
            ForEach(0..<5, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(.white.opacity(0.08))
                    .frame(width: 62, height: 94)
            }
            Spacer(minLength: 0)
        }
        .redacted(reason: .placeholder)
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

private enum InProgressDateParser {
    private static let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let standardFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func date(from value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        return fractionalFormatter.date(from: value) ?? standardFormatter.date(from: value)
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
                    Text("Profile fields, avatar upload, and preference saves need writable API contracts before they become editable here.")
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
