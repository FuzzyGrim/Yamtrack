import Foundation
import Security

/// Persists JWT access/refresh tokens for authenticated API calls.
final class KeychainTokenStore: @unchecked Sendable {
    static let shared = KeychainTokenStore()

    private let accessKey = "spine.accessToken"
    private let refreshKey = "spine.refreshToken"

    private init() {}

    var accessToken: String? {
        get { read(key: accessKey) }
        set { write(key: accessKey, value: newValue) }
    }

    var refreshToken: String? {
        get { read(key: refreshKey) }
        set { write(key: refreshKey, value: newValue) }
    }

    func clear() {
        accessToken = nil
        refreshToken = nil
    }

    private func read(key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private func write(key: String, value: String?) {
        let deleteQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(deleteQuery as CFDictionary)

        guard let value, let data = value.data(using: .utf8) else { return }

        let addQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        SecItemAdd(addQuery as CFDictionary, nil)
    }
}
