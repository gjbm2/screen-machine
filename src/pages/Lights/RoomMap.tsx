// The Room map (docs/LIGHTS.md §8.2): where the screens, lights and named places are, so a direction ("upwards",
// "to the left") means the same thing for every light and screen, and a calibrated mover can be pointed at any place.
//   - First use asks how big the room is and which wall people face (PUT /rig/space/room).
//   - The plan is drawn from above with the front wall at the top (thicker), so left and right on the page are the
//     room's left and right as people face the front; a side strip under it shows heights.
//   - Tap a thing to select it, drag it to place it (screens snap to the nearest wall); the bar under the map sets its
//     height, how a mover hangs, a screen's picture size, or takes it off the map.
//   - Add a place: the piano, the mirror ball, the dance floor or anything named; it drops in the middle to be dragged.
//   - Live: each light's beam to where it lands (GET /rig/live, 4 Hz while shown) and the flow, in words and an arrow.
// RoomDrawing is the same picture for Move it's preview (Preview.tsx). Only the things themselves take the touch (the
// page still scrolls over the rest of the map on a phone).

import { useCallback, useEffect, useId, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type Ref } from "react";
import { MapPin, Minus, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { explainLightsError, type LightsApi } from "./api";
import {
  clampToRoom,
  flowWords,
  fromPlan,
  fromSide,
  FRONTS,
  nameWords,
  parseSpace,
  placeId,
  planFrame,
  snapToWall,
  toPlan,
  toSide,
  wallSide,
  type Front,
  type LiveView,
  type Placeable,
  type PlanFrame,
  type RoomSpace,
  type Vec3,
} from "./lib";
import { useLive, useSpace } from "./useRig";

const CHIP =
  "inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3.5 text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white text-stone-700 hover:bg-stone-50";
const GAP = 30; // px between the plan and the side strip
/** Places offered to add with one tap (anything else is typed). */
const PLACE_SUGGESTIONS = ["The piano", "Mirror ball", "Dance floor"];
const HEIGHT_STEP_M = 0.1;
const MOUNTS: [string, string][] = [
  ["ceiling", "Hangs from the ceiling"],
  ["floor", "Stands on the floor"],
  ["wall", "On a wall"],
];

/** Something drawn: a placed light or screen ("t:<id>"), a place ("p:<name>") or an area ("a:<name>"). */
export interface DrawItem {
  key: string;
  label: string;
  kind: "screen" | "mover" | "lamp" | "place" | "area";
  at: Vec3;
  /** A screen's picture (w, h) or an area's (w, d), metres. */
  size?: [number, number] | null;
  normal?: Vec3 | null;
}

/** What the map draws, and which of the rig's things aren't on it yet. */
export function itemsFor(space: RoomSpace, things: Placeable[]): { placed: DrawItem[]; unplaced: Placeable[] } {
  const byId = new Map(things.map((t) => [t.id, t]));
  const placed: DrawItem[] = [];
  for (const a of space.regions) placed.push({ key: `a:${a.name}`, label: a.label, kind: "area", at: a.centre, size: a.size });
  for (const p of space.points) placed.push({ key: `p:${p.name}`, label: p.label, kind: "place", at: p.at });
  for (const [id, a] of Object.entries(space.appliances)) {
    if (!a.at) continue;
    const t = byId.get(id);
    const kind = t?.kind ?? (a.plane ? "screen" : "lamp");
    placed.push({ key: `t:${id}`, label: t?.label ?? nameWords(id), kind, at: a.at, size: a.plane?.size ?? null, normal: a.plane?.normal ?? null });
  }
  const unplaced = things.filter((t) => !space.appliances[t.id]?.at);
  return { placed, unplaced };
}

/** The width of the element the returned ref is put on (it may mount later), followed as it resizes. */
export function useWidth(): [(el: HTMLDivElement | null) => void, number] {
  const [w, setW] = useState(340);
  const ro = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    ro.current?.disconnect();
    ro.current = null;
    if (!el) return;
    const set = () => setW(Math.max(240, Math.round(el.clientWidth)));
    set();
    ro.current = new ResizeObserver(set);
    ro.current.observe(el);
  }, []);
  useEffect(() => () => ro.current?.disconnect(), []);
  return [ref, w];
}

// -- the picture --------------------------------------------------------------------------------------------------------

function sub(a: [number, number], b: [number, number]): [number, number] {
  return [a[0] - b[0], a[1] - b[1]];
}

function unit2(v: [number, number]): [number, number] {
  const l = Math.hypot(v[0], v[1]) || 1;
  return [v[0] / l, v[1] / l];
}

