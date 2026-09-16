import CryptoKit
import Foundation

public enum UpdateAvailability: Equatable, Sendable {
    case upToDate
    case available(UpdateManifest)
}

public struct UpdateManifest: Equatable, Sendable {
    public var version: String
    public var minimumOS: String
    public var architecture: String
    public var channel: String
    public var dmgURL: URL
    public var sha256: String
    public var releaseNotesURL: URL
    public var keyId: String
    public var signature: String

    public init(
        version: String,
        minimumOS: String,
        architecture: String,
        channel: String,
        dmgURL: URL,
        sha256: String,
        releaseNotesURL: URL,
        keyId: String,
        signature: String
    ) {
        self.version = version
        self.minimumOS = minimumOS
        self.architecture = architecture
        self.channel = channel
        self.dmgURL = dmgURL
        self.sha256 = sha256
        self.releaseNotesURL = releaseNotesURL
        self.keyId = keyId
        self.signature = signature
    }

    public static func parse(_ data: Data) throws -> UpdateManifest {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw TitanSkiesError.updateRejected("manifest is not an object")
        }
        func string(_ key: String) throws -> String {
            guard let value = object[key] as? String, !value.isEmpty else {
                throw TitanSkiesError.updateRejected("manifest missing \(key)")
            }
            return value
        }
        guard let dmg = URL(string: try string("dmgURL")), dmg.scheme == "https" else {
            throw TitanSkiesError.updateRejected("dmg URL must be https")
        }
        guard let notes = URL(string: try string("releaseNotesURL")), notes.scheme == "https" else {
            throw TitanSkiesError.updateRejected("release notes URL must be https")
        }
        return UpdateManifest(
            version: try string("version"),
            minimumOS: try string("minimumOS"),
            architecture: try string("architecture"),
            channel: try string("channel"),
            dmgURL: dmg,
            sha256: try string("sha256"),
            releaseNotesURL: notes,
            keyId: try string("keyId"),
            signature: try string("signature")
        )
    }

    public func canonicalPayload() throws -> Data {
        let object: [String: Any] = [
            "architecture": architecture,
            "channel": channel,
            "dmgURL": dmgURL.absoluteString,
            "keyId": keyId,
            "minimumOS": minimumOS,
            "releaseNotesURL": releaseNotesURL.absoluteString,
            "sha256": sha256,
            "version": version,
        ]
        return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
    }

    public func availability(
        currentVersion: String,
        channel: String,
        currentOS: String? = nil,
        publicKeyHex: String = TitanSkiesIdentity.updatePublicKeyHex
    ) throws -> UpdateAvailability {
        guard architecture == "arm64" else {
            throw TitanSkiesError.updateRejected("update architecture must be arm64")
        }
        guard self.channel == channel else {
            throw TitanSkiesError.updateRejected("update channel mismatch")
        }
        guard keyId == TitanSkiesIdentity.updateKeyId else {
            throw TitanSkiesError.updateRejected("update key id mismatch")
        }
        guard sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else {
            throw TitanSkiesError.updateRejected("update sha256 is invalid")
        }
        let requiredOS = minimumOS.split(separator: ".").count == 2 ? "\(minimumOS).0" : minimumOS
        let installedOS: String
        if let currentOS {
            installedOS = currentOS
        } else {
            let os = ProcessInfo.processInfo.operatingSystemVersion
            installedOS = "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)"
        }
        if try SemanticVersion(installedOS) < SemanticVersion(requiredOS) {
            throw TitanSkiesError.updateRejected("update requires a newer macOS")
        }
        try UpdateSigning.verify(message: try canonicalPayload(), signatureHex: signature, publicKeyHex: publicKeyHex)
        if try SemanticVersion(version) <= SemanticVersion(currentVersion) {
            return .upToDate
        }
        return .available(self)
    }
}

public enum UpdateSigning {
    public static func verify(message: Data, signatureHex: String, publicKeyHex: String) throws {
        guard let keyData = data(fromHex: publicKeyHex), keyData.count == 32 else {
            throw TitanSkiesError.updateRejected("embedded public key is invalid")
        }
        guard let signature = data(fromHex: signatureHex), signature.count == 64 else {
            throw TitanSkiesError.updateRejected("signature must be 64-byte hex")
        }
        let key = try Curve25519.Signing.PublicKey(rawRepresentation: keyData)
        guard key.isValidSignature(signature, for: message) else {
            throw TitanSkiesError.updateRejected("update signature is invalid")
        }
    }

