// Settings › Developer (#developer, at the foot): Test music (the Room page's TestShowSection, by import: recorded
// music and the test piano through the show engine), the admin key this device keeps for the admin page
// (sm_admin_token: shown as kept or not, and forgotten here; it's entered on the admin page), and the developer pages
// and tools (the menu's own lists, appNav.ts).

import { useState } from "react";
import { Wrench } from "lucide-react";
import { DEV_LINKS, TOOL_LINKS } from "@/components/nav/appNav";
import { useRoomLink } from "@/components/room/roomLink";
import { Button } from "@/components/ui/button";
import { ADMIN_TOKEN_KEY } from "@/shared/api/admin";
import { storageGet, storageSet, type RoomApi } from "@/pages/Room/api";
import { TestShowSection } from "@/pages/Room/TestShow";
import type { NewLogEntry } from "@/pages/Room/useRoomConnection";
import { Group, LinkRow, Row, Sub } from "./Group";

export function DeveloperGroup({
  api,
  connected,
  liveNow,
  addLog,
  reportError,
}: {
  api: RoomApi;
  connected: boolean;
  liveNow: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}) {
  const room = useRoomLink();
  const [hasKey, setHasKey] = useState(() => !!storageGet(ADMIN_TOKEN_KEY));

  return (
    <Group id="developer" title="Developer" icon={<Wrench />} summary="Test music, tools and raw data">
      <TestShowSection api={api} connected={connected} liveNow={liveNow} addLog={addLog} reportError={reportError} />

      <Sub>Admin page</Sub>
      <Row label="Admin key on this device" hint={hasKey ? "Kept here, for the admin page." : "None: the admin page asks for it."}>
        {hasKey && (
          <Button
            variant="outline"
            className="h-10 rounded-xl bg-white px-4 text-[15px]"
            onClick={() => {
              storageSet(ADMIN_TOKEN_KEY, null);
              setHasKey(false);
            }}
          >
            Forget it
          </Button>
        )}
      </Row>

      {room.onLan && (
        <>
          <Sub>Tools</Sub>
          <div className="flex flex-col">
            {TOOL_LINKS.map((l) => (
              <LinkRow key={l.label} href={l.href()} newTab>
                {l.label}
              </LinkRow>
            ))}
          </div>
        </>
      )}

      <Sub>Pages and raw data</Sub>
      <div className="flex flex-col">
        {DEV_LINKS.filter((l) => !l.lan || room.onLan).map((l) => (
          <LinkRow key={l.label} href={l.href()} newTab>
            {l.label}
          </LinkRow>
        ))}
      </div>
    </Group>
  );
}
