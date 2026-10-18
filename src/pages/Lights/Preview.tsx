// Move it's preview (docs/LIGHTS.md §8.3): a small plan and side view of where this light's beam lands (GET /rig/live,
// 4 Hz while open), the places it can be pointed at, its pattern and beam size, and the flow the show is following.
// A light that hasn't learnt where it hangs says so: Point it at 3 places so the map can show where it shines.

import { useMemo } from "react";
import { Notice } from "@/pages/Room/bits";
import { flowWords, nameWords, planFrame, poseWords, type LiveView, type RoomSpace } from "./lib";
import { itemsFor, RoomDrawing, useWidth } from "./RoomMap";

export function Preview({ fid, space, live }: { fid: string; space: RoomSpace | null; live: LiveView | null }) {
  const [box, width] = useWidth();
  const size = space?.size ?? null;
  const fr = useMemo(() => (size && space ? planFrame(size, space.front, width, 18) : null), [size, space, width]);
  const items = useMemo(() => {
    if (!space) return [];
    // the places, areas and screens, and this light; other lights only as faint beams
    return itemsFor(space, []).placed.filter((it) => !it.key.startsWith("t:") || it.key === `t:${fid}` || it.kind === "screen");
  }, [space, fid]);
  const me = live?.fixtures[fid];
  const placed = space?.appliances[fid];
  const flow = live?.flow ?? null;
  const atLabel = flow?.at ? space?.targets.find((t) => t.name === flow.at)?.label ?? nameWords(flow.at) : null;
  const facts = [
    me?.gobo ? `Pattern: ${me.gobo}` : null,
    me?.prism ? "Split" : null,
    typeof me?.zoom_deg === "number" ? `Beam ${Math.round(me.zoom_deg)}°` : null,
    flowWords(flow?.dir, atLabel),
  ].filter(Boolean);
  return (
    <div className="space-y-2" data-testid="move-preview">
      {!size ? (
        <p className="text-[15px] text-muted-foreground">Put the room on the Room map to see where it shines.</p>
      ) : (
        <div ref={box} className="rounded-xl border border-stone-200 bg-stone-50/60 p-1">
          {fr && <RoomDrawing fr={fr} items={items} live={live} focus={fid} />}
        </div>
      )}
      {size && !placed?.pose && <Notice tone="info" className="text-[15px]">{poseWords(null, placed?.teach.length ?? 0)}</Notice>}
      {facts.length > 0 && <p className="text-[15px] text-muted-foreground">{facts.join(" · ")}</p>}
    </div>
  );
}
