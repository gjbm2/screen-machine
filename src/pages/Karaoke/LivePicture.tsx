// The karaoke panel's picture controls: the Room page's own card for the karaoke screen (ScreenCard: Go live / Back to
// art on its face; the scene, its look controls and settings under More), on the same room_service connection the
// Room and Lights pages use. Nothing new behind it.
//
// The screen: ?screen=<id>, else the first screen of the deployment's default room that can go live, else the first
// that can go live (deployments/README.md: no site's screen named here).

import { useEffect, useState } from "react";
import { getDeploymentRelative, type DeploymentInfo } from "@/shared/api/deployment";
import { resolveRoomUrl } from "@/pages/Room/api";
import { Notice, Spinner } from "@/pages/Room/bits";
import { isConnected } from "@/pages/Room/connection";
import { ScreenCard } from "@/pages/Room/ScreenCard";
import { useRoomConnection } from "@/pages/Room/useRoomConnection";

function karaokeScreen(d: DeploymentInfo | null, search: string): string | null {
  const asked = new URLSearchParams(search).get("screen");
  if (asked) return asked;
  const live = (d?.screens ?? []).filter((s) => s.live);
  return (live.find((s) => s.room === d?.default_room) ?? live[0])?.id ?? null;
}

function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

/** `screen`: the karaoke screen (the panel's Settings, shared/api/karaoke.ts karaokeScreen); without it, ?screen= or
 *  the deployment's own choice as below. */
export function LivePicture({ screen }: { screen?: string | null } = {}) {
  const [baseUrl] = useState(() => resolveRoomUrl(window.location.search));
  const room = useRoomConnection(baseUrl);
  const now = useNow(1000);
  const [screenId, setScreenId] = useState<string | null>(null);
  const [depError, setDepError] = useState(false);

  useEffect(() => {
    let dead = false;
    getDeploymentRelative()
      .then((d) => !dead && setScreenId(screen ?? karaokeScreen(d, window.location.search)))
      .catch(() => !dead && setDepError(true));
    return () => {
      dead = true;
    };
  }, [screen]);

  const sc = screenId ? room.status?.screens?.[screenId] : undefined;
  if (depError) return <Notice tone="warn">Couldn't read this installation's screens.</Notice>;
  if (!screenId || !room.status) {
    return (
      <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
        <Spinner /> Connecting to the screen…
      </div>
    );
  }
  if (!sc) return <Notice tone="warn">No screen “{screenId}” here.</Notice>;
  return (
    <ScreenCard
      screenId={screenId}
      screen={sc}
      catalog={room.catalog}
      catalogError={room.catalogError}
      library={room.library}
      subscribeStream={room.subscribeStream}
      api={room.api}
      connected={isConnected(room.conn, now)}
      streamReachable={room.status?.stream_service?.reachable}
      addLog={room.addLog}
      reportError={room.reportError}
    />
  );
}
