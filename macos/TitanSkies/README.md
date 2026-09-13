# TitanSkies macOS app

Swift package for the Apple Silicon application, in-bundle launchers, and updater.

```sh
swift build -c release --arch arm64
swift run --arch arm64 TitanSkiesCoreCheck
```

Assemble a `.app` with `../../scripts/build-macos-app` after `scripts/build-macos-runtime`. Open this `Package.swift` in Xcode 15+ (macOS 13 deployment target, arm64 only).
