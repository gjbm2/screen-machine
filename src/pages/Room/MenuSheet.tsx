// The menu on the Room page: the app's one menu (src/components/nav/AppMenu.tsx: Go to · this room · Screens · Tools
// · Developer) with this room's own section, read-only (rule 7: no config on the Room console; every switch lives
// once, on the page, next to what it changes): the connection in a sentence (with Refresh), the stream service, the
// last mode change and the activity log. The room service address is shown under Developer; it is changed in Settings
// › Connection (RoomAddressEditor, exported here for it), or with ?room=<address>. "What each mode does" is Settings ›
// Room & modes (ModesInWords, exported here for it).

import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { defaultRoomUrl, normaliseRoomUrl, resolveRoomUrl, storageSet, URL_KEY } from "./api";
import { Dot, Notice, Unroll } from "./bits";
import { sceneChoices } from "./catalog";
import { ago, type LinkState } from "./connection";
import { EventLog } from "./EventLog";
import { StepList } from "./ModeSwitcher";
import { AppMenu } from "@/components/nav/AppMenu";
import { modeChanges, notConnectedWords } from "./modeWords";
import type { Catalog, LogEntry, ModeDef, RoomDescribe, RoomStatus, TrackedTransition } from "./types";
import type { ConnectionState } from "./useRoomConnection";
import { modeDescription, modeLabel } from "./words";

const SMALL_BUTTON = "h-11 rounded-xl bg-white text-[15px]";

/** Remember a room service address on this device (null: back to the default) and reload on it. */
function saveRoomUrlAndReload(url: string | null) {
  storageSet(URL_KEY, url);
  const next = new URLSearchParams(window.location.search);
  next.delete("room");
  const q = next.toString();
  window.location.replace(`${window.location.pathname}${q ? `?${q}` : ""}${window.location.hash}`);
}

/** The room service address this device uses, and a form to change it (Settings › Connection). By default it reads
 *  the address the way every page does (?room=, the remembered one, the default) and a change reloads the page. */
export function RoomAddressEditor({ baseUrl, onChangeUrl }: { baseUrl?: string; onChangeUrl?: (url: string | null) => void }) {
  const current = useMemo(() => baseUrl ?? resolveRoomUrl(window.location.search), [baseUrl]);
  const change = onChangeUrl ?? saveRoomUrlAndReload;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(current);
  useEffect(() => setDraft(current), [current]);
  const draftValid = normaliseRoomUrl(draft) !== null;
  return (
    <div className="space-y-2" data-testid="room-address">
      <p className="break-all font-mono text-sm">{current}</p>
      {editing ? (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            const n = normaliseRoomUrl(draft);
            if (!n) return;
            change(n);
            setEditing(false);
          }}
        >
          <Label htmlFor="room-url" className="text-sm font-normal text-muted-foreground">
            New address
          </Label>
          <Input
            id="room-url"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={defaultRoomUrl()}
            className="h-11 rounded-xl bg-white font-mono text-base"
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!draftValid} className="h-11 rounded-xl text-[15px]">
              Connect
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                change(null);
                setEditing(false);
              }}
              title={defaultRoomUrl()}
              className={SMALL_BUTTON}
            >
              Use default
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)} className="h-11 rounded-xl text-[15px]">
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <Button variant="outline" onClick={() => setEditing(true)} className={SMALL_BUTTON} data-testid="server-change">
          Change address
        </Button>
      )}
    </div>
  );
}

/** What each mode does in the room, in words (from GET /room/describe), with the devices that aren't connected yet
 *  (Settings › Room & modes). Read-only. */
export function ModesInWords({
  modes,
  describe,
  status,
  names,
}: {
  modes: Record<string, ModeDef> | null;
  describe?: RoomDescribe | null;
  status: RoomStatus | null;
  /** Names that beat a target's own label (the screens' names), by target. */
  names?: Record<string, string>;
}) {
  const notConnected = notConnectedWords(describe, status?.subsystems);
  const startMode = status?.mode?.default ?? null;
  return (
    <div data-testid="modes-in-words">
      <ul className="space-y-2 pb-2">
        {Object.entries(modes ?? {}).map(([name, def]) => {
          const changes = modeChanges(describe, name, { names, live: status?.subsystems });
          return (
            <li key={name} className="rounded-xl bg-white px-3 py-2">
              <p className="text-[15px] font-medium">
                {modeLabel(name, modes)}
                {name === startMode && <span className="ml-2 text-[13px] font-normal text-muted-foreground">Starts here</span>}
              </p>
              {modeDescription(def) && <p className="text-sm text-foreground">{modeDescription(def)}</p>}
              <p className="text-sm leading-relaxed text-muted-foreground">
                {changes || (describe ? "Changes nothing in the room." : "Reading what it does…")}
              </p>
            </li>
          );
        })}
        {!modes && <li className="text-sm text-muted-foreground">Modes not loaded.</li>}
      </ul>
      {notConnected.map((s) => (
        <p key={s} className="pb-2 text-sm text-muted-foreground">
          {s}
        </p>
      ))}
    </div>
  );
}

