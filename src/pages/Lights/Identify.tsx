// Identify: the light shows full white for a few seconds, whatever it was doing (POST .../fixtures/{id}/identify), so
// whoever is up the ladder can see which one it is. The button says "Showing white" for as long as it lasts; a light
// that can't (its output isn't running) says why under its row.

import { useCallback, useEffect, useRef, useState } from "react";
import { ScanEye } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Spinner } from "@/pages/Room/bits";
import { explainLightsError, type LightsApi } from "./api";

/** How long a light shows white: the contract's default (POST .../identify {seconds?: 3}). */
export const IDENTIFY_S = 3;

export type IdentifyState = "idle" | "sending" | "showing";

export function useIdentify(api: LightsApi, id: string | null) {
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
      const res = await api.identify(id, IDENTIFY_S);
      if (res && res.ok === false) {
        setWhy(res.why ? `Can't show white: ${res.why}` : "It can't show white right now.");
        setState("idle");
        return;
      }
      setState("showing");
      timer.current = setTimeout(() => setState("idle"), IDENTIFY_S * 1000);
    } catch (e) {
      setWhy(explainLightsError(e));
      setState("idle");
    }
  }, [api, id]);

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
  /** The longer wording, for the place it's the main thing to do (after saving a new light). */
  wide?: boolean;
}) {
  const showing = state === "showing";
  return (
    <Button
      type="button"
      variant="outline"
      onClick={onClick}
      aria-label={showing ? `${label} is showing white` : `Identify ${label}: it shows full white for ${IDENTIFY_S} seconds`}
      title={`It shows full white for ${IDENTIFY_S} seconds`}
      className={cn(
        "h-11 shrink-0 rounded-xl bg-white px-3 text-[15px]",
        showing && "border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-50",
        className,
      )}
    >
      {state === "sending" ? <Spinner /> : <ScanEye aria-hidden />}
      {showing ? "Showing white" : wide ? `Identify it (white for ${IDENTIFY_S} s)` : "Identify"}
    </Button>
  );
}
