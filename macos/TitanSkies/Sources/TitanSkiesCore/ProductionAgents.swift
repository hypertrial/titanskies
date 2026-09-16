import Foundation
import ServiceManagement

public enum ProductionAgentRegistration {
    private static let services = [
        (TitanSkiesIdentity.productionWebLabel, "com.hypertrial.titanskies.web.plist"),
        (TitanSkiesIdentity.productionIngestLabel, "com.hypertrial.titanskies.ingest.plist"),
    ]

    public static func register() throws -> Bool {
        var requiresApproval = false
        for (_, plist) in services {
            let service = SMAppService.agent(plistName: plist)
            switch service.status {
            case .enabled:
                continue
            case .requiresApproval:
                requiresApproval = true
            case .notRegistered:
                do {
                    try service.register()
                } catch {
                    guard service.status == .requiresApproval else { throw error }
                }
                requiresApproval = requiresApproval || service.status == .requiresApproval
            case .notFound:
                throw TitanSkiesError.missingBundleResource(plist)
            @unknown default:
                throw TitanSkiesError.launchd("unknown SMAppService status for \(plist)")
            }
        }
        return requiresApproval
    }

    public static func unregister(timeout: TimeInterval = 10) throws {
        for (_, plist) in services {
            let service = SMAppService.agent(plistName: plist)
            if service.status == .enabled || service.status == .requiresApproval {
                try service.unregister()
            }
        }
        let launchd = LaunchdClient()
        let deadline = Date().addingTimeInterval(timeout)
        while services.contains(where: { launchd.isLoaded($0.0) }) && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if services.contains(where: { launchd.isLoaded($0.0) }) {
            throw TitanSkiesError.launchd("production agents did not stop after unregister")
        }
    }

    public static func reregister() throws -> Bool {
        try unregister()
        return try register()
    }
}
