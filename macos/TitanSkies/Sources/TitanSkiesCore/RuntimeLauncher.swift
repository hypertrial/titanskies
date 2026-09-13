import Foundation

public enum LauncherKind: String {
    case web
    case ingest
}

public enum RuntimeLauncher {
    public static func environment(kind: LauncherKind, env: EnvFile, home: String) -> [String: String] {
        var merged: [String: String] = [
            "PATH": "/usr/bin:/bin",
            "HOME": home,
            "TMPDIR": FileManager.default.temporaryDirectory.path,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        ]
        let selected = kind == .web ? env.webEnvironment() : env.ingestEnvironment()
        for (key, value) in selected {
            merged[key] = value
        }
        if kind == .web {
            merged.removeValue(forKey: "AIRNOW_API_KEY")
            for key in merged.keys where key.hasPrefix("NEXT_PUBLIC_") && key.contains("AIRNOW") {
                merged.removeValue(forKey: key)
            }
        }
        return merged
    }

    public static func exec(kind: LauncherKind, paths: TitanSkiesPaths) throws -> Never {
        try paths.createPrivateDirectories()
        let env = try EnvFile.load(from: paths.env, paths: paths)
        try LogRotation.rotate(at: kind == .web ? paths.webLog : paths.ingestLog)
        let executable = kind == .web ? paths.node : paths.python
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw TitanSkiesError.missingBundleResource(executable.path)
        }
        let arguments: [String]
        if kind == .web {
            arguments = [paths.runtime.appendingPathComponent("scripts/run-web.mjs").path]
        } else {
            arguments = [paths.runtime.appendingPathComponent("scripts/watch_context.py").path, "--once"]
        }
        let process = Process()
        process.currentDirectoryURL = paths.runtime
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment(kind: kind, env: env, home: paths.home.path)
        try process.run()
        process.waitUntilExit()
        exit(process.terminationStatus)
    }
}
