import { useEffect, useState } from "react";
import { Api } from "@/utils/api";

const POLL_MS = 10_000;

interface MaskState {
  brightness: number;    // 0–1
  warmHex: string;       // "#RRGGBB"
  warmAlpha: number;     // 0–0.1 typical
}

export function useMask(destinationId?: string, disabled = false) {
  const [state, setState] = useState<MaskState>({
    brightness: 0,
    warmHex: "#FFFFFF",
    warmAlpha: 0,
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!destinationId || disabled) {
      console.debug(
        `[useMask] Skip fetch – destinationId=${destinationId}, disabled=${disabled}`,
      );
      return;
    }

    const api = new Api();
    let timer: NodeJS.Timeout;

    const fetchMask = async () => {
      try {
        setIsLoading(true);
        setError(null);
        const res = await api.getMask(destinationId);
        /*
         * Expected payload from utile.compute_mask:
         *   { brightness:0.42, warm_hex:"#F6D0B5", warm_alpha:0.031 }
         */
        setState({
          brightness: res.brightness ?? 0,
          warmHex: res.warm_hex ?? "#FFFFFF",
          warmAlpha: res.warm_alpha ?? 0,
        });
      } catch (err) {
        console.error("[useMask] fetch failed:", err);
        setError(err instanceof Error ? err.message : "Fetch failed");
        // reset to neutral so screen never looks wrong
        setState({ brightness: 0, warmHex: "#FFFFFF", warmAlpha: 0 });
      } finally {
        setIsLoading(false);
        timer = setTimeout(fetchMask, POLL_MS);
      }
    };

    fetchMask();
    return () => clearTimeout(timer);
  }, [destinationId, disabled]);

  // Compute the final rgba color string
  // brightness represents how bright the screen should be (0.1 = 10% bright)
  // so we need to invert it for the black overlay opacity (0.9 = 90% black)
  const rgba = disabled ? "rgba(0,0,0,0)" : `rgba(0,0,0,${1 - state.brightness})`;

  return { ...state, isLoading, error, rgba };
} 