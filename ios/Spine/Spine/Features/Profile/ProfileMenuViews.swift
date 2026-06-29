import SwiftUI
import UIKit

@MainActor
@Observable
private final class ProfileDiaryFilterViewModel {
    var entries: [DiaryEntry] = []
    var isLoading = false
    var errorMessage: String?

    private let filter: DiaryFilter
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(filter: DiaryFilter, diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.filter = filter
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            entries = try await diaryRepository.list(filter: filter)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct ProfileReviewsView: View {
    @State private var viewModel: ProfileDiaryFilterViewModel

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileDiaryFilterViewModel(
            filter: DiaryFilter(hasReview: true),
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ProfileDiaryEntriesScreen(
            title: "Reviews",
            emptyTitle: "No reviews yet",
            emptyMessage: "Reviews you write from media pages will appear here.",
            viewModel: viewModel,
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

struct ProfileLikesView: View {
    @State private var viewModel: ProfileLikesViewModel

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileLikesViewModel(
            profileRepository: profileRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 16) {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 320)
                    } else if let error = viewModel.errorMessage {
                        DiaryStateCard(title: "Could not load likes", systemImage: "exclamationmark.triangle", message: error)
                    } else if viewModel.media.isEmpty {
                        DiaryStateCard(title: "No liked media yet", systemImage: "heart", message: "Media you like while logging will appear here.")
                    } else {
                        mediaGrid(viewModel.media)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle("Likes")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.media.isEmpty {
                await viewModel.load()
            }
        }
    }

    private func mediaGrid(_ media: [MediaSummary]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
            ForEach(media) { item in
                NavigationLink {
                    MediaDetailView(
                        ref: item.ref,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        currentUserId: currentUserId,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    MediaArtwork(
                        url: item.displayPosterURL,
                        title: item.title,
                        slot: .tagGrid,
                        mediaType: item.ref.mediaType,
                        orientation: item.posterOrientation
                    )
                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(item.title)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

@MainActor
@Observable
private final class ProfileLikesViewModel {
    var media: [MediaSummary] = []
    var isLoading = false
    var errorMessage: String?

    private let profileRepository: ProfileRepository
    private let onUnauthorized: () -> Void

    init(profileRepository: ProfileRepository, onUnauthorized: @escaping () -> Void) {
        self.profileRepository = profileRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            media = try await profileRepository.likedMedia()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private struct ProfileDiaryEntriesScreen: View {
    let title: String
    let emptyTitle: String
    let emptyMessage: String
    let viewModel: ProfileDiaryFilterViewModel
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 320)
                    } else if let error = viewModel.errorMessage {
                        DiaryStateCard(title: "Could not load \(title.lowercased())", systemImage: "exclamationmark.triangle", message: error)
                    } else if viewModel.entries.isEmpty {
                        DiaryStateCard(title: emptyTitle, systemImage: "text.bubble", message: emptyMessage)
                    } else {
                        DiaryEntryList(entries: viewModel.entries) { entry in
                            DiaryLogDetailView(
                                entryId: entry.id,
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
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.entries.isEmpty {
                await viewModel.load()
            }
        }
    }
}

@MainActor
@Observable
private final class ProfileTagsViewModel {
    var tags: [DiaryTagSuggestion] = []
    var isLoading = false
    var errorMessage: String?
    var totalTagUses: Int {
        tags.reduce(0) { $0 + $1.usageCount }
    }

    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            tags = try await diaryRepository.allTags(mine: true).sorted { lhs, rhs in
                if lhs.usageCount != rhs.usageCount {
                    return lhs.usageCount > rhs.usageCount
                }
                return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
            }
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func filteredTags(matching query: String) -> [DiaryTagSuggestion] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return tags }
        return tags.filter { $0.name.localizedCaseInsensitiveContains(trimmed) }
    }
}

struct ProfileTagsView: View {
    @State private var viewModel: ProfileTagsViewModel
    @State private var searchText = ""

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileTagsViewModel(diaryRepository: diaryRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 320)
                    } else if let error = viewModel.errorMessage {
                        DiaryStateCard(title: "Could not load tags", systemImage: "exclamationmark.triangle", message: error)
                    } else if viewModel.tags.isEmpty {
                        DiaryStateCard(title: "No tags yet", systemImage: "tag", message: "Tags you add while logging will appear here.")
                    } else {
                        tagsContent
                    }
                }
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle("Tags")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.tags.isEmpty {
                await viewModel.load()
            }
        }
    }

    @ViewBuilder
    private var tagsContent: some View {
        ProfileTagsHeader(tagCount: viewModel.tags.count, totalUses: viewModel.totalTagUses)
        ProfileTagSearchField(text: $searchText)

        let filteredTags = viewModel.filteredTags(matching: searchText)
        if filteredTags.isEmpty {
            DiaryStateCard(
                title: "No matching tags",
                systemImage: "magnifyingglass",
                message: "Try another tag name."
            )
        } else {
            LazyVStack(spacing: 8) {
                ForEach(filteredTags, id: \.name) { tag in
                    NavigationLink {
                        TaggedDiaryView(
                            tag: tag.name,
                            diaryRepository: diaryRepository,
                            mediaRepository: mediaRepository,
                            trackingRepository: trackingRepository,
                            currentUserId: currentUserId,
                            selectedTab: selectedTab,
                            onSelectTab: onSelectTab,
                            onUnauthorized: onUnauthorized
                        )
                    } label: {
                        ProfileTagRow(tag: tag)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

private struct ProfileTagsHeader: View {
    let tagCount: Int
    let totalUses: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(tagCount) \(tagCount == 1 ? "tag" : "tags")")
                .font(.system(size: 28, weight: .black))
                .foregroundStyle(.white)

            Text("\(totalUses) total \(totalUses == 1 ? "log" : "logs") tagged")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 2)
    }
}

private struct ProfileTagSearchField: View {
    @Binding var text: String
    var placeholder = "Search tags"

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.white.opacity(0.54))

            TextField(placeholder, text: $text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .foregroundStyle(.white)
                .submitLabel(.search)

            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.48))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear tag search")
            }
        }
        .font(.system(size: 15, weight: .semibold))
        .padding(.horizontal, 13)
        .frame(height: 44)
        .background(.white.opacity(0.08), in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(0.08))
        }
    }
}

private struct ProfileTagRow: View {
    let tag: DiaryTagSuggestion

