import AppKit
import Combine
import Foundation
import ServiceManagement
import SwiftUI
import TitanSkiesCore

@MainActor
final class AppModel: ObservableObject {
    enum Lifecycle: Equatable {
        case installing
        case starting
        case initializing
        case ready
        case degraded
        case stopped
        case portConflict
        case approvalRequired
        case repairRequired
        case rolledBack
        case needsInstall
        case error(String)
    }

    @Published var lifecycle: Lifecycle = .starting
    @Published var env: EnvFile
    @Published var airNowKey: String = ""
    @Published var demoMode = false
    @Published var updateMessage: String = ""
    @Published var pendingUpdate: UpdateManifest?
    @Published var unsignedWarning: Bool
    @Published var confirmPurge = false
    @Published private(set) var nativeOperationInProgress = false

    let paths: TitanSkiesPaths
    let services: ServiceManager
    let channel: Channel
    private let health: ServiceHealth
    private var operationState = NativeOperationState()

    init(paths: TitanSkiesPaths? = nil) {
        let resolved: TitanSkiesPaths
        if let paths {
            resolved = paths
        } else if let live = try? TitanSkiesPaths.live() {
            resolved = live
        } else {
            let home = FileManager.default.homeDirectoryForCurrentUser
            resolved = TitanSkiesPaths(home: home, app: home.appendingPathComponent("Applications/TitanSkies.app"))
        }
        let channel = Channel.load(fromApp: resolved.app)
        self.paths = resolved
        self.channel = channel
        self.services = ServiceManager(paths: resolved, channel: channel)
        self.health = ServiceHealth()
        self.env = EnvFile()
        self.unsignedWarning = channel.showsUnsignedWarning
    }

    func bootstrap() {
        Task { await start() }
    }

    func start() async {
        guard beginNativeOperation() else { return }
        defer { finishNativeOperation() }
        do {
            if PathPolicy.isUntrustedInstallSource(paths.app) {
                lifecycle = .needsInstall
                return
            }
            lifecycle = .starting
            if channel == .production {
                try await registerProductionIfSigned()
            }
            let paths = paths
            let services = services
            let health = health
            let result = try await Task.detached {
                try paths.createPrivateDirectories()
                let loadedEnv = try EnvFile.load(from: paths.env, paths: paths)
                if PortProbe.occupier() != nil {
                    let snapshot = try? health.healthz()
                    if snapshot?.serviceIdentity != true {
                        throw TitanSkiesError.portConflict(TitanSkiesIdentity.port)
                    }
                }
                try services.start()
                return (loadedEnv, try? health.contextHealth())
            }.value
            env = result.0
            demoMode = result.0.values["CONTEXT_SOURCE"] == "demo"
            airNowKey = result.0.values["AIRNOW_API_KEY"] ?? ""
            lifecycle = result.1?.initializing == true ? .initializing : .ready
            loadUpdateStatusIfPresent()
        } catch TitanSkiesError.portConflict(_) {
            lifecycle = .portConflict
        } catch TitanSkiesError.serviceApprovalRequired {
            lifecycle = .approvalRequired
        } catch {
            lifecycle = .error(error.localizedDescription)
        }
    }

    func saveSettings() {
        Task { await saveSettingsNow() }
    }

    private func saveSettingsNow() async {
        guard beginNativeOperation() else { return }
        defer { finishNativeOperation() }
        var pending = env
        pending.values["CONTEXT_SOURCE"] = demoMode ? "demo" : "live"
        if airNowKey.isEmpty {
            pending.values.removeValue(forKey: "AIRNOW_API_KEY")
        } else {
            pending.values["AIRNOW_API_KEY"] = airNowKey
        }
        let paths = paths
        let services = services
        do {
            let validated = try await Task.detached {
                let validated = try pending.validated(paths: paths)
                try validated.writeAtomically(to: paths.env)
                try services.restart()
                return validated
            }.value
            env = validated
            lifecycle = .ready
        } catch {
            lifecycle = .error(error.localizedDescription)
        }
    }

    func stopServices() {
        Task { await stopServicesNow() }
    }

    private func stopServicesNow() async {
        guard beginNativeOperation() else { return }
        defer { finishNativeOperation() }
        let services = services
        do {
            try await Task.detached { try services.stop() }.value
            lifecycle = .stopped
        } catch {
            lifecycle = .error(error.localizedDescription)
        }
    }

    func restartServices() {
        Task { await start() }
    }

    func repair() {
        Task { await repairNow() }
    }

    private func repairNow() async {
        guard beginNativeOperation() else { return }
        defer { finishNativeOperation() }
        let services = services
        do {
            try await Task.detached { try services.repair() }.value
            lifecycle = .ready
        } catch {
            lifecycle = .repairRequired
        }
    }

    func installForMe() {
        Task {
            guard beginNativeOperation() else { return }
            defer { finishNativeOperation() }
            do {
                lifecycle = .installing
                let paths = paths
                let installed = try await Task.detached { try AppInstaller(paths: paths).installIfNeeded() }.value
                NSWorkspace.shared.open(installed)
                NSApp.terminate(nil)
            } catch {
                lifecycle = .error(error.localizedDescription)
            }
        }
    }

    func openLogs() {
        NSWorkspace.shared.open(paths.logs)
    }

