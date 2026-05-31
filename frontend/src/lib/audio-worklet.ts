// AudioWorklet source. Downsamples native sample rate → 16 kHz, converts
// Float32 → int16, batches ~100 ms (1600 samples) into chunks, and
// post-messages them along with periodic RMS levels.
//
// We ship the source as a string and load it via a Blob URL so the bundler
// doesn't need a separate entry for the worklet file.
export const PCM_RECORDER_SOURCE = `
class PCMRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.batchSamples = 1600;       // 100 ms @ 16 kHz
    this.buffer = new Int16Array(this.batchSamples);
    this.bufferIdx = 0;
    this.ratio = sampleRate / this.targetRate;
    this.resamplePos = 0;
    this.rmsSum = 0;
    this.rmsCount = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    for (let i = 0; i < ch.length; i++) {
      this.rmsSum += ch[i] * ch[i];
      this.rmsCount++;
      this.resamplePos += 1 / this.ratio;
      while (this.resamplePos >= 1) {
        this.resamplePos -= 1;
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.buffer[this.bufferIdx++] = s < 0 ? s * 32768 : s * 32767;
        if (this.bufferIdx >= this.batchSamples) {
          this.port.postMessage({ kind: "pcm", buf: this.buffer.buffer.slice(0) });
          this.bufferIdx = 0;
          if (this.rmsCount > 0) {
            const rms = Math.sqrt(this.rmsSum / this.rmsCount);
            this.port.postMessage({ kind: "rms", value: rms });
            this.rmsSum = 0;
            this.rmsCount = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-recorder", PCMRecorder);
`;