    var body: some View {
        HStack(spacing: 8) {
            Text(tag.name)
                .lineLimit(1)

            Spacer(minLength: 8)

            Text("\(tag.usageCount)")
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(.white.opacity(0.72))
                .padding(.horizontal, 7)
                .frame(height: 22)
                .background(.white.opacity(0.12), in: Capsule())
        }
        .font(.system(size: 16, weight: .bold))
        .foregroundStyle(.white.opacity(0.88))
        .padding(.leading, 16)
        .padding(.trailing, 10)
        .frame(height: 52)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.white.opacity(0.11), in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(0.08))
        }
        .contentShape(Capsule())
        .accessibilityLabel("\(tag.name), \(tag.usageCount) \(tag.usageCount == 1 ? "log" : "logs")")
    }
}

@MainActor
@Observable
private final class ProfileListsViewModel {
    var lists: [CustomListSummary] = []
    var isLoading = false
    var isSaving = false
    var errorMessage: String?

    private let listRepository: ListRepository
    private let onUnauthorized: () -> Void

    init(listRepository: ListRepository, onUnauthorized: @escaping () -> Void) {
        self.listRepository = listRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            lists = try await listRepository.list()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func create(_ request: CustomListWriteRequest) async -> Bool {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            _ = try await listRepository.create(request)
            await load()
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

struct ProfileListsView: View {
    @State private var viewModel: ProfileListsViewModel
    @State private var searchText = ""
    @State private var presentedForm: CustomListFormMode?

    private let listRepository: ListRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        listRepository: ListRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.listRepository = listRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileListsViewModel(listRepository: listRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: 12) {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 320)
                    } else if let error = viewModel.errorMessage {
                        DiaryStateCard(title: "Could not load lists", systemImage: "exclamationmark.triangle", message: error)
                    } else if viewModel.lists.isEmpty {
                        DiaryStateCard(title: "No lists yet", systemImage: "list.bullet.rectangle", message: "Custom lists you create will appear here.")
                    } else {
                        listsContent
                    }
                }
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle("Lists")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    presentedForm = .create
                } label: {
                    Image(systemName: "plus")
                }
                .disabled(viewModel.isSaving)
            }
        }
        .sheet(item: $presentedForm) { mode in
            CustomListFormSheet(mode: mode, isSaving: viewModel.isSaving) { request in
                await viewModel.create(request)
            }
        }
        .task {
            if viewModel.lists.isEmpty {
                await viewModel.load()
            }
        }
    }

    @ViewBuilder
    private var listsContent: some View {
        ProfileTagSearchField(text: $searchText, placeholder: "Search lists")

        if filteredLists.isEmpty {
            DiaryStateCard(
                title: "No matching lists",
                systemImage: "magnifyingglass",
                message: "Try another list name."
            )
        } else {
            ForEach(filteredLists) { list in
                NavigationLink {
                    ProfileListDetailView(
                        listId: list.id,
                        listRepository: listRepository,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    ProfileListRow(list: list)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var filteredLists: [CustomListSummary] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return viewModel.lists }
        return viewModel.lists.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }
}

private struct ProfileListRow: View {
    let list: CustomListSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Text(list.name)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(2)

                Spacer(minLength: 0)

                Text("\(list.itemsCount.formatted()) items")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.52))
                    .lineLimit(1)
                    .padding(.top, 3)

                if list.isRanked {
                    Text("Ranked")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.68))
                        .padding(.horizontal, 7)
                        .frame(height: 21)
                        .background(.white.opacity(0.1), in: Capsule())
                        .padding(.top, 1)
                }

                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white.opacity(0.24))
                    .padding(.top, 4)
            }

            posterStrip
        }
        .padding(12)
        .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(0.045), lineWidth: 1)
        }
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private var posterStrip: some View {
        let items = list.previewItems ?? []
        if items.isEmpty {
            Text("No items yet")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.white.opacity(0.38))
                .frame(maxWidth: .infinity, minHeight: PosterSlot.listPreview.size.height, alignment: .leading)
                .padding(.horizontal, 10)
                .background(.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 8) {
                    ForEach(items) { item in
                        MediaArtwork(
                            url: item.displayPosterURL,
                            title: item.title,
                            slot: .listPreview,
                            mediaType: item.ref.mediaType,
                            orientation: item.posterOrientation
                        )
                    }
                }
            }
        }
    }
}

