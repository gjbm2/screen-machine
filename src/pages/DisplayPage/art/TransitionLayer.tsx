// A scheduled picture's transition (routes/transitions.py): a video from the picture on the screen (A) to the new
// one (B), played once over A instead of the plain cross-fade (owner, 24 Nov). It fades in over A (its first frame is
// A, at rest), plays (eased in and out, rendered into the file), and once it covers the screen the page swaps to B
// underneath; when it ends (on B, at rest) it fades away to show B. A transition that won't load or play just goes.
import React, { useEffect, useRef, useState } from "react";
import type { Fit } from "../control/protocol";
import { isClip, mediaStyle } from "./styles";
import type { ArtMediaHandle } from "./ArtMedia";

const IN_MS = 1200;
const OUT_MS = 1800;
/** However it goes, the transition is gone by this long after it starts loading. */
const GUARD_MS = 45_000;
/** A transition this much older than its picture's arrival is stale (a page that saw B late doesn't play it). */
const FRESH_S = 1800;
/** The old picture's own clip comes to rest before the transition starts from its still, for at most this long. */
const REST_WAIT_MS = 40_000;

/**
 * The transition to play for a new base `src`, or null: from the new picture's sidecar (`transition`), when it is
 * fresh and not already played on this page for this publish. Waits for a moving clip on
 * screen to come to rest first.
 */
export async function transitionFor(src: string, played: Set<string>, media: () => ArtMediaHandle | null): Promise<{ url: string; aspect: number | null } | null> {
  if (isClip(src)) return null;
  try {
    const page = new URL(src, window.location.href);
    const r = await fetch(page.origin + page.pathname + ".json", { cache: "no-store" });
    if (!r.ok) return null;
    const t = (await r.json())?.transition;
    // once per publish (`at`): an upscaled copy of B carries it again, but a replay of the day publishes it anew
    const key = `${t?.url}@${t?.at}`;
    if (!t?.url || played.has(key) || !(Date.now() / 1000 - Number(t.at) < FRESH_S)) return null;
    played.add(key);
    const until = Date.now() + REST_WAIT_MS;
    for (;;) {
      const m = media();
      if (!m || !m.isEasedClip() || m.canPlayNow() || Date.now() > until) break;
      await new Promise((res) => setTimeout(res, 500));
    }
    // the old picture's shape: the transition was drawn from it stretched to the model's size, so it is shown
    // stretched back (owner, 24 Nov: an aspect-ratio jump as it began)
    const el = media()?.element();
    const w = el instanceof HTMLVideoElement ? el.videoWidth : el?.naturalWidth;
    const h = el instanceof HTMLVideoElement ? el.videoHeight : el?.naturalHeight;
    return { url: new URL(t.url, page).toString(), aspect: w && h ? w / h : null };
  } catch {
    return null;
  }
}

interface Props {
  src: string;
  /** The old picture's width / height: the transition is shown in that shape, fitted as the picture is. */
  aspect: number | null;
  displayMode: Fit;
  /** It covers the screen (or gave up): the base can change under it. */
  onCovered: () => void;
  /** It has gone. */
  onDone: () => void;
}

/** Where a picture of `aspect` sits on the screen, fitted (contain) or filling (cover). */
function boxFor(aspect: number, mode: Fit): React.CSSProperties {
  const W = window.innerWidth;
  const H = window.innerHeight;
  let w = W;
  let h = W / aspect;
  if (mode === "fill" ? h < H : h > H) {
    h = H;
    w = H * aspect;
  }
  return { position: "absolute", left: (W - w) / 2, top: (H - h) / 2, width: w, height: h };
}

export function TransitionLayer({ src, aspect, displayMode, onCovered, onDone }: Props) {
  const ref = useRef<HTMLVideoElement | null>(null);
  const [phase, setPhase] = useState<"loading" | "in" | "out">("loading");
  const cb = useRef({ onCovered, onDone });
  cb.current = { onCovered, onDone };

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    let over = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const later = (fn: () => void, ms: number) => timers.push(setTimeout(fn, ms));
    const finish = () => {
      if (over) return;
      over = true;
      cb.current.onCovered();
      setPhase("out");
      later(() => cb.current.onDone(), OUT_MS + 100);
    };
    const start = () => {
      v.play().then(
        () => {
          requestAnimationFrame(() => setPhase("in"));
          later(() => cb.current.onCovered(), IN_MS + 100);
        },
        finish,
      );
    };
    if (v.readyState >= 3) start();
    else v.addEventListener("canplaythrough", start, { once: true });
    v.addEventListener("ended", finish);
    v.addEventListener("error", finish);
    later(finish, GUARD_MS);
    return () => {
      timers.forEach(clearTimeout);
      v.removeEventListener("canplaythrough", start);
      v.removeEventListener("ended", finish);
      v.removeEventListener("error", finish);
    };
  }, [src]);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 1,
        pointerEvents: "none",
        opacity: phase === "in" ? 1 : 0,
        transition: `opacity ${phase === "out" ? OUT_MS : IN_MS}ms ease-in-out`,
        willChange: "opacity",
      }}
    >
      {aspect ? (
        <div style={boxFor(aspect, displayMode)}>
          <video ref={ref} src={src} muted playsInline preload="auto" style={{ width: "100%", height: "100%", objectFit: "fill" }} />
        </div>
      ) : (
        <video ref={ref} src={src} muted playsInline preload="auto" style={mediaStyle(displayMode)} />
      )}
    </div>
  );
}
