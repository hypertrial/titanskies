import Foundation

public struct HealthSnapshot: Equatable, Sendable {
    public var serviceIdentity: Bool
    public var initializing: Bool
    public var httpStatus: Int

    public init(serviceIdentity: Bool, initializing: Bool, httpStatus: Int) {
        self.serviceIdentity = serviceIdentity
        self.initializing = initializing
        self.httpStatus = httpStatus
    }
}

public protocol HealthChecking: Sendable {
    func fetch(url: URL) throws -> (Int, Data)
}

public struct URLHealthClient: HealthChecking {
    public init() {}

    public func fetch(url: URL) throws -> (Int, Data) {
        var capturedStatus = 0
        var capturedData = Data()
        var capturedError: Error?
        let semaphore = DispatchSemaphore(value: 0)
        let task = URLSession.shared.dataTask(with: url) { data, response, error in
            if let error {
                capturedError = error
            } else {
                capturedStatus = (response as? HTTPURLResponse)?.statusCode ?? 0
                capturedData = data ?? Data()
            }
            semaphore.signal()
        }
        task.resume()
        semaphore.wait()
        if let capturedError {
            throw capturedError
        }
        return (capturedStatus, capturedData)
    }
}

public struct ServiceHealth: Sendable {
    public let client: any HealthChecking
    public let port: Int

    public init(client: any HealthChecking = URLHealthClient(), port: Int = TitanSkiesIdentity.port) {
        self.client = client
        self.port = port
    }

    public func healthz() throws -> HealthSnapshot {
        let url = URL(string: "http://127.0.0.1:\(port)/api/healthz")!
        let (status, data) = try client.fetch(url: url)
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let identity = object?["service"] as? String == "titanskies"
        if status == 200 && !identity {
            throw TitanSkiesError.foreignService
        }
        return HealthSnapshot(serviceIdentity: identity, initializing: false, httpStatus: status)
    }

    public func contextHealth() throws -> HealthSnapshot {
        let url = URL(string: "http://127.0.0.1:\(port)/api/context-health?expectedVersion=8")!
        let (status, data) = try client.fetch(url: url)
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let initializing = (object?["state"] as? String) == "initializing" || status == 503
        return HealthSnapshot(serviceIdentity: true, initializing: initializing, httpStatus: status)
    }

    public func waitForWeb(timeout: TimeInterval = 45) throws -> HealthSnapshot {
        let deadline = Date().addingTimeInterval(timeout)
        var last: Error = TitanSkiesError.healthCheckFailed("web did not become ready")
        while Date() < deadline {
            do {
                let snapshot = try healthz()
                if snapshot.serviceIdentity {
                    return snapshot
                }
            } catch {
                last = error
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
        throw last
    }
}

public enum PortProbe {
    public static func occupier(port: Int = TitanSkiesIdentity.port, runner: any ProcessRunning = FoundationProcessRunner()) -> String? {
        let result = try? runner.run("/usr/sbin/lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN"], environment: nil)
        let output = (result?.stdout ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return output.isEmpty ? nil : output
    }
}
