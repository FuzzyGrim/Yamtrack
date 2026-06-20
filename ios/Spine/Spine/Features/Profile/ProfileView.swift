import SwiftUI

@MainActor
@Observable
final class ProfileViewModel {
    var profile: UserProfile?
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
            profile = try await profileRepository.me()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct ProfileView: View {
    @State private var viewModel: ProfileViewModel
    let onLogout: () -> Void

    init(profileRepository: ProfileRepository, onLogout: @escaping () -> Void, onUnauthorized: @escaping () -> Void = {}) {
        _viewModel = State(initialValue: ProfileViewModel(profileRepository: profileRepository, onUnauthorized: onUnauthorized))
        self.onLogout = onLogout
    }

    var body: some View {
        NavigationStack {
            List {
                if viewModel.isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                } else if let error = viewModel.errorMessage {
                    Text(error)
                        .foregroundStyle(.red)
                } else if let profile = viewModel.profile {
                    profileHeader(profile)
                    counts(profile.counts)
                    preferences(profile.preferences)
                    hof(profile.hof)
                }
            }
            .navigationTitle("Profile")
            .toolbar {
                Button(role: .destructive) {
                    onLogout()
                } label: {
                    Label("Logout", systemImage: "rectangle.portrait.and.arrow.right")
                }
            }
            .task {
                await viewModel.load()
            }
            .refreshable {
                await viewModel.load()
            }
        }
    }

    private func profileHeader(_ profile: UserProfile) -> some View {
        Section {
            HStack(spacing: 16) {
                AsyncImage(url: URL(string: profile.avatarUrl ?? "")) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                    } else {
                        Image(systemName: "person.crop.circle.fill")
                            .resizable()
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(width: 72, height: 72)
                .clipShape(Circle())

                VStack(alignment: .leading, spacing: 4) {
                    Text(profile.displayName)
                        .font(.title3.weight(.bold))
                    Text("@\(profile.username)")
                        .foregroundStyle(.secondary)
                    if profile.isPrivate {
                        Label("Private", systemImage: "lock.fill")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            if let bio = profile.bio, !bio.isEmpty {
                Text(bio)
            }
            if let location = profile.location, !location.isEmpty {
                Label(location, systemImage: "mappin.and.ellipse")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func counts(_ counts: ProfileCounts) -> some View {
        Section("Counts") {
            LabeledContent("Followers", value: "\(counts.followers)")
            LabeledContent("Following", value: "\(counts.following)")
            LabeledContent("Diary entries", value: "\(counts.diaryEntries)")
            LabeledContent("Lists", value: "\(counts.lists)")
        }
    }

    private func preferences(_ preferences: UserPreferences) -> some View {
        Section("Preferences") {
            Text(preferences.enabledMediaTypes.map(\.capitalized).joined(separator: ", "))
            LabeledContent("Release notifications", value: preferences.releaseNotificationsEnabled ? "On" : "Off")
            LabeledContent("Daily digest", value: preferences.dailyDigestEnabled ? "On" : "Off")
        }
    }

    private func hof(_ hof: [String: MediaSummary?]) -> some View {
        Section("Hall of Fame") {
            let items = hof.compactMap { key, value -> (String, MediaSummary)? in
                guard let value else { return nil }
                return (key, value)
            }

            if items.isEmpty {
                Text("No Hall of Fame items yet.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(items, id: \.0) { key, item in
                    HStack {
                        PosterImage(urlString: item.imageUrl, title: item.title)
                            .frame(width: 44)
                        VStack(alignment: .leading) {
                            Text(item.title)
                            Text(key.capitalized)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }
}
