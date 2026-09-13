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
    @Published var unsignedWarning = true
    @Published var confirmPurge = false

    let paths: TitanSkiesPaths
    let services: ServiceManager
    private let health: ServiceHealth

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
        self.paths = resolved
        self.services = ServiceManager(paths: resolved, channel: Channel.load(fromApp: resolved.app))
        self.health = ServiceHealth()
        self.env = EnvFile()
    }

    func bootstrap() {
        Task { await start() }
    }

    func start() async {
        do {
            if PathPolicy.isUntrustedInstallSource(paths.app) {
                lifecycle = .needsInstall
                return
            }
            lifecycle = .starting
            if channelIsProduction() {
                try registerProductionIfSigned()
            }
            try paths.createPrivateDirectories()
            env = try EnvFile.load(from: paths.env, paths: paths)
            demoMode = env.values["CONTEXT_SOURCE"] == "demo"
            airNowKey = env.values["AIRNOW_API_KEY"] ?? ""
            if PortProbe.occupier() != nil {
                let snapshot = try? health.healthz()
                if snapshot?.serviceIdentity != true {
                    lifecycle = .portConflict
                    return
                }
            }
            try services.start()
            let context = try? health.contextHealth()
            lifecycle = context?.initializing == true ? .initializing : .ready
            if context?.httpStatus == 200 && context?.initializing == false {
                lifecycle = .ready
            }
        } catch let error as TitanSkiesError where error == .portConflict(TitanSkiesIdentity.port) {
            lifecycle = .portConflict
        } catch {
            lifecycle = .error(error.localizedDescription)
        }
    }

    func saveSettings() {
        do {
            env.values["CONTEXT_SOURCE"] = demoMode ? "demo" : "live"
            if airNowKey.isEmpty {
                env.values.removeValue(forKey: "AIRNOW_API_KEY")
            } else {
                env.values["AIRNOW_API_KEY"] = airNowKey
            }
            let validated = try env.validated(paths: paths)
            try validated.writeAtomically(to: paths.env)
            env = validated
            try services.restart()
            lifecycle = .ready
        } catch {
            lifecycle = .error(error.localizedDescription)
        }
    }

    func stopServices() {
        do {
            try services.stop()
            lifecycle = .stopped
        } catch {
            lifecycle = .error(error.localizedDescription)
        }
    }

    func restartServices() {
        Task { await start() }
    }

    func repair() {
        do {
            try services.repair()
            lifecycle = .ready
        } catch {
            lifecycle = .repairRequired
        }
    }

    func installForMe() {
        do {
            lifecycle = .installing
            let installed = try AppInstaller(paths: paths).installIfNeeded()
            NSWorkspace.shared.open(installed)
            NSApp.terminate(nil)
        } catch {
            lifecycle = .error(error.localizedDescription)
        }
    }

    func openLogs() {
        NSWorkspace.shared.open(paths.logs)
    }

    func openInBrowser() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:8080/")!)
    }

    func uninstall(purge: Bool) {
        do {
            if channelIsProduction() {
                unregisterProductionLoginItems()
            }
            try AppUninstaller(paths: paths).uninstall(purge: purge)
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

    func checkUpdates() {
        Task {
            do {
                pendingUpdate = nil
                let lock = try loadRuntimeLock()
                guard let manifestURL = URL(string: lock["updateManifestURL"] as? String ?? ""),
                      manifestURL.scheme == "https" else {
                    updateMessage = "Update metadata is missing from the bundle."
                    return
                }
                let (bytes, _) = try await URLSession.shared.data(from: manifestURL)
                let manifest = try UpdateManifest.parse(bytes)
                let version = currentVersion()
                let publicKey = lock["updatePublicKeyHex"] as? String ?? TitanSkiesIdentity.updatePublicKeyHex
                try manifest.validate(
                    currentVersion: version,
                    channel: lock["channel"] as? String ?? Channel.unsignedBeta.rawValue,
                    publicKeyHex: publicKey
                )
                pendingUpdate = manifest
                updateMessage = "Version \(manifest.version) is available. Install it to download, verify, and replace the app."
            } catch {
                pendingUpdate = nil
                updateMessage = error.localizedDescription
            }
        }
    }

    func applyPendingUpdate() {
        guard let manifest = pendingUpdate else {
            updateMessage = "Check for an update before installing."
            return
        }
        Task {
            do {
                updateMessage = "Downloading and verifying version \(manifest.version)…"
                let work = paths.cache.appendingPathComponent("update/\(UUID().uuidString)")
                let staged = try UpdateApplier().stage(
                    manifest: manifest,
                    paths: paths,
                    work: work,
                    currentVersion: currentVersion()
                )
                try UpdateApplier().launchHelper(paths: paths, staged: staged)
                updateMessage = "Replacing the application. TitanSkies will quit and relaunch."
                NSApp.terminate(nil)
            } catch {
                updateMessage = error.localizedDescription
            }
        }
    }

    func registerProductionIfSigned() throws {
        guard channelIsProduction() else { return }
        try services.migrateBetaToProduction()
        if #available(macOS 13.0, *) {
            let web = SMAppService.agent(plistName: "com.hypertrial.titanskies.web.plist")
            let ingest = SMAppService.agent(plistName: "com.hypertrial.titanskies.ingest.plist")
            try web.register()
            try ingest.register()
            if web.status == .requiresApproval {
                SMAppService.openSystemSettingsLoginItems()
            }
        }
        if services.launchd.isLoaded(TitanSkiesIdentity.betaWebLabel)
            || services.launchd.isLoaded(TitanSkiesIdentity.betaIngestLabel) {
            throw TitanSkiesError.launchd("beta jobs still loaded after production migration")
        }
    }

    private func channelIsProduction() -> Bool {
        (try? loadRuntimeLock())?["channel"] as? String == Channel.production.rawValue
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

    private func unregisterProductionLoginItems() {
        if #available(macOS 13.0, *) {
            try? SMAppService.agent(plistName: "com.hypertrial.titanskies.web.plist").unregister()
            try? SMAppService.agent(plistName: "com.hypertrial.titanskies.ingest.plist").unregister()
        }
    }
}
