// Lights setup › Remote controls: the devices the RM4 Pro works by their own remotes (the pinspots, the glitterball;
// room_service remotes_api.py). Each button is learned once here (the RM4 Pro listens; press that button on the
// device's remote near it) and tested (it sends the code; watch the device). "Learn the rest" walks every button not
// learned yet, one after another. A light also has Reset (after it lost power at the wall it comes on white at full:
// dimmed to the floor and off, so the room knows what it is doing) and its colours' names. What a device does is
// chosen on the Room page, not here.

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Pencil, Radio, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { explainError, type RoomApi } from "@/pages/Room/api";
import { Notice, Spinner } from "@/pages/Room/bits";
import { CARD_CLASS } from "@/pages/Room/theme";
import type { RemoteButton, RemoteDevice, RemoteLearn } from "@/pages/Room/types";

const SMALL_BUTTON =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-xl border border-stone-200 bg-white px-3 text-[15px] hover:bg-stone-50 disabled:opacity-50";

/** A colour's name as a swatch, when the browser knows it as a colour ("Red", "Amber" doesn't). */
function swatchOf(name: string): string | null {
  const w = name.trim().toLowerCase().replace(/\s+/g, "");
  return w && typeof CSS !== "undefined" && CSS.supports("color", w) ? w : null;
}

/** "On · Red · 9 of 16 · pulse", or null when the room doesn't know. */
function stateWords(d: RemoteDevice): string | null {
  const n = d.state?.now;
  if (!n || n.main == null) return null;
  if (n.main === "off") return "Off";
  const colour = n.colour ? d.buttons.find((b) => b.id === n.colour)?.label ?? n.colour : null;
  const parts = ["On", colour, n.level != null ? `brightness ${n.level} of ${d.steps}` : null];
  if (n.pulse === "on") parts.push("pulse");
  if (n.flash === "on") parts.push("flash");
  return parts.filter(Boolean).join(" · ");
}

function LearnBox({ job, onStop }: { job: RemoteLearn; onStop: () => void }) {
  const big = job.phase === "sweep" || job.phase === "press" || job.phase === "found";
  return (
    <div aria-live="polite" className="space-y-2 rounded-xl bg-amber-50/70 p-3" data-testid="remote-learning">
      <p className="text-sm text-stone-600">Learning “{job.button}”</p>
      <p className={cn("flex items-center gap-2", big ? "text-lg font-semibold" : "text-base")}>
        <Spinner className="shrink-0" /> {job.words}
      </p>
      <Button variant="outline" onClick={onStop} className="h-10 rounded-xl">
        Stop
      </Button>
    </div>
  );
}

function ButtonRow({
  b,
  disabled,
  busy,
  colour,
  onLearn,
  onTest,
  onRename,
}: {
  b: RemoteButton;
  disabled: boolean;
  busy: boolean;
  colour: boolean;
  onLearn: () => void;
  onTest: () => void;
  onRename?: (name: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(b.label);
  const sw = colour ? swatchOf(b.label) : null;
  return (
    <li className="flex flex-wrap items-center gap-2 py-2">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {colour && (
          <span
            aria-hidden
            className="h-4 w-4 shrink-0 rounded-full border border-stone-300"
            style={{ background: sw ?? "transparent" }}
          />
        )}
        {editing && onRename ? (
          <form
            className="flex flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void onRename(name).then(() => setEditing(false));
            }}
          >
            <input
              autoFocus
              value={name}
              maxLength={40}
              onChange={(e) => setName(e.target.value)}
              aria-label="Colour name"
              className="min-h-10 w-full min-w-0 rounded-lg border border-stone-300 px-2 text-base"
            />
            <button type="submit" aria-label="Save the name" className={SMALL_BUTTON}>
              <Check className="h-4 w-4" aria-hidden />
            </button>
          </form>
        ) : (
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-1">
              <span className="truncate text-base font-medium">{b.label}</span>
              {onRename && (
                <button
                  type="button"
                  aria-label={`Rename ${b.label}`}
                  onClick={() => setEditing(true)}
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-stone-500 hover:bg-stone-100"
                >
                  <Pencil className="h-3.5 w-3.5" aria-hidden />
                </button>
              )}
            </div>
            <p className={cn("text-sm", b.learned ? "text-teal-700" : "text-stone-500")}>
              {b.learned ? "Learned" : "Not learned"}
            </p>
          </div>
        )}
      </div>
      {!editing && (
        <div className="flex shrink-0 gap-2">
          <button type="button" disabled={disabled} onClick={onLearn} className={SMALL_BUTTON}>
            <Radio className="h-4 w-4" aria-hidden /> {b.learned ? "Again" : "Learn"}
          </button>
          <button type="button" disabled={disabled || !b.learned} onClick={onTest} className={SMALL_BUTTON}>
            {busy ? <Spinner /> : <Send className="h-4 w-4" aria-hidden />} Test
          </button>
        </div>
      )}
    </li>
  );
}