export function MenuSheet({
  open,
  onOpenChange,
  baseUrl,
  conn,
  link,
  now,
  status,
  modes,
  transition,
  catalog,
  describe,
  names,
  onRefresh,
  log,
  statusCount,
  onClearLog,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  baseUrl: string;
  conn: ConnectionState;
  link: LinkState;
  now: number;
  status: RoomStatus | null;
  modes: Record<string, ModeDef> | null;
  transition: TrackedTransition | null;
  catalog: Catalog | null;
  describe?: RoomDescribe | null;
  /** Names that beat a target's own label (the screens' names), by target. */
  names?: Record<string, string>;
  /** @deprecated No keys on the AV stack (rule 5); ignored, and the Room page stops passing it. */
  hasToken?: boolean;
  /** @deprecated Ignored (see hasToken). */
  foreignToken?: string | null;
  /** @deprecated The address is changed in Settings › Connection now; ignored. */
  onChangeUrl?: (url: string | null) => void;
  /** @deprecated Ignored (see hasToken). */
  onOpenToken?: () => void;
  onRefresh: () => void;
  log: LogEntry[];
  statusCount: number;
  onClearLog: () => void;
}) {
  const scenes = useMemo(() => sceneChoices(catalog), [catalog]);
  const retryIn = conn.ws !== "open" && conn.reconnectAt ? Math.max(0, Math.ceil((conn.reconnectAt - now) / 1000)) : null;
  const stream = status?.stream_service;

  return (
    <AppMenu
      open={open}
      onOpenChange={onOpenChange}
      current="room"
      pageTitle="This room"
      dev={
        <div className="space-y-1 px-1" data-testid="menu-room-address">
          <p className="text-sm font-semibold text-muted-foreground">Room service address</p>
          <p className="break-all font-mono text-sm">{baseUrl}</p>
          <p className="text-sm text-muted-foreground">Change it in Settings › Connection.</p>
        </div>
      }
    >
      <div className="space-y-3" data-testid="menu-sheet">
        <div className="space-y-1" data-testid="menu-connection">
          <p className="flex items-center gap-2 text-base font-medium">
            <Dot tone={link.tone} pulse={link.pulse} className="h-2.5 w-2.5" />
            {link.sentence}
          </p>
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
            <span>
              Last update {ago(now, conn.lastStatusAt)}
              {retryIn !== null && conn.ws === "closed" ? ` · trying live updates again in ${retryIn} s` : ""}
              {conn.version ? ` · version ${conn.version}` : ""}
            </span>
            <button
              type="button"
              onClick={onRefresh}
              title="Re-read modes, scenes and status"
              data-testid="menu-refresh"
              className="inline-flex min-h-9 items-center gap-1 rounded-md font-medium text-teal-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RefreshCw aria-hidden className="h-3.5 w-3.5" /> Refresh
            </button>
          </p>
          {conn.lastError && link.tone !== "ok" && !conn.authRequired && <Notice tone="warn">{conn.lastError}</Notice>}
          <p className="flex items-center gap-2 pt-1 text-[15px]" data-testid="menu-stream">
            <Dot tone={stream ? (stream.reachable ? "ok" : "bad") : "muted"} />
            {stream
              ? stream.reachable
                ? "Working: live pictures are available."
                : "Not answering: going live waits until it's back."
              : "Live pictures: not known yet."}
          </p>
        </div>

        <div className="space-y-1">
          <Unroll title="Last mode change" hint={transition ? `${modeLabel(transition.to, modes)}` : "none yet"} testId="menu-last-change">
            {transition ? (
              <div className="space-y-2 pb-2">
                <p className="text-sm text-muted-foreground">
                  {modeLabel(transition.from, modes)} → {modeLabel(transition.to, modes)} ·{" "}
                  {transition.finishedAt
                    ? `${transition.phase === "cancelled" ? "interrupted" : "finished"} ${new Date(transition.finishedAt).toLocaleTimeString()}`
                    : "running"}
                </p>
                <StepList steps={transition.steps ?? []} scenes={scenes} describe={describe} names={names} />
              </div>
            ) : (
              <p className="pb-2 text-sm text-muted-foreground">No mode change seen since this page opened.</p>
            )}
          </Unroll>

          <Unroll title="Activity log" hint={`${log.length}`} testId="menu-activity" revealOnOpen>
            <EventLog log={log} statusCount={statusCount} onClear={onClearLog} className="mb-2 h-[60vh]" />
          </Unroll>
        </div>
      </div>
    </AppMenu>
  );
}