    func openInBrowser() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:8080/")!)
    }

    func uninstall(purge: Bool) {
        Task {
            guard beginNativeOperation() else { return }
            defer { finishNativeOperation() }
            do {
                if channel == .production {
                    unregisterProductionLoginItems()
                }
                let paths = paths
                try await Task.detached { try AppUninstaller(paths: paths).uninstall(purge: purge) }.value
                NSWorkspace.shared.recycle([paths.app]) { _, error in
                    DispatchQueue.main.async {
                        if let error {
                            self.lifecycle = .error(error.localizedDescription)
                        } else {
                            NSApp.terminate(nil)
                        }
                    }
                }
            } catch {
                lifecycle = .error(error.localizedDescription)
            }
        }
    }

    func checkUpdates() {
        Task { await checkUpdatesNow() }
    }

    private func checkUpdatesNow() async {
        guard beginNativeOperation() else { return }
        defer { finishNativeOperation() }
        do {
            pendingUpdate = nil
            let lock = try loadRuntimeLock()
            guard let manifestURL = URL(string: lock["updateManifestURL"] as? String ?? ""),
                  manifestURL.scheme == "https" else {
                updateMessage = "Update metadata is missing from the bundle."
                return
            }
            let (bytes, response) = try await URLSession.shared.data(from: manifestURL)
            guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
                throw TitanSkiesError.updateRejected("update manifest did not return HTTP success")
            }
            let manifest = try UpdateManifest.parse(bytes)
            let version = currentVersion()
            let publicKey = lock["updatePublicKeyHex"] as? String ?? TitanSkiesIdentity.updatePublicKeyHex
            switch try manifest.availability(
                currentVersion: version,
                channel: channel.rawValue,
                publicKeyHex: publicKey
            ) {
            case .upToDate:
                updateMessage = "TitanSkies \(version) is up to date."
            case .available(let available):
                pendingUpdate = available
                updateMessage = "Version \(available.version) is available. Install it to download, verify, and replace the app."
            }
        } catch {
            pendingUpdate = nil
            updateMessage = error.localizedDescription
        }
    }

    func applyPendingUpdate() {
        guard let manifest = pendingUpdate else {
            updateMessage = "Check for an update before installing."
            return
        }
        Task {
            guard beginNativeOperation() else { return }
            defer { finishNativeOperation() }
            do {
                updateMessage = "Downloading and verifying version \(manifest.version)…"
                let paths = paths
                let version = currentVersion()
                let work = paths.cache.appendingPathComponent("update/\(UUID().uuidString)")
                let staged = try await Task.detached {
                    try await UpdateApplier().stage(
                        manifest: manifest,
                        paths: paths,
                        work: work,
                        currentVersion: version
                    )
                }.value
                try await Task.detached { try UpdateApplier().launchHelper(paths: paths, staged: staged) }.value
                updateMessage = "Replacing the application. TitanSkies will quit and relaunch."
                NSApp.terminate(nil)
            } catch {
                updateMessage = error.localizedDescription
            }
        }
    }

    private func registerProductionIfSigned() async throws {
        let services = services
        try await Task.detached { try services.migrateBetaToProduction() }.value
        if #available(macOS 13.0, *) {
            let web = SMAppService.agent(plistName: "com.hypertrial.titanskies.web.plist")
            let ingest = SMAppService.agent(plistName: "com.hypertrial.titanskies.ingest.plist")
            if web.status == .notRegistered { try web.register() }
            if ingest.status == .notRegistered { try ingest.register() }
            if web.status == .requiresApproval || ingest.status == .requiresApproval {
                SMAppService.openSystemSettingsLoginItems()
                throw TitanSkiesError.serviceApprovalRequired
            }
        }
        let betaJobsRemain = await Task.detached {
            services.launchd.isLoaded(TitanSkiesIdentity.betaWebLabel)
                || services.launchd.isLoaded(TitanSkiesIdentity.betaIngestLabel)
        }.value
        if betaJobsRemain {
            throw TitanSkiesError.launchd("beta jobs still loaded after production migration")
        }
    }

    private func beginNativeOperation() -> Bool {
        guard operationState.begin() else { return false }
        nativeOperationInProgress = operationState.isActive
        return operationState.isActive
    }

    private func finishNativeOperation() {
        operationState.finish()
        nativeOperationInProgress = operationState.isActive
    }

    private func currentVersion() -> String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0"
    }

    private func loadRuntimeLock() throws -> [String: Any] {
        let lockURL = paths.app.appendingPathComponent("Contents/Resources/runtime-lock.json")
        let data = try Data(contentsOf: lockURL)
        guard let lock = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw TitanSkiesError.missingBundleResource("runtime-lock.json")
        }
        return lock
    }

    private func loadUpdateStatusIfPresent() {
        guard let message = try? String(contentsOf: paths.updateStatus, encoding: .utf8), !message.isEmpty else { return }
        updateMessage = message.trimmingCharacters(in: .whitespacesAndNewlines)
        try? FileManager.default.removeItem(at: paths.updateStatus)
    }

    private func unregisterProductionLoginItems() {
        if #available(macOS 13.0, *) {
            try? SMAppService.agent(plistName: "com.hypertrial.titanskies.web.plist").unregister()
            try? SMAppService.agent(plistName: "com.hypertrial.titanskies.ingest.plist").unregister()
        }
    }
}
