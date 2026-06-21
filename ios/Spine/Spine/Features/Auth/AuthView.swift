import SwiftUI

struct AuthView: View {
    enum Mode: String, CaseIterable, Identifiable {
        case login = "Login"
        case register = "Register"

        var id: String { rawValue }
    }

    private enum Field: Hashable {
        case usernameOrEmail
        case username
        case email
        case password
        case passwordConfirm
    }

    let session: AppSession

    @State private var mode: Mode = .login
    @State private var usernameOrEmail = ""
    @State private var username = ""
    @State private var email = ""
    @State private var password = ""
    @State private var passwordConfirm = ""
    @State private var isSubmitting = false
    @FocusState private var focusedField: Field?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Picker("Mode", selection: $mode) {
                        ForEach(Mode.allCases) { mode in
                            Text(mode.rawValue).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: mode) {
                        clearInactiveFields()
                        focusedField = mode == .login ? .usernameOrEmail : .username
                    }

                    VStack(spacing: 12) {
                        if mode == .login {
                            TextField("Username or email", text: $usernameOrEmail)
                                .focused($focusedField, equals: .usernameOrEmail)
                                .textContentType(.username)
                                .keyboardType(.emailAddress)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .submitLabel(.next)
                                .onSubmit { focusedField = .password }
                        } else {
                            TextField("Username", text: $username)
                                .focused($focusedField, equals: .username)
                                .textContentType(.username)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .submitLabel(.next)
                                .onSubmit { focusedField = .email }

                            TextField("Email", text: $email)
                                .focused($focusedField, equals: .email)
                                .textContentType(.emailAddress)
                                .keyboardType(.emailAddress)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .submitLabel(.next)
                                .onSubmit { focusedField = .password }
                        }

                        SecureField("Password", text: $password)
                            .focused($focusedField, equals: .password)
                            .textContentType(mode == .login ? .password : .newPassword)
                            .submitLabel(mode == .login ? .go : .next)
                            .onSubmit {
                                if mode == .login {
                                    Task { await submit() }
                                } else {
                                    focusedField = .passwordConfirm
                                }
                            }

                        if mode == .register {
                            SecureField("Confirm password", text: $passwordConfirm)
                                .focused($focusedField, equals: .passwordConfirm)
                                .textContentType(.newPassword)
                                .submitLabel(.go)
                                .onSubmit { Task { await submit() } }
                        }
                    }
                    .textFieldStyle(.roundedBorder)
                    .disabled(isSubmitting)

                    if let error = session.errorMessage {
                        Text(error)
                            .font(.callout)
                            .foregroundStyle(.red)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityLabel("Authentication error")
                    }

                    #if DEBUG
                    Text("API: \(AppConfig.apiBaseURL.absoluteString)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    #endif

                    Button {
                        Task { await submit() }
                    } label: {
                        if isSubmitting {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Text(mode.rawValue)
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(!canSubmit || isSubmitting)
                }
                .padding(20)
                .frame(maxWidth: 480, alignment: .leading)
            }
            .navigationTitle("Spine")
            .scrollDismissesKeyboard(.interactively)
            .task {
                focusedField = mode == .login ? .usernameOrEmail : .username
            }
        }
    }

    private var canSubmit: Bool {
        switch mode {
        case .login:
            !usernameOrEmail.isEmpty && !password.isEmpty
        case .register:
            !username.isEmpty && !email.isEmpty && !password.isEmpty && password == passwordConfirm
        }
    }

    private func submit() async {
        guard canSubmit, !isSubmitting else { return }
        focusedField = nil
        isSubmitting = true
        defer { isSubmitting = false }
        switch mode {
        case .login:
            await session.login(usernameOrEmail: usernameOrEmail, password: password)
        case .register:
            await session.register(username: username, email: email, password: password)
        }
    }

    private func clearInactiveFields() {
        switch mode {
        case .login:
            username = ""
            email = ""
            passwordConfirm = ""
        case .register:
            usernameOrEmail = ""
        }
        password = ""
    }
}

#Preview {
    AuthView(session: AppSession(repositories: .live()))
}
