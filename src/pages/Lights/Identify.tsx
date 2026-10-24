// Identify: the light flashes for a few seconds, whatever it was doing, so whoever is up the ladder can see which one
// it is. A DMX light shows full white (show_service's rig, POST .../rig/fixtures/{id}/identify); a Hue lamp breathes
// (room_service, POST /api/v1/fixtures/{id}/identify, which drives the Hue bridge). One button for every light, on the
// Lights setup page and the Room page alike, always there (also while the light follows the music). The button says
// "Flashing" while it lasts; a light that can't says so under its row, never with a file or config path in it.

import { useCallback, useEffect, useRef, useState } from "react";
import { ScanEye } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Spinner } from "@/pages/Room/bits";
import { LightsApiError, unreached, explainLightsError, type LightsApi } from "./api";
import { identifyWhy } from "./lib";

/** How long a DMX light shows white: the contract's default (POST .../identify {seconds?: 3}). */
export const IDENTIFY_S = 3;

export type IdentifyState = "idle" | "sending" | "showing";

export interface Identify {
  state: IdentifyState;
  why: string | null;
  run: () => Promise<void>;
}

export function useIdentify(api: LightsApi, id: string | null, hue = false): Identify {
  const [state, setState] = useState<IdentifyState>("idle");
  const [why, setWhy] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  const run = useCallback(async () => {
    if (!id) return;
    clearTimeout(timer.current);
    setState("sending");
    setWhy(null);
    try {
      const res = await api.identify(id, IDENTIFY_S, hue);
      if (res && res.ok === false) {
        setWhy(identifyWhy(res.why));
        setState("idle");
        return;
      }
      setState("showing");
      timer.current = setTimeout(() => setState("idle"), IDENTIFY_S * 1000);
    } catch (e) {
      // Can't reach it: the page's own words. Refused: the reason, unless it names a file or setting.
      setWhy(!(e instanceof LightsApiError) || e.status === 0 || unreached(e) ? explainLightsError(e) : identifyWhy(e.detail));
      setState("idle");
    }
  }, [api, id, hue]);

  return { state, why, run };
}

export function IdentifyButton({
  state,
  onClick,
  label,
  className,
  wide = false,
}: {
  state: IdentifyState;
  onClick: () => void;
  /** The light's name, for screen readers. */
  label: string;
  className?: string;
  /** Words at every width (after saving a new light, where it's the main thing to do); else an icon on a phone. */
  wide?: boolean;
}) {
  const showing = state === "showing";
  return (
    <Button
      type="button"
      variant="outline"
      onClick={onClick}
      aria-label={showing ? `${label} is flashing` : `Identify ${label}: it flashes for a few seconds`}
      title="It flashes for a few seconds"
      data-testid="identify"
      className={cn(
        "h-11 shrink-0 rounded-xl bg-white text-[15px]",
        wide ? "px-3" : "w-11 px-0 sm:w-auto sm:px-3",
        showing && "border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-50",
        className,
      )}
    >
      {state === "sending" ? <Spinner /> : <ScanEye aria-hidden />}
      <span className={wide ? undefined : "sr-only sm:not-sr-only"}>{showing ? "Flashing" : "Identify"}</span>
    </Button>
  );
}
