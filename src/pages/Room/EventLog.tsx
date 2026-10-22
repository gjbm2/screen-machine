// The event log: room_service events (LIVE_V1 §3) plus this page's own
// actions ("ui") and connection changes ("conn"). The last 200 entries are
// kept; full status pushes are counted rather than listed.

import { useMemo, useState } from "react";
import { Pause, Play, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { LogEntry, LogLevel } from "./types";

const LEVEL_RANK: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const KNOWN_TYPES = ["mode.transition", "screen.phase", "screen.mask", "stream", "log", "ui", "conn"];

const LEVEL_TEXT: Record<LogLevel, string> = {
  debug: "text-muted-foreground",
  info: "text-foreground",
  warn: "text-amber-800",
  error: "text-rose-700",
};

function fmtClock(t: number): string {
  const d = new Date(t);
  return `${d.toLocaleTimeString([], { hour12: false })}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

export function EventLog({
  log,
  statusCount,
  onClear,
  className,
}: {
  log: LogEntry[];
  statusCount: number;
  onClear: () => void;
  className?: string;
}) {
  const [text, setText] = useState("");
  const [minLevel, setMinLevel] = useState<LogLevel>("debug");
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(() => new Set());
  const [paused, setPaused] = useState<LogEntry[] | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const source = paused ?? log;
  const types = useMemo(() => {
    const seen = new Set(KNOWN_TYPES);
    for (const e of log) seen.add(e.type);
    return [...seen];
  }, [log]);

  const shown = useMemo(() => {
    const q = text.trim().toLowerCase();
    return source.filter(
      (e) =>
        !hiddenTypes.has(e.type) &&
        LEVEL_RANK[e.level] >= LEVEL_RANK[minLevel] &&
        (!q || e.summary.toLowerCase().includes(q) || e.type.includes(q) || (e.screen ?? "").includes(q)),
    );
  }, [source, hiddenTypes, minLevel, text]);

  const toggleType = (t: string) =>
    setHiddenTypes((s) => {
      const n = new Set(s);
      if (n.has(t)) n.delete(t);
      else n.add(t);
      return n;
    });

  const newSincePause = paused ? log.filter((e) => !paused.some((p) => p.id === e.id && p.count === e.count)).length : 0;

  return (
    <section
      className={cn("flex min-h-0 flex-col rounded-2xl border border-stone-200/80 bg-card", className)}
      data-testid="event-log"
    >
      <div className="space-y-2 border-b border-stone-200/80 p-3">
        <div className="flex items-center gap-1">
          <span className="mr-auto text-xs text-muted-foreground" title="Full status pushes received (not listed)">
            {statusCount} status updates
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-11 rounded-xl text-sm"
            onClick={() => setPaused((p) => (p ? null : log))}
            title={paused ? "Resume" : "Pause the view"}
          >
            {paused ? <Play /> : <Pause />}
            {paused ? `Resume${newSincePause ? ` (${newSincePause})` : ""}` : "Pause"}
          </Button>
          <Button variant="ghost" size="sm" className="h-11 w-11 rounded-xl p-0" onClick={onClear} title="Clear the log" aria-label="Clear the log">
            <Trash2 />
          </Button>
        </div>
        <div className="flex gap-2">
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Filter…"
            className="h-11 rounded-xl bg-white text-base"
            aria-label="Filter events"
          />
          <select
            value={minLevel}
            onChange={(e) => setMinLevel(e.target.value as LogLevel)}
            className="h-11 rounded-xl border border-input bg-white px-2 text-base"
            aria-label="Minimum level"
          >
            <option value="debug">all</option>
            <option value="info">info+</option>
            <option value="warn">warn+</option>
            <option value="error">errors</option>
          </select>
        </div>
        <div className="flex flex-wrap gap-1">
          {types.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => toggleType(t)}
              aria-pressed={!hiddenTypes.has(t)}
              className={cn(
                "min-h-9 rounded-full border px-3 py-1 font-mono text-xs",
                hiddenTypes.has(t)
                  ? "border-stone-200 text-muted-foreground line-through"
                  : "border-teal-200 bg-teal-50 text-teal-800",
              )}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <ol className="min-h-0 flex-1 overflow-y-auto p-1 font-mono text-xs leading-snug">
        {shown.length === 0 && <li className="p-3 text-center text-muted-foreground">No events{log.length ? " match" : " yet"}.</li>}
        {shown.map((e) => (
          <li key={e.id} className="rounded-lg px-2 py-1 hover:bg-stone-100/80">
            <button
              type="button"
              className="flex w-full min-w-0 items-baseline gap-2 text-left"
              onClick={() => setExpanded((x) => (x === e.id ? null : e.id))}
            >
              <span className="shrink-0 text-muted-foreground">{fmtClock(e.t)}</span>
              <span className="shrink-0 text-teal-700">{e.type}</span>
              <span className={cn("min-w-0 flex-1", expanded === e.id ? "break-words" : "truncate", LEVEL_TEXT[e.level])}>
                {e.summary}
              </span>
              {e.count > 1 && <span className="shrink-0 text-muted-foreground">×{e.count}</span>}
            </button>
            {expanded === e.id && e.data !== undefined && (
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-stone-100 p-2 text-[11px] text-stone-700">
                {JSON.stringify(e.data, null, 2)}
              </pre>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
