import SwiftUI
import TitanSkiesCore
import WebKit

struct BrowserView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NativeWebView(port: TitanSkiesIdentity.port)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct NativeWebView: NSViewRepresentable {
    let port: Int

    func makeCoordinator() -> Coordinator {
        Coordinator(port: port)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.setValue(false, forKey: "developerExtrasEnabled")
        configuration.preferences.setValue(true, forKey: "WebKitWebGLEnabled")
        configuration.preferences.setValue(true, forKey: "WebKitWebGL2Enabled")
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        if #available(macOS 13.3, *) {
            view.isInspectable = false
        }
        view.customUserAgent = NavigationPolicy.nativeUserAgent(version: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0")
        view.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
        return view
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        let port: Int

        init(port: Int) {
            self.port = port
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            if navigationAction.shouldPerformDownload {
                decisionHandler(.cancel)
                return
            }
            switch NavigationPolicy.decide(url: url, port: port) {
            case .allow:
                decisionHandler(.allow)
            case .openInBrowser:
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
            case .reject:
                decisionHandler(.cancel)
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationResponse: WKNavigationResponse,
            decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
        ) {
            if navigationResponse.canShowMIMEType {
                decisionHandler(.allow)
            } else {
                decisionHandler(.cancel)
            }
        }
    }
}
