// wrenote system-audio capture helper (macOS, ScreenCaptureKit).
//
// Captures system audio output and writes raw PCM to stdout for the Python
// backend to mix into the transcription pipeline. We exclude our own process's
// audio so playback inside Wrenote isn't re-captured. Requires the Screen
// Recording permission (TCC), prompted on first run.
//
// Usage:
//   syscap                       everything the machine is playing
//   syscap --app <bundle-id>     only that app (repeatable)
//
// `--app` is "record the meeting, not the video you have open in the other
// window": ScreenCaptureKit filters audio by application, so the filter is
// built with `including:` instead of the whole display. Matching is by bundle
// identifier and includes *every* running process of that bundle — Zoom,
// Chrome and Slack all emit audio from helper processes, and filtering to the
// one process that owns the window would silently capture nothing.
//
// Output: signed 16-bit little-endian PCM, mono, 16 kHz, on stdout.
// Diagnostics go to stderr. Exit on EOF of stdin or SIGTERM.
//
// Build: swiftc -O -o syscap syscap.swift -framework ScreenCaptureKit -framework AVFoundation -framework CoreMedia

import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

let SAMPLE_RATE = 16000
let CHANNELS = 1

func log(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

@available(macOS 13.0, *)
final class SysCap: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private let out = FileHandle.standardOutput

    /// Bundle identifiers to capture, or empty for the whole output.
    private let apps: [String]

    init(apps: [String]) {
        self.apps = apps
        super.init()
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            log("syscap: no display available"); exit(2)
        }

        let filter: SCContentFilter
        if apps.isEmpty {
            filter = SCContentFilter(
                display: display, excludingApplications: [], exceptingWindows: [])
        } else {
            let wanted = Set(apps)
            let matched = content.applications.filter {
                wanted.contains($0.bundleIdentifier)
            }
            if matched.isEmpty {
                // Not an error to fail on: the app may not be running yet, or
                // may have quit. Capturing nothing is the honest answer, and
                // the caller sees silence rather than the whole desktop.
                log("syscap: no running application matches \(apps.joined(separator: ", "))")
            } else {
                log(
                    "syscap: capturing \(matched.count) process(es) of "
                        + apps.joined(separator: ", "))
            }
            filter = SCContentFilter(
                display: display, including: matched, exceptingWindows: [])
        }

        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.sampleRate = SAMPLE_RATE
        cfg.channelCount = CHANNELS
        cfg.excludesCurrentProcessAudio = true
        // We only want audio; keep the video path as cheap as possible.
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let stream = SCStream(filter: filter, configuration: cfg, delegate: self)
        try stream.addStreamOutput(
            self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "wrenote.syscap.audio"))
        try await stream.startCapture()
        self.stream = stream
        log("syscap: started (16kHz mono s16le on stdout)")
    }

    func stream(
        _ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .audio, sampleBuffer.isValid, sampleBuffer.numSamples > 0 else { return }
        do {
            try sampleBuffer.withAudioBufferList { abl, _ in
                guard let ptr = abl.unsafePointer.pointee.mBuffers.mData else { return }
                let byteCount = Int(abl.unsafePointer.pointee.mBuffers.mDataByteSize)
                let floatCount = byteCount / MemoryLayout<Float32>.size
                let floats = ptr.assumingMemoryBound(to: Float32.self)
                // float32 [-1,1] -> int16 LE
                var pcm = [Int16](repeating: 0, count: floatCount)
                for i in 0..<floatCount {
                    let v = max(-1.0, min(1.0, floats[i]))
                    pcm[i] = Int16(v * 32767.0)
                }
                pcm.withUnsafeBytes { raw in out.write(Data(raw)) }
            }
        } catch {
            log("syscap: buffer error \(error)")
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        log("syscap: stopped with error \(error)")
        exit(3)
    }
}

guard #available(macOS 13.0, *) else {
    log("syscap: requires macOS 13+"); exit(1)
}

/// `--app <bundle-id>`, repeatable. Anything else is ignored: this helper is
/// spawned by the engine, never typed by a person.
func parseApps(_ argv: [String]) -> [String] {
    var out: [String] = []
    var i = 1
    while i < argv.count {
        if argv[i] == "--app", i + 1 < argv.count {
            let id = argv[i + 1].trimmingCharacters(in: .whitespaces)
            if !id.isEmpty { out.append(id) }
            i += 2
        } else {
            i += 1
        }
    }
    return out
}

let cap = SysCap(apps: parseApps(CommandLine.arguments))
Task {
    do {
        try await cap.start()
    } catch {
        log("syscap: start failed: \(error)")
        exit(4)
    }
}

// Exit when the parent closes our stdin (parent died / asked us to stop).
DispatchQueue.global().async {
    let stdin = FileHandle.standardInput
    while true {
        let d = stdin.availableData
        if d.isEmpty { break }  // EOF
    }
    log("syscap: stdin closed, exiting")
    exit(0)
}

RunLoop.main.run()
