// Reference input field for macOS.
//
// A minimal Cocoa application whose only view implements NSTextInputClient. Every call the input method
// makes on that protocol is written as a content-free `gt/1` line (`src: "mac_nstic"`; the contract is
// specified in ../formats/FORMATS.md): the kind of call, lengths, ranges and times, and nothing else.
//
// The text under composition is kept in memory only, because the input method has to be answered truthfully
// (marked range, selected range, substring queries). It is never written to the log. As soon as no
// composition remains (after a commit or an unmark) the in-memory text and selection are emptied, so every
// trial starts with no left context. Unlike an ordinary text field, this view does not retain committed text.
//
// The log is written by exactly two functions: `record`, whose parameters are an enumeration and integers
// (there is no overload that accepts a string), and `writeHeader`, which takes no parameters. The byte-level
// writer is private to `Sink`, so within the source no other path leads to the log. The recorded key codes
// (`vk`) are a property of the physical typing, and the log of a person typing is to be handled as keystroke
// content; see the README.
//
// Nothing here interprets the calls: they are written in the order received. Assigning meaning to that
// order is left to downstream analysis.
//
//   RefInput --out <path>              open one window and record until it is closed
//   RefInput --selftest [--out <path>] no window; drive the protocol methods directly and read the log back

import AppKit
import Foundation

let appVersion = "0.1.1"

// MARK: - Log vocabulary

enum Ev: String {
    case setMarked = "set_marked"
    case insertText = "insert_text"
    case unmark = "unmark"
    case firstRect = "first_rect"
    case doCommand = "do_command"
    case keyDown = "key_down"
    case focusIn = "focus_in"
    case focusOut = "focus_out"
}

enum Cmd: String {
    case insertNewline = "insertNewline:"
    case cancelOperation = "cancelOperation:"
    case deleteBackward = "deleteBackward:"
    case deleteForward = "deleteForward:"
    case moveLeft = "moveLeft:"
    case moveRight = "moveRight:"
    case moveUp = "moveUp:"
    case moveDown = "moveDown:"
    case insertTab = "insertTab:"
    case insertBacktab = "insertBacktab:"
    case noop = "noop:"
    case other = "other"
}

// MARK: - Sink

// The only owner of the log file descriptor. `write` is private: callers can only use `record`,
// `writeHeader`, `create` and `close`.
enum Sink {
    private static var handle: FileHandle?
    static var writeFailed = false

    // Creates the file exclusively: an existing path (file, directory, or symbolic link) is refused.
    static func create(path: String) -> Bool {
        let fd = Darwin.open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        if fd < 0 { return false }
        handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        writeHeader()
        return !writeFailed
    }

    static func close() {
        try? handle?.close()
        handle = nil
    }

    private static func write(_ bytes: [UInt8]) {
        guard let h = handle else {
            fail()
            return
        }
        do {
            // One write per line, unbuffered, so a record survives if the process is killed later.
            try h.write(contentsOf: Data(bytes))
        } catch {
            fail()
        }
    }

    private static func fail() {
        if !writeFailed {
            writeFailed = true
            // A fixed message: it never contains any content.
            FileHandle.standardError.write(Data("refinput: writing the log failed\n".utf8))
        }
    }

    private static func wallMs() -> Int64 {
        return Int64((Date().timeIntervalSince1970 * 1000).rounded(.down))
    }

    private static func monoNs() -> UInt64 {
        return clock_gettime_nsec_np(CLOCK_UPTIME_RAW)
    }

    static func writeHeader() {
        let line = "{\"kind\":\"header\",\"format\":\"gt/1\",\"src\":\"mac_nstic\",\"app_version\":\"\(appVersion)\",\"os\":\"macos\"}\n"
        write(Array(line.utf8))
    }

