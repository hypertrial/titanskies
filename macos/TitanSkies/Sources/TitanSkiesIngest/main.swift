import Foundation
import TitanSkiesCore

@main
enum TitanSkiesIngestMain {
    static func main() {
        do {
            let paths = try TitanSkiesPaths.live()
            try RuntimeLauncher.exec(kind: .ingest, paths: paths)
        } catch {
            FileHandle.standardError.write(Data("TitanSkies ingest launcher failed: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
}