    public static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var digest = SHA256()
        while let chunk = try handle.read(upToCount: 1_048_576), !chunk.isEmpty {
            digest.update(data: chunk)
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func data(fromHex hex: String) -> Data? {
        guard hex.count % 2 == 0 else { return nil }
        var data = Data()
        var index = hex.startIndex
        while index < hex.endIndex {
            let next = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<next], radix: 16) else { return nil }
            data.append(byte)
            index = next
        }
        return data
    }
}

public struct UpdateSwap: Sendable {
    public let liveApp: URL
    public let stagedApp: URL
    public let rollbackApp: URL

    public init(liveApp: URL, stagedApp: URL, rollbackApp: URL) {
        self.liveApp = liveApp
        self.stagedApp = stagedApp
        self.rollbackApp = rollbackApp
    }

    public var steps: [String] {
        [
            "bootout-web",
            "bootout-ingest",
            "rename-live-to-rollback",
            "rename-staged-to-live",
            "register-agents",
            "start-web",
            "kickstart-ingest",
            "healthz",
        ]
    }

    public var restoreSteps: [String] {
        [
            "bootout-failed-jobs",
            "verify-rollback-app",
            "rename-failed-aside",
            "rename-rollback-to-live",
            "register-agents",
            "start-web",
            "retain-logs",
        ]
    }
}

public enum CodeSignature {
    public static func teamIdentifier(of app: URL, runner: any ProcessRunning = FoundationProcessRunner()) throws -> String {
        let details = try runner.run("/usr/bin/codesign", ["-dv", "--verbose=4", app.path], environment: nil)
        let output = details.stderr + "\n" + details.stdout
        guard let line = output.split(separator: "\n").first(where: { $0.hasPrefix("TeamIdentifier=") }) else {
            throw TitanSkiesError.updateRejected("application signature has no Team ID")
        }
        return String(line.dropFirst("TeamIdentifier=".count))
    }

    public static func validateProductionApp(
        _ app: URL,
        expectedTeamID: String,
        runner: any ProcessRunning = FoundationProcessRunner()
    ) throws {
        let verification = try runner.run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path], environment: nil)
        guard verification.succeeded else {
            throw TitanSkiesError.updateRejected("staged application signature is invalid")
        }
        try validateIdentity(app, expectedTeamID: expectedTeamID, runner: runner)
        if let enumerator = FileManager.default.enumerator(at: app, includingPropertiesForKeys: [.isRegularFileKey]) {
            for case let path as URL in enumerator {
                guard (try? path.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true else { continue }
                let kind = try runner.run("/usr/bin/file", ["-b", path.path], environment: nil)
                guard kind.stdout.contains("Mach-O") else { continue }
                let nested = try runner.run("/usr/bin/codesign", ["--verify", "--strict", path.path], environment: nil)
                guard nested.succeeded else {
                    throw TitanSkiesError.updateRejected("nested application signature is invalid")
                }
                try validateIdentity(path, expectedTeamID: expectedTeamID, runner: runner)
            }
        }
        let gatekeeper = try runner.run("/usr/sbin/spctl", ["--assess", "--type", "execute", app.path], environment: nil)
        guard gatekeeper.succeeded else {
            throw TitanSkiesError.updateRejected("staged application was rejected by Gatekeeper")
        }
    }

    private static func validateIdentity(_ target: URL, expectedTeamID: String, runner: any ProcessRunning) throws {
        let details = try runner.run("/usr/bin/codesign", ["-dv", "--verbose=4", target.path], environment: nil)
        let output = details.stderr + "\n" + details.stdout
        guard output.contains("Authority=Developer ID Application:") else {
            throw TitanSkiesError.updateRejected("staged application is not Developer ID signed")
        }
        guard output.split(separator: "\n").contains(where: { $0 == "TeamIdentifier=\(expectedTeamID)" }) else {
            throw TitanSkiesError.updateRejected("staged application Team ID mismatch")
        }
        guard output.split(separator: "\n").contains(where: {
            $0.hasPrefix("CodeDirectory") && $0.localizedCaseInsensitiveContains("runtime")
        }) else {
            throw TitanSkiesError.updateRejected("staged application is missing Hardened Runtime")
        }
    }
}

