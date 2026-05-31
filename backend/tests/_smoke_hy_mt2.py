"""Quick Hy-MT2 spike: load + inspect metadata + try translation."""
import os
import time

from llama_cpp import Llama

MODEL_PATH = os.path.expanduser("~/.interpreter/models/Hy-MT2-1.8B-Q4_K_M.gguf")

print(f"Loading {MODEL_PATH} ...", flush=True)
t0 = time.monotonic()
llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=4096,
    n_gpu_layers=-1,
    verbose=True,
)
print(f"Loaded in {time.monotonic()-t0:.1f}s", flush=True)
print()

md = llm.metadata
print("Model metadata keys (relevant subset):", flush=True)
for k in sorted(md.keys()):
    if any(kw in k.lower() for kw in ["template", "chat", "tokenizer", "general.name", "general.architecture"]):
        v = md[k]
        if isinstance(v, str) and len(v) > 200:
            v = v[:197] + "..."
        print(f"  {k}: {v!r}", flush=True)
print()

print("--- Trying chat-template based translation ---", flush=True)
sentences = [
    "Good morning, good afternoon and good evening.",
    "Today we are doing something a little bit different.",
    "We've got a random question generator.",
]
for s in sentences:
    t1 = time.monotonic()
    try:
        resp = llm.create_chat_completion(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Translate the following English text into Chinese. "
                        "Output only the translation.\n\n" + s
                    ),
                },
            ],
            max_tokens=256,
            temperature=0.0,
        )
        out = resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        out = f"<error: {type(e).__name__}: {e}>"
    dt = time.monotonic() - t1
    print(f"\nEN: {s}", flush=True)
    print(f"ZH: {out}", flush=True)
    print(f"   ({dt:.2f}s)", flush=True)