    // The recording function. Arguments are an enumeration and integers only; `cmd` is emitted from the
    // enumeration's raw value.
    static func record(ev: Ev, cmd: Cmd? = nil, vk: Int? = nil, len: Int? = nil, selLoc: Int? = nil,
                       selLen: Int? = nil, repLoc: Int? = nil, repLen: Int? = nil) {
        var line = "{\"kind\":\"ev\",\"t_wall_ms\":\(wallMs()),\"t_mono_ns\":\(monoNs()),\"ev\":\"\(ev.rawValue)\""
        if let cmd = cmd { line += ",\"cmd\":\"\(cmd.rawValue)\"" }
        if let vk = vk { line += ",\"vk\":\(vk)" }
        if let len = len { line += ",\"len\":\(len)" }
        if let selLoc = selLoc { line += ",\"sel_loc\":\(selLoc)" }
        if let selLen = selLen { line += ",\"sel_len\":\(selLen)" }
        // NSNotFound means "no replacement range was given"; the field is left out in that case.
        if let repLoc = repLoc, repLoc != NSNotFound, repLoc >= 0 {
            line += ",\"rep_loc\":\(repLoc)"
            if let repLen = repLen { line += ",\"rep_len\":\(repLen)" }
        }
        line += "}\n"
        write(Array(line.utf8))
    }
}

// MARK: - The view

private let noRange = NSRange(location: NSNotFound, length: 0)

final class RefInputView: NSView, NSTextInputClient {
    // The text under composition lives in memory only (UTF-16 units, as the protocol counts them).
    // Invariant: whenever there is no marked text, `text` is empty and `selected` is (0, 0). Whenever there is,
    // `text` consists of exactly the marked text.
    private let text = NSMutableString()
    private var marked = noRange
    private var selected = NSRange(location: 0, length: 0)
    private var focused = false