function RemoteCard({ d, api, onChanged }: { d: RemoteDevice; api: RoomApi; onChanged: () => void }) {
  const [job, setJob] = useState<RemoteLearn | null>(d.job?.running ? d.job : null);
  const [last, setLast] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [sweep, setSweep] = useState(false);
  const queue = useRef<string[]>([]);

  const learn = useCallback(
    async (button: string) => {
      setErr(null);
      setLast(null);
      try {
        setJob(await api.learnButton(d.id, button, sweep));
        setSweep(false);
      } catch (e) {
        queue.current = [];
        setErr(explainError(e));
      }
    },
    [api, d.id, sweep],
  );

  // While learning: its progress every half second; when it ends, the next in the queue (after a breath) or done.
  const running = !!job?.running;
  useEffect(() => {
    if (!running) return;
    let alive = true;
    const t = window.setInterval(async () => {
      try {
        const j = await api.learnProgress(d.id);
        if (!alive) return;
        setJob(j);
        if (j.running) return;
        onChanged();
        if (j.phase === "done") {
          setLast(`Learned “${j.button}”.`);
          const next = queue.current.shift();
          if (next) window.setTimeout(() => alive && void learn(next), 1500);
        } else {
          queue.current = [];
          setErr(j.words);
        }
      } catch (e) {
        if (alive) setErr(explainError(e));
      }
    }, 500);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [running, api, d.id, onChanged, learn]);

  const stop = async () => {
    queue.current = [];
    try {
      setJob(await api.stopLearning(d.id));
    } catch (e) {
      setErr(explainError(e));
    }
  };

  const act = async (what: string, fn: () => Promise<{ status: string; detail: string }>) => {
    setBusy(what);
    setErr(null);
    setLast(null);
    try {
      const out = await fn();
      setLast(out.detail ? out.detail.charAt(0).toUpperCase() + out.detail.slice(1) + "." : "Sent.");
      onChanged();
    } catch (e) {
      setErr(explainError(e));
    } finally {
      setBusy(null);
    }
  };

  const missing = d.buttons.filter((b) => !b.learned).map((b) => b.id);
  const learnedCount = d.buttons.length - missing.length;
  const groups = d.buttons.reduce<[string, RemoteButton[]][]>((out, b) => {
    const g = out.find(([name]) => name === b.group);
    if (g) g[1].push(b);
    else out.push([b.group, [b]]);
    return out;
  }, []);
  const how =
    d.medium === "ir"
      ? "Infrared: point its remote at the RM4 Pro."
      : d.freq_mhz
        ? `Radio, ${d.freq_mhz.toFixed(2)} MHz: press each button near the RM4 Pro.`
        : "Radio: the first button finds the frequency (hold it down until asked to let go).";
  const now = d.light ? stateWords(d) : null;
  const disabled = running || !!busy;

  return (
    <div className={cn(CARD_CLASS, "space-y-3 px-3 py-3 sm:px-4")} data-testid={`remote-${d.id}`}>
      <div>
        <h3 className="text-lg font-semibold text-stone-800">{d.label}</h3>
        <p className="text-[15px] text-muted-foreground">
          {learnedCount} of {d.buttons.length} buttons learned. {how}
        </p>
        {d.light && (
          <p className="text-[15px] text-stone-700">
            {now ? `Now: ${now}.` : "The room isn't sure what it's doing: Reset puts that right."}
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {missing.length > 0 && (
          <Button
            disabled={disabled}
            onClick={() => {
              queue.current = missing.slice(1);
              void learn(missing[0]);
            }}
            className="h-11 rounded-xl"
          >
            <Radio aria-hidden /> {learnedCount ? "Learn the rest" : "Learn every button"}
          </Button>
        )}
        {d.light && (
          <Button
            variant="outline"
            disabled={disabled || missing.some((m) => ["down", "off"].includes(m))}
            onClick={() => void act("reset", () => api.resetLight(d.id))}
            className="h-11 rounded-xl"
          >
            {busy === "reset" && <Spinner />} Reset
          </Button>
        )}
      </div>
      {d.light && (
        <p className="text-sm text-muted-foreground">
          Reset is for after it was off at the wall (it comes back white at full): it dims to the floor and switches off,
          so the room knows exactly what it is doing.
        </p>
      )}

      {job?.running && <LearnBox job={job} onStop={() => void stop()} />}
      {last && <Notice tone="ok" className="text-base">{last}</Notice>}
      {err && <Notice tone="bad" className="text-base">{err}</Notice>}

      {groups.map(([group, buttons]) => (
        <div key={group}>
          <h4 className="text-sm font-semibold uppercase tracking-wide text-stone-500">{group}</h4>
          <ul className="divide-y divide-stone-100">
            {buttons.map((b) => (
              <ButtonRow
                key={b.id}
                b={b}
                disabled={disabled}
                busy={busy === b.id}
                colour={group === "Colour"}
                onLearn={() => {
                  queue.current = [];
                  void learn(b.id);
                }}
                onTest={() => void act(b.id, () => api.sendButton(d.id, b.id))}
                onRename={
                  d.light && group === "Colour"
                    ? async (name) => {
                        try {
                          await api.nameColours(d.id, { [b.id]: name });
                          onChanged();
                        } catch (e) {
                          setErr(explainError(e));
                          throw e;
                        }
                      }
                    : undefined
                }
              />
            ))}
          </ul>
        </div>
      ))}

      {d.medium === "rf" && d.freq_mhz && (
        <label className="flex items-center gap-2 text-sm text-stone-600">
          <input type="checkbox" checked={sweep} onChange={(e) => setSweep(e.target.checked)} />
          Find the frequency again for the next button (if a button won't learn)
        </label>
      )}
    </div>
  );
}

/** Every remote-controlled device; nothing when room_service has none (or no such route yet). */
export function RemoteControls({ api }: { api: RoomApi }) {
  const [devices, setDevices] = useState<RemoteDevice[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    api
      .remotes()
      .then((v) => {
        setDevices(v);
        setErr(null);
      })
      .catch((e) => setErr(explainError(e)));
  }, [api]);
  useEffect(load, [load]);

  if (!devices?.length && !err) return null;
  return (
    <section aria-label="Remote controls" className="space-y-2" data-testid="remote-controls">
      <h2 className="text-lg font-semibold text-stone-800">Remote controls</h2>
      <p className="text-sm text-muted-foreground">
        Lights and effects the room works with their own remotes, through the RM4 Pro. Learn each button once, then Test
        it while watching the device.
      </p>
      {err && <Notice tone="bad" className="text-base">{err}</Notice>}
      {devices?.map((d) => <RemoteCard key={d.id} d={d} api={api} onChanged={load} />)}
    </section>
  );
}
