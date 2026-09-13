import Foundation
import TitanSkiesCore

@main
enum TitanSkiesUpdaterMain {
    static func main() {
        do {
            try run(arguments: Array(CommandLine.arguments.dropFirst()))
        } catch {
            FileHandle.standardError.write(Data("TitanSkies updater failed: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }

    static func run(arguments: [String]) throws {
        var live: String?
        var staged: String?
        var rollback: String?
        var index = 0
        while index < arguments.count {
            switch arguments[index] {
            case "--live":
                live = arguments[index + 1]
                index += 2
            case "--staged":
                staged = arguments[index + 1]
                index += 2
            case "--rollback":
                rollback = arguments[index + 1]
                index += 2
            default:
                index += 1
            }
        }
        guard let live, let staged, let rollback else {
            throw TitanSkiesError.updateRejected("updater requires --live --staged --rollback")
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        let liveURL = URL(fileURLWithPath: live)
        let stagedURL = URL(fileURLWithPath: staged)
        let rollbackDir = URL(fileURLWithPath: rollback)
        let rollbackApp = rollbackDir.appendingPathComponent("TitanSkies.app")
        if liveURL.path.contains("Application Support") {
            throw TitanSkiesError.unsafePath("live app must not be the rollback area")
        }
        try StagedApp.validate(stagedURL)
        let paths = TitanSkiesPaths(home: home, app: liveURL)
        let services = ServiceManager(paths: paths, channel: Channel.load(fromApp: liveURL))
        try services.stop()
        try FileManager.default.createDirectory(at: rollbackDir, withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: rollbackApp.path) {
            try FileManager.default.removeItem(at: rollbackApp)
        }
        if FileManager.default.fileExists(atPath: liveURL.path) {
            try FileManager.default.moveItem(at: liveURL, to: rollbackApp)
        }
        do {
            try FileManager.default.moveItem(at: stagedURL, to: liveURL)
            let installed = TitanSkiesPaths(home: home, app: liveURL)
            let next = ServiceManager(paths: installed, channel: Channel.load(fromApp: liveURL))
            try next.start()
        } catch {
            try services.stop()
            if FileManager.default.fileExists(atPath: liveURL.path) {
                let failed = rollbackDir.appendingPathComponent("TitanSkies.failed.app")
                if FileManager.default.fileExists(atPath: failed.path) {
                    try FileManager.default.removeItem(at: failed)
                }
                try FileManager.default.moveItem(at: liveURL, to: failed)
            }
            try StagedApp.validate(rollbackApp)
            if FileManager.default.fileExists(atPath: rollbackApp.path) {
                try FileManager.default.moveItem(at: rollbackApp, to: liveURL)
            }
            let restored = ServiceManager(paths: TitanSkiesPaths(home: home, app: liveURL), channel: Channel.load(fromApp: liveURL))
            try restored.start()
            throw TitanSkiesError.rollbackFailed(error.localizedDescription)
        }
    }
}
