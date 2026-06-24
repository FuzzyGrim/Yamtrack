import SwiftUI

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
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
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
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }
}

struct ProfileLikesView: View {
    @State private var viewModel: ProfileDiaryFilterViewModel

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileDiaryFilterViewModel(
            filter: DiaryFilter(liked: true),
            diaryRepository: diaryRepository,
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
                    } else if likedMedia.isEmpty {
                        DiaryStateCard(title: "No liked media yet", systemImage: "heart", message: "Media you like while logging will appear here.")
                    } else {
                        mediaGrid(likedMedia)
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
            if viewModel.entries.isEmpty {
                await viewModel.load()
            }
        }
    }

    private var likedMedia: [TaggedDiaryMedia] {
        TaggedDiaryViewModel.uniqueMedia(from: viewModel.entries)
    }

    private func mediaGrid(_ media: [TaggedDiaryMedia]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
            ForEach(media) { item in
                NavigationLink {
                    MediaDetailView(
                        ref: item.media.ref,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    MediaArtwork(
                        url: item.media.displayPosterURL,
                        title: item.media.title,
                        slot: .tagGrid,
                        mediaType: item.media.ref.mediaType,
                        orientation: item.media.posterOrientation
                    )
                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(item.media.title)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
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
            FlowLayout(spacing: 9) {
                ForEach(filteredTags, id: \.name) { tag in
                    NavigationLink {
                        TaggedDiaryView(
                            tag: tag.name,
                            diaryRepository: diaryRepository,
                            mediaRepository: mediaRepository,
                            trackingRepository: trackingRepository,
                            selectedTab: selectedTab,
                            onSelectTab: onSelectTab,
                            onUnauthorized: onUnauthorized
                        )
                    } label: {
                        ProfileTagPill(tag: tag)
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

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.white.opacity(0.54))

            TextField("Search tags", text: $text)
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

private struct ProfileTagPill: View {
    let tag: DiaryTagSuggestion

    var body: some View {
        HStack(spacing: 8) {
            Text(tag.name)
                .lineLimit(1)

            Text("\(tag.usageCount)")
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(.white.opacity(0.72))
                .padding(.horizontal, 7)
                .frame(height: 22)
                .background(.white.opacity(0.12), in: Capsule())
        }
        .font(.system(size: 16, weight: .bold))
        .foregroundStyle(.white.opacity(0.88))
        .padding(.leading, 14)
        .padding(.trailing, 8)
        .frame(height: 38)
        .background(.white.opacity(0.11), in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(0.08))
        }
        .accessibilityLabel("\(tag.name), \(tag.usageCount) \(tag.usageCount == 1 ? "log" : "logs")")
    }
}

@MainActor
@Observable
private final class ProfileListsViewModel {
    var lists: [CustomListSummary] = []
    var isLoading = false
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
}

struct ProfileListsView: View {
    @State private var viewModel: ProfileListsViewModel

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
                LazyVStack(spacing: 6) {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 320)
                    } else if let error = viewModel.errorMessage {
                        DiaryStateCard(title: "Could not load lists", systemImage: "exclamationmark.triangle", message: error)
                    } else if viewModel.lists.isEmpty {
                        DiaryStateCard(title: "No lists yet", systemImage: "list.bullet.rectangle", message: "Custom lists you create will appear here.")
                    } else {
                        ForEach(viewModel.lists) { list in
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
        .task {
            if viewModel.lists.isEmpty {
                await viewModel.load()
            }
        }
    }
}

private struct ProfileListRow: View {
    let list: CustomListSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                Text(list.name)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(2)

                Spacer(minLength: 0)

                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white.opacity(0.24))
                    .padding(.top, 4)
            }

            VStack(alignment: .leading, spacing: 10) {
                Text("\(list.itemsCount.formatted()) items")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.white.opacity(0.46))

                posterStrip
            }
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
                .frame(maxWidth: .infinity, minHeight: PosterSlot.profileRow.size.height, alignment: .leading)
                .padding(.horizontal, 10)
                .background(.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 8) {
                    ForEach(items) { item in
                        MediaArtwork(
                            url: item.displayPosterURL,
                            title: item.title,
                            slot: .profileRow,
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
}

private struct ProfileListDetailView: View {
    @State private var viewModel: ProfileListDetailViewModel

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
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileListDetailViewModel(listId: listId, listRepository: listRepository, onUnauthorized: onUnauthorized))
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
                        DiaryStateCard(title: "Could not load list", systemImage: "exclamationmark.triangle", message: error)
                    } else if let list = viewModel.list {
                        listHeader(list)
                        if list.items.isEmpty {
                            DiaryStateCard(title: "No items yet", systemImage: "square.grid.2x2", message: "Media added to this list will appear here.")
                        } else {
                            mediaGrid(list.items)
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
        .navigationTitle(viewModel.list?.name ?? "List")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.list == nil {
                await viewModel.load()
            }
        }
    }

    private func listHeader(_ list: CustomListDetail) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(list.name)
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(2)

            Text("\(list.itemsCount.formatted()) items")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.white.opacity(0.48))

            let description = list.description.trimmingCharacters(in: .whitespacesAndNewlines)
            if !description.isEmpty {
                Text(description)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white.opacity(0.66))
                    .lineLimit(4)
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
            }
        }
    }
}