public enum StagedApp {
    public static func validate(
        _ app: URL,
        expectedIdentifier: String = TitanSkiesIdentity.bundleIdentifier,
        newerThan currentVersion: String? = nil,
        expectedTeamID: String? = nil,
        requireProductionTrust: Bool = false,
        runner: any ProcessRunning = FoundationProcessRunner()
    ) throws {
        try PathPolicy.rejectSymlink(app, label: "application")
        let infoURL = app.appendingPathComponent("Contents/Info.plist")
        let data = try Data(contentsOf: infoURL)
        guard let plist = try PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any] else {
            throw TitanSkiesError.updateRejected("staged Info.plist is invalid")
        }
        guard plist["CFBundleIdentifier"] as? String == expectedIdentifier else {
            throw TitanSkiesError.updateRejected("staged bundle identifier mismatch")
        }
        let version = plist["CFBundleShortVersionString"] as? String ?? ""
        if let currentVersion, try SemanticVersion(version) <= SemanticVersion(currentVersion) {
            throw TitanSkiesError.updateRejected("staged version is not newer")
        }
        let executable = app.appendingPathComponent("Contents/MacOS/TitanSkies")
        try PathPolicy.rejectSymlink(executable, label: "TitanSkies executable")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw TitanSkiesError.updateRejected("staged app is missing TitanSkies")
        }
        let info = try runner.run("/usr/bin/file", ["-b", executable.path], environment: nil)
        if info.stdout.contains("x86_64") {
            throw TitanSkiesError.updateRejected("staged executable must be arm64-only")
        }
        if !info.stdout.contains("arm64") {
            throw TitanSkiesError.updateRejected("staged executable is not arm64")
        }
        if requireProductionTrust {
            guard let expectedTeamID, !expectedTeamID.isEmpty else {
                throw TitanSkiesError.updateRejected("expected Developer ID Team ID is missing")
            }
            try CodeSignature.validateProductionApp(app, expectedTeamID: expectedTeamID, runner: runner)
        }
    }
}

public protocol UpdateDownloading: Sendable {
    func download(from url: URL, to destination: URL) async throws
}

public struct URLSessionDownloader: UpdateDownloading {
    public init() {}

    public func download(from url: URL, to destination: URL) async throws {
        let (temporaryDownload, response) = try await URLSession.shared.download(from: url)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            throw TitanSkiesError.updateRejected("update download did not return HTTP success")
        }
        let manager = FileManager.default
        let parent = destination.deletingLastPathComponent()
        try manager.createDirectory(at: parent, withIntermediateDirectories: true)
        let temporary = parent.appendingPathComponent(".\(destination.lastPathComponent).\(UUID().uuidString)")
        defer { try? manager.removeItem(at: temporary) }
        try manager.copyItem(at: temporaryDownload, to: temporary)
        if manager.fileExists(atPath: destination.path) {
            try manager.removeItem(at: destination)
        }
        try manager.moveItem(at: temporary, to: destination)
    }
}

public struct UpdateApplier: Sendable {
    public let downloader: any UpdateDownloading
    public let runner: any ProcessRunning

    public init(downloader: any UpdateDownloading = URLSessionDownloader(), runner: any ProcessRunning = FoundationProcessRunner()) {
        self.downloader = downloader
        self.runner = runner
    }

    public func verifyDMG(at dmg: URL, expectedSHA256: String) throws {
        let digest = try UpdateSigning.sha256(of: dmg)
        guard digest == expectedSHA256 else {
            throw TitanSkiesError.updateRejected("dmg hash mismatch")
        }
    }

