import SwiftUI

struct AppShellView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab = AppTab.home
    @State private var requestedLibraryShelf: LibraryShelf?

    let session: AppSession

    var body: some View {
        TabView(selection: $selectedTab) {
            HomeView(
                profileRepository: session.repositories.profile,
                mediaRepository: session.repositories.media,
                trackingRepository: session.repositories.tracking,
                diaryRepository: session.repositories.diary,
                listRepository: session.repositories.lists,
                selectedTab: selectedTab,
                onSelectTab: { selectedTab = $0 },
                onUnauthorized: unauthorized
            )
            .tabItem {
                Label("Home", systemImage: "house")
            }
            .tag(AppTab.home)

            LazyTab(isSelected: selectedTab == .search) {
                SearchView(
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    diaryRepository: session.repositories.diary,
                    listRepository: session.repositories.lists,
                    selectedTab: selectedTab,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .tabItem {
                Label("Search", systemImage: "magnifyingglass")
            }
            .tag(AppTab.search)

            LazyTab(isSelected: selectedTab == .library) {
                LibraryView(
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    diaryRepository: session.repositories.diary,
                    listRepository: session.repositories.lists,
                    requestedShelf: $requestedLibraryShelf,
                    selectedTab: selectedTab,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .tabItem {
                Label("Library", systemImage: "books.vertical")
            }
            .tag(AppTab.library)

            LazyTab(isSelected: selectedTab == .diary) {
                DiaryView(
                    diaryRepository: session.repositories.diary,
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    selectedTab: selectedTab,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .tabItem {
                Label("Diary", systemImage: "calendar")
            }
            .tag(AppTab.diary)

            LazyTab(isSelected: selectedTab == .profile) {
                ProfileView(
                    profileRepository: session.repositories.profile,
                    diaryRepository: session.repositories.diary,
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    listRepository: session.repositories.lists,
                    importCoordinator: session.letterboxdImportCoordinator,
                    storygraphImportCoordinator: session.storygraphImportCoordinator,
                    onLogout: {
                        Task { await session.logout() }
                    },
                    onOpenDiary: {
                        selectedTab = .diary
                    },
                    onOpenLibrary: { shelf in
                        requestedLibraryShelf = shelf
                        selectedTab = .library
                    },
                    selectedTab: selectedTab,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .tabItem {
                Label("Profile", systemImage: "person.crop.circle")
            }
            .tag(AppTab.profile)
        }
        .onChange(of: scenePhase) {
            guard scenePhase == .active else { return }
            session.letterboxdImportCoordinator.resumeIfNeeded()
            session.storygraphImportCoordinator.resumeIfNeeded()
        }
    }

    private func unauthorized() {
        Task { await session.logout() }
    }
}

enum AppTab: Hashable {
    case home
    case search
    case library
    case diary
    case profile
}

private struct LazyTab<Content: View>: View {
    let isSelected: Bool
    @ViewBuilder let content: () -> Content

    @State private var hasLoaded = false

    var body: some View {
        Group {
            if isSelected || hasLoaded {
                content()
            } else {
                Color.clear
            }
        }
        .onChange(of: isSelected, initial: true) {
            if isSelected {
                hasLoaded = true
            }
        }
    }
}

#Preview {
    AppShellView(session: AppSession(repositories: .live()))
}
