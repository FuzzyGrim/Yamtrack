import SwiftUI

struct AppShellView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab = AppTab.search

    let session: AppSession

    var body: some View {
        TabView(selection: $selectedTab) {
            SearchView(
                mediaRepository: session.repositories.media,
                trackingRepository: session.repositories.tracking,
                diaryRepository: session.repositories.diary,
                selectedTab: selectedTab,
                onSelectTab: { selectedTab = $0 },
                onUnauthorized: unauthorized
            )
            .tabItem {
                Label("Search", systemImage: "magnifyingglass")
            }
            .tag(AppTab.search)

            LazyTab(isSelected: selectedTab == .library) {
                LibraryView(
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
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
                    importCoordinator: session.letterboxdImportCoordinator,
                    onLogout: {
                        Task { await session.logout() }
                    },
                    onOpenDiary: {
                        selectedTab = .diary
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
        }
    }

    private func unauthorized() {
        Task { await session.logout() }
    }
}

enum AppTab: Hashable {
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
