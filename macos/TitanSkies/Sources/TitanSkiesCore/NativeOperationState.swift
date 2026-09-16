public struct NativeOperationState: Sendable {
    public private(set) var isActive = false

    public init() {}

    public mutating func begin() -> Bool {
        guard !isActive else { return false }
        isActive = true
        return true
    }

    public mutating func finish() {
        isActive = false
    }
}
