"use client";

import { useEffect } from "react";

export default function HashTargetScroller() {
  useEffect(() => {
    const scrollToHash = () => {
      const encodedId = window.location.hash.slice(1);
      if (!encodedId) return;
      let targetId = encodedId;
      try {
        targetId = decodeURIComponent(encodedId);
      } catch {
        // A malformed external hash should be ignored, not break the explorer.
      }
      const target = document.getElementById(targetId);
      target?.scrollIntoView({ block: "start" });
    };

    let settleObserver: ResizeObserver | undefined;
    let settleTimer: number | undefined;
    const settleHashTarget = () => {
      scrollToHash();
      settleObserver?.disconnect();
      if (typeof ResizeObserver !== "undefined") {
        settleObserver = new ResizeObserver(scrollToHash);
        settleObserver.observe(document.body);
      }
      if (settleTimer !== undefined) window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(() => {
        scrollToHash();
        settleObserver?.disconnect();
      }, 1_500);
    };

    const firstFrame = window.requestAnimationFrame(settleHashTarget);
    window.addEventListener("hashchange", settleHashTarget);

    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (settleTimer !== undefined) window.clearTimeout(settleTimer);
      settleObserver?.disconnect();
      window.removeEventListener("hashchange", settleHashTarget);
    };
  }, []);

  return null;
}