@MainActor
@Observable
private final class ProfileListDetailViewModel {
    var list: CustomListDetail?
    var isLoading = false
    var isSaving = false
    var errorMessage: String?

    private let listId: Int
    private let listRepository: ListRepository
    private let onUnauthorized: () -> Void

    init(listId: Int, listRepository: ListRepository, onUnauthorized: @escaping () -> Void) {
        self.listId = listId
        self.listRepository = listRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            list = try await listRepository.detail(id: listId)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func update(_ request: CustomListWriteRequest) async -> Bool {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            list = try await listRepository.update(id: listId, request)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func deleteList() async -> Bool {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            try await listRepository.delete(id: listId)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func remove(_ item: MediaSummary) async {
        guard let itemId = item.ref.itemId else { return }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            try await listRepository.removeItem(listId: listId, itemId: itemId)
            await load()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func move(from source: IndexSet, to destination: Int) async {
        guard var items = list?.items else { return }
        items.move(fromOffsets: source, toOffset: destination)
        guard items.allSatisfy({ $0.ref.itemId != nil }) else { return }
        list = list.map { current in
            CustomListDetail(
                id: current.id,
                name: current.name,
                slug: current.slug,
                description: current.description,
                visibility: current.visibility,
                isRanked: current.isRanked,
                owner: current.owner,
                imageUrl: current.imageUrl,
                itemsCount: current.itemsCount,
                updatedAt: current.updatedAt,
                likeCount: current.likeCount,
                items: items
            )
        }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            list = try await listRepository.reorderItems(listId: listId, itemIds: items.compactMap(\.ref.itemId))
        } catch {
            errorMessage = error.localizedDescription
            await load()
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private struct ProfileListDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ProfileListDetailViewModel
    @State private var presentedForm: CustomListFormMode?
    @State private var isDeleteAlertPresented = false
    @State private var topSafeAreaInset: CGFloat = 0

    private let listRepository: ListRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        listId: Int,
        listRepository: ListRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.listRepository = listRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileListDetailViewModel(listId: listId, listRepository: listRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 320)
                            .padding(.horizontal, 14)
                            .padding(.top, 12)
                    } else if let error = viewModel.errorMessage {
                        DiaryStateCard(title: "Could not load list", systemImage: "exclamationmark.triangle", message: error)
                            .padding(.horizontal, 14)
                            .padding(.top, 12)
                    } else if let list = viewModel.list {
                        listHeader(list)
                            .padding(.top, -topSafeAreaInset)
                        if list.items.isEmpty {
                            DiaryStateCard(title: "No items yet", systemImage: "square.grid.2x2", message: "Add items from any media detail page.")
                                .padding(.horizontal, 14)
                        } else {
                            mediaGrid(list.items)
                                .padding(.horizontal, 14)
                        }
                    }
                }
                .padding(.bottom, 28)
            }
            .refreshable {
                await viewModel.load()
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)
        }
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: ProfileListTopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(ProfileListTopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Edit List", systemImage: "slider.horizontal.3") {
                        if let list = viewModel.list {
                            presentedForm = .edit(list)
                        }
                    }
                    Button("Delete List", systemImage: "trash", role: .destructive) {
                        isDeleteAlertPresented = true
                    }
                } label: {
                    Image(systemName: "ellipsis")
                }
                .disabled(viewModel.list == nil || viewModel.isSaving)
            }
        }
        .sheet(item: $presentedForm) { mode in
            CustomListFormSheet(
                mode: mode,
                currentList: viewModel.list,
                isSaving: viewModel.isSaving,
                onDeleteItem: { item in
                    await viewModel.remove(item)
                },
                onMoveItem: { source, destination in
                    await viewModel.move(from: source, to: destination)
                }
            ) { request in
                await viewModel.update(request)
            }
        }
        .alert("Delete List?", isPresented: $isDeleteAlertPresented) {
            Button("Delete", role: .destructive) {
                Task {
                    if await viewModel.deleteList() {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes the list. Items stay in your library.")
        }
        .task {
            if viewModel.list == nil {
                await viewModel.load()
            }
        }
    }

    private func listHeader(_ list: CustomListDetail) -> some View {
        let backdropURL = CustomListBackdropSelection.artworkURL(from: list.items)

        return ZStack(alignment: .bottomLeading) {
            if let backdropURL {
                ProfileListBackdropArtwork(urlString: backdropURL)
            }

            listHeaderText(list)
                .padding(.horizontal, 18)
                .padding(.bottom, 20)
                .padding(.top, backdropURL == nil ? 18 : topSafeAreaInset + 112)
        }
        .frame(maxWidth: .infinity, minHeight: backdropURL == nil ? nil : topSafeAreaInset + 308, alignment: .bottomLeading)
        .padding(.top, 10)
    }

    private func listHeaderText(_ list: CustomListDetail) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(list.name)
                .font(.system(size: 34, weight: .black))
                .foregroundStyle(.white)
                .lineLimit(3)
                .minimumScaleFactor(0.72)
                .shadow(color: .black.opacity(0.35), radius: 14, y: 8)

            HStack(spacing: 8) {
                Text("\(list.itemsCount.formatted()) items")
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.72))

                if list.isRanked {
                    Text("Ranked")
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.76))
                        .padding(.horizontal, 8)
                        .frame(height: 22)
                        .background(.white.opacity(0.13), in: Capsule())
                }
            }

            let description = list.description.trimmingCharacters(in: .whitespacesAndNewlines)
            if !description.isEmpty {
                Text(description)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.72))
                    .lineLimit(4)
                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
            }
        }
    }

    private func mediaGrid(_ items: [MediaSummary]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
            ForEach(items) { item in
                NavigationLink {
                    MediaDetailView(
                        ref: item.ref,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    VStack(spacing: 5) {
                        MediaArtwork(
                            url: item.displayPosterURL,
                            title: item.title,
                            slot: .tagGrid,
                            mediaType: item.ref.mediaType,
                            orientation: item.posterOrientation
                        )
                        .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                        if viewModel.list?.isRanked == true, let position = item.position {
                            Text("\(position)")
                                .font(.system(size: 12, weight: .heavy))
                                .monospacedDigit()
                                .foregroundStyle(.white.opacity(0.72))
                                .frame(maxWidth: .infinity)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
        }
    }
}

enum CustomListBackdropSelection {
    static func artworkURL(from items: [MediaSummary]) -> String? {
        items.lazy.compactMap { item -> String? in
            guard item.ref.mediaType == "movie" || item.ref.mediaType == "tv" else { return nil }
            return item.displayBackdropURL
        }.first
    }
}

private struct ProfileListTopSafeAreaInsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct ProfileListBackdropArtwork: View {
    let urlString: String
    private let pageBackground = Color(red: 0.07, green: 0.07, blue: 0.065)

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
                        .init(color: .black.opacity(0.48), location: 0),
                        .init(color: .black.opacity(0.2), location: 0.42),
                        .init(color: pageBackground.opacity(0.1), location: 0.68),
                        .init(color: pageBackground, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .mask(
                LinearGradient(
                    stops: [
                        .init(color: .white, location: 0),
                        .init(color: .white, location: 0.58),
                        .init(color: .white.opacity(0.34), location: 0.82),
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

private enum CustomListFormMode: Identifiable {
    case create
    case edit(CustomListDetail)

    var id: String {
        switch self {
        case .create: "create"
        case let .edit(list): "edit-\(list.id)"
        }
    }

    var title: String {
        switch self {
        case .create: "New List"
        case .edit: "Edit List"
        }
    }
}

private struct CustomListFormSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var description: String
    @State private var visibility: String
    @State private var isRanked: Bool
    @State private var errorMessage: String?

    let mode: CustomListFormMode
    let currentList: CustomListDetail?
    let isSaving: Bool
    let onDeleteItem: (MediaSummary) async -> Void
    let onMoveItem: (IndexSet, Int) async -> Void
    let onSave: (CustomListWriteRequest) async -> Bool

    init(
        mode: CustomListFormMode,
        currentList: CustomListDetail? = nil,
        isSaving: Bool,
        onDeleteItem: @escaping (MediaSummary) async -> Void = { _ in },
        onMoveItem: @escaping (IndexSet, Int) async -> Void = { _, _ in },
        onSave: @escaping (CustomListWriteRequest) async -> Bool
    ) {
        self.mode = mode
        self.currentList = currentList
        self.isSaving = isSaving
        self.onDeleteItem = onDeleteItem
        self.onMoveItem = onMoveItem
        self.onSave = onSave
        switch mode {
        case .create:
            _name = State(initialValue: "")
            _description = State(initialValue: "")
            _visibility = State(initialValue: "private")
            _isRanked = State(initialValue: false)
        case let .edit(list):
            _name = State(initialValue: list.name)
            _description = State(initialValue: list.description)
            _visibility = State(initialValue: list.visibility)
            _isRanked = State(initialValue: list.isRanked)
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 22) {
                    detailsSection
                    settingsSection

                    if let editableList, !editableList.items.isEmpty {
                        editableItemsSection(editableList)
                    }

                    if let errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.red)
                            .padding(14)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 24)
                .padding(.bottom, 34)
            }
            .background(Color.black)
            .navigationTitle(mode.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            await save()
                        }
                    } label: {
                        if isSaving {
                            ProgressView()
                        } else {
                            Text("Save")
                        }
                    }
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSaving)
                }
            }
        }
    }

    private var detailsSection: some View {
        VStack(spacing: 0) {
            TextField("Name", text: $name)
                .font(.system(size: 15, weight: .semibold))
                .padding(.vertical, 13)

            Divider()
                .overlay(.white.opacity(0.12))

            TextField("Description", text: $description, axis: .vertical)
                .font(.system(size: 15, weight: .medium))
                .lineLimit(3, reservesSpace: true)
                .padding(.vertical, 13)
        }
        .padding(.horizontal, 14)
        .background(Color.white.opacity(0.16), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var settingsSection: some View {
        VStack(spacing: 0) {
            Picker("Visibility", selection: $visibility) {
                Text("Public").tag("public")
                Text("Unlisted").tag("unlisted")
                Text("Private").tag("private")
            }
            .font(.system(size: 15, weight: .semibold))
            .padding(.vertical, 9)

            Divider()
                .overlay(.white.opacity(0.12))

            Toggle("Ranked", isOn: $isRanked)
                .font(.system(size: 15, weight: .semibold))
                .padding(.vertical, 9)
        }
        .padding(.horizontal, 14)
        .background(Color.white.opacity(0.16), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var editableList: CustomListDetail? {
        switch mode {
        case .create:
            nil
        case let .edit(list):
            currentList ?? list
        }
    }

    @ViewBuilder
    private func editableItemsSection(_ list: CustomListDetail) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Items")
                .font(.system(size: 15, weight: .heavy))
                .foregroundStyle(.white.opacity(0.52))
                .padding(.horizontal, 2)

            if isRanked {
                CustomListRankedReorderRows(
                    items: list.items,
                    isSaving: isSaving,
                    onDeleteItem: onDeleteItem,
                    onMoveItem: onMoveItem
                )
            } else {
                VStack(spacing: 8) {
                    ForEach(list.items) { item in
                        CustomListEditableItemRow(
                            item: item,
                            isRanked: isRanked,
                            showsDeleteButton: true,
                            onDelete: {
                                Task { await onDeleteItem(item) }
                            }
                        )
                        .frame(height: CustomListReorderMath.rowHeight)
                    }
                }
            }
        }
        .disabled(isSaving)
    }

    private func save() async {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { return }
        errorMessage = nil
        let request = CustomListWriteRequest(
            name: trimmedName,
            description: description.trimmingCharacters(in: .whitespacesAndNewlines),
            visibility: visibility,
            isRanked: isRanked
        )
        if await onSave(request) {
            dismiss()
        } else {
            errorMessage = "Could not save list."
        }
    }
}

private struct CustomListRankedReorderRows: View {
    let items: [MediaSummary]
    let isSaving: Bool
    let onDeleteItem: (MediaSummary) async -> Void
    let onMoveItem: (IndexSet, Int) async -> Void

    @State private var draggedItemId: String?
    @State private var sourceIndex: Int?
    @State private var dragTranslation = 0.0
    @State private var targetIndex: Int?

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                CustomListEditableItemRow(
                    item: item,
                    isRanked: true,
                    showsDeleteButton: true,
                    showsReorderHandle: true,
                    onDelete: {
                        Task { await onDeleteItem(item) }
                    },
                    onReorderChanged: { value in
                        handleReorderChanged(value, item: item, at: index)
                    },
                    onReorderEnded: { value in
                        handleReorderEnded(value)
                    }
                )
                .frame(height: CustomListReorderMath.rowHeight)
                .offset(
                    x: 0,
                    y: Double(index) * CustomListReorderMath.rowHeight
                        + CustomListReorderMath.rowOffset(
                            for: index,
                            sourceIndex: sourceIndex,
                            targetIndex: targetIndex,
                            activeTranslation: dragTranslation,
                            rowHeight: CustomListReorderMath.rowHeight
                        )
                )
                .zIndex(draggedItemId == item.id ? 10 : 0)
                .shadow(color: draggedItemId == item.id ? .black.opacity(0.26) : .clear, radius: 12, y: 6)
                .transaction { transaction in
                    if draggedItemId == item.id {
                        transaction.animation = nil
                    }
                }
                .animation(draggedItemId == item.id ? nil : .snappy(duration: 0.14), value: targetIndex)
            }
        }
        .frame(height: Double(items.count) * CustomListReorderMath.rowHeight, alignment: .topLeading)
        .disabled(isSaving)
    }

    private func handleReorderChanged(_ value: DragGesture.Value, item: MediaSummary, at index: Int) {
        if draggedItemId == nil {
            draggedItemId = item.id
            sourceIndex = index
            targetIndex = index
            UIImpactFeedbackGenerator(style: .light).prepare()
        }
        guard let sourceIndex else { return }
        let translation = CustomListReorderMath.clampedTranslation(
            Double(value.translation.height),
            sourceIndex: sourceIndex,
            count: items.count
        )
        let nextTargetIndex = CustomListReorderMath.targetIndex(
            from: sourceIndex,
            translation: translation,
            count: items.count
        )
        dragTranslation = translation
        if nextTargetIndex != targetIndex {
            targetIndex = nextTargetIndex
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }

    private func handleReorderEnded(_ value: DragGesture.Value) {
        guard let sourceIndex else { resetDrag(); return }
        let destination = CustomListReorderMath.destination(
            from: sourceIndex,
            translation: Double(value.translation.height),
            count: items.count
        )
        resetDrag()
        guard destination != sourceIndex else { return }
        Task {
            await onMoveItem(IndexSet(integer: sourceIndex), destination)
        }
    }

    private func resetDrag() {
        draggedItemId = nil
        sourceIndex = nil
        dragTranslation = 0
        targetIndex = nil
    }
}

