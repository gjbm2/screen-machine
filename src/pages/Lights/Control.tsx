// /lights, Lights: work the room's lights and appliances (owner, 28 Oct: "an appliance control page not an appliance
// config page"). House lights and Lights low, then each room's lamps and its appliances worked by remote (the
// glitterball, pinspots), the same controls as the Room page's Lights fold (LightsControls.tsx). Setting them up (adding
// a light or an appliance, placing it, learning its remote) is the light setup page in Settings, one link away.

import { useEffect, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { AppMenu } from "@/components/nav/AppMenu";
import { lightSetupUrl } from "@/components/nav/appNav";
import { PageHeader, type HeaderStatus } from "@/components/nav/PageHeader";
import { cn } from "@/lib/utils";
import { loadToken, resolveRoomUrl } from "@/pages/Room/api";
import { Spinner } from "@/pages/Room/bits";
import { isConnected } from "@/pages/Room/connection";
import { LightsControls } from "@/pages/Room/LightsControls";
import { useShowFeed } from "@/pages/Room/MusicCard";
import { buildSections } from "@/pages/Room/sections";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { useDescribe } from "@/pages/Room/useDescribe";
import { useFixtures } from "@/pages/Room/useFixtures";
import { useRoomConnection } from "@/pages/Room/useRoomConnection";

function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

export default function LightsControlPage() {
  useEffect(() => {
    const prev = document.title;
    document.title = "Lights · screen/machine";
    return () => {
      document.title = prev;
    };
  }, []);
  const [baseUrl] = useState(() => resolveRoomUrl(window.location.search));
  const [token] = useState(() => loadToken(baseUrl).token);
  const room = useRoomConnection(baseUrl, token);
  const { conn, status } = room;
  const now = useNow(1000);
  const connected = isConnected(conn, now);
  const [menuOpen, setMenuOpen] = useState(false);

  const fixtures = useFixtures(room.api, !!status);
  const show = useShowFeed(room.api, !!status, room.addLog);
  const { describe } = useDescribe(room.api, status?.config?.revision, !!status);
  const lampIds = useMemo(() => {
    if (fixtures.missing || (fixtures.view && !fixtures.view.enabled)) return [];
    return fixtures.view ? fixtures.view.fixtures.filter((f) => f.kind === "hue").map((f) => f.id) : null;
  }, [fixtures.missing, fixtures.view]);
  const sections = useMemo(() => buildSections(status, lampIds), [status, lampIds]);
  const groups = sections.filter((sec) => !!sec.lights || sec.effects.length > 0);

  const header: HeaderStatus | null = !status
    ? { tone: "muted", word: "Connecting" }
    : connected
      ? null
      : { tone: "warn", word: "Reconnecting", sentence: "The room isn't answering just now; the page keeps trying.", pulse: true };

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 sm:px-6">
        <PageHeader
          title="Lights"
          current="lights"
          onMenu={() => setMenuOpen(true)}
          status={header}
          actions={
            <AppLink
              href={lightSetupUrl()}
              className="inline-flex min-h-11 items-center gap-0.5 rounded-xl px-2 text-[15px] font-medium text-teal-800 hover:bg-stone-100/70 hover:underline"
            >
              Set up <ChevronRight className="h-4 w-4" aria-hidden />
            </AppLink>
          }
        />
        <main className="space-y-4 pt-2" data-testid="lights-control">
          {!status ? (
            <p className="flex items-center gap-2 py-3 text-base text-muted-foreground">
              <Spinner /> Finding the lights…
            </p>
          ) : (
            <LightsControls
              api={room.api}
              connected={connected}
              addLog={room.addLog}
              reportError={room.reportError}
              fixtures={fixtures}
              show={show}
              status={status}
              describe={describe}
              groups={groups}
              lampIds={lampIds}
              flat={sections.length === 0}
            />
          )}
        </main>
      </div>
      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="lights" />
    </div>
  );
}
