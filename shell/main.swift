// Woodshed.app shell: a window around the player UI, which is served by the bundled Python
// server running as a child process on a private loopback port.
//
//   woodshed://grab              grab whatever Spotify is playing
//   woodshed://grab?url=<url>    grab a YouTube URL
//   ⌃⌥⌘W (global)                bring Woodshed forward and grab from Spotify

import Carbon.HIToolbox
import Cocoa
import WebKit

let serverStartTimeout: TimeInterval = 30

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var port: UInt16 = 0
    private var pageIsReady = false
    private var pendingGrab: String??   // outer nil: nothing pending; inner nil: grab from Spotify
    private var titleObservation: NSKeyValueObservation?
    private var hotKey: EventHotKeyRef?

    func applicationWillFinishLaunching(_ notification: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleURLEvent(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        registerGlobalHotKey()
        startServer()
    }

    func applicationWillTerminate(_ notification: Notification) { server?.terminate() }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // MARK: server

    private func startServer() {
        guard let serverURL = Bundle.main.resourceURL?.appendingPathComponent("server/woodshed-server"),
              FileManager.default.isExecutableFile(atPath: serverURL.path) else {
            return fail("The bundled server is missing from the app.")
        }
        guard let freePort = Self.findFreePort() else { return fail("Couldn't find a free local port.") }
        port = freePort

        let process = Process()
        process.executableURL = serverURL
        process.arguments = ["--serve", String(port), "--exit-with-parent"]
        let logURL = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0].appendingPathComponent("Logs/Woodshed.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let log = try? FileHandle(forWritingTo: logURL) {
            process.standardOutput = log
            process.standardError = log
        }
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                guard let self, self.server === finished, NSApp.isRunning else { return }
                self.fail("The Woodshed server stopped unexpectedly (exit \(finished.terminationStatus)). See ~/Library/Logs/Woodshed.log.")
            }
        }
        do { try process.run() } catch { return fail("Couldn't start the server: \(error.localizedDescription)") }
        server = process
        waitForServer(deadline: Date().addingTimeInterval(serverStartTimeout))
    }

    private func waitForServer(deadline: Date) {
        let probe = URLSession.shared.dataTask(with: URL(string: "http://127.0.0.1:\(port)/api/songs")!) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    self.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.port)/")!))
                } else if Date() < deadline {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { self.waitForServer(deadline: deadline) }
                } else {
                    self.fail("The Woodshed server didn't start in time. See ~/Library/Logs/Woodshed.log.")
                }
            }
        }
        probe.resume()
    }

    private static func findFreePort() -> UInt16? {
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { return nil }
        defer { close(descriptor) }
        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        address.sin_port = 0
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let bound = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(descriptor, $0, length) == 0 && getsockname(descriptor, $0, &length) == 0 }
        }
        return bound ? UInt16(bigEndian: address.sin_port) : nil
    }

    private func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Woodshed can't continue"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }

    // MARK: window

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")  // no white flash before the dark UI paints
        if #available(macOS 13.3, *) { webView.isInspectable = true }

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 760),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Woodshed"
        window.backgroundColor = NSColor(red: 0.082, green: 0.090, blue: 0.102, alpha: 1)
        window.minSize = NSSize(width: 720, height: 420)
        window.contentView = webView
        window.setFrameAutosaveName("WoodshedMainWindow")
        if !window.setFrameUsingName("WoodshedMainWindow") { window.center() }
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(webView)
        titleObservation = webView.observe(\.title) { [weak self] view, _ in
            self?.window.title = view.title.flatMap { $0.isEmpty ? nil : $0 } ?? "Woodshed"
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        pageIsReady = true
        if let grab = pendingGrab { pendingGrab = nil; runGrab(url: grab) }
    }

    // Links out of the player (there are none today) belong in the browser, not in this window.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, url.host != "127.0.0.1" {
            NSWorkspace.shared.open(url)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    // MARK: grabbing

    private func runGrab(url: String?) {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        guard pageIsReady else { pendingGrab = .some(url); return }
        // The URL travels as a JSON string literal so nothing in it can be read as script.
        let argument = url.flatMap { try? JSONEncoder().encode($0) }.flatMap { String(data: $0, encoding: .utf8) } ?? "null"
        webView.evaluateJavaScript("window.woodshed.grab(\(argument))", completionHandler: nil)
    }

    @objc private func handleURLEvent(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        guard let text = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
              let components = URLComponents(string: text), components.host == "grab" else { return }
        runGrab(url: components.queryItems?.first { $0.name == "url" }?.value)
    }

    @objc private func grabFromSpotify(_ sender: Any?) { runGrab(url: nil) }
    @objc private func showSongs(_ sender: Any?) { webView.evaluateJavaScript("window.woodshed.openSongList()", completionHandler: nil) }
    @objc private func reloadPage(_ sender: Any?) { webView.reload() }

    private func registerGlobalHotKey() {
        var handler = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            let delegate = Unmanaged<AppDelegate>.fromOpaque(userData!).takeUnretainedValue()
            DispatchQueue.main.async { delegate.runGrab(url: nil) }
            return noErr
        }, 1, &handler, Unmanaged.passUnretained(self).toOpaque(), nil)
        let identifier = EventHotKeyID(signature: OSType(0x57445348) /* "WDSH" */, id: 1)
        RegisterEventHotKey(UInt32(kVK_ANSI_W), UInt32(controlKey | optionKey | cmdKey), identifier, GetApplicationEventTarget(), 0, &hotKey)
    }

    // MARK: menu

    private func buildMenu() {
        let main = NSMenu()

        let app = NSMenu()
        app.addItem(withTitle: "About Woodshed", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Hide Woodshed", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(withTitle: "Quit Woodshed", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let song = NSMenu(title: "Song")
        song.addItem(withTitle: "Grab from Spotify  (⌃⌥⌘W anywhere)", action: #selector(grabFromSpotify(_:)), keyEquivalent: "g")
        song.addItem(withTitle: "Songs…", action: #selector(showSongs(_:)), keyEquivalent: "o")
        song.addItem(.separator())
        song.addItem(withTitle: "Reload", action: #selector(reloadPage(_:)), keyEquivalent: "r")

        // No Undo/Redo items on purpose: a menu key equivalent would swallow ⌘Z before the
        // player's own undo (labels, loops) ever saw it.
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        for menu in [app, song, edit, windowMenu] {
            let item = NSMenuItem()
            item.submenu = menu
            main.addItem(item)
        }
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu
    }
}

let delegate = AppDelegate()
NSApplication.shared.delegate = delegate
NSApplication.shared.setActivationPolicy(.regular)
NSApplication.shared.run()