export function RoomDrawing({
  fr,
  items,
  live,
  selected,
  focus,
  svgRef,
  onItemDown,
  onPointerMove,
  onPointerUp,
  className,
}: {
  fr: PlanFrame;
  items: DrawItem[];
  live: LiveView | null;
  selected?: string | null;
  /** Preview: this light's beam stands out, the rest are faint. */
  focus?: string | null;
  svgRef?: Ref<SVGSVGElement>;
  onItemDown?: (key: string, part: "plan" | "side", e: ReactPointerEvent<SVGElement>) => void;
  onPointerMove?: (e: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerUp?: (e: ReactPointerEvent<SVGSVGElement>) => void;
  className?: string;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const arrow = `arrow-${uid}`;
  const [X, Y, Z] = fr.size;
  const sideTop = fr.h + GAP;
  const total = sideTop + fr.sideH;
  const room = [toPlan(fr, 0, 0), toPlan(fr, X, 0), toPlan(fr, X, Y), toPlan(fr, 0, Y)];
  const roomPts = room.map((p) => p.join(",")).join(" ");
  const left = fr.pad;
  const right = fr.w - fr.pad;
  const top = fr.pad;
  const bottom = fr.h - fr.pad;
  const side = (x: number, y: number, z: number): [number, number] => {
    const [px, py] = toSide(fr, x, y, z);
    return [px, py + sideTop];
  };
  const letter: Record<"top" | "right" | "bottom" | "left", [number, number]> = {
    top: [(left + right) / 2, top - 8],
    bottom: [(left + right) / 2, bottom + 17],
    left: [left - 12, (top + bottom) / 2 + 5],
    right: [right + 12, (top + bottom) / 2 + 5],
  };
  const down = (key: string, part: "plan" | "side") => (e: ReactPointerEvent<SVGElement>) => onItemDown?.(key, part, e);
  const flow = live?.flow ?? null;
  const fc: [number, number] = flow?.at_xyz ? toPlan(fr, flow.at_xyz[0], flow.at_xyz[1]) : toPlan(fr, X / 2, Y / 2);

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${fr.w} ${total}`}
      width="100%"
      className={cn("block select-none", className)}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      role="img"
      aria-label="The room from above, and from the side"
    >
      <defs>
        <marker id={arrow} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="fill-amber-600" />
        </marker>
      </defs>
      {/* the plan */}
      <polygon points={roomPts} className="fill-white stroke-stone-300" strokeWidth={1.5} />
      <line x1={left} y1={top} x2={right} y2={top} className="stroke-stone-600" strokeWidth={5} strokeLinecap="round" />
      {FRONTS.map((w) => {
        const where = wallSide(fr, w);
        const [x, y] = letter[where];
        return (
          <text key={w} x={x} y={y} textAnchor="middle" className={cn("fill-stone-500 text-[12px]", w === fr.front && "fill-stone-800 font-semibold")}>
            {w[0].toUpperCase()}
          </text>
        );
      })}
      {items
        .filter((it) => it.kind === "area" && it.size)
        .map((it) => {
          const [w, d] = it.size as [number, number];
          const pts = [
            [it.at[0] - w / 2, it.at[1] - d / 2],
            [it.at[0] + w / 2, it.at[1] - d / 2],
            [it.at[0] + w / 2, it.at[1] + d / 2],
            [it.at[0] - w / 2, it.at[1] + d / 2],
          ].map(([x, y]) => toPlan(fr, x, y).join(","));
          const [lx, ly] = toPlan(fr, it.at[0], it.at[1]);
          return (
            <g key={it.key} onPointerDown={down(it.key, "plan")} className={cn("cursor-grab", onItemDown && "touch-none")}>
              <polygon points={pts.join(" ")} className={cn("fill-teal-100/60 stroke-teal-300", selected === it.key && "stroke-teal-700")} strokeWidth={selected === it.key ? 2 : 1} strokeDasharray="4 3" />
              <text x={lx} y={ly + 4} textAnchor="middle" className="fill-teal-900 text-[11px]">
                {it.label}
              </text>
            </g>
          );
        })}
      {/* beams under the things */}
      {Object.entries(live?.fixtures ?? {}).map(([id, f]) => {
        if (!f.at || !f.hit) return null;
        const a = toPlan(fr, f.at[0], f.at[1]);
        const b = toPlan(fr, f.hit[0], f.hit[1]);
        const sa = side(f.at[0], f.at[1], f.at[2]);
        const sb = side(f.hit[0], f.hit[1], f.hit[2]);
        const faint = !!focus && focus !== id;
        const cls = cn(faint ? "stroke-stone-300" : "stroke-amber-500", "transition-[x1,y1,x2,y2]");
        return (
          <g key={`beam-${id}`} opacity={faint ? 0.6 : Math.max(0.35, f.intensity)}>
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className={cls} strokeWidth={faint ? 1.5 : 3} strokeLinecap="round" />
            <circle cx={b[0]} cy={b[1]} r={faint ? 3 : 6} className={faint ? "fill-stone-300" : "fill-amber-300/80"} />
            <line x1={sa[0]} y1={sa[1]} x2={sb[0]} y2={sb[1]} className={cls} strokeWidth={faint ? 1.5 : 3} strokeLinecap="round" />
          </g>
        );
      })}
      {items
        .filter((it) => it.kind !== "area")
        .map((it) => {
          const [px, py] = toPlan(fr, it.at[0], it.at[1]);
          const sel = selected === it.key || focus === it.key.slice(2);
          const liveF = it.key.startsWith("t:") ? live?.fixtures[it.key.slice(2)] : undefined;
          let shape: JSX.Element;
          if (it.kind === "screen") {
            const n = it.normal ?? [0, -1, 0];
            const along = unit2(sub(toPlan(fr, it.at[0] - n[1], it.at[1] + n[0]), [px, py]));
            const half = it.size ? (it.size[0] / 2) * fr.s : 14;
            shape = (
              <line
                x1={px - along[0] * half}
                y1={py - along[1] * half}
                x2={px + along[0] * half}
                y2={py + along[1] * half}
                className={sel ? "stroke-teal-700" : "stroke-sky-700"}
                strokeWidth={7}
                strokeLinecap="round"
              />
            );
          } else if (it.kind === "place") {
            shape = (
              <g>
                <circle cx={px} cy={py} r={6} className={sel ? "fill-teal-700" : "fill-rose-500"} />
                <circle cx={px} cy={py} r={2} className="fill-white" />
              </g>
            );
          } else {
            const hex = liveF?.hex && liveF.intensity > 0.02 ? liveF.hex : null;
            shape = (
              <circle
                cx={px}
                cy={py}
                r={it.kind === "mover" ? 8 : 6}
                fill={hex ?? undefined}
                className={cn(!hex && "fill-stone-200", sel ? "stroke-teal-700" : "stroke-stone-600")}
                strokeWidth={it.kind === "mover" ? 3 : 1.5}
              />
            );
          }
          const [sx, sy] = side(it.at[0], it.at[1], it.at[2]);
          const sh = it.kind === "screen" && it.size ? (it.size[1] / 2) * fr.s : 0;
          return (
            <g key={it.key}>
              <g onPointerDown={down(it.key, "plan")} className={cn("cursor-grab", onItemDown && "touch-none")}>
                <circle cx={px} cy={py} r={18} className="fill-transparent" />
                {shape}
                <text x={px} y={py - 12} textAnchor="middle" className="fill-stone-700 text-[11px]" style={{ paintOrder: "stroke" }} stroke="white" strokeWidth={3}>
                  {it.label}
                </text>
              </g>
              <g onPointerDown={down(it.key, "side")} className={cn("cursor-ns-resize", onItemDown && "touch-none")}>
                <circle cx={sx} cy={sy} r={14} className="fill-transparent" />
                {it.kind === "screen" ? (
                  <line x1={sx} y1={sy - (sh || 8)} x2={sx} y2={sy + (sh || 8)} className={sel ? "stroke-teal-700" : "stroke-sky-700"} strokeWidth={6} strokeLinecap="round" />
                ) : (
                  <circle cx={sx} cy={sy} r={it.kind === "place" ? 4 : 5} className={sel ? "fill-teal-700" : it.kind === "place" ? "fill-rose-500" : "fill-stone-500"} />
                )}
              </g>
            </g>
          );
        })}
      {/* the flow */}
      {flow && <FlowArrow fr={fr} dir={flow.dir} c={fc} marker={arrow} sideTop={sideTop} />}
      {/* the side strip */}
      <text x={left} y={sideTop - 9} className="fill-stone-500 text-[11px]">
        From the side (heights)
      </text>
      <line x1={left} y1={side(0, 0, 0)[1]} x2={right} y2={side(0, 0, 0)[1]} className="stroke-stone-400" strokeWidth={2} />
      <line x1={left} y1={side(0, 0, Z)[1]} x2={right} y2={side(0, 0, Z)[1]} className="stroke-stone-300" strokeWidth={1} strokeDasharray="5 4" />
    </svg>
  );
}

function FlowArrow({ fr, dir, c, marker, sideTop }: { fr: PlanFrame; dir: string; c: [number, number]; marker: string; sideTop: number }) {
  const m = `url(#${marker})`;
  const cls = "stroke-amber-600 fill-none";
  const L = Math.min(48, (fr.w - 2 * fr.pad) / 4);
  const seg = (a: [number, number], b: [number, number], k: string) => (
    <line key={k} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className={cls} strokeWidth={3} markerEnd={m} />
  );
  switch (dir) {
    case "right":
    case "left": {
      const s = dir === "right" ? 1 : -1;
      return seg([c[0] - s * L, c[1]], [c[0] + s * L, c[1]], "lr");
    }
    case "cw":
    case "ccw": {
      const r = L * 0.8;
      const end = dir === "cw" ? [c[0] - r, c[1]] : [c[0] + r, c[1]];
      return <path d={`M ${c[0]} ${c[1] - r} A ${r} ${r} 0 1 ${dir === "cw" ? 1 : 0} ${end[0]} ${end[1]}`} className={cls} strokeWidth={3} markerEnd={m} />;
    }
    case "outward":
    case "inward": {
      const out = dir === "outward";
      return (
        <g>
          {[[1, 0], [0, 1], [-1, 0], [0, -1]].map(([dx, dy], i) => {
            const a: [number, number] = [c[0] + dx * 12, c[1] + dy * 12];
            const b: [number, number] = [c[0] + dx * L, c[1] + dy * L];
            return out ? seg(a, b, `o${i}`) : seg(b, a, `i${i}`);
          })}
        </g>
      );
    }
    case "towards":
    case "away": {
      const corners: [number, number][] = [
        [fr.pad + 16, fr.pad + 16],
        [fr.w - fr.pad - 16, fr.pad + 16],
        [fr.w - fr.pad - 16, fr.h - fr.pad - 16],
        [fr.pad + 16, fr.h - fr.pad - 16],
      ];
      return (
        <g>
          {corners.map((k, i) => {
            const d = unit2(sub(c, k));
            const a: [number, number] = [k[0] + d[0] * 6, k[1] + d[1] * 6];
            const b: [number, number] = [k[0] + d[0] * Math.min(L, Math.hypot(c[0] - k[0], c[1] - k[1]) - 14), k[1] + d[1] * Math.min(L, Math.hypot(c[0] - k[0], c[1] - k[1]) - 14)];
            return dir === "towards" ? seg(a, b, `t${i}`) : seg(b, a, `a${i}`);
          })}
        </g>
      );
    }
    case "up":
    case "down": {
      const x = fr.w - fr.pad - 14;
      const lo = sideTop + fr.sideH - fr.pad - 4;
      const hi = sideTop + fr.pad + 4;
      return dir === "up" ? seg([x, lo], [x, hi], "u") : seg([x, hi], [x, lo], "d");
    }
    default:
      return null;
  }
}

// -- the tab ------------------------------------------------------------------------------------------------------------

const fmt = (x: number) => (Math.round(x * 100) / 100).toFixed(2);
const metres = (s: string) => {
  const v = parseFloat(s.replace(",", "."));
  return Number.isFinite(v) && v > 0 ? v : null;
};

export function RoomMap({
  api,
  things,
  selectedId,
}: {
  api: LightsApi;
  /** Every light and screen the rig knows (useRig().all). */
  things: Placeable[];
  /** A light to select on arrival ("Put it on the Room map"). */
  selectedId?: string | null;
}) {
  const sp = useSpace(api);
  const live = useLive(api, true);
  const [box, width] = useWidth();
  const [sel, setSel] = useState<string | null>(selectedId ? `t:${selectedId}` : null);
  const [drag, setDrag] = useState<{ key: string; part: "plan" | "side"; at: Vec3; normal?: Vec3 } | null>(null);
  const [editRoom, setEditRoom] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svg = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (selectedId) setSel(`t:${selectedId}`);
  }, [selectedId]);

  const space = sp.space;
  const size = space?.size ?? null;
  const fr = useMemo(() => (size && space ? planFrame(size, space.front, width) : null), [size, space, width]);
  const { placed, unplaced } = useMemo(() => (space ? itemsFor(space, things) : { placed: [], unplaced: [] }), [space, things]);
  const items = drag ? placed.map((it) => (it.key === drag.key ? { ...it, at: drag.at, normal: drag.normal ?? it.normal } : it)) : placed;
  const selected = items.find((it) => it.key === sel) ?? null;
  const selectedThing = sel?.startsWith("t:") ? things.find((t) => t.id === sel.slice(2)) ?? null : null;
  const unplacedSel = selectedThing && !selected ? selectedThing : null;

  const run = async (fn: () => Promise<unknown>, after: "reload" | ((ans: unknown) => void) = "reload") => {
    setBusy(true);
    setNote(null);
    try {
      const ans = await fn();
      if (after === "reload") sp.reload();
      else after(ans);
    } catch (e) {
      setNote(explainLightsError(e));
      sp.reload();
    } finally {
      setBusy(false);
    }
  };

  const commit = (it: DrawItem, at: Vec3, normal?: Vec3 | null) => {
    const name = it.key.slice(2);
    const r3 = at.map((v) => Math.round(v * 100) / 100) as Vec3;
    if (it.kind === "place") return run(() => api.setPlace(name, { at: r3, label: it.label }));
    if (it.kind === "area") {
      const a = space?.regions.find((g) => g.name === name);
      return run(() => api.setArea(name, { surface: a?.surface ?? "floor", centre: r3, size: a?.size ?? [1, 1], label: it.label }));
    }
    const body: Record<string, unknown> = { at: r3 };
    if (it.kind === "screen" && normal) body.plane = { normal, up: [0, 0, 1], ...(it.size ? { size: it.size } : {}) };
    return run(() => api.place(name, body));
  };

  const roomXY = (e: { clientX: number; clientY: number }): [number, number] | null => {
    const el = svg.current;
    if (!el || !fr) return null;
    const rect = el.getBoundingClientRect();
    const k = fr.w / rect.width;
    return [(e.clientX - rect.left) * k, (e.clientY - rect.top) * k];
  };

  const onItemDown = (key: string, part: "plan" | "side", e: ReactPointerEvent<SVGElement>) => {
    e.stopPropagation();
    setSel(key);
    const it = placed.find((p) => p.key === key);
    if (!it) return;
    svg.current?.setPointerCapture(e.pointerId);
    setDrag({ key, part, at: it.at, normal: it.normal ?? undefined });
  };

  const onMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag || !fr || !size) return;
    const p = roomXY(e);
    if (!p) return;
    const it = placed.find((x) => x.key === drag.key);
    if (!it) return;
    if (drag.part === "side") {
      const z = Math.min(size[2], Math.max(0, fromSide(fr, p[1] - (fr.h + GAP))));
      setDrag({ ...drag, at: [drag.at[0], drag.at[1], Math.round(z * 100) / 100] });
      return;
    }
    let [x, y] = clampToRoom(size, ...fromPlan(fr, p[0], p[1]));
    let normal = drag.normal;
    if (it.kind === "screen") {
      const s = snapToWall(size, x, y);
      x = s.x;
      y = s.y;
      normal = s.normal;
    }
    setDrag({ ...drag, at: [x, y, drag.at[2]], normal });
  };

  const onUp = () => {
    if (!drag) return;
    const it = placed.find((x) => x.key === drag.key);
    const moved = it && (Math.abs(it.at[0] - drag.at[0]) > 0.005 || Math.abs(it.at[1] - drag.at[1]) > 0.005 || Math.abs(it.at[2] - drag.at[2]) > 0.005);
    if (it && moved) void commit(it, drag.at, drag.normal).finally(() => setDrag(null));
    else setDrag(null);
  };

  /** A thing not on the map yet goes in the middle (a screen on the front wall) to be dragged where it is. */
  const putOnMap = (t: Placeable) => {
    if (!size || !space) return;
    setSel(`t:${t.id}`);
    if (t.kind === "screen") {
      const front = snapToWall(size, ...frontMiddle(size, space.front));
      void run(() => api.place(t.id, { at: [front.x, front.y, size[2] / 2], plane: { normal: front.normal, up: [0, 0, 1] } }));
      return;
    }
    const body: Record<string, unknown> = { at: [size[0] / 2, size[1] / 2, t.kind === "mover" ? size[2] : size[2] / 2] };
    if (t.kind === "mover") body.mount = "ceiling";
    void run(() => api.place(t.id, body));
  };

  const addPlace = (label: string) => {
    if (!size || !space) return;
    const name = placeId(label);
    if (!name) return;
    setSel(`p:${name}`);
    if (space.points.some((p) => p.name === name) || space.regions.some((a) => a.name === name)) return;
    void run(() => api.setPlace(name, { at: [size[0] / 2, size[1] / 2, 0], label: label.trim() }));
  };

  if (!space) {
    return sp.error ? (
      <Notice tone="warn" className="text-base">
        {sp.error}
      </Notice>
    ) : (
      <p className="flex items-center gap-2 py-3 text-base text-muted-foreground">
        <Spinner /> Reading the room…
      </p>
    );
  }

  if (!size || editRoom) {
    return (
      <div className="space-y-3">
        {space.error && !size && (
          <Notice tone="warn" className="text-[15px]">
            {space.error}
          </Notice>
        )}
        <RoomForm
          space={space}
          onCancel={size ? () => setEditRoom(false) : undefined}
          onSave={async (body) => {
            await run(
              () => api.setRoom(body),
              (ans) => sp.set(parseSpace(ans)),
            );
            setEditRoom(false);
          }}
        />
        {note && <Notice tone="bad" className="text-[15px]">{note}</Notice>}
      </div>
    );
  }

  const flow = live?.flow ?? null;
  const atLabel = flow?.at ? space.targets.find((t) => t.name === flow.at)?.label ?? nameWords(flow.at) : null;
  return (
    <div className="space-y-3" data-testid="room-map">
      <div className="flex flex-wrap items-center gap-x-2 text-[15px] text-muted-foreground">
        <span>
          {fmt(size[0])} × {fmt(size[1])} m, {fmt(size[2])} m high · people face {space.front}
        </span>
        <Button variant="ghost" className="h-9 rounded-xl px-2 text-[15px] text-teal-800" onClick={() => setEditRoom(true)}>
          Change
        </Button>
        {busy && <Spinner />}
      </div>

      <div ref={box} className="rounded-2xl border border-stone-200 bg-stone-50/60 p-1">
        {fr && <RoomDrawing fr={fr} items={items} live={live} selected={sel} svgRef={svg} onItemDown={onItemDown} onPointerMove={onMove} onPointerUp={onUp} />}
      </div>
      <p className="text-[15px] font-medium" data-testid="flow-words">
        {flowWords(flow?.dir, atLabel)}
      </p>

      {note && <Notice tone="bad" className="text-[15px]">{note}</Notice>}

      {selected && (
        <SelectedBar
          key={selected.key}
          item={selected}
          space={space}
          mount={selected.key.startsWith("t:") ? space.appliances[selected.key.slice(2)]?.mount ?? null : null}
          onClose={() => setSel(null)}
          onHeight={(z) => void commit(selected, [selected.at[0], selected.at[1], z], selected.normal)}
          onMount={(m) => void run(() => api.place(selected.key.slice(2), { mount: m }))}
          onPicture={(wh) => void run(() => api.place(selected.key.slice(2), { plane: { normal: selected.normal ?? [0, -1, 0], up: [0, 0, 1], size: wh } }))}
          onAreaSize={(wd) => {
            const a = space.regions.find((g) => g.name === selected.key.slice(2));
            if (a) void run(() => api.setArea(a.name, { surface: a.surface, centre: a.centre, size: wd, label: a.label }));
          }}
          onMakeArea={(wd) => {
            const name = selected.key.slice(2);
            void run(async () => {
              await api.setArea(name, { surface: "floor", centre: [selected.at[0], selected.at[1], 0], size: wd, label: selected.label });
              await api.removePlace(name);
            });
            setSel(`a:${name}`);
          }}
          onRemove={() => {
            const name = selected.key.slice(2);
            setSel(null);
            if (selected.kind === "place") void run(() => api.removePlace(name));
            else if (selected.kind === "area") void run(() => api.removeArea(name));
            else void run(() => api.unplace(name));
          }}
        />
      )}
      {unplacedSel && (
        <div className="rounded-2xl border border-stone-200 bg-white p-3 text-[15px]">
          <p className="font-medium">{unplacedSel.label} isn't on the map yet.</p>
          <Button className={cn(TAP_BUTTON_CLASS, "mt-2")} onClick={() => putOnMap(unplacedSel)}>
            <MapPin aria-hidden /> Put it on the map
          </Button>
        </div>
      )}

      {unplaced.length > 0 && (
        <section className="space-y-1.5">
          <h3 className="text-[15px] font-semibold">Not on the map yet</h3>
          <p className="text-sm text-muted-foreground">Tap one: it goes in the middle (a screen on the front wall), then drag it where it is.</p>
          <div className="flex flex-wrap gap-2">
            {unplaced.map((t) => (
              <button key={t.id} type="button" className={cn(CHIP, CHIP_OFF)} onClick={() => putOnMap(t)}>
                <Plus className="h-4 w-4" aria-hidden /> {t.label}
              </button>
            ))}
          </div>
        </section>
      )}

      <AddPlace onAdd={addPlace} />

      {space.problems.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-muted-foreground">
          {space.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The middle of the wall people face (x, y). */
function frontMiddle(size: Vec3, front: Front): [number, number] {
  if (front === "north") return [size[0] / 2, size[1]];
  if (front === "south") return [size[0] / 2, 0];
  if (front === "east") return [size[0], size[1] / 2];
  return [0, size[1] / 2];
}

function AddPlace({ onAdd }: { onAdd: (label: string) => void }) {
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  return (
    <section className="space-y-1.5">
      <h3 className="text-[15px] font-semibold">Add a place</h3>
      <p className="text-sm text-muted-foreground">Somewhere lights can point at. It drops in the middle; drag it where it is.</p>
      <div className="flex flex-wrap gap-2">
        {PLACE_SUGGESTIONS.map((p) => (
          <button key={p} type="button" className={cn(CHIP, CHIP_OFF)} onClick={() => onAdd(p)}>
            <Plus className="h-4 w-4" aria-hidden /> {p}
          </button>
        ))}
        <button type="button" className={cn(CHIP, typing ? CHIP_ON : CHIP_OFF)} onClick={() => setTyping((t) => !t)}>
          Somewhere else…
        </button>
      </div>
      {typing && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            onAdd(text);
            setText("");
            setTyping(false);
          }}
        >
          <Input aria-label="What the place is called" value={text} onChange={(e) => setText(e.target.value)} className="h-11 rounded-xl bg-white text-base" autoFocus />
          <Button type="submit" className={TAP_BUTTON_CLASS} disabled={!text.trim()}>
            Add
          </Button>
        </form>
      )}
    </section>
  );
}

