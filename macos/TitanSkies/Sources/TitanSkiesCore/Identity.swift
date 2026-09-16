import Foundation

public enum TitanSkiesIdentity {
    public static let bundleIdentifier = "com.hypertrial.titanskies"
    public static let appName = "TitanSkies"
    public static let betaWebLabel = "com.hypertrial.titanskies.beta.web"
    public static let betaIngestLabel = "com.hypertrial.titanskies.beta.ingest"
    public static let productionWebLabel = "com.hypertrial.titanskies.web"
    public static let productionIngestLabel = "com.hypertrial.titanskies.ingest"
    public static let nativeUserAgentToken = "TitanSkiesNative/"
    public static let bindAddress = "127.0.0.1"
    public static let port = 8080
    public static let updateKeyId = "titanskies-macos-ed25519-1"
    public static let updatePublicKeyHex = "f95139626e4beea0594b72e50908da1c672c90f88b098c0d70f39fd30c6826ea"
    public static let ingestMinutes = [0, 15, 30, 45]
    public static let allowedEnvKeys: Set<String> = [
        "CONTEXT_SOURCE",
        "TITANSKIES_DATA_DIR",
        "TITANSKIES_CACHE_DIR",
        "TITANSKIES_BIND_ADDR",
        "TITANSKIES_PORT",
        "CONTEXT_WATCH_SECONDS",
        "FRAME_RETENTION_HOURS",
        "AIRNOW_API_KEY",
        "INGEST_BUDGET_SECONDS",
        "INGEST_HTTP_CONCURRENCY",
        "CONTEXT_SOURCE_CONCURRENCY",
        "HRRR_CONCURRENCY",
        "FIREWORK_CONCURRENCY",
        "SINAICA_CONCURRENCY",
    ]
    public static let secretEnvKeys: Set<String> = ["AIRNOW_API_KEY"]
}

public enum Channel: String, Sendable {
    case unsignedBeta = "unsigned-beta"
    case production = "production"

    public var showsUnsignedWarning: Bool { self == .unsignedBeta }

    public static func load(fromApp app: URL) -> Channel {
        let lockURL = app.appendingPathComponent("Contents/Resources/runtime-lock.json")
        guard let data = try? Data(contentsOf: lockURL),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = object["channel"] as? String,
              let channel = Channel(rawValue: raw) else {
            return .unsignedBeta
        }
        return channel
    }
}

public struct SemanticVersion: Comparable, Sendable {
    public let major: Int
    public let minor: Int
    public let patch: Int

    public init(_ raw: String) throws {
        let trimmed = raw.hasPrefix("v") ? String(raw.dropFirst()) : raw
        let parts = trimmed.split(separator: ".")
        guard parts.count == 3, let major = Int(parts[0]), let minor = Int(parts[1]), let patch = Int(parts[2]) else {
            throw TitanSkiesError.invalidVersion(raw)
        }
        self.major = major
        self.minor = minor
        self.patch = patch
    }

    public static func < (lhs: SemanticVersion, rhs: SemanticVersion) -> Bool {
        (lhs.major, lhs.minor, lhs.patch) < (rhs.major, rhs.minor, rhs.patch)
    }
}

public enum TitanSkiesError: Error, Equatable, LocalizedError {
    case invalidEnv(String)
    case unsafePath(String)
    case untrustedInstallSource(String)
    case portConflict(Int)
    case foreignService
    case invalidVersion(String)
    case updateRejected(String)
    case serviceApprovalRequired
    case launchd(String)
    case missingBundleResource(String)
    case healthCheckFailed(String)
    case rollbackFailed(String)

    public var errorDescription: String? {
        switch self {
        case .invalidEnv(let message),
             .unsafePath(let message),
             .untrustedInstallSource(let message),
             .invalidVersion(let message),
             .updateRejected(let message),
             .launchd(let message),
             .missingBundleResource(let message),
             .healthCheckFailed(let message),
             .rollbackFailed(let message):
            return message
        case .portConflict(let port):
            return "Port \(port) is already in use by another process."
        case .foreignService:
            return "A different service is answering on 127.0.0.1:8080."
        case .serviceApprovalRequired:
            return "Allow TitanSkies background items in System Settings, then choose Start again."
        }
    }
}
