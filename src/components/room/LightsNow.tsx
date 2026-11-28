// The lights' layers as the art director plays them, was / now / next (owner, 28 Nov: "we surface was / now / next
// for vj scenes, but nowhere do we surface the scenes & effects etc. that are being commissioned for the lighting
// layers ... a panel for that (rolled up by default) under the screen panels"). The same cards and the same holding
// still as the screens' (moments.tsx). Now: each layer's scene and effects (show_service GET /api/v1/lights/now);
// Was: each layer's scene before its last change (kept by this browser as it watches); Next: the scenes of a change
// the director has drawn and not yet played. A scene is rated with the lights library's stars
// (POST /api/v1/lights/rate, which steer the director's draw). Read while open, every POLL_MS.

import { useEffect, useMemo, useState } from "react";
import { Star } from "lucide-react";
import { cn } from "@/lib/utils";
import { storageGet, storageSet, type LightsNow as Now, type RoomApi } from "@/pages/Room/api";
import { Unroll } from "@/pages/Room/bits";
import { CatchUp, MomentCard, useHold, type When } from "./moments";

const POLL_MS = 3000;
const PREV_KEY = "sm_lights_prev";

interface Layer {
  group: string;
  label: string;
  scene: string;
  effects: string[];
}
type Lib = Map<string, { label: string; thumb: string | null }>;

const pretty = (s: string) => s.replace(/[-_]+/g, " ").replace(/^./, (c) => c.toUpperCase());

/** The lights belong to the room, not to a screen (owner, 28 Nov: "rooms may have 0...n screens, but that's separate
 *  from lighting scenes"): this goes in the room's own place (the Room page's Lights section, a card of its own on the
 *  Karaoke page), never inside a screen's. */
export function LightsPanel({ api, title = "Lights" }: { api: RoomApi; title?: string }) {
  return (
    <Unroll title={title} testId="lights-now">
      <LightsBody api={api} />
    </Unroll>
  );
}

function LightsBody({ api }: { api: RoomApi }) {
  const [now, setNow] = useState<Now | null>(null);
  const [failed, setFailed] = useState(false);
  const [lib, setLib] = useState<Lib>(new Map());
  const [ratings, setRatings] = useState<Record<string, { stars?: number; reject?: boolean }>>({});
  const [prev, setPrev] = useState<Record<string, Layer>>(() => {
    try {
      return JSON.parse(storageGet(PREV_KEY) ?? "{}") ?? {};
    } catch {
      return {};
    }
  });

  useEffect(() => {
    const ctl = new AbortController();
    api
      .lightsLibrary(ctl.signal)
      .then((l) => setLib(new Map(l.items.filter((i) => i.kind === "light").map((i) => [i.name, { label: i.label, thumb: api.showUrl(i.thumb) }]))))
      .catch(() => undefined);
    api
      .lightRatings(ctl.signal)
      .then((r) => setRatings(r.ratings ?? {}))
      .catch(() => undefined);
    return () => ctl.abort();
  }, [api]);

  useEffect(() => {
    let alive = true;
    const read = () => {
      if (document.visibilityState === "hidden") return;
      api
        .lightsNow()
        .then((n) => {
          if (!alive) return;
          setFailed(false);
          setNow(n);
        })
        .catch(() => alive && setFailed(true));
    };
    read();
    const t = setInterval(read, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [api]);

  const layers: Layer[] = useMemo(
    () =>
      Object.entries(now?.programs ?? {}).map(([group, p]) => ({
        group,
        label: p.label === "*" ? "Other lights" : p.label,
        scene: p.scene,
        effects: p.effects ?? [],
      })),
    [now],
  );

  // a layer's scene before its last change
  useEffect(() => {
    if (!layers.length) return;
    setPrev((old) => {
      const cur = JSON.parse(storageGet("sm_lights_cur") ?? "{}") as Record<string, Layer>;
      let next = old;
      for (const l of layers) {
        const was = cur[l.group];
        if (was && was.scene !== l.scene) next = { ...next, [l.group]: was };
      }
      storageSet("sm_lights_cur", JSON.stringify(Object.fromEntries(layers.map((l) => [l.group, l]))));
      if (next !== old) storageSet(PREV_KEY, JSON.stringify(next));
      return next;
    });
  }, [layers]);

  const nextLayers: Layer[] = Object.entries(now?.next?.scenes ?? {})
    .filter(([g, sc]) => now?.programs[g]?.scene !== sc)
    .map(([g, sc]) => ({ group: g, label: now?.programs[g]?.label ?? pretty(g), scene: sc, effects: [] }));
  const wasLayers: Layer[] = layers.map((l) => prev[l.group]).filter((x): x is Layer => !!x);

  const live = { was: wasLayers, now: layers, next: nextLayers };
  const sig = (v: typeof live) => [...v.now, ...v.next].map((l) => `${l.group}:${l.scene}:${l.effects.join(",")}`).join("|");
  const { view: v, stale, release, handlers } = useHold(live, sig);

  const rate = (scene: string, stars: number) => {
    const key = `light/${scene}`;
    const cur = ratings[key]?.stars ?? 0;
    const want = cur === stars ? 0 : stars; // the stars it has: cleared
    setRatings((r) => ({ ...r, [key]: { ...r[key], stars: want } }));
    void api.lightRate(key, { stars: want }).catch(() => setRatings((r) => ({ ...r, [key]: { ...r[key], stars: cur } })));
  };

  if (!now) return <p className="px-1 text-sm text-muted-foreground">{failed ? "The show isn't answering." : "…"}</p>;

  const card = (when: When, ls: Layer[]) =>
    ls.length > 0 && (
      <MomentCard key={when} when={when} testId={`lights-${when}`}>
        <div className="divide-y divide-stone-200/70">
          {ls.map((l) => {
            const item = lib.get(l.scene);
            const stars = ratings[`light/${l.scene}`]?.stars ?? 0;
            return (
              <div key={l.group} className="flex items-center gap-2 py-1.5">
                {item?.thumb ? (
                  <img src={item.thumb} alt="" className="aspect-video w-14 shrink-0 rounded-md object-cover" loading="lazy" />
                ) : (
                  <span className="aspect-video w-14 shrink-0 rounded-md bg-stone-200" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs text-stone-500">{l.label}</div>
                  <div className="truncate text-sm font-medium">{item?.label ?? pretty(l.scene)}</div>
                  {l.effects.length > 0 && <div className="truncate text-xs text-stone-600">+ {l.effects.join(", ")}</div>}
                </div>
                {when !== "next" && (
                  <div role="group" aria-label="Rate this scene" className="flex shrink-0 items-center">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        type="button"
                        aria-label={`${n} star${n > 1 ? "s" : ""}`}
                        aria-pressed={stars === n}
                        onClick={() => rate(l.scene, n)}
                        className="grid h-9 w-6 place-items-center rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <Star className={cn("h-4 w-4", n <= stars ? "fill-amber-400 text-amber-500" : "text-stone-400")} aria-hidden />
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </MomentCard>
    );

  return (
    <div className="relative space-y-2" {...handlers}>
      {stale && <CatchUp onClick={release} />}
      {card("was", v.was)}
      {card("now", v.now)}
      {card("next", v.next)}
    </div>
  );
}