function Stepper({ label, value, onChange, step, min, max }: { label: string; value: number; onChange: (v: number) => void; step: number; min: number; max: number }) {
  const set = (v: number) => onChange(Math.round(Math.min(max, Math.max(min, v)) * 100) / 100);
  return (
    <div className="flex items-center gap-2 text-[15px]">
      <span className="min-w-0 flex-1">
        {label} <span className="font-medium tabular-nums">{fmt(value)} m</span>
      </span>
      <Button variant="outline" size="icon" className="h-11 w-11 rounded-xl bg-white" aria-label={`${label} lower`} onClick={() => set(value - step)}>
        <Minus aria-hidden />
      </Button>
      <Button variant="outline" size="icon" className="h-11 w-11 rounded-xl bg-white" aria-label={`${label} higher`} onClick={() => set(value + step)}>
        <Plus aria-hidden />
      </Button>
    </div>
  );
}

function TwoSizes({ labels, value, onSave, action }: { labels: [string, string]; value: [number, number] | null; onSave: (wh: [number, number]) => void; action: string }) {
  const [a, setA] = useState(value ? fmt(value[0]) : "");
  const [b, setB] = useState(value ? fmt(value[1]) : "");
  const wa = metres(a);
  const wb = metres(b);
  return (
    <div className="flex flex-wrap items-end gap-2">
      {[
        [labels[0], a, setA],
        [labels[1], b, setB],
      ].map(([l, v, set]) => (
        <label key={l as string} className="flex flex-col text-sm text-muted-foreground">
          {l as string}
          <Input
            value={v as string}
            onChange={(e) => (set as (s: string) => void)(e.target.value)}
            inputMode="decimal"
            className="h-11 w-24 rounded-xl bg-white text-base"
            aria-label={`${l as string} in metres`}
          />
        </label>
      ))}
      <Button variant="outline" className="h-11 rounded-xl bg-white" disabled={wa === null || wb === null} onClick={() => wa !== null && wb !== null && onSave([wa, wb])}>
        {action}
      </Button>
    </div>
  );
}

