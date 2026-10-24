// Settings › Connection (#connection): how this device reaches the room. The room service address (this device's
// sm_room_url, as ?room= sets it; moved here from the Room page's menu), the services as they answer now (the room,
// live pictures, the music side), the last mode change, and the activity log (read only).
// No link to Spectre: the owner ruled it must not be reachable from this app.

import { useEffect, useState } from "react";
import { RefreshCw, Wifi } from "lucide-react";
import { useRoomLink } from "@/components/room/roomLink";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { defaultRoomUrl, normaliseRoomUrl } from "@/pages/Room/api";
import { Dot, Notice, Unroll } from "@/pages/Room/bits";
import { ago, type LinkState } from "@/pages/Room/connection";
import { EventLog } from "@/pages/Room/EventLog";
import type { LogEntry, ModeDef, RoomStatus, TrackedTransition } from "@/pages/Room/types";
import type { ConnectionState } from "@/pages/Room/useRoomConnection";
import { modeLabel } from "@/pages/Room/words";
import { Group, Hint, ReadRow, Row, Sub } from "./Group";

export function ConnectionGroup({
  baseUrl,
  onChangeUrl,
  conn,
  link,
  now,
  status,
  showAnswering,
  modes,
  transition,
  log,
  statusCount,
  onClearLog,
  onRefresh,
}: {
  baseUrl: string;
  onChangeUrl: (url: string | null) => void;
  conn: ConnectionState;
  link: LinkState;
  now: number;
  status: RoomStatus | null;
  /** The music side answered lately (null: not known yet). */
  showAnswering: boolean | null;
  modes: Record<string, ModeDef> | null;
  transition: TrackedTransition | null;
  log: LogEntry[];
  statusCount: number;
  onClearLog: () => void;
  onRefresh: () => void;
}) {
  const room = useRoomLink();
  const [draft, setDraft] = useState(baseUrl);
  useEffect(() => setDraft(baseUrl), [baseUrl]);
  const valid = normaliseRoomUrl(draft) !== null;
  const isDefault = baseUrl === defaultRoomUrl();
  const stream = status?.stream_service;

  return (
    <Group id="connection" title="Connection" icon={<Wifi />} summary={`${link.word} · ${baseUrl.replace(/^https?:\/\//, "")}`}>
      <div className="space-y-1">
        <p className="flex items-center gap-2 text-base font-medium">
          <Dot tone={link.tone} pulse={link.pulse} className="h-2.5 w-2.5" />
          {link.word}
        </p>
        <Hint>
          {link.sentence} Last update {ago(now, conn.lastStatusAt)}
          {conn.version ? ` · version ${conn.version}` : ""}.
        </Hint>
        {conn.lastError && link.tone !== "ok" && <Notice tone="warn">{conn.lastError}</Notice>}
        <Button variant="outline" onClick={onRefresh} className="h-11 rounded-xl bg-white text-[15px]">
          <RefreshCw /> Refresh
        </Button>
      </div>

      <Row label="Room service address" hint={isDefault ? "This device uses the usual address." : "This device uses its own address."} stack testId="settings-room-url">
        <p className="mb-2 break-all font-mono text-sm">{baseUrl}</p>
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            const n = normaliseRoomUrl(draft);
            if (n) onChangeUrl(n);
          }}
        >
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={defaultRoomUrl()}
            aria-label="Room service address"
            className="h-11 rounded-xl bg-white font-mono text-base"
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!valid || normaliseRoomUrl(draft) === baseUrl} className="h-11 rounded-xl px-5 text-[15px]">
              Connect
            </Button>
            {!isDefault && (
              <Button type="button" variant="outline" onClick={() => onChangeUrl(null)} title={defaultRoomUrl()} className="h-11 rounded-xl bg-white text-[15px]">
                Use the usual address
              </Button>
            )}
          </div>
        </form>
      </Row>

      <Sub>Services</Sub>
      <ReadRow label="The room" value={status ? (link.tone === "ok" ? "Answering" : link.word) : "Not answering"} />
      <ReadRow
        label="Live pictures"
        value={stream ? (stream.reachable ? "Working" : "Not answering: going live waits until it's back") : "Not known yet"}
      />
      <ReadRow label="The music side" value={showAnswering === null ? "Not known yet" : showAnswering ? "Answering" : "Not answering"} />

      <Unroll
        title="Last mode change"
        hint={transition ? modeLabel(transition.to, modes) : status?.mode?.current ? `now ${modeLabel(status.mode.current, modes)}` : "none seen"}
        testId="settings-last-change"
      >
        <Hint className="pb-2">
          {transition
            ? `${modeLabel(transition.from, modes)} → ${modeLabel(transition.to, modes)} · ${
                transition.finishedAt
                  ? `${transition.phase === "cancelled" ? "interrupted" : "finished"} ${new Date(transition.finishedAt).toLocaleTimeString()}`
                  : "running"
              }`
            : "No mode change seen since this page opened."}
        </Hint>
      </Unroll>

      <Unroll title="Activity log" hint={`${log.length}`} testId="settings-activity" revealOnOpen>
        <EventLog log={log} statusCount={statusCount} onClear={onClearLog} className="mb-2 h-[60vh]" />
      </Unroll>

    </Group>
  );
}
