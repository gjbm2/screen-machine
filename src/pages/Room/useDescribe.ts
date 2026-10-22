// GET /api/v1/room/describe: every target's label and value schema (device config + the modes.json labels) and every
// mode's resolved entries. Re-read whenever the modes config's revision changes (status.config.revision, pushed over
// the events socket), so an edit to modes.json shows at once.

import { useCallback, useEffect, useState } from "react";
import type { RoomApi } from "./api";
import type { RoomDescribe } from "./types";

export function useDescribe(api: RoomApi, revision: string | undefined, enabled: boolean) {
  const [describe, setDescribe] = useState<RoomDescribe | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  useEffect(() => {
    if (!enabled) return;
    const ac = new AbortController();
    api
      .describe(ac.signal)
      .then((d) => setDescribe(d))
      .catch(() => {
        /* keep the last good one; the next revision or refresh tries again */
      });
    return () => ac.abort();
  }, [api, revision, enabled, nonce]);
  return { describe, refresh };
}
