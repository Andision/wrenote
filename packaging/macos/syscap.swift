// wrenote system-audio capture helper (macOS, ScreenCaptureKit).
//
// Captures the system audio output and writes raw PCM to stdout for the Python
// backend to mix into the transcription pipeline. We exclude our own process's
// audio so playback inside Wrenote isn't re-captured. Requires the Screen
// Recording permission (TCC), prompted on first run.
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

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            log("syscap: no display available"); exit(2)
        }
        let filter = SCContentFilter(
            display: display, excludingApplications: [], exceptingWindows: [])

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

let cap = SysCap()
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
