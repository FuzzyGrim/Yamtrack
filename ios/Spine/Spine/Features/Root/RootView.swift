import SwiftUI

struct RootView: View {
    @State private var session: AppSession

    init(repositories: AppRepositories = .current()) {
        _session = State(initialValue: AppSession(repositories: repositories))
    }

    var body: some View {
        Group {
            switch session.state {
            case .checking:
                ProgressView("Checking session")
            case .signedOut:
                AuthView(session: session)
            case .signedIn:
                AppShellView(session: session)
            }
        }
        .task {
            await session.start()
        }
    }
}

#Preview {
    RootView()
}