    override var acceptsFirstResponder: Bool { return true }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let win = window else { return }
        let center = NotificationCenter.default
        center.addObserver(forName: NSWindow.didBecomeKeyNotification, object: win, queue: nil) { [weak self] _ in
            self?.setFocus(true)
        }
        center.addObserver(forName: NSWindow.didResignKeyNotification, object: win, queue: nil) { [weak self] _ in
            self?.setFocus(false)
        }
    }

    // Records a focus event only when the state changes, so that the initial synchronisation and the
    // notification do not both record the same transition.
    func setFocus(_ now: Bool) {
        if now == focused { return }
        focused = now
        Sink.record(ev: now ? .focusIn : .focusOut)
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.white.setFill()
        bounds.fill()
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 24), .foregroundColor: NSColor.black]
        text.draw(at: NSPoint(x: 12, y: bounds.height / 2), withAttributes: attrs)
    }

    override func keyDown(with event: NSEvent) {
        Sink.record(ev: .keyDown, vk: Int(event.keyCode))
        interpretKeyEvents([event])
    }

    // Mouse events go to the input method first (it may end or move a composition); otherwise to the view.
    override func mouseDown(with event: NSEvent) {
        if inputContext?.handleEvent(event) != true { super.mouseDown(with: event) }
    }

    override func mouseDragged(with event: NSEvent) {
        if inputContext?.handleEvent(event) != true { super.mouseDragged(with: event) }
    }

    override func mouseUp(with event: NSEvent) {
        if inputContext?.handleEvent(event) != true { super.mouseUp(with: event) }
    }

    private func clamp(_ range: NSRange, to length: Int) -> NSRange {
        let start = min(max(range.location, 0), length)
        let len = min(max(range.length, 0), length - start)
        return NSRange(location: start, length: len)
    }

    // Range the next edit replaces: the explicit range if given, else the marked range, else the selection.
    private func target(_ replacement: NSRange) -> NSRange {
        var r = replacement
        if r.location == NSNotFound { r = (marked.location != NSNotFound) ? marked : selected }
        return clamp(r, to: text.length)
    }

    // Restores the invariant after an edit: with no composition left, nothing is retained.
    private func settle() {
        if marked.location == NSNotFound || text.length == 0 {
            text.setString("")
            marked = noRange
            selected = NSRange(location: 0, length: 0)
        } else {
            marked = NSRange(location: 0, length: text.length)
            selected = clamp(selected, to: text.length)
        }
        needsDisplay = true
    }

    private func plain(_ value: Any) -> NSString {
        if let a = value as? NSAttributedString { return a.string as NSString }
        if let s = value as? NSString { return s }
        return ""
    }

    // MARK: NSTextInputClient

    func insertText(_ string: Any, replacementRange: NSRange) {
        let s = plain(string)
        Sink.record(ev: .insertText, len: s.length, repLoc: replacementRange.location, repLen: replacementRange.length)
        let r = target(replacementRange)
        text.replaceCharacters(in: r, with: s as String)
        // The committed text is not retained.
        marked = noRange
        settle()
    }

    func setMarkedText(_ string: Any, selectedRange selRange: NSRange, replacementRange: NSRange) {
        let s = plain(string)
        // The raw values are recorded; NSNotFound (Int.max) may arrive and is clamped before any arithmetic.
        Sink.record(ev: .setMarked, len: s.length, selLoc: selRange.location, selLen: selRange.length,
                    repLoc: replacementRange.location, repLen: replacementRange.length)
        let r = target(replacementRange)
        text.replaceCharacters(in: r, with: s as String)
        marked = text.length > 0 ? NSRange(location: 0, length: text.length) : noRange
        // The selection is relative to the new marked string, which starts at r.location.
        let rel = clamp(selRange, to: s.length)
        selected = NSRange(location: r.location + rel.location, length: rel.length)
        settle()
    }

    func unmarkText() {
        Sink.record(ev: .unmark)
        marked = noRange
        settle()
    }

    func selectedRange() -> NSRange { return clamp(selected, to: text.length) }

    func markedRange() -> NSRange { return marked }

    func hasMarkedText() -> Bool { return marked.location != NSNotFound }

    func attributedSubstring(forProposedRange range: NSRange, actualRange: NSRangePointer?) -> NSAttributedString? {
        let r = clamp(range, to: text.length)
        actualRange?.pointee = r
        return NSAttributedString(string: text.substring(with: r))
    }

    func validAttributesForMarkedText() -> [NSAttributedString.Key] {
        return [.markedClauseSegment, .underlineStyle, .underlineColor]
    }

    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer?) -> NSRect {
        Sink.record(ev: .firstRect)
        actualRange?.pointee = clamp(range, to: text.length)
        guard let win = window else { return .zero }
        let caret = NSRect(x: 12, y: bounds.height / 2 - 4, width: 1, height: 32)
        return win.convertToScreen(convert(caret, to: nil))
    }

    // The position of a character is not computed: no character index is reported.
    func characterIndex(for point: NSPoint) -> Int { return NSNotFound }

    override func doCommand(by selector: Selector) {
        let cmd = Cmd(rawValue: NSStringFromSelector(selector)) ?? .other
        Sink.record(ev: .doCommand, cmd: cmd)
        // Editing commands act on the (normally empty) text when no composition is active.
        if marked.location != NSNotFound { return }
        let sel = clamp(selected, to: text.length)
        switch cmd {
        case .deleteBackward:
            let r = sel.length > 0 ? sel : NSRange(location: max(sel.location - 1, 0), length: sel.location > 0 ? 1 : 0)
            text.replaceCharacters(in: r, with: "")
            selected = NSRange(location: r.location, length: 0)
        case .deleteForward:
            let r = sel.length > 0 ? sel : NSRange(location: sel.location, length: min(1, text.length - sel.location))
            text.replaceCharacters(in: r, with: "")
            selected = NSRange(location: r.location, length: 0)
        case .moveLeft:
            selected = NSRange(location: sel.length > 0 ? sel.location : max(sel.location - 1, 0), length: 0)
        case .moveRight:
            let end = sel.location + sel.length
            selected = NSRange(location: sel.length > 0 ? end : min(end + 1, text.length), length: 0)
        default:
            break
        }
        needsDisplay = true
    }
}

