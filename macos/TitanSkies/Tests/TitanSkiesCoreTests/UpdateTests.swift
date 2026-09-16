import CryptoKit
import Foundation
import XCTest
@testable import TitanSkiesCore

final class UpdateTests: XCTestCase {
    func testSignedManifestReturnsSameAndOlderAsUpToDateAndNewerAsAvailable() throws {
        let key = Curve25519.Signing.PrivateKey()
        let same = try signedManifest(version: "0.1.0", key: key)
        XCTAssertEqual(
            try same.availability(currentVersion: "0.1.0", channel: "production", currentOS: "13.0.0", publicKeyHex: key.publicKey.rawRepresentation.hex),
            .upToDate
        )
        let older = try signedManifest(version: "0.0.9", key: key)
        XCTAssertEqual(
            try older.availability(currentVersion: "0.1.0", channel: "production", currentOS: "13.0.0", publicKeyHex: key.publicKey.rawRepresentation.hex),
            .upToDate
        )
        let newer = try signedManifest(version: "0.1.1", key: key)
        XCTAssertEqual(
            try newer.availability(currentVersion: "0.1.0", channel: "production", currentOS: "13.0.0", publicKeyHex: key.publicKey.rawRepresentation.hex),
            .available(newer)
        )
    }

    func testManifestRejectsSignatureChannelArchitectureAndMinimumOS() throws {
        let key = Curve25519.Signing.PrivateKey()
        let manifest = try signedManifest(version: "0.1.1", key: key)
        XCTAssertThrowsError(
            try manifest.availability(currentVersion: "0.1.0", channel: "unsigned-beta", currentOS: "13.0.0", publicKeyHex: key.publicKey.rawRepresentation.hex)
        )

        var intel = manifest
        intel.architecture = "x86_64"
        XCTAssertThrowsError(
            try intel.availability(currentVersion: "0.1.0", channel: "production", currentOS: "13.0.0", publicKeyHex: key.publicKey.rawRepresentation.hex)
        )

        var tooNew = manifest
        tooNew.minimumOS = "14.0"
        tooNew = try resign(tooNew, key: key)
        XCTAssertThrowsError(
            try tooNew.availability(currentVersion: "0.1.0", channel: "production", currentOS: "13.6.0", publicKeyHex: key.publicKey.rawRepresentation.hex)
        )

        var tampered = manifest
        tampered.sha256 = String(repeating: "b", count: 64)
        XCTAssertThrowsError(
            try tampered.availability(currentVersion: "0.1.0", channel: "production", currentOS: "13.0.0", publicKeyHex: key.publicKey.rawRepresentation.hex)
        )
    }

    func testUpdateTransactionStartsAndRelaunchesNewApplication() throws {
        let directory = try temporaryDirectory()
        let live = try app(at: directory.appendingPathComponent("Applications/TitanSkies.app"), marker: "old")
        let staged = try app(at: directory.appendingPathComponent("Applications/TitanSkies.staged.app"), marker: "new")
        let recorder = Recorder()
        let transaction = UpdateTransaction(
            services: FakeServices(recorder: recorder),
            validator: FakeValidator(recorder: recorder),
            relauncher: FakeRelauncher(recorder: recorder)
        )

        XCTAssertEqual(
            try transaction.run(liveApp: live, stagedApp: staged, rollbackDirectory: directory.appendingPathComponent("rollback")),
            .updated
        )
        XCTAssertEqual(try String(contentsOf: live.appendingPathComponent("marker"), encoding: .utf8), "new")
        XCTAssertEqual(Array(recorder.events.suffix(2)), ["start:TitanSkies.app", "relaunch:TitanSkies.app"])
    }

    func testUpdateTransactionRestoresAndRelaunchesOldApplicationAfterStartFailure() throws {
        let directory = try temporaryDirectory()
        let live = try app(at: directory.appendingPathComponent("Applications/TitanSkies.app"), marker: "old")
        let staged = try app(at: directory.appendingPathComponent("Applications/TitanSkies.staged.app"), marker: "new")
        let recorder = Recorder()
        let status = directory.appendingPathComponent("update-status.txt")
        let result = try UpdateTransaction(
            services: FakeServices(recorder: recorder, failFirstStart: true),
            validator: FakeValidator(recorder: recorder),
            relauncher: FakeRelauncher(recorder: recorder)
        ).run(
            liveApp: live,
            stagedApp: staged,
            rollbackDirectory: directory.appendingPathComponent("rollback"),
            rollbackStatusURL: status
        )

        guard case .rolledBack = result else {
            return XCTFail("expected rollback")
        }
        XCTAssertEqual(try String(contentsOf: live.appendingPathComponent("marker"), encoding: .utf8), "old")
        XCTAssertTrue(try String(contentsOf: status, encoding: .utf8).contains("rolled back"))
        XCTAssertEqual(Array(recorder.events.suffix(2)), ["start:TitanSkies.app", "relaunch:TitanSkies.app"])
    }

