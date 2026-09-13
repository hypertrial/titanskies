import SwiftUI
import TitanSkiesCore

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Data") {
                Toggle("Use deterministic demo data", isOn: $model.demoMode)
                SecureField("AirNow API key", text: $model.airNowKey)
                Button("Clear AirNow key") { model.airNowKey = "" }
                Button("Save and restart services") { model.saveSettings() }
            }
            Section("Services") {
                Text(statusText)
                Button("Start") { model.restartServices() }
                Button("Stop") { model.stopServices() }
                Button("Restart") { model.restartServices() }
                Button("Repair") { model.repair() }
                Button("Open logs") { model.openLogs() }
                Button("Open in browser") { model.openInBrowser() }
            }
            Section("Updates") {
                Text(model.updateMessage.isEmpty ? "Updates are user-initiated and verified before replacement." : model.updateMessage)
                Button("Check for update") { model.checkUpdates() }
                Button("Install verified update") { model.applyPendingUpdate() }
                    .disabled(model.pendingUpdate == nil)
                Text("Unsigned beta: trust the published DMG checksum, then this app verifies later manifests and hashes. This is not Gatekeeper/notarization trust.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Uninstall") {
                Button("Uninstall (keep data)") { model.uninstall(purge: false) }
                Button("Uninstall and delete data", role: .destructive) { model.confirmPurge = true }
            }
        }
        .formStyle(.grouped)
        .padding()
        .alert("Delete TitanSkies data?", isPresented: $model.confirmPurge) {
            Button("Cancel", role: .cancel) {}
            Button("Delete default data", role: .destructive) { model.uninstall(purge: true) }
        } message: {
            Text("This removes only TitanSkies-owned default folders after the services stop. Custom paths and other files are not deleted. Symlinks are not followed.")
        }
    }

    private var statusText: String {
        switch model.lifecycle {
        case .ready: return "Web service is healthy on 127.0.0.1:8080."
        case .initializing: return "Web service is up; first ingest is still initializing."
        case .degraded: return "Web service is up; one or more sources are degraded."
        case .stopped: return "Services are stopped."
        case .portConflict: return "Port 8080 is occupied by another process."
        case .repairRequired: return "Repair is required."
        case .rolledBack: return "Rolled back to the previous application."
        case .needsInstall: return "Install to ~/Applications before starting services."
        case .installing: return "Installing to ~/Applications."
        case .starting: return "Starting services."
        case .error(let message): return message
        }
    }
}
