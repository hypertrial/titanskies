import Foundation
import TitanSkiesCore

@main
enum TitanSkiesCoreCheck {
    static func main() {
        do {
            try run()
            FileHandle.standardOutput.write(Data("TitanSkiesCore checks passed\n".utf8))
        } catch {
            FileHandle.standardError.write(Data("TitanSkiesCore checks failed: \(error)\n".utf8))
            exit(1)
        }
    }

    static func run() throws {
        let parsed = try EnvFile.parse("CONTEXT_SOURCE=demo\nAIRNOW_API_KEY=secret-value\n")
        guard parsed.values["AIRNOW_API_KEY"] == "secret-value" else { throw CheckError("secret not parsed") }
        guard parsed.webEnvironment()["AIRNOW_API_KEY"] == nil else { throw CheckError("secret leaked to web") }
        do {
            _ = try EnvFile.parse("PATH=/bin\n")
            throw CheckError("PATH should be rejected")
        } catch TitanSkiesError.invalidEnv { }

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("env")
        try EnvFile(values: ["CONTEXT_SOURCE": "demo"]).writeAtomically(to: url)
        let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber
        guard mode?.intValue == 0o600 else { throw CheckError("env mode is \(String(describing: mode))") }

        let app = directory.appendingPathComponent("TitanSkies.app")
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
        let realData = directory.appendingPathComponent("real-data")
        try FileManager.default.createDirectory(at: realData, withIntermediateDirectories: true)
        let dataAlias = directory.appendingPathComponent("data-alias")
        try FileManager.default.createSymbolicLink(at: dataAlias, withDestinationURL: realData)
        let checkPaths = TitanSkiesPaths(home: directory, app: app)
        do {
            _ = try EnvFile(values: ["TITANSKIES_DATA_DIR": dataAlias.path]).validated(paths: checkPaths)
            throw CheckError("data symlink should be rejected")
        } catch TitanSkiesError.unsafePath { }
        let cacheAlias = directory.appendingPathComponent("cache-alias")
        try FileManager.default.createSymbolicLink(at: cacheAlias, withDestinationURL: realData)
        do {
            _ = try EnvFile(values: ["TITANSKIES_CACHE_DIR": cacheAlias.path]).validated(paths: checkPaths)
            throw CheckError("cache symlink should be rejected")
        } catch TitanSkiesError.unsafePath { }

        guard NavigationPolicy.decide(url: URL(string: "http://127.0.0.1:8080/data")!) == .allow else { throw CheckError("loopback should allow") }
        guard NavigationPolicy.decide(url: URL(string: "https://www.airnow.gov/")!) == .openInBrowser else { throw CheckError("https should open") }
        guard NavigationPolicy.decide(url: URL(string: "file:///etc/passwd")!) == .reject else { throw CheckError("file should reject") }
        guard NavigationPolicy.decide(url: URL(string: "http://127.0.0.1:3000/")!) == .reject else { throw CheckError("other port should reject") }
        guard NavigationPolicy.decide(url: URL(string: "javascript:alert(1)")!) == .reject else { throw CheckError("javascript should reject") }
        guard NavigationPolicy.nativeUserAgent(version: "0.1.0").contains(TitanSkiesIdentity.nativeUserAgentToken) else {
            throw CheckError("native user agent missing")
        }

        guard PathPolicy.isUntrustedInstallSource(URL(fileURLWithPath: "/Volumes/TitanSkies/TitanSkies.app")) else {
            throw CheckError("DMG path should be untrusted")
        }
        guard PathPolicy.isUntrustedInstallSource(URL(fileURLWithPath: "/private/var/folders/zz/AppTranslocation/TitanSkies.app")) else {
            throw CheckError("translocation should be untrusted")
        }
        guard !PathPolicy.isUntrustedInstallSource(URL(fileURLWithPath: "/Users/me/Applications/TitanSkies.app")) else {
            throw CheckError("Applications path should be trusted")
        }

        let json = """
        {"architecture":"arm64","channel":"unsigned-beta","dmgURL":"https://example.invalid/TitanSkies.dmg","keyId":"titanskies-macos-ed25519-1","minimumOS":"13.0","releaseNotesURL":"https://example.invalid/notes","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","version":"0.1.0","signature":"00"}
        """
        let manifest = try UpdateManifest.parse(Data(json.utf8))
        let canonical = String(data: try manifest.canonicalPayload(), encoding: .utf8) ?? ""
        guard canonical.contains("https://example.invalid/TitanSkies.dmg") else { throw CheckError("canonical JSON escaped slashes") }
        guard !canonical.contains("\\/") else { throw CheckError("canonical JSON must not escape slashes") }
        do {
            _ = try manifest.availability(currentVersion: "0.1.0", channel: "unsigned-beta")
            throw CheckError("invalid signature should be rejected before version comparison")
        } catch TitanSkiesError.updateRejected { }
        var intel = try UpdateManifest.parse(Data(json.replacingOccurrences(of: "arm64", with: "x86_64").utf8))
        intel.architecture = "x86_64"
        do {
            _ = try intel.availability(currentVersion: "0.0.1", channel: "unsigned-beta")
            throw CheckError("intel architecture should be rejected")
        } catch TitanSkiesError.updateRejected { }

        let swap = UpdateSwap(
            liveApp: URL(fileURLWithPath: "/Users/me/Applications/TitanSkies.app"),
            stagedApp: URL(fileURLWithPath: "/tmp/TitanSkies.app"),
            rollbackApp: URL(fileURLWithPath: "/Users/me/Library/Application Support/TitanSkies/rollback/TitanSkies.app")
        )
        guard Array(swap.steps.prefix(4)) == ["bootout-web", "bootout-ingest", "rename-live-to-rollback", "rename-staged-to-live"] else {
            throw CheckError("rollback order")
        }
        guard swap.restoreSteps.first == "bootout-failed-jobs" else { throw CheckError("restore first step") }

        let work = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        let dmg = work.appendingPathComponent("TitanSkies.dmg")
        try Data("synthetic-dmg".utf8).write(to: dmg)
        do {
            try UpdateApplier().verifyDMG(at: dmg, expectedSHA256: String(repeating: "a", count: 64))
            throw CheckError("dmg hash mismatch should fail")
        } catch TitanSkiesError.updateRejected { }
        try UpdateApplier().verifyDMG(at: dmg, expectedSHA256: try UpdateSigning.sha256(of: dmg))

        let fakeApp = work.appendingPathComponent("TitanSkies.app")
        try FileManager.default.createDirectory(at: fakeApp.appendingPathComponent("Contents/MacOS"), withIntermediateDirectories: true)
        let info = """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
        <key>CFBundleIdentifier</key><string>com.hypertrial.titanskies</string>
        <key>CFBundleShortVersionString</key><string>0.2.0</string>
        </dict></plist>
        """
        try Data(info.utf8).write(to: fakeApp.appendingPathComponent("Contents/Info.plist"))
        try FileManager.default.copyItem(at: URL(fileURLWithPath: "/usr/bin/true"), to: fakeApp.appendingPathComponent("Contents/MacOS/TitanSkies"))
        try StagedApp.validate(fakeApp, newerThan: "0.1.0")
        do {
            try StagedApp.validate(fakeApp, expectedIdentifier: "com.example.other", newerThan: "0.1.0")
            throw CheckError("wrong bundle id should fail")
        } catch TitanSkiesError.updateRejected { }

        final class FakeHealth: HealthChecking, @unchecked Sendable {
            func fetch(url: URL) throws -> (Int, Data) {
                if url.path.contains("healthz") {
                    return (200, Data("{\"service\":\"titanskies\"}".utf8))
                }
                return (503, Data("{\"state\":\"initializing\"}".utf8))
            }
        }
        let client = ServiceHealth(client: FakeHealth())
        guard try client.healthz().serviceIdentity else { throw CheckError("identity") }
        guard try client.contextHealth().initializing else { throw CheckError("initializing is not failure") }
    }
}

private struct CheckError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}