// MARK: - Self-test

func selftestFailure(_ what: String) -> Int32 {
    FileHandle.standardError.write(Data("selftest failed: \(what)\n".utf8))
    return 1
}

// Compares the protocol's answers with the expected state. Only booleans and lengths are compared.
func checkState(_ view: RefInputView, _ step: String, marked: NSRange?, selected: NSRange, textLen: Int) -> String? {
    if view.hasMarkedText() != (marked != nil) { return "\(step): hasMarkedText" }
    if let m = marked {
        if view.markedRange() != m { return "\(step): markedRange \(view.markedRange())" }
    } else if view.markedRange().location != NSNotFound {
        return "\(step): markedRange must be NSNotFound"
    }
    if view.selectedRange() != selected { return "\(step): selectedRange \(view.selectedRange())" }
    var actual = NSRange(location: 0, length: 0)
    let got = view.attributedSubstring(forProposedRange: NSRange(location: 0, length: textLen), actualRange: &actual)
    if got?.length != textLen || actual != NSRange(location: 0, length: textLen) { return "\(step): substring length" }
    // A request past the end is clamped to the same range.
    let wide = view.attributedSubstring(forProposedRange: NSRange(location: 0, length: textLen + 5), actualRange: nil)
    if wide?.length != textLen { return "\(step): clamped substring length" }
    return nil
}

