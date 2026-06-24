import Foundation

@MainActor
@Observable
final class AppSession {
    enum State {
        case checking
        case signedOut
        case signedIn(AuthUser?)
    }

    var state: State = .checking
    var errorMessage: String?

    let repositories: AppRepositories
    let letterboxdImportCoordinator: LetterboxdImportCoordinator
    let storygraphImportCoordinator: StoryGraphImportCoordinator

    init(repositories: AppRepositories) {
        self.repositories = repositories
        self.letterboxdImportCoordinator = LetterboxdImportCoordinator(importRepository: repositories.imports)
        self.storygraphImportCoordinator = StoryGraphImportCoordinator(importRepository: repositories.imports)
        self.letterboxdImportCoordinator.onUnauthorized = { [weak self] in
            Task { await self?.logout() }
        }
        self.storygraphImportCoordinator.onUnauthorized = { [weak self] in
            Task { await self?.logout() }
        }
    }

    func start() async {
        guard repositories.auth.hasStoredTokens else {
            state = .signedOut
            return
        }

        do {
            try await repositories.auth.refresh()
            state = .signedIn(nil)
            letterboxdImportCoordinator.resumeIfNeeded()
            storygraphImportCoordinator.resumeIfNeeded()
        } catch {
            await repositories.auth.logout()
            errorMessage = error.localizedDescription
            state = .signedOut
        }
    }

    func login(usernameOrEmail: String, password: String) async {
        errorMessage = nil
        do {
            let user = try await repositories.auth.login(usernameOrEmail: usernameOrEmail, password: password)
            state = .signedIn(user)
            letterboxdImportCoordinator.resumeIfNeeded()
            storygraphImportCoordinator.resumeIfNeeded()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func register(username: String, email: String, password: String) async {
        errorMessage = nil
        do {
            let user = try await repositories.auth.register(username: username, email: email, password: password)
            state = .signedIn(user)
            letterboxdImportCoordinator.resumeIfNeeded()
            storygraphImportCoordinator.resumeIfNeeded()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func logout() async {
        await repositories.auth.logout()
        letterboxdImportCoordinator.clearFinishedJob()
        storygraphImportCoordinator.clearFinishedJob()
        state = .signedOut
    }
}
