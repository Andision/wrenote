// wrenote screen/window capture helper (macOS, ScreenCaptureKit).
//
// Two modes:
//   screencap --list
//       Enumerate capturable displays + windows as JSON on stdout, then exit:
//         {"displays":[{"id":N,"title":..,"width":W,"height":H}],
//          "windows":[{"id":N,"title":..,"app":..,"width":W,"height":H}]}
//   screencap --window <id> --out <file.mp4>     (or --display <id>)
//       Capture that target's VIDEO (no audio) to an H.264 MP4 until stdin EOF
//       (parent died / asked us to stop), then finalize the file and exit.
//
// Audio is captured separately by the existing pipeline and muxed in by Python,
// mirroring the ffmpeg full-screen path — this helper owns video only.
// Requires the Screen Recording permission (TCC), prompted on first capture.
//
// Build: swiftc -O -o screencap screencap.swift \
//   -framework ScreenCaptureKit -framework AVFoundation -framework CoreMedia -framework AppKit

import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

func log(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

func fail(_ s: String, _ code: Int32) -> Never { log("screencap: " + s); exit(code) }

// ---------- arg parsing ----------

func argValue(_ name: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: name), i + 1 < a.count else { return nil }
    return a[i + 1]
}

let wantList = CommandLine.arguments.contains("--list")
let windowArg = argValue("--window")
let displayArg = argValue("--display")
let outArg = argValue("--out")

// ---------- JSON helpers (no external deps) ----------

func jsonString(_ s: String) -> String {
    var out = "\""
    for ch in s.unicodeScalars {
        switch ch {
        case "\"": out += "\\\""
        case "\\": out += "\\\\"
        case "\n": out += "\\n"
        case "\r": out += "\\r"
        case "\t": out += "\\t"
        default:
            if ch.value < 0x20 { out += String(format: "\\u%04x", ch.value) } else { out.unicodeScalars.append(ch) }
        }
    }
    return out + "\""
}

// ---------- list mode ----------

@available(macOS 12.3, *)
func runList() async {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: true)
        var displays: [String] = []
        for (idx, d) in content.displays.enumerated() {
            let title = "Display \(idx + 1)"
            displays.append(
                "{\"id\":\(d.displayID),\"title\":\(jsonString(title)),"
                    + "\"width\":\(Int(d.width)),\"height\":\(Int(d.height))}")
        }
        var windows: [String] = []
        for w in content.windows {
            guard w.isOnScreen, let title = w.title, !title.isEmpty else { continue }
            let app = w.owningApplication?.applicationName ?? ""
            // Skip our own helper / tiny chrome windows.
            if Int(w.frame.width) < 80 || Int(w.frame.height) < 80 { continue }
            windows.append(
                "{\"id\":\(w.windowID),\"title\":\(jsonString(title)),"
                    + "\"app\":\(jsonString(app)),"
                    + "\"width\":\(Int(w.frame.width)),\"height\":\(Int(w.frame.height))}")
        }
        let out = "{\"displays\":[\(displays.joined(separator: ","))],"
            + "\"windows\":[\(windows.joined(separator: ","))]}"
        print(out)
        exit(0)
    } catch {
        fail("list failed: \(error)", 2)
    }
}

// ---------- capture mode ----------

