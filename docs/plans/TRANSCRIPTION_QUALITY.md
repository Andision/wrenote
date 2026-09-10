# Transcription quality — what was measured, and on what

Every number here comes from one real recording: a 28-minute engineering
meeting in English, recorded through a MacBook's built-in microphone in a
room with several speakers. Far-field, conversational, accented, with heavy
backchannel. It is the hardest case this app has, and it is also the normal
one.

Machine: Apple Silicon, Metal. Measured 2026-09-10.

Keeping this file is the point: three of the five things tried below look
obviously right and are not, and the only way to know was to run them on
real audio rather than on a clip.

---

## 1. The post-recording pass: rows

| | rows | median chars | rows ≤12 chars | ending mid-sentence |
|---|---|---|---|---|
| before | 830 | 16 | 365 | 106 |
| after | **291** | **66** | **1** | **9** |

Two causes, both fixed (`core/batch.py`):

* **`max_len=80`** is a subtitle setting — it exists so a caption fits on a
  screen — and it chops a segment wherever the 80th character lands. From
  this recording, verbatim: `"…sent to the cloud fair first then"` (77
  chars) / `"we"` (2) / `"build a cloud fair tunnel…"` (72).
* **`merge_whisper_segments` never merged.** Whisper emits a segment per
  utterance and in a meeting most utterances are "Yeah."; nothing joined
  them into rows.

## 2. The post-recording pass: punctuation and case

Whisper's casing on this recording is **bimodal and unstable**: the first
three minutes came out lowercase and unpunctuated, then it flipped to
correct punctuation right after a run of sixteen `"yeah"`s. Three attempts
to stabilise it, all rejected:

| tried | effect | why not |
|---|---|---|
| `initial_prompt` modelling punctuated style | rows correctly capitalised 90% → 100% | costs accuracy on the same audio: "we discussed" → "we're disgusting", "standalone services" → "standard rule". One wording also hallucinated "I will see you in the comments" over the opening. |
| **whisper.cpp VAD** (`vad=True`, ggml silero) | first 15 minutes clearly better: 92% → 98% capitalised, 1 → 0 repetition loops, opening dead-air row gone | **over the whole file it is worse**: 96% → 47% capitalised, 8 → 11 loops. It fixes the first half and pushes the second half into the unpunctuated mode. Measuring only the first window would have shipped a regression. |
| trimming leading/trailing silence | — | the hypothesis was wrong. The 27-second opening row of six words is not silence; an RMS trim found 0.1 s. It is very quiet speech. |

`no_context=True` produced byte-identical output and was not investigated
further.

**What is left.** No whisper knob stabilised the casing; each one only moved
where the instability landed. The remaining idea is **punctuation and case
restoration as a separate step**, which is separable in the way that
matters — it must not change the words, so it can be verified by diffing the
word sequence. Two candidates: sherpa-onnx's CT-Transformer punctuation
model (zh-en, ~300 MB), or the chat model that is already downloaded, under
a "repunctuate, do not reword" constraint plus that diff as a guard.

The eight remaining repetition runs (`"Yeah. Yeah. Yeah." ×7`) were checked
by hand and look like real backchannel — several people agreeing at once —
not hallucination. Left alone.

---

## 3. The live path: what it costs per utterance

The live path cuts utterances with a VAD and transcribes each one, so the
number that matters is time-to-text after someone stops speaking. Same
audio, same VAD segmentation, model kept warm:

| | median | worst | the same sentence, as transcribed |
|---|---|---|---|
| **whisper large-v3-turbo q5** | **935 ms** | 1060 ms | "Yeah, yesterday we discussed, we want to build a standard law, services" |
| whisper small q5 | 325 ms | 417 ms | "Yeah, yesterday we discussed and we want to build a standard rule services" |
| whisper base q5 | 127 ms | 954 ms | "Yeah, we're discussing, we want to build a standard road" |
| SenseVoice int8 (240 MB) | 119 ms | — | "Yeah, you stay were discussing we want to build a standard room services" |
| Zipformer streaming zh-en | streaming | — | "YEAH YOU STAY WHICH CUSTIN OWN TO BUILD / STANDARD / SUBACIST" |

Ground truth for that sentence: *"yesterday we discussed, we want to build a
standalone service, a dedicated service for artificial analysis"*.

**On a machine with an accelerator there is nothing to solve.** Whisper
large-v3-turbo answers under a second after each utterance and is far the
most accurate; VAD-segmented Whisper is not the fallback, it is the answer.

**The Zipformer is unusable on this audio** and that is a model fact, not an
integration bug: the mishearings are phonetically plausible ("cloff air" for
Cloudflare), the feature path is correct, and Whisper reads the same audio
well. It is a bilingual model trained mostly on Chinese, and it had only
ever been checked against its own clean test clips.

**SenseVoice does not earn its 240 MB here**: it lost to whisper-base, which
is 60 MB and the same speed. But this recording is English-only and
SenseVoice's claim is Chinese, so that verdict does not transfer to a mixed
meeting.

---

## 4. Reproducing any of this

The engine's own venv has neither speech binding by default — they are
platform-specific extras. For a measurement run:

```bash
cd engine && python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pip install pywhispercpp sherpa-onnx   # the two used above
```

Then feed a recording from `~/.wrenote/recordings/` through
`wrenote.core.batch.transcribe_pcm_sync` for the offline pass, or through
`sherpa_onnx.OnlineRecognizer` in 100 ms frames for the streaming path —
which is the framing `core/pipeline.py` uses, and the reason a model that
looks fine on a whole clip can fall apart in the live path.

Measure on **at least the whole recording**. Section 2's VAD row is the
warning: fifteen minutes said one thing and twenty-eight said the opposite.

## 5. What has not been measured

* **Anything in Chinese, or code-switched.** Every recording available was
  100% English. The zh-en case — the one this app is for — is untested end
  to end, which makes these open:
  * Whisper detects **one language per segment**, so a sentence that
    switches mid-way is decoded as one or the other. `core/lang.py`'s
    `LanguagePolicy` (a main language plus the ones that may come up, a
    secondary winning only above 0.6 confidence) exists for this and its
    threshold is a figure from clean test clips.
  * Whether SenseVoice beats whisper-base on Chinese, which is the only case
    where it would earn a place in the catalogue.
  * Whether pinning a main language beats `srcLang: auto` for mixed speech.
* **Diarization against the new row shape.** Rows are 3× longer now; speaker
  labels are assigned by time overlap, so they are coarser by construction.
* Any recording made with a headset or a real meeting microphone. Everything
  above is the worst case.

**The next measurement to take**, in order: set a session's main language to
Chinese with English as a secondary, record a real mixed meeting, and run
that recording through the offline pass, through whisper in the live
framing, and through SenseVoice. That answers all three open questions above
at once, and none of them can be answered without it.