    public func stage(manifest: UpdateManifest, paths: TitanSkiesPaths, work: URL, currentVersion: String) async throws -> URL {
        guard manifest.dmgURL.scheme == "https" else {
            throw TitanSkiesError.updateRejected("dmg URL must be https")
        }
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path)
        let dmg = work.appendingPathComponent("TitanSkies.dmg")
        try await downloader.download(from: manifest.dmgURL, to: dmg)
        try verifyDMG(at: dmg, expectedSHA256: manifest.sha256)
        let mount = work.appendingPathComponent("mnt")
        try FileManager.default.createDirectory(at: mount, withIntermediateDirectories: true)
        let attach = try runner.run(
            "/usr/bin/hdiutil",
            ["attach", "-readonly", "-nobrowse", "-mountpoint", mount.path, dmg.path],
            environment: nil
        )
        guard attach.succeeded else {
            throw TitanSkiesError.updateRejected(attach.stderr.isEmpty ? "unable to mount update DMG" : attach.stderr)
        }
        defer {
            _ = try? runner.run("/usr/bin/hdiutil", ["detach", mount.path, "-force"], environment: nil)
        }
        let mountedApp = mount.appendingPathComponent("TitanSkies.app")
        let production = manifest.channel == Channel.production.rawValue
        let expectedTeamID = production ? try CodeSignature.teamIdentifier(of: paths.app, runner: runner) : nil
        try StagedApp.validate(
            mountedApp,
            newerThan: currentVersion,
            expectedTeamID: expectedTeamID,
            requireProductionTrust: production,
            runner: runner
        )
        guard try Channel.validated(fromApp: mountedApp).rawValue == manifest.channel else {
            throw TitanSkiesError.updateRejected("staged application channel mismatch")
        }
        let staged = paths.applications.appendingPathComponent("TitanSkies.staged.app")
        if staged.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("staged app must stay on the Applications volume")
        }
        if FileManager.default.fileExists(atPath: staged.path) {
            try FileManager.default.removeItem(at: staged)
        }
        try FileManager.default.copyItem(at: mountedApp, to: staged)
        try StagedApp.validate(
            staged,
            newerThan: currentVersion,
            expectedTeamID: expectedTeamID,
            requireProductionTrust: production,
            runner: runner
        )
        guard try Channel.validated(fromApp: staged).rawValue == manifest.channel else {
            throw TitanSkiesError.updateRejected("copied application channel mismatch")
        }
        return staged
    }

    public func launchHelper(paths: TitanSkiesPaths, staged: URL) throws {
        if paths.updater.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("updater must stay inside the live app")
        }
        let helper = staged.appendingPathComponent("Contents/MacOS/TitanSkiesUpdater")
        let liveChannel = try Channel.validated(fromApp: paths.app)
        let production = liveChannel == .production
        let expectedTeamID = production ? try CodeSignature.teamIdentifier(of: paths.app, runner: runner) : nil
        try StagedApp.validate(
            staged,
            expectedTeamID: expectedTeamID,
            requireProductionTrust: production,
            runner: runner
        )
        try PathPolicy.requireInsideApp(helper, app: staged, label: "updater")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            throw TitanSkiesError.missingBundleResource("TitanSkiesUpdater")
        }
        if helper.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("updater must stay inside the live app")
        }
        let helperInfo = try runner.run("/usr/bin/file", ["-b", helper.path], environment: nil)
        guard helperInfo.stdout.contains("arm64"), !helperInfo.stdout.contains("x86_64") else {
            throw TitanSkiesError.updateRejected("updater must be arm64-only")
        }
        let process = Process()
        process.executableURL = helper
        process.arguments = [
            "--live", paths.installedApp.path,
            "--staged", staged.path,
            "--rollback", paths.rollback.path,
        ]
        process.environment = [
            "PATH": "/usr/bin:/bin",
            "HOME": paths.home.path,
        ]
        try paths.createPrivateDirectories()
        try LogRotation.rotate(at: paths.updateLog)
        if !FileManager.default.fileExists(atPath: paths.updateLog.path) {
            FileManager.default.createFile(atPath: paths.updateLog.path, contents: nil)
        }
        let logHandle = try FileHandle(forWritingTo: paths.updateLog)
        try logHandle.seekToEnd()
        process.standardOutput = logHandle
        process.standardError = logHandle
        try process.run()
        try? logHandle.close()
    }
}

public protocol UpdateServiceManaging: Sendable {
    func stop(app: URL) throws
    func start(app: URL) throws
}

public struct LiveUpdateServiceManager: UpdateServiceManaging {
    public let home: URL
    public let expectedTeamID: String
    public let runner: any ProcessRunning

    public init(home: URL, expectedTeamID: String = "", runner: any ProcessRunning = FoundationProcessRunner()) {
        self.home = home
        self.expectedTeamID = expectedTeamID
        self.runner = runner
    }

    public func stop(app: URL) throws {
        try manageProductionAgents(app: app, action: "unregister")
    }

    public func start(app: URL) throws {
        try manageProductionAgents(app: app, action: "reregister")
        try ServiceManager(paths: TitanSkiesPaths(home: home, app: app), channel: .production).start()
    }

    private func manageProductionAgents(app: URL, action: String) throws {
        guard try Channel.validated(fromApp: app) == .production else {
            throw TitanSkiesError.updateRejected("update services require a production application")
        }
        try StagedApp.validate(
            app,
            expectedTeamID: expectedTeamID,
            requireProductionTrust: true,
            runner: runner
        )
        let helper = app.appendingPathComponent("Contents/MacOS/TitanSkiesUpdater")
        try PathPolicy.requireInsideApp(helper, app: app, label: "updater")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            throw TitanSkiesError.missingBundleResource("TitanSkiesUpdater")
        }
        let result = try runner.run(helper.path, ["--service-action", action], environment: [
            "PATH": "/usr/bin:/bin",
            "HOME": home.path,
        ])
        guard result.succeeded else {
            throw TitanSkiesError.launchd(result.stderr.isEmpty ? "unable to \(action) production agents" : result.stderr)
        }
    }
}

