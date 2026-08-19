"use client";

import { useEffect } from "react";

export default function HashTargetScroller() {
  useEffect(() => {
    const scrollToHash = () => {
      const encodedId = window.location.hash.slice(1);
      if (!encodedId) return;
      const target = document.getElementById(decodeURIComponent(encodedId));
      target?.scrollIntoView({ block: "start" });
    };

    let settleObserver: ResizeObserver | undefined;
    let settleTimer: number | undefined;
    const settleHashTarget = () => {
      scrollToHash();
      settleObserver?.disconnect();
      settleObserver = new ResizeObserver(scrollToHash);
      settleObserver.observe(document.body);
      if (settleTimer !== undefined) window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(() => settleObserver?.disconnect(), 750);
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