function SelectedBar({
  item,
  space,
  mount,
  onClose,
  onHeight,
  onMount,
  onPicture,
  onAreaSize,
  onMakeArea,
  onRemove,
}: {
  item: DrawItem;
  space: RoomSpace;
  mount: string | null;
  onClose: () => void;
  onHeight: (z: number) => void;
  onMount: (m: string) => void;
  onPicture: (wh: [number, number]) => void;
  onAreaSize: (wd: [number, number]) => void;
  onMakeArea: (wd: [number, number]) => void;
  onRemove: () => void;
}) {
  const Z = space.size?.[2] ?? 3;
  const kindWords = { screen: "Screen", mover: "Moving light", lamp: "Light", place: "Place", area: "Area" }[item.kind];
  return (
    <div className="sticky bottom-2 z-10 space-y-3 rounded-2xl border border-stone-200 bg-white p-3 shadow-lg" data-testid="map-selected">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[17px] font-semibold leading-snug">{item.label}</p>
          <p className="text-sm text-muted-foreground">{kindWords} · drag it on the map</p>
        </div>
        <Button variant="ghost" size="icon" className="h-10 w-10 rounded-full" aria-label="Done with this one" onClick={onClose}>
          <X aria-hidden />
        </Button>
      </div>
      {item.kind !== "area" && <Stepper label="Height" value={item.at[2]} onChange={onHeight} step={HEIGHT_STEP_M} min={0} max={Z} />}
      {item.kind === "mover" && (
        <div role="radiogroup" aria-label="How it hangs" className="flex flex-wrap gap-2">
          {MOUNTS.map(([m, words]) => (
            <button key={m} type="button" role="radio" aria-checked={mount === m} className={cn(CHIP, mount === m ? CHIP_ON : CHIP_OFF)} onClick={() => onMount(m)}>
              {words}
            </button>
          ))}
        </div>
      )}
      {item.kind === "screen" && (
        <div className="space-y-1">
          <p className="text-[15px]">Picture size (metres)</p>
          <TwoSizes labels={["Width", "Height"]} value={item.size ?? null} onSave={onPicture} action="Keep" />
        </div>
      )}
      {item.kind === "area" && (
        <div className="space-y-1">
          <p className="text-[15px]">Its size (metres)</p>
          <TwoSizes labels={["Across", "Deep"]} value={item.size ?? null} onSave={onAreaSize} action="Keep" />
        </div>
      )}
      {item.kind === "place" && (
        <details className="text-[15px]">
          <summary className="min-h-11 cursor-pointer py-2 text-teal-800">Make it an area (a size on the floor)</summary>
          <TwoSizes labels={["Across", "Deep"]} value={null} onSave={onMakeArea} action="Make it an area" />
        </details>
      )}
      <Button variant="ghost" className="h-11 rounded-xl px-3 text-[15px] text-rose-700 hover:bg-rose-50" onClick={onRemove}>
        {item.kind === "place" ? "Remove this place" : item.kind === "area" ? "Remove this area" : "Take it off the map"}
      </Button>
    </div>
  );
}

