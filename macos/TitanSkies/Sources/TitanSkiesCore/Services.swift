import Foundation

public struct ServiceManager: Sendable {
    public let paths: TitanSkiesPaths
    public let launchd: LaunchdClient
    public let health: ServiceHealth
    public let channel: Channel
    public let portOccupier: @Sendable () -> String?

    public init(
        paths: TitanSkiesPaths,
        launchd: LaunchdClient = LaunchdClient(),
        health: ServiceHealth = ServiceHealth(),
        channel: Channel = .unsignedBeta,
        portOccupier: @escaping @Sendable () -> String? = { PortProbe.occupier() }
    ) {
        self.paths = paths
        self.launchd = launchd
        self.health = health
        self.channel = channel
        self.portOccupier = portOccupier
    }

    public func writeBetaAgents() throws {
        try LaunchdPlist.write(LaunchdPlist.betaWeb(paths: paths), to: paths.betaWebPlist)
        try LaunchdPlist.write(LaunchdPlist.betaIngest(paths: paths), to: paths.betaIngestPlist)
    }

    public func start() throws {
        if paths.app.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("never execute the rollback app from Application Support")
        }
        if PathPolicy.isUntrustedInstallSource(paths.app) {
            throw TitanSkiesError.untrustedInstallSource("refuse to register services from \(paths.app.path)")
        }
        for (label, executable) in [
            ("web launcher", paths.webLauncher),
            ("ingest launcher", paths.ingestLauncher),
            ("Node", paths.node),
            ("Python", paths.python),
        ] {
            try PathPolicy.requireInsideApp(executable, app: paths.app, label: label)
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw TitanSkiesError.missingBundleResource(executable.path)
            }
        }
        try PathPolicy.requireInsideApp(paths.runtime, app: paths.app, label: "runtime")
        try LogRotation.rotate(at: paths.webLog)
        try LogRotation.rotate(at: paths.ingestLog)
        let webLabel = channel == .unsignedBeta
            ? TitanSkiesIdentity.betaWebLabel
            : TitanSkiesIdentity.productionWebLabel
        let ingestLabel = channel == .unsignedBeta
            ? TitanSkiesIdentity.betaIngestLabel
            : TitanSkiesIdentity.productionIngestLabel
        if let listener = portOccupier() {
            guard launchd.ownsListener(webLabel, executable: paths.webLauncher, listener: listener) else {
                throw TitanSkiesError.foreignService
            }
        }
        if channel == .unsignedBeta {
            try writeBetaAgents()
            try launchd.bootstrap(plist: paths.betaWebPlist)
            try launchd.bootstrap(plist: paths.betaIngestPlist)
            try launchd.kickstart(TitanSkiesIdentity.betaWebLabel)
            try launchd.kickstart(TitanSkiesIdentity.betaIngestLabel)
        } else {
            try launchd.kickstart(TitanSkiesIdentity.productionWebLabel)
            try launchd.kickstart(TitanSkiesIdentity.productionIngestLabel)
        }
        _ = try health.waitForWeb()
        guard let listener = portOccupier(),
              launchd.ownsListener(webLabel, executable: paths.webLauncher, listener: listener) else {
            throw TitanSkiesError.foreignService
        }
    }

    public func stop() throws {
        guard channel == .unsignedBeta else {
            throw TitanSkiesError.launchd("production agents must be stopped through SMAppService")
        }
        try launchd.bootout(TitanSkiesIdentity.betaWebLabel)
        try launchd.bootout(TitanSkiesIdentity.betaIngestLabel)
    }

    public func restart() throws {
        if channel == .unsignedBeta {
            try stop()
        }
        try start()
    }

    public func repair() throws {
        if channel == .unsignedBeta {
            try stop()
        }
        try paths.createPrivateDirectories()
        if FileManager.default.fileExists(atPath: paths.betaWebPlist.path) {
            try FileManager.default.removeItem(at: paths.betaWebPlist)
        }
        if FileManager.default.fileExists(atPath: paths.betaIngestPlist.path) {
            try FileManager.default.removeItem(at: paths.betaIngestPlist)
        }
        try start()
    }

    public func migrateBetaToProduction() throws {
        try launchd.bootout(TitanSkiesIdentity.betaWebLabel)
        try launchd.bootout(TitanSkiesIdentity.betaIngestLabel)
        if FileManager.default.fileExists(atPath: paths.betaWebPlist.path) {
            try FileManager.default.removeItem(at: paths.betaWebPlist)
        }
        if FileManager.default.fileExists(atPath: paths.betaIngestPlist.path) {
            try FileManager.default.removeItem(at: paths.betaIngestPlist)
        }
    }
}

public struct AppInstaller: Sendable {
    public let paths: TitanSkiesPaths

    public init(paths: TitanSkiesPaths) {
        self.paths = paths
    }

    public func installIfNeeded() throws -> URL {
        if PathPolicy.isUntrustedInstallSource(paths.app) {
            try FileManager.default.createDirectory(at: paths.applications, withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: paths.installedApp.path) {
                try FileManager.default.removeItem(at: paths.installedApp)
            }
            try FileManager.default.copyItem(at: paths.app, to: paths.installedApp)
            return paths.installedApp
        }
        return paths.app
    }
}

public struct AppUninstaller: Sendable {
    public let paths: TitanSkiesPaths
    public let launchd: LaunchdClient

    public init(paths: TitanSkiesPaths, launchd: LaunchdClient = LaunchdClient()) {
        self.paths = paths
        self.launchd = launchd
    }

    public func uninstall(purge: Bool) throws {
        try launchd.bootout(TitanSkiesIdentity.betaWebLabel)
        try launchd.bootout(TitanSkiesIdentity.betaIngestLabel)
        for label in [
            TitanSkiesIdentity.betaWebLabel,
            TitanSkiesIdentity.betaIngestLabel,
            TitanSkiesIdentity.productionWebLabel,
            TitanSkiesIdentity.productionIngestLabel,
        ] where launchd.isLoaded(label) {
            throw TitanSkiesError.launchd("service \(label) is still loaded")
        }
        for plist in [paths.betaWebPlist, paths.betaIngestPlist] {
            if FileManager.default.fileExists(atPath: plist.path) {
                try FileManager.default.removeItem(at: plist)
            }
        }
        if FileManager.default.fileExists(atPath: paths.rollback.path) {
            try FileManager.default.removeItem(at: paths.rollback)
        }
        if purge {
            for url in [paths.support, paths.cache, paths.logs] {
                if FileManager.default.fileExists(atPath: url.path) {
                    let values = try url.resourceValues(forKeys: [.isSymbolicLinkKey])
                    if values.isSymbolicLink == true {
                        throw TitanSkiesError.unsafePath("purge refuses to follow symlinks")
                    }
                    try FileManager.default.removeItem(at: url)
                }
            }
        }
    }
}
