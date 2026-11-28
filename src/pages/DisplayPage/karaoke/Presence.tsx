// How the karaoke screen's overlays come and go (owner, 28 Nov: "let's smooth (or dramatise) the entry & exit of
// overlays rather than just cutting in and out"): one wrapper for all of them. In, it rises (or slides in from its own
// edge) out of a blur and settles with a little overshoot; out, it sinks back into the blur, a touch quicker. While
// it leaves, it keeps showing what it last showed. Web Animations on a full-screen box that clicks through.

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export type From = "below" | "right" | "left" | "above" | "centre";

const IN_MS = 900;
const OUT_MS = 600;
const SETTLE = "cubic-bezier(0.16, 1.2, 0.3, 1)"; // a little past, then back: lands rather than appears
const LEAVE = "cubic-bezier(0.5, 0, 0.75, 0)";

function offset(from: From): string {
  switch (from) {
    case "right":
      return "translateX(8vw) scale(0.96)";
    case "left":
      return "translateX(-8vw) scale(0.96)";
    case "above":
      return "translateY(-6vh) scale(0.96)";
    case "centre":
      return "scale(0.85)";
    default:
      return "translateY(5vh) scale(0.96)";
  }
}

export function Presence({ show, from = "below", children }: { show: boolean; from?: From; children: ReactNode }) {
  const [mounted, setMounted] = useState(show);
  const last = useRef<ReactNode>(children);
  const box = useRef<HTMLDivElement>(null);
  const anim = useRef<Animation | null>(null);
  if (show) last.current = children;

  useEffect(() => {
    if (show) setMounted(true);
  }, [show]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el || !mounted) return;
    anim.current?.cancel();
    const hidden = { opacity: 0, transform: offset(from), filter: "blur(14px)" };
    const shown = { opacity: 1, transform: "none", filter: "blur(0px)" };
    if (show) {
      // "backwards": once in, nothing is left on the box (a filter on it would stop the cards' backdrop blur)
      anim.current = el.animate([hidden, shown], { duration: IN_MS, easing: SETTLE, fill: "backwards" });
    } else {
      const a = el.animate([shown, hidden], { duration: OUT_MS, easing: LEAVE, fill: "forwards" });
      anim.current = a;
      a.onfinish = () => setMounted(false);
    }
  }, [show, mounted, from]);

  if (!mounted) return null;
  return (
    <div ref={box} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {show ? children : last.current}
    </div>
  );
}
