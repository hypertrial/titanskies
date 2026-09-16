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
        var values: [String: String] = [:]
        var index = 0
        while index < arguments.count {
            let option = arguments[index]
            guard ["--live", "--staged", "--rollback"].contains(option) else {
                throw TitanSkiesError.updateRejected("unknown updater argument \(option)")
            }
            guard index + 1 < arguments.count else {
                throw TitanSkiesError.updateRejected("missing value for \(option)")
            }
            values[option] = arguments[index + 1]
            index += 2
        }
        guard let live = values["--live"], let staged = values["--staged"], let rollback = values["--rollback"] else {
            throw TitanSkiesError.updateRejected("updater requires --live --staged --rollback")
        }

        let home = FileManager.default.homeDirectoryForCurrentUser
        let liveURL = URL(fileURLWithPath: live)
        let stagedURL = URL(fileURLWithPath: staged)
        let rollbackDirectory = URL(fileURLWithPath: rollback)
        let paths = TitanSkiesPaths(home: home, app: liveURL)
        guard liveURL.standardizedFileURL == paths.installedApp.standardizedFileURL else {
            throw TitanSkiesError.unsafePath("live app must be ~/Applications/TitanSkies.app")
        }
        let expectedStaged = paths.applications.appendingPathComponent("TitanSkies.staged.app")
        guard stagedURL.standardizedFileURL == expectedStaged.standardizedFileURL else {
            throw TitanSkiesError.unsafePath("staged app must be ~/Applications/TitanSkies.staged.app")
        }
        guard rollbackDirectory.standardizedFileURL == paths.rollback.standardizedFileURL else {
            throw TitanSkiesError.unsafePath("rollback directory must be TitanSkies-owned Application Support")
        }
        try PathPolicy.rejectSymlink(paths.applications, label: "Applications")
        try PathPolicy.rejectSymlink(paths.support, label: "support")
        try PathPolicy.rejectSymlink(rollbackDirectory, label: "rollback")
        let result = try UpdateTransaction(services: LiveUpdateServiceManager(home: home)).run(
            liveApp: liveURL,
            stagedApp: stagedURL,
            rollbackDirectory: rollbackDirectory,
            rollbackStatusURL: paths.updateStatus
        )
        if case .rolledBack(let reason) = result {
            throw TitanSkiesError.rollbackFailed("previous version restored after update failed: \(reason)")
        }
    }
}
