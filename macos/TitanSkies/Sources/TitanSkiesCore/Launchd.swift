import Foundation

public struct ProcessResult: Sendable {
    public let status: Int32
    public let stdout: String
    public let stderr: String

    public init(status: Int32, stdout: String, stderr: String) {
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
    }

    public var succeeded: Bool { status == 0 }
}

public protocol ProcessRunning: Sendable {
    func run(_ executable: String, _ arguments: [String], environment: [String: String]?) throws -> ProcessResult
}

public struct FoundationProcessRunner: ProcessRunning {
    public init() {}

    public func run(_ executable: String, _ arguments: [String], environment: [String: String]?) throws -> ProcessResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        if let environment {
            process.environment = environment
        }
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        process.waitUntilExit()
        return ProcessResult(
            status: process.terminationStatus,
            stdout: String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "",
            stderr: String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        )
    }
}

public struct LaunchdClient: Sendable {
    public let runner: any ProcessRunning
    public let uid: uid_t

    public init(runner: any ProcessRunning = FoundationProcessRunner(), uid: uid_t = getuid()) {
        self.runner = runner
        self.uid = uid
    }

    public var domain: String { "gui/\(uid)" }

    public func bootout(_ label: String) throws {
        let result = try runner.run("/bin/launchctl", ["bootout", "\(domain)/\(label)"], environment: nil)
        let missing = result.stderr.contains("Could not find service")
            || result.stderr.contains("No such process")
            || result.stderr.contains("service not found")
        if !result.succeeded && !missing {
            throw TitanSkiesError.launchd(result.stderr.isEmpty ? "bootout failed" : result.stderr)
        }
    }

    public func bootstrap(plist: URL) throws {
        let result = try runner.run("/bin/launchctl", ["bootstrap", domain, plist.path], environment: nil)
        if !result.succeeded && !result.stderr.contains("already loaded") && result.status != 0 {
            throw TitanSkiesError.launchd(result.stderr.isEmpty ? "bootstrap failed" : result.stderr)
        }
    }

    public func kickstart(_ label: String) throws {
        let result = try runner.run("/bin/launchctl", ["kickstart", "-k", "\(domain)/\(label)"], environment: nil)
        if !result.succeeded {
            throw TitanSkiesError.launchd(result.stderr.isEmpty ? "kickstart failed" : result.stderr)
        }
    }

    public func isLoaded(_ label: String, executable: URL? = nil) -> Bool {
        let result = try? runner.run("/bin/launchctl", ["print", "\(domain)/\(label)"], environment: nil)
        guard result?.succeeded == true else { return false }
        guard let executable else { return true }
        return launchdProgram(in: result?.stdout ?? "") == executable.path
    }

    public func ownsListener(_ label: String, executable: URL, listener: String) -> Bool {
        guard let result = try? runner.run("/bin/launchctl", ["print", "\(domain)/\(label)"], environment: nil),
              result.succeeded,
              launchdProgram(in: result.stdout) == executable.path,
              let pid = launchdPID(in: result.stdout) else {
            return false
        }
        for listenerPID in listenerPIDs(in: listener) {
            if listenerPID == pid || parentPID(of: listenerPID) == pid {
                return true
            }
        }
        return false
    }

    private func launchdProgram(in output: String) -> String? {
        value(after: "program =", in: output)
    }

    private func launchdPID(in output: String) -> String? {
        value(after: "pid =", in: output)
    }

    private func value(after prefix: String, in output: String) -> String? {
        for line in output.split(separator: "\n") {
            let value = line.trimmingCharacters(in: .whitespaces)
            guard value.hasPrefix(prefix) else { continue }
            let result = value.dropFirst(prefix.count).trimmingCharacters(in: .whitespaces)
            return result.isEmpty ? nil : result
        }
        return nil
    }

    private func listenerPIDs(in output: String) -> Set<String> {
        Set(output.split(separator: "\n").compactMap { line in
            let fields = line.split(whereSeparator: { $0.isWhitespace })
            guard fields.count > 1, fields[1].allSatisfy({ $0.isNumber }) else { return nil }
            return String(fields[1])
        })
    }

    private func parentPID(of pid: String) -> String? {
        guard let result = try? runner.run("/bin/ps", ["-o", "ppid=", "-p", pid], environment: nil),
              result.succeeded else {
            return nil
        }
        let parent = result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        return parent.allSatisfy({ $0.isNumber }) && !parent.isEmpty ? parent : nil
    }
}

public enum LaunchdPlist {
    public static func betaWeb(paths: TitanSkiesPaths) -> [String: Any] {
        [
            "Label": TitanSkiesIdentity.betaWebLabel,
            "ProgramArguments": [paths.webLauncher.path],
            "WorkingDirectory": paths.runtime.path,
            "RunAtLoad": true,
            "KeepAlive": true,
            "ThrottleInterval": 10,
            "ProcessType": "Background",
            "StandardOutPath": paths.webLog.path,
            "StandardErrorPath": paths.webLog.path,
            "AssociatedBundleIdentifiers": [TitanSkiesIdentity.bundleIdentifier],
        ]
    }

    public static func betaIngest(paths: TitanSkiesPaths) -> [String: Any] {
        [
            "Label": TitanSkiesIdentity.betaIngestLabel,
            "ProgramArguments": [paths.ingestLauncher.path],
            "WorkingDirectory": paths.runtime.path,
            "RunAtLoad": false,
            "ProcessType": "Background",
            "StartCalendarInterval": TitanSkiesIdentity.ingestMinutes.map { ["Minute": $0] },
            "StandardOutPath": paths.ingestLog.path,
            "StandardErrorPath": paths.ingestLog.path,
            "AssociatedBundleIdentifiers": [TitanSkiesIdentity.bundleIdentifier],
        ]
    }

    public static func write(_ payload: [String: Any], to url: URL) throws {
        if let executable = (payload["ProgramArguments"] as? [String])?.first, executable.contains("Application Support") {
            throw TitanSkiesError.unsafePath("executable path must not be in Application Support")
        }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try PropertyListSerialization.data(fromPropertyList: payload, format: .xml, options: 0)
        try data.write(to: url, options: .atomic)
    }
}
