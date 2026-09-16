import Foundation

public struct TitanSkiesPaths: Sendable {
    public let home: URL
    public let app: URL

    public init(home: URL, app: URL) {
        self.home = home
        self.app = app
    }

    public static func live() throws -> TitanSkiesPaths {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let bundle = Bundle.main.bundleURL
        let app = bundle.pathExtension == "app" ? bundle : home.appendingPathComponent("Applications/TitanSkies.app")
        return TitanSkiesPaths(home: home, app: app)
    }

    public var applications: URL { home.appendingPathComponent("Applications") }
    public var installedApp: URL { applications.appendingPathComponent("TitanSkies.app") }
    public var support: URL { home.appendingPathComponent("Library/Application Support/TitanSkies") }
    public var config: URL { support.appendingPathComponent("config") }
    public var env: URL { config.appendingPathComponent("env") }
    public var data: URL { support.appendingPathComponent("data") }
    public var rollback: URL { support.appendingPathComponent("rollback") }
    public var updateStatus: URL { support.appendingPathComponent("update-status.txt") }
    public var updateLog: URL { logs.appendingPathComponent("update.log") }
    public var cache: URL { home.appendingPathComponent("Library/Caches/TitanSkies") }
    public var logs: URL { home.appendingPathComponent("Library/Logs/TitanSkies") }
    public var webLog: URL { logs.appendingPathComponent("web.log") }
    public var ingestLog: URL { logs.appendingPathComponent("ingest.log") }
    public var agents: URL { home.appendingPathComponent("Library/LaunchAgents") }
    public var betaWebPlist: URL { agents.appendingPathComponent("\(TitanSkiesIdentity.betaWebLabel).plist") }
    public var betaIngestPlist: URL { agents.appendingPathComponent("\(TitanSkiesIdentity.betaIngestLabel).plist") }
    public var contents: URL { app.appendingPathComponent("Contents") }
    public var macos: URL { contents.appendingPathComponent("MacOS") }
    public var webLauncher: URL { macos.appendingPathComponent("TitanSkiesWeb") }
    public var ingestLauncher: URL { macos.appendingPathComponent("TitanSkiesIngest") }
    public var updater: URL { macos.appendingPathComponent("TitanSkiesUpdater") }
    public var node: URL { contents.appendingPathComponent("Resources/Host/node/bin/node") }
    public var python: URL { contents.appendingPathComponent("Resources/Host/python/bin/python3") }
    public var runtime: URL { contents.appendingPathComponent("Resources/Runtime") }
    public var infoPlist: URL { contents.appendingPathComponent("Info.plist") }
    public var productionWebPlist: URL { contents.appendingPathComponent("Library/LaunchAgents/\(TitanSkiesIdentity.productionWebLabel).plist") }
    public var productionIngestPlist: URL { contents.appendingPathComponent("Library/LaunchAgents/\(TitanSkiesIdentity.productionIngestLabel).plist") }

    public func createPrivateDirectories() throws {
        let manager = FileManager.default
        try create(support, mode: 0o700)
        try create(config, mode: 0o700)
        try create(data, mode: 0o700)
        try create(rollback, mode: 0o700)
        try create(cache, mode: 0o700)
        try create(logs, mode: 0o700)
        try create(agents, mode: 0o755)
        _ = manager
    }

    private func create(_ url: URL, mode: Int) throws {
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: url.path)
    }
}

public enum PathPolicy {
    public static func isUntrustedInstallSource(_ url: URL) -> Bool {
        let path = url.path
        return path.hasPrefix("/Volumes/")
            || path.contains("AppTranslocation")
            || path.contains("/.Trash/")
            || path.hasSuffix(".dmg")
    }

    public static func rejectUnsafe(_ raw: String, app: URL, home: URL) throws -> URL {
        if raw.contains("\0") || raw.unicodeScalars.contains(where: { $0.value < 32 }) {
            throw TitanSkiesError.unsafePath("path contains control characters")
        }
        guard raw.hasPrefix("/") else {
            throw TitanSkiesError.unsafePath("path must be absolute")
        }
        let url = URL(fileURLWithPath: raw)
        if url.path.hasPrefix(app.path) {
            throw TitanSkiesError.unsafePath("path must not be inside the app bundle")
        }
        let rollback = TitanSkiesPaths(home: home, app: app).rollback
        if url.path.hasPrefix(rollback.path) {
            throw TitanSkiesError.unsafePath("path must not use the rollback area")
        }
        return url
    }

    public static func rejectSymlink(_ url: URL, label: String) throws {
        let values = try? url.resourceValues(forKeys: [.isSymbolicLinkKey])
        if values?.isSymbolicLink == true {
            throw TitanSkiesError.unsafePath("\(label) path must not be a symlink")
        }
    }

    public static func requireInsideApp(_ url: URL, app: URL, label: String) throws {
        let root = app.resolvingSymlinksInPath().standardizedFileURL.path
        let resolved = url.resolvingSymlinksInPath().standardizedFileURL.path
        guard resolved.hasPrefix(root + "/") else {
            throw TitanSkiesError.unsafePath("\(label) must resolve inside the application bundle")
        }
    }
}