private struct CustomListEditableItemRow: View {
    let item: MediaSummary
    let isRanked: Bool
    var showsDeleteButton = false
    var showsReorderHandle = false
    var onDelete: () -> Void = {}
    var onReorderChanged: (DragGesture.Value) -> Void = { _ in }
    var onReorderEnded: (DragGesture.Value) -> Void = { _ in }

    var body: some View {
        HStack(spacing: 12) {
            MediaArtwork(
                url: item.displayPosterURL,
                title: item.title,
                slot: .diaryRow,
                mediaType: item.ref.mediaType,
                orientation: item.posterOrientation
            )
            .scaleEffect(0.75)
            .frame(width: 42, height: 63)

            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(2)

                if isRanked, let position = item.position {
                    Text("#\(position)")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.5))
                }
            }

            Spacer(minLength: 8)

            if showsDeleteButton {
                Button(role: .destructive, action: onDelete) {
                    Image(systemName: "trash")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.red.opacity(0.78))
                        .frame(width: 36, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Remove \(item.title)")
            }

            if showsReorderHandle {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.52))
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 4)
                            .onChanged(onReorderChanged)
                            .onEnded(onReorderEnded)
                    )
                    .accessibilityLabel("Reorder \(item.title)")
            }
        }
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 12))
        .listRowBackground(Color.white.opacity(0.055))
    }
}

