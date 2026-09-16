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

    func testRollbackNoticeFailureDoesNotPreventOldApplicationRelaunch() throws {
        let directory = try temporaryDirectory()
        let live = try app(at: directory.appendingPathComponent("Applications/TitanSkies.app"), marker: "old")
        let staged = try app(at: directory.appendingPathComponent("Applications/TitanSkies.staged.app"), marker: "new")
        let recorder = Recorder()
        let result = try UpdateTransaction(
            services: FakeServices(recorder: recorder, failFirstStart: true),
            validator: FakeValidator(recorder: recorder),
            relauncher: FakeRelauncher(recorder: recorder)
        ).run(
            liveApp: live,
            stagedApp: staged,
            rollbackDirectory: directory.appendingPathComponent("rollback"),
            rollbackStatusURL: directory.appendingPathComponent("missing/status.txt")
        )

        guard case .rolledBack = result else { return XCTFail("expected rollback") }
        XCTAssertEqual(recorder.events.last, "relaunch:TitanSkies.app")
    }

    func testUpdateTransactionRestartsOldApplicationWhenRollbackPreparationFails() throws {
        let directory = try temporaryDirectory()
        let live = try app(at: directory.appendingPathComponent("Applications/TitanSkies.app"), marker: "old")
        let staged = try app(at: directory.appendingPathComponent("Applications/TitanSkies.staged.app"), marker: "new")
        let blockedRollback = directory.appendingPathComponent("rollback")
        try Data("not a directory".utf8).write(to: blockedRollback)
        let recorder = Recorder()

        let result = try UpdateTransaction(
            services: FakeServices(recorder: recorder),
            validator: FakeValidator(recorder: recorder),
            relauncher: FakeRelauncher(recorder: recorder)
        ).run(liveApp: live, stagedApp: staged, rollbackDirectory: blockedRollback)

        guard case .rolledBack = result else { return XCTFail("expected rollback") }
        XCTAssertEqual(try String(contentsOf: live.appendingPathComponent("marker"), encoding: .utf8), "old")
        XCTAssertEqual(Array(recorder.events.suffix(2)), ["start:TitanSkies.app", "relaunch:TitanSkies.app"])
    }

    func testUpdateTransactionRestartsOldApplicationWhenInitialStopFails() throws {
        let directory = try temporaryDirectory()
        let live = try app(at: directory.appendingPathComponent("Applications/TitanSkies.app"), marker: "old")
        let staged = try app(at: directory.appendingPathComponent("Applications/TitanSkies.staged.app"), marker: "new")
        let recorder = Recorder()

        let result = try UpdateTransaction(
            services: FakeServices(recorder: recorder, failFirstStop: true),
            validator: FakeValidator(recorder: recorder),
            relauncher: FakeRelauncher(recorder: recorder)
        ).run(liveApp: live, stagedApp: staged, rollbackDirectory: directory.appendingPathComponent("rollback"))

        guard case .rolledBack = result else { return XCTFail("expected rollback") }
        XCTAssertEqual(try String(contentsOf: live.appendingPathComponent("marker"), encoding: .utf8), "old")
        XCTAssertEqual(recorder.events.filter { $0 == "stop:TitanSkies.app" }.count, 2)
        XCTAssertEqual(Array(recorder.events.suffix(2)), ["start:TitanSkies.app", "relaunch:TitanSkies.app"])
    }

    func testProductionServiceLifecycleUnregistersAndRegistersAcrossSuccessfulUpdate() throws {
        let directory = try temporaryDirectory()
        let live = try app(at: directory.appendingPathComponent("Applications/TitanSkies.app"), marker: "old")
        let staged = try app(at: directory.appendingPathComponent("Applications/TitanSkies.staged.app"), marker: "new")
        let recorder = Recorder()

        XCTAssertEqual(
            try UpdateTransaction(
                services: FakeProductionServices(recorder: recorder),
                validator: FakeValidator(recorder: recorder),
                relauncher: FakeRelauncher(recorder: recorder)
            ).run(liveApp: live, stagedApp: staged, rollbackDirectory: directory.appendingPathComponent("rollback")),
            .updated
        )
        XCTAssertEqual(
            recorder.events.filter { $0.hasPrefix("unregister:") || $0.hasPrefix("register:") },
            ["unregister:TitanSkies.app", "register:TitanSkies.app"]
        )
    }

    func testProductionServiceLifecycleReregistersRollbackAfterNewServiceFails() throws {
        let directory = try temporaryDirectory()
        let live = try app(at: directory.appendingPathComponent("Applications/TitanSkies.app"), marker: "old")
        let staged = try app(at: directory.appendingPathComponent("Applications/TitanSkies.staged.app"), marker: "new")
        let recorder = Recorder()

        let result = try UpdateTransaction(
            services: FakeProductionServices(recorder: recorder, failFirstRegister: true),
            validator: FakeValidator(recorder: recorder),
            relauncher: FakeRelauncher(recorder: recorder)
        ).run(liveApp: live, stagedApp: staged, rollbackDirectory: directory.appendingPathComponent("rollback"))

        guard case .rolledBack = result else { return XCTFail("expected rollback") }
        XCTAssertEqual(
            recorder.events.filter { $0.hasPrefix("unregister:") || $0.hasPrefix("register:") },
            [
                "unregister:TitanSkies.app",
                "register:TitanSkies.app",
                "unregister:TitanSkies.app",
                "register:TitanSkies.app",
            ]
        )
        XCTAssertEqual(try String(contentsOf: live.appendingPathComponent("marker"), encoding: .utf8), "old")
    }

    func testLiveUpdateServiceManagerRejectsMissingProductionChannelMetadata() throws {
        let directory = try temporaryDirectory()
        let app = directory.appendingPathComponent("Applications/TitanSkies.app")
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)

        let manager = LiveUpdateServiceManager(
            home: directory,
            expectedTeamID: "EXPECTEDTEAM",
            runner: SignatureProcessRunner()
        )
        XCTAssertThrowsError(try manager.start(app: app)) { error in
            guard let error = error as? TitanSkiesError, case .updateRejected = error else {
                return XCTFail("expected invalid production metadata rejection, got \(error)")
            }
        }
    }

    func testLiveUpdateServiceManagerRejectsUnsignedBetaApp() throws {
        let directory = try temporaryDirectory()
        let app = directory.appendingPathComponent("Applications/TitanSkies.app")
        try runtimeLock(channel: "unsigned-beta", in: app)

        let manager = LiveUpdateServiceManager(
            home: directory,
            expectedTeamID: "EXPECTEDTEAM",
            runner: SignatureProcessRunner()
        )
        XCTAssertThrowsError(try manager.start(app: app)) { error in
            guard let error = error as? TitanSkiesError, case .updateRejected = error else {
                return XCTFail("expected non-production channel rejection, got \(error)")
            }
        }
    }

    func testLaunchdBootoutPropagatesFailure() throws {
        let client = LaunchdClient(
            runner: FixedProcessRunner(result: ProcessResult(status: 3, stdout: "", stderr: "permission denied")),
            uid: 501
        )
        XCTAssertThrowsError(try client.bootout("com.hypertrial.titanskies.web")) { error in
            XCTAssertEqual(error as? TitanSkiesError, .launchd("permission denied"))
        }
    }

    func testLaunchdBootoutTreatsMissingServiceAsAlreadyStopped() {
        let client = LaunchdClient(
            runner: FixedProcessRunner(
                result: ProcessResult(status: 3, stdout: "", stderr: "Could not find service")
            ),
            uid: 501
        )
        XCTAssertNoThrow(try client.bootout("com.hypertrial.titanskies.web"))
    }

    func testLaunchdOwnershipRequiresExactProgramPath() throws {
        let expected = URL(fileURLWithPath: "/Applications/TitanSkies.app/Contents/MacOS/TitanSkiesWeb")
        let spoofed = ProcessResult(
            status: 0,
            stdout: "program = /tmp\(expected.path)\nstate = running\n",
            stderr: ""
        )
        XCTAssertFalse(LaunchdClient(runner: FixedProcessRunner(result: spoofed), uid: 501).isLoaded("web", executable: expected))

        let owned = ProcessResult(status: 0, stdout: "program = \(expected.path)\nstate = running\n", stderr: "")
        XCTAssertTrue(LaunchdClient(runner: FixedProcessRunner(result: owned), uid: 501).isLoaded("web", executable: expected))
    }

    func testLaunchdOwnershipAcceptsOnlyTheSupervisedProcessOrItsDirectChild() {
        let expected = URL(fileURLWithPath: "/Applications/TitanSkies.app/Contents/MacOS/TitanSkiesWeb")
        let client = LaunchdClient(runner: ProcessTopologyRunner(program: expected.path, launchdPID: "100", parents: ["200": "100"]), uid: 501)
        let nodeListener = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\nnode 200 user 20u IPv4 0 0t0 TCP 127.0.0.1:8080 (LISTEN)"
        XCTAssertTrue(client.ownsListener("web", executable: expected, listener: nodeListener))

        let foreignListener = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\nnode 300 user 20u IPv4 0 0t0 TCP 127.0.0.1:8080 (LISTEN)"
        XCTAssertFalse(client.ownsListener("web", executable: expected, listener: foreignListener))
    }

    func testStagedApplicationRejectsNonDeveloperIDAuthority() throws {
        let directory = try temporaryDirectory()
        let staged = try signedApp(at: directory.appendingPathComponent("TitanSkies.app"))
        XCTAssertThrowsError(
            try StagedApp.validate(
                staged,
                expectedTeamID: "EXPECTEDTEAM",
                requireProductionTrust: true,
                runner: SignatureProcessRunner(authority: "Apple Development: Example")
            )
        )
    }

    func testStagedApplicationRejectsRuntimeSignedByDifferentTeam() throws {
        let directory = try temporaryDirectory()
        let staged = try signedApp(at: directory.appendingPathComponent("TitanSkies.app"))
        XCTAssertThrowsError(
            try StagedApp.validate(
                staged,
                expectedTeamID: "EXPECTEDTEAM",
                requireProductionTrust: true,
                runner: SignatureProcessRunner(runtimeTeamIdentifier: "FOREIGNTEAM")
            )
        )
    }

    func testStagedApplicationRejectsInvalidRuntimeSignature() throws {
        let directory = try temporaryDirectory()
        let staged = try signedApp(at: directory.appendingPathComponent("TitanSkies.app"))
        XCTAssertThrowsError(
            try StagedApp.validate(
                staged,
                expectedTeamID: "EXPECTEDTEAM",
                requireProductionTrust: true,
                runner: SignatureProcessRunner(invalidRuntimeSignature: true)
            )
        )
    }

    func testStagedApplicationRejectsSymlinkedMainExecutable() throws {
        let directory = try temporaryDirectory()
        let app = directory.appendingPathComponent("TitanSkies.app")
        try FileManager.default.createDirectory(at: app.appendingPathComponent("Contents/MacOS"), withIntermediateDirectories: true)
        let info: [String: Any] = [
            "CFBundleIdentifier": TitanSkiesIdentity.bundleIdentifier,
            "CFBundleShortVersionString": "0.2.0",
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
        try data.write(to: app.appendingPathComponent("Contents/Info.plist"))
        try FileManager.default.createSymbolicLink(
            at: app.appendingPathComponent("Contents/MacOS/TitanSkies"),
            withDestinationURL: URL(fileURLWithPath: "/usr/bin/true")
        )
        XCTAssertThrowsError(try StagedApp.validate(app, newerThan: "0.1.0"))
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
        for executable in [paths.webLauncher, paths.ingestLauncher, paths.node, paths.python] {
            try FileManager.default.createDirectory(at: executable.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: URL(fileURLWithPath: "/usr/bin/true"), to: executable)
        }
        try FileManager.default.createDirectory(at: paths.runtime, withIntermediateDirectories: true)
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

    func testBundledExecutableCannotResolveOutsideApplication() throws {
        let directory = try temporaryDirectory()
        let app = directory.appendingPathComponent("TitanSkies.app")
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
        let link = app.appendingPathComponent("Contents/Resources/Host/node/bin/node")
        try FileManager.default.createDirectory(at: link.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: URL(fileURLWithPath: "/usr/bin/true"))
        XCTAssertThrowsError(try PathPolicy.requireInsideApp(link, app: app, label: "Node"))
    }

    func testValidatedChannelRejectsMissingRuntimeMetadata() throws {
        let directory = try temporaryDirectory()
        let app = directory.appendingPathComponent("TitanSkies.app")
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
        XCTAssertThrowsError(try Channel.validated(fromApp: app))
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

    private func runtimeLock(channel: String, in app: URL) throws {
        let resources = app.appendingPathComponent("Contents/Resources")
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: ["channel": channel]).write(to: resources.appendingPathComponent("runtime-lock.json"))
    }

    private func signedApp(at app: URL) throws -> URL {
        let executable = app.appendingPathComponent("Contents/MacOS/TitanSkies")
        let runtime = app.appendingPathComponent("Contents/Resources/Host/node/bin/node")
        for target in [executable, runtime] {
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: URL(fileURLWithPath: "/usr/bin/true"), to: target)
        }
        let info: [String: Any] = [
            "CFBundleIdentifier": TitanSkiesIdentity.bundleIdentifier,
            "CFBundleShortVersionString": "0.2.0",
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
        try data.write(to: app.appendingPathComponent("Contents/Info.plist"))
        return app
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
    private var failFirstStop: Bool
    private var failFirstStart: Bool

    init(recorder: Recorder, failFirstStop: Bool = false, failFirstStart: Bool = false) {
        self.recorder = recorder
        self.failFirstStop = failFirstStop
        self.failFirstStart = failFirstStart
    }

    func stop(app: URL) throws {
        recorder.events.append("stop:\(app.lastPathComponent)")
        if failFirstStop {
            failFirstStop = false
            throw TitanSkiesError.launchd("forced stop failure")
        }
    }

    func start(app: URL) throws {
        recorder.events.append("start:\(app.lastPathComponent)")
        if failFirstStart {
            failFirstStart = false
            throw TitanSkiesError.healthCheckFailed("forced start failure")
        }
    }
}

private final class FakeProductionServices: UpdateServiceManaging, @unchecked Sendable {
    private let recorder: Recorder
    private var failFirstRegister: Bool

    init(recorder: Recorder, failFirstRegister: Bool = false) {
        self.recorder = recorder
        self.failFirstRegister = failFirstRegister
    }

    func stop(app: URL) throws {
        recorder.events.append("unregister:\(app.lastPathComponent)")
    }

    func start(app: URL) throws {
        recorder.events.append("register:\(app.lastPathComponent)")
        if failFirstRegister {
            failFirstRegister = false
            throw TitanSkiesError.healthCheckFailed("forced production registration failure")
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

private struct FixedProcessRunner: ProcessRunning {
    let result: ProcessResult

    func run(_ executable: String, _ arguments: [String], environment: [String: String]?) throws -> ProcessResult {
        result
    }
}

private struct ProcessTopologyRunner: ProcessRunning {
    let program: String
    let launchdPID: String
    let parents: [String: String]

    func run(_ executable: String, _ arguments: [String], environment: [String: String]?) throws -> ProcessResult {
        if executable == "/bin/launchctl" {
            return ProcessResult(status: 0, stdout: "program = \(program)\npid = \(launchdPID)\n", stderr: "")
        }
        if executable == "/bin/ps", let pid = arguments.last, let parent = parents[pid] {
            return ProcessResult(status: 0, stdout: " \(parent)\n", stderr: "")
        }
        return ProcessResult(status: 1, stdout: "", stderr: "not found")
    }
}

private struct SignatureProcessRunner: ProcessRunning {
    let authority: String
    let appTeamIdentifier: String
    let runtimeTeamIdentifier: String
    let invalidRuntimeSignature: Bool

    init(
        authority: String = "Developer ID Application: Hypertrial",
        appTeamIdentifier: String = "EXPECTEDTEAM",
        runtimeTeamIdentifier: String = "EXPECTEDTEAM",
        invalidRuntimeSignature: Bool = false
    ) {
        self.authority = authority
        self.appTeamIdentifier = appTeamIdentifier
        self.runtimeTeamIdentifier = runtimeTeamIdentifier
        self.invalidRuntimeSignature = invalidRuntimeSignature
    }

    func run(_ executable: String, _ arguments: [String], environment: [String: String]?) throws -> ProcessResult {
        if executable == "/usr/bin/file" {
            return ProcessResult(status: 0, stdout: "Mach-O 64-bit executable arm64", stderr: "")
        }
        if executable == "/usr/sbin/spctl" {
            return ProcessResult(status: 0, stdout: "", stderr: "")
        }
        guard executable == "/usr/bin/codesign" else {
            return ProcessResult(status: 127, stdout: "", stderr: "unexpected executable")
        }
        let target = arguments.last ?? ""
        let isRuntime = target.contains("/Resources/Host/")
        if invalidRuntimeSignature && isRuntime && arguments.contains("--verify") {
            return ProcessResult(status: 1, stdout: "", stderr: "code object is not signed at all")
        }
        let team = isRuntime ? runtimeTeamIdentifier : appTeamIdentifier
        return ProcessResult(
            status: 0,
            stdout: "",
            stderr: "Authority=\(authority)\nTeamIdentifier=\(team)\nCodeDirectory v=20500 flags=0x10000(runtime)\n"
        )
    }
}