    func testSHA256StreamsFilesLargerThanOneChunk() throws {
        let directory = try temporaryDirectory()
        let file = directory.appendingPathComponent("large.dmg")
        let bytes = Data(repeating: 0x5a, count: 2_500_000)
        try bytes.write(to: file)
        let expected = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(try UpdateSigning.sha256(of: file), expected)
    }

    func testChannelControlsUnsignedTrustMessage() {
        XCTAssertTrue(Channel.unsignedBeta.showsUnsignedWarning)
        XCTAssertFalse(Channel.production.showsUnsignedWarning)
    }

    func testNativeOperationStateRejectsDuplicateUntilAwaitedWorkFinishes() async {
        var state = NativeOperationState()
        XCTAssertTrue(state.begin())
        XCTAssertFalse(state.begin())
        await Task.yield()
        XCTAssertFalse(state.begin())
        state.finish()
        XCTAssertTrue(state.begin())
    }

    func testOccupiedPortRejectsServiceNotOwnedByExpectedLaunchdJob() throws {
        let directory = try temporaryDirectory()
        let paths = TitanSkiesPaths(
            home: directory,
            app: directory.appendingPathComponent("Applications/TitanSkies.app")
        )
        let manager = ServiceManager(
            paths: paths,
            launchd: LaunchdClient(runner: ForeignLaunchdRunner(), uid: 501),
            health: ServiceHealth(client: IdentityHealth()),
            channel: .unsignedBeta,
            portOccupier: { "foreign-process" }
        )
        XCTAssertThrowsError(try manager.start()) { error in
            XCTAssertEqual(error as? TitanSkiesError, .foreignService)
        }
    }

    private func signedManifest(version: String, key: Curve25519.Signing.PrivateKey) throws -> UpdateManifest {
        try resign(
            UpdateManifest(
                version: version,
                minimumOS: "13.0",
                architecture: "arm64",
                channel: "production",
                dmgURL: URL(string: "https://github.com/hypertrial/titanskies/releases/download/v\(version)/TitanSkies-\(version)-macos-arm64.dmg")!,
                sha256: String(repeating: "a", count: 64),
                releaseNotesURL: URL(string: "https://github.com/hypertrial/titanskies/releases/tag/v\(version)")!,
                keyId: TitanSkiesIdentity.updateKeyId,
                signature: ""
            ),
            key: key
        )
    }

    private func resign(_ manifest: UpdateManifest, key: Curve25519.Signing.PrivateKey) throws -> UpdateManifest {
        var signed = manifest
        signed.signature = try key.signature(for: signed.canonicalPayload()).hex
        return signed
    }

    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    private func app(at url: URL, marker: String) throws -> URL {
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        try marker.write(to: url.appendingPathComponent("marker"), atomically: true, encoding: .utf8)
        return url
    }
}

private extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}

private final class Recorder: @unchecked Sendable {
    var events: [String] = []
}

private final class FakeServices: UpdateServiceManaging, @unchecked Sendable {
    private let recorder: Recorder
    private var failFirstStart: Bool

    init(recorder: Recorder, failFirstStart: Bool = false) {
        self.recorder = recorder
        self.failFirstStart = failFirstStart
    }

    func stop(app: URL) throws {
        recorder.events.append("stop:\(app.lastPathComponent)")
    }

    func start(app: URL) throws {
        recorder.events.append("start:\(app.lastPathComponent)")
        if failFirstStart {
            failFirstStart = false
            throw TitanSkiesError.healthCheckFailed("forced start failure")
        }
    }
}

private struct FakeValidator: ApplicationValidating {
    let recorder: Recorder

    func validate(_ app: URL) throws {
        recorder.events.append("validate:\(app.lastPathComponent)")
        guard FileManager.default.fileExists(atPath: app.path) else {
            throw TitanSkiesError.updateRejected("missing app")
        }
    }
}

private struct FakeRelauncher: ApplicationRelaunching {
    let recorder: Recorder

    func relaunch(_ app: URL) throws {
        recorder.events.append("relaunch:\(app.lastPathComponent)")
    }
}

private struct ForeignLaunchdRunner: ProcessRunning {
    func run(_ executable: String, _ arguments: [String], environment: [String: String]?) throws -> ProcessResult {
        ProcessResult(status: 0, stdout: "program = /tmp/Foreign.app/Contents/MacOS/TitanSkiesWeb", stderr: "")
    }
}

private struct IdentityHealth: HealthChecking {
    func fetch(url: URL) throws -> (Int, Data) {
        (200, Data("{\"service\":\"titanskies\"}".utf8))
    }
}
