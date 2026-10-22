// /display/<id>?artonly=1 — the snapshotter's page (LIVE_V1 §2.3).
// Only the art stack (still or clip, brightness layer, warm layer) on black: no overlays, controls,
// live layer, polling or sockets. It renders exactly the ArtState it is handed:
//
//   window.__smArtReady === true            once mounted
//   await window.__smRenderArt(state)       applies base + mask + flags with no CSS transitions,
//                                           waits for img.decode() (or the clip's `seeked` at clip_t),
//                                           then two requestAnimationFrames
//
// It uses the same components and styles as the controlled page's art stack (ArtMedia, MaskLayers),
// so the composited pixels match for the same state and viewport.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ArtState, Fit } from "../control/protocol";
import { LayerBoundary } from "../common/LayerBoundary";
import { ArtMedia, type ArtMediaHandle } from "./ArtMedia";
import { MaskController, normaliseMask } from "./MaskController";
import { MaskLayers } from "./MaskLayers";
import { rootContainerStyle } from "./styles";

declare global {
  interface Window {
    __smArtReady?: boolean;
    __smRenderArt?: (state: ArtState) => Promise<void>;
  }
}

function twoFrames(): Promise<void> {
  return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())));
}

function normaliseState(s: Partial<ArtState> | null | undefined): ArtState {
  const flags = s?.flags ?? ({} as ArtState["flags"]);
  const fit: Fit = flags.fit === "fill" ? "fill" : "fit";
  const kind = s?.kind === "video" ? "video" : "image";
  return {
    base_url: typeof s?.base_url === "string" && s.base_url ? s.base_url : null,
    kind,
    clip_t: kind === "video" && typeof s?.clip_t === "number" && Number.isFinite(s.clip_t) ? s.clip_t : kind === "video" ? 0 : null,
    mask: normaliseMask(s?.mask),
    flags: { nomask: !!flags.nomask, nocolour: !!flags.nocolour, fit },
    viewport: s?.viewport ?? { w: window.innerWidth, h: window.innerHeight },
  };
}

export function ArtOnlyPage() {
  const [art, setArt] = useState<{ seq: number; state: ArtState } | null>(null);
  const mask = useMemo(() => new MaskController({ nomask: false, nocolour: false }), []);
  const mediaRef = useRef<ArtMediaHandle>(null);
  const committed = useRef(new Map<number, () => void>());

  // Resolve the render call once React has committed its state (layout effects run before paint).
  useLayoutEffect(() => {
    if (!art) return;
    const done = committed.current.get(art.seq);
    committed.current.delete(art.seq);
    done?.();
  }, [art]);

  useEffect(() => {
    let seq = 0;
    let chain: Promise<void> = Promise.resolve();
    const render = async (input: ArtState): Promise<void> => {
      const state = normaliseState(input);
      const my = ++seq;
      await new Promise<void>((resolve) => {
        committed.current.set(my, resolve);
        setArt({ seq: my, state });
      });
      mask.applyNow(state.mask, state.flags);
      await mediaRef.current?.ready();
      await twoFrames();
    };
    // Calls are serialised: each resolves after its own state is on screen.
    window.__smRenderArt = (state: ArtState) => {
      const p = chain.then(() => render(state));
      chain = p.catch(() => undefined);
      return p;
    };
    window.__smArtReady = true;
    return () => {
      window.__smArtReady = false;
      delete window.__smRenderArt;
      mask.dispose();
    };
  }, [mask]);

  const s = art?.state;
  return (
    <div style={rootContainerStyle} data-sm-artonly="">
      {s?.base_url && (
        <LayerBoundary name="media" resetKey={s.base_url}>
          <ArtMedia
            ref={mediaRef}
            src={s.base_url}
            kind={s.kind}
            videoKey={s.base_url}
            displayMode={s.flags.fit}
            mode="static"
            clipT={s.clip_t}
          />
        </LayerBoundary>
      )}
      <LayerBoundary name="mask">
        <MaskLayers controller={mask} noColour={!!s?.flags.nocolour} />
      </LayerBoundary>
    </div>
  );
}