func runSelftest(outPath: String?) -> Int32 {
    let path = outPath ?? (NSTemporaryDirectory() + "refinput-selftest-\(getpid()).jsonl")
    let removeAfter = (outPath == nil)
    // Only a file this function created itself is removed; an explicit --out is never removed or replaced.
    guard Sink.create(path: path) else { return selftestFailure("cannot create the output (it may already exist)") }
    defer { if removeAfter { try? FileManager.default.removeItem(atPath: path) } }

    _ = NSApplication.shared
    let view = RefInputView(frame: NSRect(x: 0, y: 0, width: 400, height: 100))
    let none = NSRange(location: NSNotFound, length: 0)
    let empty = NSRange(location: 0, length: 0)
    var problem: String?
    func expect(_ step: String, marked: NSRange?, selected: NSRange, textLen: Int) {
        if problem == nil { problem = checkState(view, step, marked: marked, selected: selected, textLen: textLen) }
    }

    // Strings are built from code points so that the read-back check below has a fixed thing to look for.
    let reading = String(String.UnicodeScalarView([0x306B, 0x307B, 0x3093, 0x3054].compactMap(Unicode.Scalar.init)))
    let kanji = String(String.UnicodeScalarView([0x65E5, 0x672C, 0x8A9E].compactMap(Unicode.Scalar.init)))
    let astral = String(String.UnicodeScalarView([0x20BB7, 0x61].compactMap(Unicode.Scalar.init)))

    view.setFocus(true)
    view.setFocus(true)  // a repeated state records nothing
    view.setMarkedText(reading, selectedRange: NSRange(location: 4, length: 0), replacementRange: none)
    expect("after set_marked 1", marked: NSRange(location: 0, length: 4), selected: NSRange(location: 4, length: 0), textLen: 4)
    view.insertText(kanji, replacementRange: NSRange(location: 0, length: 4))
    expect("after insert_text 1", marked: nil, selected: empty, textLen: 0)
    view.setMarkedText("", selectedRange: NSRange(location: 0, length: 0), replacementRange: none)
    expect("after empty set_marked", marked: nil, selected: empty, textLen: 0)
    view.setMarkedText(astral, selectedRange: NSRange(location: 3, length: 0), replacementRange: NSRange(location: 3, length: 0))
    expect("after set_marked 2", marked: NSRange(location: 0, length: 3), selected: NSRange(location: 3, length: 0), textLen: 3)
    view.setMarkedText(NSAttributedString(string: "\(kanji)"), selectedRange: NSRange(location: 0, length: 3), replacementRange: none)
    expect("after set_marked 3", marked: NSRange(location: 0, length: 3), selected: NSRange(location: 0, length: 3), textLen: 3)
    var actual = NSRange(location: 0, length: 0)
    _ = view.firstRect(forCharacterRange: NSRange(location: 1, length: 99), actualRange: &actual)
    if actual != NSRange(location: 1, length: 2) { return selftestFailure("firstRect actualRange \(actual)") }
    view.unmarkText()
    expect("after unmark", marked: nil, selected: empty, textLen: 0)
    // NSNotFound and Int.max in the ranges must not trap, and the state must stay inside the text.
    view.setMarkedText(reading, selectedRange: NSRange(location: Int.max, length: Int.max), replacementRange: none)
    expect("after set_marked 4", marked: NSRange(location: 0, length: 4), selected: NSRange(location: 4, length: 0), textLen: 4)
    // A replacement range that covers only part of the marked text.
    view.setMarkedText(kanji, selectedRange: NSRange(location: 1, length: 1), replacementRange: NSRange(location: 2, length: 5))
    expect("after set_marked 5", marked: NSRange(location: 0, length: 5), selected: NSRange(location: 3, length: 1), textLen: 5)
    // A selection location of NSNotFound with a non-zero replacement start must not overflow.
    view.setMarkedText(kanji, selectedRange: NSRange(location: Int.max, length: 1), replacementRange: NSRange(location: 2, length: 5))
    expect("after set_marked 5b", marked: NSRange(location: 0, length: 5), selected: NSRange(location: 5, length: 0), textLen: 5)
    // A commit that replaces a range covering only part of the marked text leaves nothing behind.
    view.insertText(astral, replacementRange: NSRange(location: 1, length: 1))
    expect("after insert_text 2", marked: nil, selected: empty, textLen: 0)
    view.setMarkedText(astral, selectedRange: none, replacementRange: NSRange(location: Int.max, length: Int.max))
    expect("after set_marked 6", marked: NSRange(location: 0, length: 3), selected: NSRange(location: 3, length: 0), textLen: 3)
    view.insertText(kanji, replacementRange: none)
    expect("after insert_text 3", marked: nil, selected: empty, textLen: 0)
    if view.characterIndex(for: NSPoint(x: 1, y: 1)) != NSNotFound { return selftestFailure("characterIndex") }
    if view.validAttributesForMarkedText().isEmpty { return selftestFailure("validAttributesForMarkedText") }
    view.doCommand(by: #selector(NSResponder.cancelOperation(_:)))
    view.doCommand(by: #selector(NSResponder.deleteBackward(_:)))
    view.doCommand(by: #selector(NSResponder.moveLeft(_:)))
    view.doCommand(by: #selector(NSResponder.moveRight(_:)))
    expect("after commands", marked: nil, selected: empty, textLen: 0)
    view.doCommand(by: #selector(NSResponder.selectAll(_:)))
    Sink.record(ev: .keyDown, vk: 36)
    view.setFocus(false)
    view.setFocus(false)
    if let p = problem { return selftestFailure(p) }
    if Sink.writeFailed { return selftestFailure("a write failed") }
    Sink.close()

    guard let data = FileManager.default.contents(atPath: path) else { return selftestFailure("cannot read the output back") }
    // (b) Nothing that was passed in may appear: the whole output must be plain ASCII.
    if data.contains(where: { $0 >= 0x80 }) { return selftestFailure("non-ASCII bytes in the output") }
    guard let body = String(data: data, encoding: .utf8) else { return selftestFailure("output is not UTF-8") }
    if body.contains(reading) || body.contains(kanji) || body.contains(astral) {
        return selftestFailure("a passed string appears in the output")
    }

    var rows: [[String: Any]] = []
    for line in body.split(separator: "\n") {
        guard let obj = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any] else {
            return selftestFailure("a line is not a JSON object")
        }
        rows.append(obj)
    }
    guard let header = rows.first, header["kind"] as? String == "header", header["src"] as? String == "mac_nstic",
          header["os"] as? String == "macos" else { return selftestFailure("bad header") }
    let events = Array(rows.dropFirst())

    // (a) The recorded lengths equal the UTF-16 lengths of what was passed (see expectedLens).
    // The supplementary-plane string has two characters but three UTF-16 units; the units are what is counted.
    if astral.count != 2 { return selftestFailure("test string is not a supplementary-plane one") }
    let expectedLens = [4, 3, 0, 3, 3, 4, 3, 3, 3, 3, 3]
    let lens = events.filter { $0["ev"] as? String == "set_marked" || $0["ev"] as? String == "insert_text" }
        .compactMap { $0["len"] as? Int }
    if lens != expectedLens { return selftestFailure("lengths \(lens) differ from \(expectedLens)") }
    let evNames = events.compactMap { $0["ev"] as? String }
    let expectedEv = ["focus_in", "set_marked", "insert_text", "set_marked", "set_marked", "set_marked", "first_rect",
                      "unmark", "set_marked", "set_marked", "set_marked", "insert_text", "set_marked", "insert_text", "do_command",
                      "do_command", "do_command", "do_command", "do_command", "key_down", "focus_out"]
    if evNames != expectedEv { return selftestFailure("event order \(evNames)") }
    // An absent replacement range leaves rep_* out; a given one records it.
    let firstSet = events[1]
    if firstSet["rep_loc"] != nil || firstSet["rep_len"] != nil || firstSet["sel_loc"] as? Int != 4 {
        return selftestFailure("replacement range handling")
    }
    let insertRow = events[2]
    if insertRow["rep_loc"] as? Int != 0 || insertRow["rep_len"] as? Int != 4 {
        return selftestFailure("replacement range was not recorded")
    }
    // (c) A selector outside the enumeration is recorded as `other`.
    let cmds = events.filter { $0["ev"] as? String == "do_command" }.compactMap { $0["cmd"] as? String }
    if cmds != ["cancelOperation:", "deleteBackward:", "moveLeft:", "moveRight:", "other"] {
        return selftestFailure("commands \(cmds)")
    }
    return 0
}

// MARK: - Application

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { return true }

    func applicationWillTerminate(_ notification: Notification) {
        Sink.close()
        if Sink.writeFailed { exit(1) }
    }
}

var delegate: AppDelegate?

func runApp() -> Int32 {
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    let d = AppDelegate()
    delegate = d
    app.delegate = d
    let view = RefInputView(frame: NSRect(x: 0, y: 0, width: 640, height: 200))
    let win = NSWindow(contentRect: view.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
    win.title = "Reference input field"
    win.contentView = view
    win.center()
    win.makeKeyAndOrderFront(nil)
    win.makeFirstResponder(view)
    app.activate(ignoringOtherApps: true)
    // The window may already be key before the notification arrives; a repeated state is not recorded twice.
    view.setFocus(win.isKeyWindow)
    app.run()
    return Sink.writeFailed ? 1 : 0
}

// MARK: - Entry

func usage() -> Int32 {
    FileHandle.standardError.write(Data("usage: refinput-macos --out <path> | --selftest [--out <path>]\n".utf8))
    return 2
}

func main() -> Int32 {
    var outPath: String?
    var selftest = false
    var i = 1
    let args = CommandLine.arguments
    while i < args.count {
        switch args[i] {
        case "--selftest":
            selftest = true
        case "--out":
            i += 1
            if i >= args.count { return usage() }
            outPath = args[i]
        default:
            return usage()
        }
        i += 1
    }
    if selftest { return runSelftest(outPath: outPath) }
    guard let path = outPath else { return usage() }
    // A recording run never replaces an existing path, so that a raw log cannot be destroyed.
    guard Sink.create(path: path) else {
        FileHandle.standardError.write(Data("cannot create the output (it may already exist)\n".utf8))
        return 1
    }
    return runApp()
}

exit(main())
