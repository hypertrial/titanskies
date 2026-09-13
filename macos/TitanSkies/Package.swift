// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TitanSkies",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .library(name: "TitanSkiesCore", targets: ["TitanSkiesCore"]),
        .executable(name: "TitanSkies", targets: ["TitanSkies"]),
        .executable(name: "TitanSkiesWeb", targets: ["TitanSkiesWeb"]),
        .executable(name: "TitanSkiesIngest", targets: ["TitanSkiesIngest"]),
        .executable(name: "TitanSkiesUpdater", targets: ["TitanSkiesUpdater"]),
        .executable(name: "TitanSkiesCoreCheck", targets: ["TitanSkiesCoreCheck"]),
    ],
    targets: [
        .target(
            name: "TitanSkiesCore"
        ),
        .executableTarget(
            name: "TitanSkies",
            dependencies: ["TitanSkiesCore"]
        ),
        .executableTarget(
            name: "TitanSkiesWeb",
            dependencies: ["TitanSkiesCore"]
        ),
        .executableTarget(
            name: "TitanSkiesIngest",
            dependencies: ["TitanSkiesCore"]
        ),
        .executableTarget(
            name: "TitanSkiesUpdater",
            dependencies: ["TitanSkiesCore"]
        ),
        .executableTarget(
            name: "TitanSkiesCoreCheck",
            dependencies: ["TitanSkiesCore"]
        ),
    ]
)
