import { useEffect, useState } from "react";
import { Api } from "@/utils/api";

const MASK_FETCH_INTERVAL = 60_000; // 60 seconds

export function useMask(destinationId: string | undefined, disabled: boolean = false) {
  const [rgba, setRgba] = useState("rgba(0,0,0,0)");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Skip if no destination ID or if disabled
    if (!destinationId || disabled) {
      console.log(`[useMask] Skipping mask fetch - destinationId: ${destinationId}, disabled: ${disabled}`);
      return;
    }

    const api = new Api();
    let timeoutId: NodeJS.Timeout;
    
    const fetchMask = async () => {
      try {
        setIsLoading(true);
        setError(null);
        
        console.log(`[useMask] Fetching mask for destination: ${destinationId}`);
        const response = await api.getMask(destinationId);
        
        // Convert hex + alpha to rgba
        const { hex, alpha } = response;
        const bigint = parseInt(hex.slice(1), 16);
        const r = (bigint >> 16) & 255;
        const g = (bigint >> 8) & 255;
        const b = bigint & 255;
        const rgbaString = `rgba(${r},${g},${b},${alpha})`;
        
        console.log(`[useMask] Mask updated for ${destinationId}: ${hex} @ ${alpha} -> ${rgbaString}`);
        setRgba(rgbaString);
      } catch (err) {
        console.error(`[useMask] Error fetching mask for ${destinationId}:`, err);
        setError(err instanceof Error ? err.message : 'Failed to fetch mask');
        // On error, set transparent mask
        setRgba("rgba(0,0,0,0)");
      } finally {
        setIsLoading(false);
      }
      
      // Schedule next fetch
      timeoutId = setTimeout(fetchMask, MASK_FETCH_INTERVAL);
    };
    
    // Initial fetch
    fetchMask();
    
    // Cleanup
    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [destinationId, disabled]);

  return { rgba, isLoading, error };
} 