function RoomForm({
  space,
  onSave,
  onCancel,
}: {
  space: RoomSpace;
  onSave: (body: { size?: [number, number, number]; front?: string }) => Promise<void>;
  onCancel?: () => void;
}) {
  const s = space.size;
  const [w, setW] = useState(s ? fmt(s[0]) : "");
  const [l, setL] = useState(s ? fmt(s[1]) : "");
  const [h, setH] = useState(s ? fmt(s[2]) : "");
  const [front, setFront] = useState<Front>(space.front);
  const [saving, setSaving] = useState(false);
  const vals = [metres(w), metres(l), metres(h)];
  const ok = vals.every((v) => v !== null);
  const bump = (v: string, set: (s: string) => void, d: number) => {
    const x = metres(v);
    if (x !== null) set(fmt(Math.max(0.1, x + d)));
  };
  const fields: [string, string, (s: string) => void][] = [
    ["Width, east to west", w, setW],
    ["Length, north to south", l, setL],
    ["Height, floor to ceiling", h, setH],
  ];
  return (
    <section className="space-y-4 rounded-2xl border border-stone-200 bg-white p-4" data-testid="room-form">
      <div className="space-y-2">
        <h3 className="text-[17px] font-semibold">How big is the room?</h3>
        <p className="text-sm text-muted-foreground">In metres, as measured.</p>
        {fields.map(([label, v, set]) => (
          <div key={label} className="flex items-center gap-2">
            <label className="min-w-0 flex-1 text-[15px]">
              {label}
              <Input value={v} onChange={(e) => set(e.target.value)} inputMode="decimal" className="mt-1 h-11 w-28 rounded-xl bg-white text-base" />
            </label>
            <Button variant="outline" size="icon" className="h-11 w-11 self-end rounded-xl bg-white" aria-label={`${label}: less`} disabled={metres(v) === null} onClick={() => bump(v, set, -0.1)}>
              <Minus aria-hidden />
            </Button>
            <Button variant="outline" size="icon" className="h-11 w-11 self-end rounded-xl bg-white" aria-label={`${label}: more`} disabled={metres(v) === null} onClick={() => bump(v, set, 0.1)}>
              <Plus aria-hidden />
            </Button>
          </div>
        ))}
      </div>
      <div className="space-y-2">
        <h3 className="text-[17px] font-semibold">Which wall do people face?</h3>
        <div role="radiogroup" aria-label="Which wall do people face" className="flex flex-wrap gap-2">
          {FRONTS.map((f) => (
            <button key={f} type="button" role="radio" aria-checked={front === f} className={cn(CHIP, "w-14 justify-center", front === f ? CHIP_ON : CHIP_OFF)} onClick={() => setFront(f)}>
              {f[0].toUpperCase()}
            </button>
          ))}
        </div>
        <p className="text-sm text-muted-foreground">Left and right are as people see them facing it.</p>
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        {onCancel && (
          <Button variant="outline" className={cn(TAP_BUTTON_CLASS, "bg-white")} onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button
          className={TAP_BUTTON_CLASS}
          disabled={!ok || saving}
          onClick={async () => {
            const size = vals as [number, number, number];
            const body: { size?: [number, number, number]; front?: string } = {};
            if (!s || size.some((v, i) => Math.abs(v - s[i]) > 0.001)) body.size = size;
            if (!body.size && !body.front) {
              onCancel?.();
              return;
            }
            // Only what changed: the front set in space.yaml by hand can't be changed here, and that's no reason
            // not to keep the size.
            if (front !== space.front) body.front = front;
            setSaving(true);
            try {
              await onSave(body);
            } finally {
              setSaving(false);
            }
          }}
          data-testid="room-save"
        >
          {saving && <Spinner />} Save the room
        </Button>
      </div>
    </section>
  );
}
