// The menu: the app's shared menu (Go to · Screens · Developer, src/components/nav/AppMenu.tsx, the same
// as the generator's), with this room's own section: only the technical details, never a control of the room (every
// switch lives once, on the page, next to what it changes): the connection and its address, the stream service,
// refresh, the last mode change, what each mode does in the room (from describe, in words, with the devices that
// aren't connected yet), and the activity log.

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { KeyRound, Lightbulb, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { defaultRoomUrl, normaliseRoomUrl } from "./api";
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

function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn("space-y-2 border-b border-stone-200/80 pb-5", className)}>
      <h3 className="text-sm font-semibold text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

const SMALL_BUTTON = "h-11 rounded-xl bg-white text-[15px]";

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
  hasToken,
  foreignToken,
  onChangeUrl,
  onOpenToken,
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
  /** GET /room/describe: what each mode does, in words. */
  describe?: RoomDescribe | null;
  /** Names that beat a target's own label (the screens' names), by target. */
  names?: Record<string, string>;
  /** A setup key is saved for this server (and is being sent). */
  hasToken: boolean;
  /** A key is saved, but for another server. */
  foreignToken: string | null;
  onChangeUrl: (url: string | null) => void;
  onOpenToken: () => void;
  onRefresh: () => void;
  log: LogEntry[];
  statusCount: number;
  onClearLog: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(baseUrl);
  useEffect(() => setDraft(baseUrl), [baseUrl]);
  const draftValid = normaliseRoomUrl(draft) !== null;
  const scenes = useMemo(() => sceneChoices(catalog), [catalog]);
  const retryIn = conn.ws !== "open" && conn.reconnectAt ? Math.max(0, Math.ceil((conn.reconnectAt - now) / 1000)) : null;
  const stream = status?.stream_service;
  const notConnected = notConnectedWords(describe, status?.subsystems);
  const startMode = status?.mode?.default ?? null;

  return (
    <AppMenu open={open} onOpenChange={onOpenChange} current="room" pageTitle="This room">
      <div className="space-y-5" data-testid="menu-sheet">
        {/* Navigation only: the lights' setup page (src/pages/Lights) */}
        <a
          href="/lights"
          data-testid="menu-lights-setup"
          className="flex min-h-12 items-center gap-3 rounded-xl border border-stone-200/80 bg-white px-3 py-2 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-stone-100 text-stone-600">
            <Lightbulb className="h-5 w-5" aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-base font-semibold leading-snug">Lights setup</span>
            <span className="block truncate text-sm leading-snug text-muted-foreground">Add a light, name it, find it</span>
          </span>
        </a>

        <Section title="Connection">
          <p className="flex items-center gap-2 text-base font-medium">
            <Dot tone={link.tone} pulse={link.pulse} className="h-2.5 w-2.5" />
            {link.word}
          </p>
          <p className="text-[15px] text-foreground">{link.sentence}</p>
          <p className="text-sm text-muted-foreground">
            Last update {ago(now, conn.lastStatusAt)}
            {retryIn !== null && conn.ws === "closed" ? ` · trying live updates again in ${retryIn} s` : ""}
            {conn.version ? ` · version ${conn.version}` : ""}
          </p>
          {conn.lastError && link.tone !== "ok" && !conn.authRequired && <Notice tone="warn">{conn.lastError}</Notice>}
          <Button variant="outline" onClick={onRefresh} className={SMALL_BUTTON} title="Re-read modes, scenes and status">
            <RefreshCw /> Refresh
          </Button>
        </Section>

        <Section title="Room service address">
          <p className="break-all font-mono text-sm">{baseUrl}</p>
          {editing ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                const n = normaliseRoomUrl(draft);
                if (!n) return;
                onChangeUrl(n);
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
                    onChangeUrl(null);
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
        </Section>

        <Section title="Stream service">
          {stream ? (
            <>
              <p className="flex items-center gap-2 text-[15px]">
                <Dot tone={stream.reachable ? "ok" : "bad"} />
                {stream.reachable ? "Working: live pictures are available." : "Not answering: going live waits until it's back."}
              </p>
              {stream.url && <p className="break-all font-mono text-sm text-muted-foreground">{stream.url}</p>}
            </>
          ) : (
            <p className="text-[15px] text-muted-foreground">Not known yet.</p>
          )}
        </Section>

        <div className="space-y-1 border-b border-stone-200/80 pb-4">
          <Unroll
            title="Last mode change"
            hint={transition ? `${modeLabel(transition.to, modes)}` : "none yet"}
            testId="menu-last-change"
          >
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

          <Unroll title="What each mode does" testId="menu-modes">
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
          </Unroll>

          <Unroll title="Activity log" hint={`${log.length}`} testId="menu-activity" revealOnOpen>
            <EventLog log={log} statusCount={statusCount} onClear={onClearLog} className="mb-2 h-[60vh]" />
          </Unroll>
        </div>
      </div>
    </AppMenu>
  );
}