public protocol ApplicationValidating: Sendable {
    func validate(_ app: URL) throws
}

public struct StagedApplicationValidator: ApplicationValidating {
    public let expectedTeamID: String?

    public init(expectedTeamID: String? = nil) {
        self.expectedTeamID = expectedTeamID
    }

    public func validate(_ app: URL) throws {
        try StagedApp.validate(
            app,
            expectedTeamID: expectedTeamID,
            requireProductionTrust: expectedTeamID != nil
        )
    }
}

public protocol ApplicationRelaunching: Sendable {
    func relaunch(_ app: URL) throws
}

public struct WorkspaceApplicationRelauncher: ApplicationRelaunching {
    public let runner: any ProcessRunning

    public init(runner: any ProcessRunning = FoundationProcessRunner()) {
        self.runner = runner
    }

    public func relaunch(_ app: URL) throws {
        if app.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("never relaunch an app from Application Support")
        }
        let result = try runner.run("/usr/bin/open", ["-n", app.path], environment: ["PATH": "/usr/bin:/bin"])
        guard result.succeeded else {
            throw TitanSkiesError.updateRejected(result.stderr.isEmpty ? "unable to relaunch TitanSkies" : result.stderr)
        }
    }
}

public enum UpdateTransactionResult: Equatable, Sendable {
    case updated
    case rolledBack(String)
}

public struct UpdateTransaction: Sendable {
    public let services: any UpdateServiceManaging
    public let validator: any ApplicationValidating
    public let relauncher: any ApplicationRelaunching

    public init(
        services: any UpdateServiceManaging,
        validator: any ApplicationValidating = StagedApplicationValidator(),
        relauncher: any ApplicationRelaunching = WorkspaceApplicationRelauncher()
    ) {
        self.services = services
        self.validator = validator
        self.relauncher = relauncher
    }

    public func run(
        liveApp: URL,
        stagedApp: URL,
        rollbackDirectory: URL,
        rollbackStatusURL: URL? = nil
    ) throws -> UpdateTransactionResult {
        guard !liveApp.path.contains("Application Support"), !stagedApp.path.contains("Application Support") else {
            throw TitanSkiesError.unsafePath("live and staged apps must stay outside Application Support")
        }
        try PathPolicy.rejectSymlink(rollbackDirectory, label: "rollback")
        try validator.validate(liveApp)
        try validator.validate(stagedApp)
        let manager = FileManager.default
        let rollbackApp = rollbackDirectory.appendingPathComponent("TitanSkies.app")
        var liveWasMoved = false

        do {
            try services.stop(app: liveApp)
            try manager.createDirectory(at: rollbackDirectory, withIntermediateDirectories: true)
            if manager.fileExists(atPath: rollbackApp.path) {
                try manager.removeItem(at: rollbackApp)
            }
            guard manager.fileExists(atPath: liveApp.path) else {
                throw TitanSkiesError.updateRejected("live application is missing")
            }
            try manager.moveItem(at: liveApp, to: rollbackApp)
            liveWasMoved = true
            try manager.moveItem(at: stagedApp, to: liveApp)
            try validator.validate(liveApp)
            try services.start(app: liveApp)
            try relauncher.relaunch(liveApp)
            return .updated
        } catch {
            let updateFailure = error
            do {
                try? services.stop(app: liveApp)
                if liveWasMoved {
                    if manager.fileExists(atPath: liveApp.path) {
                        let failed = rollbackDirectory.appendingPathComponent("TitanSkies.failed.app")
                        if manager.fileExists(atPath: failed.path) {
                            try manager.removeItem(at: failed)
                        }
                        try manager.moveItem(at: liveApp, to: failed)
                    }
                    try validator.validate(rollbackApp)
                    try manager.moveItem(at: rollbackApp, to: liveApp)
                }
                try validator.validate(liveApp)
                try services.start(app: liveApp)
                if let rollbackStatusURL {
                    let notice = "TitanSkies rolled back to the previous version after an update failed: \(updateFailure.localizedDescription)\n"
                    do {
                        try notice.write(to: rollbackStatusURL, atomically: true, encoding: .utf8)
                        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: rollbackStatusURL.path)
                    } catch {
                        // Restoring and relaunching the previous app is more important than its one-shot notice.
                    }
                }
                try relauncher.relaunch(liveApp)
                return .rolledBack(updateFailure.localizedDescription)
            } catch {
                throw TitanSkiesError.rollbackFailed(
                    "update failed: \(updateFailure.localizedDescription); rollback failed: \(error.localizedDescription)"
                )
            }
        }
    }
}
