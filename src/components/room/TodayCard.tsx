// "Today": a room's daily art cascade, as its schedules export it. Every public variable exported to the room's
// scheduler group (routes/scheduler_room.py: names starting with "_" and exports with "room": false never show), in the
// order a schedule run sets them, under its friendly name: the value (long text clamped, tap to read it all), who set
// it and when, and which screens use it. Edit changes it through the exported-vars API; the screens pick it up on their
// next run. Under them, the room's actions (EventsList: e.g. "Get inspiration", "Develop theme").
// No variable or event name is written here. No frame: the caller passes the card's classes.

import { useState } from "react";
import { Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { setRoomVariable, type RoomEvent, type RoomVariable } from "@/shared/api/roomSchedule";
import { EventsList } from "./EventsList";
import { refreshRoomSchedule, useRoomSchedule } from "./useRoomSchedule";

export interface TodayCardProps {
  /** The room (rooms.json id). */
  room: string;
  title?: string;
  className?: string;
  /** Told about each change and event sent (e.g. the page's log). */
  onChange?: (what: string) => void;
}

const CLAMP_AT = 140;

/** "06:00" today, "Fri 06:00" this week, else "23 Oct". */
export function whenWords(iso: string | null, now = new Date()): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  const days = (now.getTime() - d.getTime()) / 86_400_000;
  if (days >= 0 && days < 6) return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
  return d.toLocaleDateString([], { day: "numeric", month: "short" });
}

/** Names with a last word they all share dropped: ["North screen", "South screen"] -> ["North", "South"]. */
export function shortNames(names: string[]): string[] {
  if (names.length < 2) return names;
  const last = names.map((n) => n.trim().split(/\s+/));
  const tail = last[0][last[0].length - 1];
  if (last.every((w) => w.length > 1 && w[w.length - 1].toLowerCase() === tail.toLowerCase())) {
    return last.map((w) => w.slice(0, -1).join(" "));
  }
  return names;
}

function valueText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return `${v.length} items`;
  if (typeof v === "object") return `${Object.keys(v as object).length} entries`;
  return String(v);
}

function VariableRow({ v, room, onChange }: { v: RoomVariable; room: string; onChange?: (what: string) => void }) {
  const text = valueText(v.value);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const long = text.length > CLAMP_AT;
  const when = whenWords(v.set_at);
  const users = shortNames(v.used_by.map((u) => u.name));

  const save = async () => {
    if (draft === null) return;
    setBusy(true);
    setError(null);
    try {
      await setRoomVariable(v.name, draft);
      setDraft(null);
      onChange?.(`${v.label} changed`);
      await refreshRoomSchedule(room);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-semibold leading-snug">{v.label}</p>
          {v.description && <p className="text-sm text-muted-foreground">{v.description}</p>}
        </div>
        {v.editable && draft === null && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-mr-2 -mt-1 h-9 shrink-0 px-2 text-muted-foreground"
            aria-label={`Change ${v.label}`}
            onClick={() => {
              setDraft(typeof v.value === "string" ? v.value : text);
            }}
          >
            <Pencil aria-hidden /> Edit
          </Button>
        )}
      </div>

      {draft !== null ? (
        <div className="mt-2 space-y-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.min(8, Math.max(3, Math.ceil(draft.length / 60)))}
            className="bg-card text-base"
            aria-label={v.label}
            autoFocus
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" disabled={busy} onClick={() => void save()}>
              {busy ? "Saving…" : "Save"}
            </Button>
            <Button type="button" size="sm" variant="outline" className="bg-card" disabled={busy} onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <span className="text-sm text-muted-foreground">The screens use it on their next run.</span>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
      ) : text ? (
        <button
          type="button"
          disabled={!long}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={long ? open : undefined}
          className="mt-1 block w-full rounded-md text-left text-base leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
        >
          <span className={cn("block whitespace-pre-wrap break-words", long && !open && "line-clamp-3")}>{text}</span>
          {long && <span className="text-sm font-medium text-primary">{open ? "Show less" : "Show all"}</span>}
        </button>
      ) : (
        <p className="mt-1 text-base text-muted-foreground">Not set yet</p>
      )}

      <p className="mt-1 text-sm text-muted-foreground">
        {when ? `Set ${when} by ${v.set_by}` : `From ${v.owner_name}`}
        {users.length > 0 && ` · Used by ${users.join(" · ")}`}
      </p>
    </li>
  );
}

export function TodayCard({ room, title = "Today", className, onChange }: TodayCardProps) {
  const { data, error } = useRoomSchedule(room);
  if (!data) return null;
  const { variables, actions } = data;
  if (!variables.length && !actions.length) return null;
  const sent = (ev: RoomEvent) => onChange?.(`${ev.label} sent`);

  return (
    <section aria-label={title} className={cn("p-4", className)}>
      <h3 className="mb-3 text-[17px] font-semibold">{title}</h3>
      {variables.length > 0 && (
        <ul className="divide-y divide-border">
          {variables.map((v) => (
            <VariableRow key={`${v.owner}|${v.name}`} v={v} room={room} onChange={onChange} />
          ))}
        </ul>
      )}
      {actions.length > 0 && (
        <EventsList room={room} onSent={sent} className={cn(variables.length > 0 && "mt-4 border-t border-border pt-4")} />
      )}
      {error && <p className="mt-3 text-sm text-muted-foreground">Couldn't refresh just now; showing the last read.</p>}
    </section>
  );
}
