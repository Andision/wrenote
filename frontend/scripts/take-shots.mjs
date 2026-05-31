// Take screenshots of the running app in several states for visual review.
//
// Usage: node scripts/take-shots.mjs
//
// The Vite dev server must already be running on http://localhost:5173/.
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const OUT_DIR = "/tmp/interpreter-shots";
await mkdir(OUT_DIR, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,           // retina-ish output so the screenshots aren't blurry
});
const page = await ctx.newPage();

console.log("Navigating to http://localhost:5173/");
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });

// 1. Idle / empty state
await page.screenshot({ path: `${OUT_DIR}/01-idle.png` });
console.log("Saved 01-idle.png");

// 2. Sidebar open
await page.click('button[title="Toggle sessions sidebar"]');
await page.waitForTimeout(300);
await page.screenshot({ path: `${OUT_DIR}/02-sidebar-open.png` });
console.log("Saved 02-sidebar-open.png");

// 3. Settings drawer open (close sidebar first)
await page.click('button[title="Toggle sessions sidebar"]');
await page.waitForTimeout(150);
await page.click('button[title="Settings"]');
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT_DIR}/03-settings.png` });
console.log("Saved 03-settings.png");

// 4. Inject fake session data so we can see the populated transcript view.
await page.keyboard.press("Escape");
await page.waitForTimeout(200);
await page.evaluate(() => {
  // Reach into zustand. Vite/HMR exposes the store via module side-effects;
  // we attach a debug hook in dev so this works.
  const store = window.__INTERPRETER_STORE__;
  if (!store) {
    console.warn("no __INTERPRETER_STORE__ — falling back to bare state");
    return;
  }
  const s = store.getState();
  s.startNewSession();
  const samples = [
    {
      id: "seg-a",
      t0: 0.0,
      t1: 8.5,
      orig:
        "Good morning, good afternoon and good evening. Welcome to a very different setup.",
      trans: "早上好，下午好，晚上好。欢迎来到一个截然不同的环境。",
    },
    {
      id: "seg-b",
      t0: 8.6,
      t1: 16.3,
      orig:
        "Today we're doing something a little bit different. We're just gonna have a chat.",
      trans: "今天我们将做一些与众不同的事情——我们只是聊聊天而已。",
    },
    {
      id: "seg-c",
      t0: 16.4,
      t1: 26.8,
      orig:
        "We're not going to try to be clear. We're going to talk to each other exactly as we would in the kitchen.",
      trans: "我们不会试图表达得清晰明了，而是像在厨房里那样互相交流。",
    },
    {
      id: "seg-d",
      t0: 27.0,
      t1: 31.2,
      orig: "We've got a random question generator and we'll ask each other",
      trans: "我们有一个随机问题生成器，我们会互相提问",
    },
  ];
  for (const x of samples) {
    s.handleSpeechStart({ type: "speech_start", segment_id: x.id, ts: x.t0 });
    s.handleTranscript({
      type: "final",
      segment_id: x.id,
      text: x.orig,
      lang: "en",
      t0: x.t0,
      t1: x.t1,
    });
    s.handleTranslation({
      type: "translation",
      segment_id: x.id,
      text: x.trans,
      src_lang: "en",
      tgt_lang: "zh",
      partial: false,
    });
    s.handleSpeechEnd({ type: "speech_end", segment_id: x.id, ts: x.t1 });
  }
  // Add one in-progress (partial) row at the bottom
  s.handleSpeechStart({ type: "speech_start", segment_id: "seg-live", ts: 32.0 });
  s.handleTranscript({
    type: "partial",
    segment_id: "seg-live",
    text: "some questions, have a bit of a chat",
    lang: "en",
    t0: 32.0,
    t1: 34.5,
  });
  s.handleTranslation({
    type: "translation",
    segment_id: "seg-live",
    text: "一些问题，聊聊天",
    src_lang: "en",
    tgt_lang: "zh",
    partial: true,
  });
});
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT_DIR}/04-transcript-populated.png` });
console.log("Saved 04-transcript-populated.png");

// 5. Sidebar open with the populated session
await page.click('button[title="Toggle sessions sidebar"]');
await page.waitForTimeout(300);
// Save session to localStorage so it shows up in the sidebar list
await page.evaluate(() => window.__INTERPRETER_STORE__.getState().saveCurrent());
await page.waitForTimeout(200);
await page.screenshot({ path: `${OUT_DIR}/05-with-sidebar.png` });
console.log("Saved 05-with-sidebar.png");

await browser.close();
console.log(`\nAll screenshots in ${OUT_DIR}`);
