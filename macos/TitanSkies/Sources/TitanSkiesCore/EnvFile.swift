import Foundation

public struct EnvFile: Equatable, Sendable {
    public var values: [String: String]

    public init(values: [String: String] = [:]) {
        self.values = values
    }

    public static func parse(_ text: String) throws -> EnvFile {
        var values: [String: String] = [:]
        for (index, raw) in text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline).enumerated() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") {
                continue
            }
            if raw.unicodeScalars.contains(where: { $0.value < 32 && $0 != "\t" }) {
                throw TitanSkiesError.invalidEnv("control character on line \(index + 1)")
            }
            guard let split = line.firstIndex(of: "=") else {
                throw TitanSkiesError.invalidEnv("malformed env line \(index + 1)")
            }
            let key = String(line[..<split])
            let value = String(line[line.index(after: split)...]).trimmingCharacters(in: CharacterSet(charactersIn: "\"' "))
            guard key.range(of: "^[A-Z][A-Z0-9_]*$", options: .regularExpression) != nil else {
                throw TitanSkiesError.invalidEnv("unsupported env key \(key)")
            }
            guard TitanSkiesIdentity.allowedEnvKeys.contains(key) else {
                throw TitanSkiesError.invalidEnv("unsupported env key \(key)")
            }
            if value.unicodeScalars.contains(where: { $0.value < 32 }) {
                throw TitanSkiesError.invalidEnv("control character in \(key)")
            }
            values[key] = value
        }
        return EnvFile(values: values)
    }

    public func validated(paths: TitanSkiesPaths) throws -> EnvFile {
        var next = values
        let source = next["CONTEXT_SOURCE", default: "live"]
        guard source == "live" || source == "demo" else {
            throw TitanSkiesError.invalidEnv("CONTEXT_SOURCE must be live or demo")
        }
        if next["TITANSKIES_BIND_ADDR", default: TitanSkiesIdentity.bindAddress] != TitanSkiesIdentity.bindAddress {
            throw TitanSkiesError.invalidEnv("native macOS bind address must be 127.0.0.1")
        }
        if next["TITANSKIES_PORT", default: String(TitanSkiesIdentity.port)] != String(TitanSkiesIdentity.port) {
            throw TitanSkiesError.invalidEnv("native macOS port must be 8080")
        }
        let data = next["TITANSKIES_DATA_DIR"] ?? paths.data.path
        let cache = next["TITANSKIES_CACHE_DIR"] ?? paths.cache.path
        _ = try PathPolicy.rejectUnsafe(data, app: paths.app, home: paths.home)
        _ = try PathPolicy.rejectUnsafe(cache, app: paths.app, home: paths.home)
        if FileManager.default.fileExists(atPath: data) {
            let attrs = try FileManager.default.attributesOfItem(atPath: data)
            if attrs[.type] as? FileAttributeType == .typeSymbolicLink {
                throw TitanSkiesError.unsafePath("data path must not be a symlink")
            }
        }
        next["CONTEXT_SOURCE"] = source
        next["TITANSKIES_BIND_ADDR"] = TitanSkiesIdentity.bindAddress
        next["TITANSKIES_PORT"] = String(TitanSkiesIdentity.port)
        next["TITANSKIES_DATA_DIR"] = data
        next["TITANSKIES_CACHE_DIR"] = cache
        return EnvFile(values: next)
    }

    public func webEnvironment() -> [String: String] {
        values.filter { !TitanSkiesIdentity.secretEnvKeys.contains($0.key) }
    }

    public func ingestEnvironment() -> [String: String] {
        values
    }

    public var serialized: String {
        values.keys.sorted().map { "\($0)=\(values[$0] ?? "")" }.joined(separator: "\n") + "\n"
    }

    public func writeAtomically(to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let temporary = url.deletingLastPathComponent().appendingPathComponent("env.\(UUID().uuidString)")
        try serialized.write(to: temporary, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
        if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
        }
        try FileManager.default.moveItem(at: temporary, to: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    public static func load(from url: URL, paths: TitanSkiesPaths) throws -> EnvFile {
        if !FileManager.default.fileExists(atPath: url.path) {
            let defaults = EnvFile(values: [
                "CONTEXT_SOURCE": "live",
                "TITANSKIES_BIND_ADDR": TitanSkiesIdentity.bindAddress,
                "TITANSKIES_PORT": String(TitanSkiesIdentity.port),
                "TITANSKIES_DATA_DIR": paths.data.path,
                "TITANSKIES_CACHE_DIR": paths.cache.path,
            ])
            try defaults.writeAtomically(to: url)
            return try defaults.validated(paths: paths)
        }
        let text = try String(contentsOf: url, encoding: .utf8)
        return try EnvFile.parse(text).validated(paths: paths)
    }
}

public enum LogRotation {
    public static func rotate(at url: URL, limitBytes: Int = 2_097_152) throws {
        let manager = FileManager.default
        guard manager.fileExists(atPath: url.path) else { return }
        let size = (try manager.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.intValue ?? 0
        guard size >= limitBytes else { return }
        let rotated = url.appendingPathExtension("1")
        if manager.fileExists(atPath: rotated.path) {
            try manager.removeItem(at: rotated)
        }
        try manager.moveItem(at: url, to: rotated)
    }
}
