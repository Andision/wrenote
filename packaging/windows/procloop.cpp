// wrenote per-process system-audio capture helper (Windows, WASAPI).
//
// The same job `packaging/macos/syscap.swift` does on macOS: capture what an
// application is playing and write raw PCM to stdout, so the engine can
// transcribe the meeting without also transcribing the video in the other
// window.
//
// Why a native helper at all: the Python side captures system audio with
// `soundcard`, which uses *endpoint* loopback — the whole output mix, with no
// way to say whose. Per-process loopback is a different activation path,
// ActivateAudioInterfaceAsync with AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
// (Windows 10 2004+), and it is not reachable from any Python audio binding.
//
// Usage:
//   procloop --pid <n>
//
// Always the process *tree*: the app that owns the window is usually not the
// one making the sound (Zoom, Chrome and Slack all play audio from helper
// processes), and the API offers no "this process alone" mode anyway — its
// two modes are include-tree and exclude-tree.
//
// Output: signed 16-bit little-endian PCM, mono, 16 kHz, on stdout.
// Diagnostics go to stderr. Exits on EOF of stdin, matching the macOS helper
// so the Python side can treat the two the same.
//
// Build: cl /O2 /EHsc /std:c++17 procloop.cpp /link ole32.lib mmdevapi.lib

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <wrl/implements.h>

#include <fcntl.h>
#include <io.h>

#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <thread>
#include <vector>

using Microsoft::WRL::ComPtr;
using Microsoft::WRL::RuntimeClass;
using Microsoft::WRL::RuntimeClassFlags;
using Microsoft::WRL::ClassicCom;
using Microsoft::WRL::FtmBase;

static const int kSampleRate = 16000;
static const int kChannels = 1;

static void logf(const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  fprintf(stderr, "procloop: ");
  vfprintf(stderr, fmt, ap);
  fprintf(stderr, "\n");
  fflush(stderr);
  va_end(ap);
}

// ActivateAudioInterfaceAsync answers on another thread; this is the wait.
class ActivationHandler
    : public RuntimeClass<RuntimeClassFlags<ClassicCom>, FtmBase,
                          IActivateAudioInterfaceCompletionHandler> {
 public:
  ActivationHandler() : done_(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}
  ~ActivationHandler() { if (done_) CloseHandle(done_); }

  STDMETHODIMP ActivateCompleted(IActivateAudioInterfaceAsyncOperation* op) override {
    HRESULT activate_hr = S_OK;
    ComPtr<IUnknown> unknown;
    HRESULT hr = op->GetActivateResult(&activate_hr, &unknown);
    result_ = SUCCEEDED(hr) ? activate_hr : hr;
    if (SUCCEEDED(result_)) unknown.As(&client_);
    SetEvent(done_);
    return S_OK;
  }

  HRESULT Wait(ComPtr<IAudioClient>* out) {
    if (!done_) return E_FAIL;
    WaitForSingleObject(done_, INFINITE);
    *out = client_;
    return result_;
  }

 private:
  HANDLE done_ = nullptr;
  HRESULT result_ = E_FAIL;
  ComPtr<IAudioClient> client_;
};

static std::atomic<bool> g_stop{false};

// The parent closing our stdin is how it says "stop" — same contract as the
// macOS helper, and it survives the parent dying without a signal.
static void watch_stdin() {
  char buf[256];
  DWORD n = 0;
  HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
  while (ReadFile(in, buf, sizeof(buf), &n, nullptr) && n > 0) {
  }
  logf("stdin closed, exiting");
  g_stop = true;
}

