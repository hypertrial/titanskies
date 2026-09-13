import CryptoKit
import Foundation

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

    public func validate(currentVersion: String, channel: String, publicKeyHex: String = TitanSkiesIdentity.updatePublicKeyHex) throws {
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
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let currentOS = "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)"
        if try SemanticVersion(currentOS) < SemanticVersion(requiredOS) {
            throw TitanSkiesError.updateRejected("update requires a newer macOS")
        }
        if try SemanticVersion(version) <= SemanticVersion(currentVersion) {
            throw TitanSkiesError.updateRejected("update version is not newer")
        }
        try UpdateSigning.verify(message: try canonicalPayload(), signatureHex: signature, publicKeyHex: publicKeyHex)
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
        let data = try Data(contentsOf: url, options: [.mappedIfSafe])
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
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

public enum StagedApp {
    public static func validate(
        _ app: URL,
        expectedIdentifier: String = TitanSkiesIdentity.bundleIdentifier,
        newerThan currentVersion: String? = nil,
        runner: any ProcessRunning = FoundationProcessRunner()
    ) throws {
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
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw TitanSkiesError.updateRejected("staged app is missing TitanSkies")
        }
        let info = try runner.run("/usr/bin/file", ["-b", executable.path], environment: nil)
        if info.stdout.contains("x86_64") && !info.stdout.contains("arm64") {
            throw TitanSkiesError.updateRejected("staged executable must not be intel-only")
        }
        if !info.stdout.contains("arm64") {
            throw TitanSkiesError.updateRejected("staged executable is not arm64")
        }
    }
}

public protocol UpdateDownloading: Sendable {
    func download(from url: URL, to destination: URL) throws
}

public struct FileURLDownloader: UpdateDownloading {
    public init() {}

    public func download(from url: URL, to destination: URL) throws {
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try Data(contentsOf: url)
        try data.write(to: destination, options: .atomic)
    }
}

public struct UpdateApplier: Sendable {
    public let downloader: any UpdateDownloading
    public let runner: any ProcessRunning

    public init(downloader: any UpdateDownloading = FileURLDownloader(), runner: any ProcessRunning = FoundationProcessRunner()) {
        self.downloader = downloader
        self.runner = runner
    }

    public func verifyDMG(at dmg: URL, expectedSHA256: String) throws {
        let digest = try UpdateSigning.sha256(of: dmg)
        guard digest == expectedSHA256 else {
            throw TitanSkiesError.updateRejected("dmg hash mismatch")
        }
    }

    public func stage(manifest: UpdateManifest, paths: TitanSkiesPaths, work: URL, currentVersion: String) throws -> URL {
        guard manifest.dmgURL.scheme == "https" else {
            throw TitanSkiesError.updateRejected("dmg URL must be https")
        }
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: work.path)
        let dmg = work.appendingPathComponent("TitanSkies.dmg")
        try downloader.download(from: manifest.dmgURL, to: dmg)
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
        try StagedApp.validate(mountedApp, newerThan: currentVersion, runner: runner)
        let staged = paths.applications.appendingPathComponent("TitanSkies.staged.app")
        if staged.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("staged app must stay on the Applications volume")
        }
        if FileManager.default.fileExists(atPath: staged.path) {
            try FileManager.default.removeItem(at: staged)
        }
        try FileManager.default.copyItem(at: mountedApp, to: staged)
        try StagedApp.validate(staged, newerThan: currentVersion, runner: runner)
        return staged
    }

    public func launchHelper(paths: TitanSkiesPaths, staged: URL) throws {
        if paths.updater.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("updater must stay inside the live app")
        }
        let helper = staged.appendingPathComponent("Contents/MacOS/TitanSkiesUpdater")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            throw TitanSkiesError.missingBundleResource("TitanSkiesUpdater")
        }
        if helper.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("updater must stay inside the live app")
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
        try process.run()
    }
}
