import Foundation

public enum NavigationDecision: Equatable, Sendable {
    case allow
    case openInBrowser
    case reject
}

public enum NavigationPolicy {
    public static func decide(url: URL, port: Int = TitanSkiesIdentity.port) -> NavigationDecision {
        let scheme = url.scheme?.lowercased() ?? ""
        if scheme == "http", url.host == TitanSkiesIdentity.bindAddress, url.port == port || (url.port == nil && port == 80) {
            return .allow
        }
        if scheme == "http", url.host == TitanSkiesIdentity.bindAddress, (url.port ?? 80) == port {
            return .allow
        }
        if scheme == "https" {
            return .openInBrowser
        }
        return .reject
    }

    public static func nativeUserAgent(version: String) -> String {
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) \(TitanSkiesIdentity.nativeUserAgentToken)\(version)"
    }
}