int wmain(int argc, wchar_t** argv) {
  DWORD pid = 0;
  for (int i = 1; i < argc; i++) {
    if (!wcscmp(argv[i], L"--pid") && i + 1 < argc) {
      pid = static_cast<DWORD>(_wtoi(argv[++i]));
    }
  }
  if (pid == 0) {
    logf("usage: procloop --pid <n>");
    return 2;
  }

  // Binary stdout: a 0x0A byte inside PCM must not become 0x0D 0x0A.
  if (_setmode(_fileno(stdout), _O_BINARY) == -1) {
    logf("could not put stdout in binary mode");
    return 2;
  }

  HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
  if (FAILED(hr)) {
    logf("CoInitializeEx failed: 0x%08lx", hr);
    return 3;
  }

  AUDIOCLIENT_ACTIVATION_PARAMS params = {};
  params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
  params.ProcessLoopbackParams.TargetProcessId = pid;
  params.ProcessLoopbackParams.ProcessLoopbackMode =
      PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;

  PROPVARIANT pv = {};
  pv.vt = VT_BLOB;
  pv.blob.cbSize = sizeof(params);
  pv.blob.pBlobData = reinterpret_cast<BYTE*>(&params);

  ComPtr<ActivationHandler> handler = Microsoft::WRL::Make<ActivationHandler>();
  ComPtr<IActivateAudioInterfaceAsyncOperation> op;
  hr = ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
                                   __uuidof(IAudioClient), &pv, handler.Get(), &op);
  if (FAILED(hr)) {
    logf("ActivateAudioInterfaceAsync failed: 0x%08lx", hr);
    return 4;
  }

  ComPtr<IAudioClient> client;
  hr = handler->Wait(&client);
  if (FAILED(hr) || !client) {
    // The usual cause is a Windows older than 10 2004, or a pid that has gone.
    logf("process loopback unavailable for pid %lu: 0x%08lx", pid, hr);
    return 5;
  }

  // The mix format is not queryable on this activation path; ask for what the
  // pipeline wants and let WASAPI convert.
  WAVEFORMATEX fmt = {};
  fmt.wFormatTag = WAVE_FORMAT_PCM;
  fmt.nChannels = kChannels;
  fmt.nSamplesPerSec = kSampleRate;
  fmt.wBitsPerSample = 16;
  fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample / 8;
  fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign;

  HANDLE ready = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  hr = client->Initialize(
      AUDCLNT_SHAREMODE_SHARED,
      AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
      // 200 ms of buffer: the reader wakes on the event, and a slow wake
      // costs latency rather than a gap.
      2000000, 0, &fmt, nullptr);
  if (FAILED(hr)) {
    logf("IAudioClient::Initialize failed: 0x%08lx", hr);
    return 6;
  }
  client->SetEventHandle(ready);

  ComPtr<IAudioCaptureClient> capture;
  hr = client->GetService(__uuidof(IAudioCaptureClient), &capture);
  if (FAILED(hr)) {
    logf("GetService(IAudioCaptureClient) failed: 0x%08lx", hr);
    return 7;
  }

  hr = client->Start();
  if (FAILED(hr)) {
    logf("IAudioClient::Start failed: 0x%08lx", hr);
    return 8;
  }
  logf("started (16kHz mono s16le on stdout, pid=%lu, process tree)", pid);

  std::thread(watch_stdin).detach();

  std::vector<BYTE> silence;
  while (!g_stop) {
    if (WaitForSingleObject(ready, 500) != WAIT_OBJECT_0) continue;
    UINT32 frames = 0;
    while (SUCCEEDED(capture->GetNextPacketSize(&frames)) && frames > 0) {
      BYTE* data = nullptr;
      DWORD flags = 0;
      if (FAILED(capture->GetBuffer(&data, &frames, &flags, nullptr, nullptr))) break;
      const size_t bytes = static_cast<size_t>(frames) * fmt.nBlockAlign;
      if (flags & AUDCLNT_BUFFERFLAGS_SILENT) {
        // A silent packet carries no data; the timeline still needs the time.
        if (silence.size() < bytes) silence.assign(bytes, 0);
        fwrite(silence.data(), 1, bytes, stdout);
      } else if (data != nullptr) {
        fwrite(data, 1, bytes, stdout);
      }
      fflush(stdout);
      capture->ReleaseBuffer(frames);
    }
  }

  client->Stop();
  CoUninitialize();
  return 0;
}
