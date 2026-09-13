import SwiftUI
import TitanSkiesCore

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            if model.unsignedWarning && model.lifecycle != .needsInstall {
                UnsignedBanner()
            }
            switch model.lifecycle {
            case .needsInstall:
                InstallPrompt()
            case .installing:
                StatusPanel(title: "Installing", detail: "Copying TitanSkies to ~/Applications. Services are not registered from this disk image.")
            case .starting:
                StatusPanel(title: "Starting", detail: "Starting the local web service on 127.0.0.1:8080.")
            case .initializing:
                VStack(spacing: 0) {
                    StatusPanel(title: "Initializing data", detail: "The explorer is up. First ingest is still publishing data.")
                    BrowserView()
                }
            case .ready, .degraded:
                BrowserView()
            case .stopped:
                StatusPanel(title: "Stopped", detail: "Background services are stopped. Publications and settings are preserved.")
            case .portConflict:
                StatusPanel(title: "Port 8080 is in use", detail: "TitanSkies will not load a foreign listener. Stop the other process or choose Repair after freeing the port.")
            case .repairRequired:
                StatusPanel(title: "Repair required", detail: "The local services could not be registered. Use Settings → Repair.")
            case .rolledBack:
                StatusPanel(title: "Rolled back", detail: "The previous application was restored after a failed update.")
            case .error(let message):
                StatusPanel(title: "TitanSkies needs attention", detail: message)
            }
        }
        .environmentObject(model)
    }
}

private struct UnsignedBanner: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack {
            Text("Unsigned beta. Gatekeeper warnings are expected; this build is not notarized.")
                .font(.callout)
            Spacer()
            Button("Dismiss") { model.unsignedWarning = false }
        }
        .padding(8)
        .background(Color.yellow.opacity(0.25))
    }
}

private struct InstallPrompt: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 16) {
            Text("Install TitanSkies")
                .font(.title)
            Text("This copy is running from a disk image or translocated path. Background services are not registered until the app is copied to ~/Applications/TitanSkies.app.")
                .multilineTextAlignment(.center)
                .frame(maxWidth: 480)
            Button("Install for Me") { model.installForMe() }
                .keyboardShortcut(.defaultAction)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct StatusPanel: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(spacing: 12) {
            Text(title).font(.title2)
            Text(detail)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 520)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
