import SwiftUI

struct AppShellView: View {
    let session: AppSession

    var body: some View {
        TabView {
            SearchView(
                mediaRepository: session.repositories.media,
                trackingRepository: session.repositories.tracking,
                diaryRepository: session.repositories.diary,
                onUnauthorized: unauthorized
            )
            .tabItem {
                Label("Search", systemImage: "magnifyingglass")
            }

            LibraryView(
                mediaRepository: session.repositories.media,
                trackingRepository: session.repositories.tracking,
                onUnauthorized: unauthorized
            )
            .tabItem {
                Label("Library", systemImage: "books.vertical")
            }

            DiaryView(
                diaryRepository: session.repositories.diary,
                mediaRepository: session.repositories.media,
                onUnauthorized: unauthorized
            )
            .tabItem {
                Label("Diary", systemImage: "calendar")
            }

            ProfileView(
                profileRepository: session.repositories.profile,
                onLogout: {
                    Task { await session.logout() }
                },
                onUnauthorized: unauthorized
            )
            .tabItem {
                Label("Profile", systemImage: "person.crop.circle")
            }
        }
    }

    private func unauthorized() {
        Task { await session.logout() }
    }
}

#Preview {
    AppShellView(session: AppSession(repositories: .live()))
}