@available(macOS 12.3, *)
final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    // @unchecked Sendable: all mutable state below is guarded by `lock`.
    private let outURL: URL
    private var stream: SCStream?
    private var writer: AVAssetWriter?
    private var videoInput: AVAssetWriterInput?
    private var started = false
    private let lock = NSLock()

    init(outURL: URL) { self.outURL = outURL }

    func start(filter: SCContentFilter, width: Int, height: Int) async throws {
        let cfg = SCStreamConfiguration()
        cfg.width = max(2, width)
        cfg.height = max(2, height)
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 25)  // ~25 fps
        cfg.pixelFormat = kCVPixelFormatType_32BGRA
        cfg.queueDepth = 6
        cfg.showsCursor = true

        let writer = try AVAssetWriter(url: outURL, fileType: .mp4)
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: cfg.width,
            AVVideoHeightKey: cfg.height,
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        guard writer.canAdd(input) else { throw NSError(domain: "screencap", code: 10) }
        writer.add(input)
        self.writer = writer
        self.videoInput = input

        let stream = SCStream(filter: filter, configuration: cfg, delegate: self)
        try stream.addStreamOutput(
            self, type: .screen, sampleHandlerQueue: DispatchQueue(label: "wrenote.screencap"))
        try await stream.startCapture()
        self.stream = stream
        log("screencap: started (\(cfg.width)x\(cfg.height) -> \(outURL.path))")
    }

    func stream(
        _ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .screen, sampleBuffer.isValid, sampleBuffer.numSamples > 0 else { return }
        // Only append "complete" frames (skip idle/blank status frames).
        if let arr = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false)
            as? [[SCStreamFrameInfo: Any]],
            let raw = arr.first?[.status] as? Int,
            let status = SCFrameStatus(rawValue: raw), status != .complete
        {
            return
        }
        lock.lock(); defer { lock.unlock() }
        guard let writer = writer, let input = videoInput else { return }
        if !started {
            writer.startWriting()
            writer.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
            started = true
        }
        if writer.status == .writing, input.isReadyForMoreMediaData {
            input.append(sampleBuffer)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        log("screencap: stream stopped with error \(error)")
        finish(exitCode: 3)
    }

    func finish(exitCode: Int32) {
        lock.lock()
        let stream = self.stream
        self.stream = nil
        lock.unlock()
        let group = DispatchGroup()
        if let stream = stream {
            group.enter()
            stream.stopCapture { _ in group.leave() }
        }
        group.wait()
        lock.lock()
        let writer = self.writer
        let input = self.videoInput
        self.writer = nil
        self.videoInput = nil
        lock.unlock()
        if let writer = writer, writer.status == .writing {
            input?.markAsFinished()
            let group2 = DispatchGroup()
            group2.enter()
            writer.finishWriting { group2.leave() }
            group2.wait()
            log("screencap: finalized \(outURL.path)")
        }
        exit(exitCode)
    }
}

@available(macOS 12.3, *)
func runCapture() async {
    guard let outPath = outArg else { fail("--out required for capture", 64) }
    let outURL = URL(fileURLWithPath: outPath)
    try? FileManager.default.removeItem(at: outURL)

    let content: SCShareableContent
    do {
        content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: true)
    } catch {
        fail("could not query shareable content (Screen Recording permission?): \(error)", 5)
    }

    let filter: SCContentFilter
    var width = 0
    var height = 0
    if let wid = windowArg.flatMap({ UInt32($0) }) {
        guard let win = content.windows.first(where: { $0.windowID == wid }) else {
            fail("window \(wid) not found", 6)
        }
        filter = SCContentFilter(desktopIndependentWindow: win)
        width = Int(win.frame.width)
        height = Int(win.frame.height)
    } else if let did = displayArg.flatMap({ UInt32($0) }) {
        guard let disp = content.displays.first(where: { $0.displayID == did }) else {
            fail("display \(did) not found", 6)
        }
        filter = SCContentFilter(display: disp, excludingWindows: [])
        width = Int(disp.width)
        height = Int(disp.height)
    } else {
        fail("one of --window <id> / --display <id> required", 64)
    }

    let rec = Recorder(outURL: outURL)
    do {
        try await rec.start(filter: filter, width: width, height: height)
    } catch {
        fail("capture start failed: \(error)", 7)
    }

    // Stop cleanly when the parent closes our stdin (died / asked us to stop).
    DispatchQueue.global().async {
        let stdin = FileHandle.standardInput
        while !stdin.availableData.isEmpty {}  // block until EOF
        log("screencap: stdin closed, finalizing")
        rec.finish(exitCode: 0)
    }
}

// ---------- entry ----------

guard #available(macOS 12.3, *) else { fail("requires macOS 12.3+", 1) }

Task {
    if wantList {
        await runList()
    } else {
        await runCapture()
    }
}

RunLoop.main.run()
