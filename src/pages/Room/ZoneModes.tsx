// A mode zone's own mode strip (a room with "own_modes" in rooms.json; status.zones): the same tiles, progress line,
// Edit modes and mode editor as the site's, on that room's own modes file (api.forRoom). No Auto row: Auto switches
// the site's modes. The Room page draws one per zone under the site's modes, each under its room's name; it is never
// drawn without zones.

import { useState } from "react";
import type { RoomApi } from "./api";
import { ModeEditor } from "./ModeEditor";
import { ModeSwitcher } from "./ModeSwitcher";
import type { SceneTagIndex } from "./sceneTags";
import type { Catalog, ModeDef, RoomStatus, TrackedTransition, ZoneStatus } from "./types";
import { useDescribe } from "./useDescribe";
import type { NewLogEntry } from "./useRoomConnection";

export function ZoneModes({
  api,
  zone,
  modes,
  modesError,
  transition,
  status,
  catalog,
  connected,
  now,
  addLog,
  reportError,
  names,
  tagIndex,
  onChanged,
}: {
  api: RoomApi;
  zone: ZoneStatus;
  modes: Record<string, ModeDef> | null;
  modesError: string | null;
  transition: TrackedTransition | null;
  status: RoomStatus;
  catalog: Catalog | null;
  connected: boolean;
  now: number;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  names: Record<string, string>;
  tagIndex: SceneTagIndex;
  /** The zone's modes file was saved here: re-read the modes. */
  onChanged: () => void;
}) {
  const zoneApi = api.forRoom(zone.room);
  const { describe, refresh } = useDescribe(zoneApi, zone.config?.revision, true);
  const [editing, setEditing] = useState<string | null>(null);
  const changed = () => {
    onChanged();
    refresh();
  };
  return (
    <div data-testid={`zone-modes-${zone.room}`}>
      <ModeSwitcher
        api={api}
        room={zone.room}
        modes={modes}
        modesError={modesError}
        mode={zone.mode}
        transition={transition}
        catalog={catalog}
        connected={connected}
        now={now}
        addLog={addLog}
        reportError={reportError}
        onEdit={setEditing}
        describe={describe}
        subsystems={status.subsystems}
        names={names}
        onChanged={changed}
      />
      <ModeEditor
        name={editing}
        room={zone.room}
        onClose={() => setEditing(null)}
        api={api}
        describe={describe}
        status={status}
        tagIndex={tagIndex}
        connected={connected}
        onSaved={changed}
        addLog={addLog}
        names={names}
      />
    </div>
  );
}
