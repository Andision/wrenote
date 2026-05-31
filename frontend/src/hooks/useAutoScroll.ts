// "Sticky bottom" autoscroll: as long as the user is near the bottom we keep
// scrolling them down on new content; if they scroll up, we leave them
// where they are until they return to the bottom.
import { useEffect, useRef, useState } from "react";

export function useAutoScroll<T extends HTMLElement>(deps: unknown[]) {
  const ref = useRef<T | null>(null);
  const [pinned, setPinned] = useState(true);

  // Track whether the user is near the bottom.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      setPinned(distance < 80);
    };
    el.addEventListener("scroll", onScroll);
    onScroll();
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Auto-scroll on dependency change when pinned.
  useEffect(() => {
    if (!pinned) return;
    const el = ref.current;
    if (!el) return;
    // RAF so layout has flushed before we measure.
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { ref, pinned, scrollToBottom: () => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  } };
}