enum CustomListReorderMath {
    static let rowHeight = 79.0

    static func clampedTranslation(
        _ translation: Double,
        sourceIndex: Int,
        count: Int,
        rowHeight: Double = rowHeight
    ) -> Double {
        guard count > 1 else { return 0 }
        let minTranslation = -Double(sourceIndex) * rowHeight
        let maxTranslation = Double(count - sourceIndex - 1) * rowHeight
        return min(max(translation, minTranslation), maxTranslation)
    }

    static func targetIndex(
        from sourceIndex: Int,
        translation: Double,
        count: Int,
        rowHeight: Double = rowHeight
    ) -> Int {
        guard count > 1 else { return sourceIndex }
        let clamped = clampedTranslation(
            translation,
            sourceIndex: sourceIndex,
            count: count,
            rowHeight: rowHeight
        )
        return min(
            max(sourceIndex + Int((clamped / rowHeight).rounded()), 0),
            count - 1
        )
    }

    static func destination(
        from sourceIndex: Int,
        translation: Double,
        count: Int,
        rowHeight: Double = rowHeight
    ) -> Int {
        let targetIndex = Self.targetIndex(
            from: sourceIndex,
            translation: translation,
            count: count,
            rowHeight: rowHeight
        )
        return targetIndex > sourceIndex ? targetIndex + 1 : targetIndex
    }

    static func rowOffset(
        for index: Int,
        sourceIndex: Int?,
        targetIndex: Int?,
        activeTranslation: Double,
        rowHeight: Double = rowHeight
    ) -> Double {
        guard let sourceIndex, let targetIndex else { return 0 }
        if index == sourceIndex {
            return activeTranslation
        }
        if targetIndex > sourceIndex, index > sourceIndex, index <= targetIndex {
            return -rowHeight
        }
        if targetIndex < sourceIndex, index >= targetIndex, index < sourceIndex {
            return rowHeight
        }
        return 0
    }
}
