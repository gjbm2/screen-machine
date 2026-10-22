// "PC busy" (owner, 22 Oct: "may be useful to have some sort of indication of that loading issue on the rooms page"):
// other work on PC22 (builds, renders, analysis) can slow the live screens. Every 10 s while the page is visible it
// reads the renderer's host load and the live screens' frame rates (GET /renderer/api/diag and /api/state) and says,
// in one sentence, when the PC is overloaded and what that costs the screens. Read-only: it changes nothing.

import { useEffect, useState } from "react";
import type { RoomApi } from "./api";
import { friendlyName } from "./words";

const POLL_MS = 10_000;
/** The 1-minute load at or over this share of the cores reads as busy (it is already a smoothed average). */
const LOAD_BUSY = 0.85;
/** Memory left under this share of the total reads as nearly full; so does swap over SWAP_HIGH used while memory left
 *  is under MEM_TIGHT (swap stays used long after the pressure has gone, so on its own it would hold the badge up). */
const MEM_LOW = 0.1;
const MEM_TIGHT = 0.25;
const SWAP_HIGH = 0.75;

export interface PcLoad {
  busy: boolean;
  /** One plain sentence: what is busy and what it costs the screens. */
  sentence: string;
}

const theList = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

export function usePcLoad(api: RoomApi, enabled: boolean): PcLoad | null {
  const [load, setLoad] = useState<PcLoad | null>(null);
  useEffect(() => {
    if (!enabled) {
      setLoad(null);
      return;
    }
    let alive = true;
    let ctl: AbortController | null = null;
    const read = async () => {
      if (document.visibilityState === "hidden") return;
      ctl?.abort();
      ctl = new AbortController();
      try {
        const { diag, state } = await api.pcLoad(ctl.signal);
        if (!alive) return;
        const h = diag?.host ?? {};
        const cpus = h.cpus && h.cpus > 0 ? h.cpus : null;
        const load1 = h.load?.[0] ?? null;
        const cpuBusy = cpus !== null && load1 !== null && load1 >= LOAD_BUSY * cpus;
        const left = h.mem_total_mb && h.mem_available_mb != null ? h.mem_available_mb / h.mem_total_mb : null;
        const swapped = h.swap_total_mb && h.swap_used_mb != null ? h.swap_used_mb / h.swap_total_mb : null;
        const memLow = left !== null && (left < MEM_LOW || (swapped !== null && swapped > SWAP_HIGH && left < MEM_TIGHT));
        // Screens slowed for the host's sake (the renderer's own words: "computer busy", also "in step with <screen>
        // (computer busy)"); a GPU-heavy scene slowing itself is the scene's cost, not the PC's load.
        const slowed = (state?.sessions ?? []).filter(
          (s) => s.state === "live" && (s.degrade?.level ?? 0) > 0 && /computer busy/.test(s.degrade?.why ?? ""),
        );
        const busy = cpuBusy || !!memLow || slowed.length > 0;
        if (!busy) {
          setLoad({ busy: false, sentence: "" });
          return;
        }
        const what = [
          cpuBusy || slowed.length ? `other work is using most of the PC${load1 !== null && cpus ? ` (load ${Math.round(load1)} on ${cpus} cores)` : ""}` : null,
          memLow ? "its memory is nearly full" : null,
        ].filter(Boolean).join(" and ");
        let costs = "";
        if (slowed.length) {
          const names = theList(slowed.map((s) => friendlyName(s.screen ?? "")));
          const s0 = slowed[0].degrade ?? {};
          const fps = s0.out_fps ?? null;
          const scene = fps && s0.scene_div && s0.scene_div > 1 ? Math.round(fps / s0.scene_div) : null;
          costs = ` ${names} ${slowed.length > 1 ? "are" : "is"} running at ${fps ?? "a lower"} fps${scene ? `, the picture changing ${scene} times a second` : ""}, to keep up.`;
        } else costs = " The live screens may slow down.";
        const first = what.charAt(0).toUpperCase() + what.slice(1);
        setLoad({ busy: true, sentence: `${first}.${costs}` });
      } catch {
        if (alive) setLoad(null);                 // can't tell: say nothing
      }
    };
    void read();
    const id = window.setInterval(() => void read(), POLL_MS);
    const onVis = () => document.visibilityState === "visible" && void read();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      ctl?.abort();
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [api, enabled]);
  return load;
}
