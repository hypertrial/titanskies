import Foundation
import TitanSkiesCore

@main
enum TitanSkiesWebMain {
    static func main() {
        do {
            let paths = try TitanSkiesPaths.live()
            try RuntimeLauncher.exec(kind: .web, paths: paths)
        } catch {
            FileHandle.standardError.write(Data("TitanSkies web launcher failed: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
}
