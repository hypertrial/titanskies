import SwiftUI
import TitanSkiesCore

@main
struct TitanSkiesApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("TitanSkies") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 1024, minHeight: 720)
                .onAppear { model.bootstrap() }
        }
        Settings {
            SettingsView()
                .environmentObject(model)
                .frame(minWidth: 480, minHeight: 520)
        }
    }
}
