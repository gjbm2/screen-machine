// "Today": a room's daily art cascade, as its schedules export it. Every public variable exported to the room's
// scheduler group (routes/scheduler_room.py: names starting with "_" and exports with "room": false never show), in the
// order a schedule run sets them, under its friendly name: the value (long text clamped, tap to read it all), who set
// it and when, and which screens use it. Edit changes it through the exported-vars API; the screens pick it up on their
// next run. Each variable is one line, "Style · Color Field painting…", two to a row when the card is 640 px wide
// (owner, 25 Oct: "vertical space is at a HIGH PREMIUM"); a tap opens it across the row: the whole value, who set it
// and when, which screens use it, and Edit. Under them, the room's actions (EventsList: e.g. "Get inspiration",
// "Develop theme") as one row of chips, each one's description in its tooltip, with the caller's footer (the Room
// page: "Schedules ›") at the end of that row.
// No variable or event name is written here. No frame: the caller passes the card's classes.

import { useState, type ReactNode } from "react";
import { ChevronDown, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { setRoomVariable, type RoomEvent, type RoomVariable } from "@/shared/api/roomSchedule";
import { EventsList } from "./EventsList";
import { refreshRoomSchedule, useRoomSchedule } from "./useRoomSchedule";

export interface TodayCardProps {
  /** The room (rooms.json id). */
  room: string;
  /** The card's heading; null for none (the Room page's fold already says "Today's art"). */
  title?: string | null;
  className?: string;
  /** Told about each change and event sent (e.g. the page's log). */
  onChange?: (what: string) => void;
  /** Shown at the foot while the card shows (e.g. a link to the schedules). */
  footer?: ReactNode;
}

/** "06:00" today, "Fri 06:00" this week, else "23 Oct". */
export function whenWords(iso: string | null, now = new Date()): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
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

export function VariableRow({ v, room, onChange }: { v: RoomVariable; room: string; onChange?: (what: string) => void }) {
  const text = valueText(v.value);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const when = whenWords(v.set_at);
  const users = shortNames(v.used_by.map((u) => u.name).sort((a, b) => a.localeCompare(b)));
  const expanded = open || draft !== null;

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
    <li className={cn("min-w-0", expanded && "col-span-full")}>
      {/* closed: one line, "Style · Color Field painting…"; a tap shows it all, who set it, which screens use it, Edit */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={expanded}
        disabled={draft !== null}
        className="flex min-h-11 w-full min-w-0 items-center gap-2 rounded-lg px-2 text-left hover:bg-stone-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:hover:bg-transparent"
      >
        <span className="min-w-0 flex-1 truncate text-[15px] leading-snug">
          <span className="font-semibold">{v.label}</span>
          {!expanded && (
            <span className={text ? "text-stone-700" : "text-muted-foreground"}> · {text || "Not set yet"}</span>
          )}
        </span>
        <ChevronDown
          aria-hidden
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", expanded && "rotate-180")}
        />
      </button>

      {expanded && (
        <div className="space-y-2 px-2 pb-3">
          {v.description && <p className="text-sm text-muted-foreground">{v.description}</p>}
          {draft !== null ? (
            <div className="space-y-2">
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
          ) : (
            <p className={cn("whitespace-pre-wrap break-words text-base leading-relaxed", !text && "text-muted-foreground")}>
              {text || "Not set yet"}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="min-w-0 flex-1 text-sm text-muted-foreground">
              {when ? `Set ${when} by ${v.set_by}` : `From ${v.owner_name}`}
              {users.length > 0 && ` · Used by ${users.join(" · ")}`}
            </p>
            {v.editable && draft === null && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-9 shrink-0 bg-card px-3"
                aria-label={`Change ${v.label}`}
                onClick={() => setDraft(typeof v.value === "string" ? v.value : text)}
              >
                <Pencil aria-hidden /> Edit
              </Button>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export function TodayCard({ room, title = "Today", className, onChange, footer }: TodayCardProps) {
  const { data, error } = useRoomSchedule(room);
  if (!data) return null;
  const { variables, actions } = data;
  if (!variables.length && !actions.length) return null;
  const sent = (ev: RoomEvent) => onChange?.(`${ev.label} sent`);

  return (
    <section aria-label={title ?? "Today"} className={cn("p-2 [container-type:inline-size] sm:px-3", className)}>
      {title && <h3 className="mb-1 px-2 pt-1 text-[17px] font-semibold">{title}</h3>}
      {variables.length > 0 && (
        <ul className="grid gap-x-4 [@container(min-width:640px)]:grid-cols-2">
          {variables.map((v) => (
            <VariableRow key={`${v.owner}|${v.name}`} v={v} room={room} onChange={onChange} />
          ))}
        </ul>
      )}
      {/* the actions as one row of chips (what each does in its tooltip), the footer at its end */}
      {actions.length > 0 ? (
        <EventsList
          room={room}
          onSent={sent}
          lead="Ask the schedule for new ideas:"
          inline
          end={footer}
          className={cn("px-2", variables.length > 0 && "mt-1 border-t border-border pt-2")}
        />
      ) : (
        footer && <div className="mt-1 px-1">{footer}</div>
      )}
      {error && <p className="mt-3 text-sm text-muted-foreground">Couldn't refresh just now; showing the last read.</p>}
    </section>
  );
}
