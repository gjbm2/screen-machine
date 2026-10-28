// Light setup (Settings) › Appliances worked by remote: the devices the RM4 Pro works by their own remotes (room_service
// remotes_api.py). Config-driven: none until one is added here from a device type (a pinspot, a glitterball) into a
// room; it then has a card on /lights and the Room page. Each appliance folds open to its buttons: each learned once
// (the RM4 Pro listens; press that button on the appliance's remote near it) and tested (it sends the code; watch the
// appliance). A button the file lists under "repeat" (a pinspot's + and −) keeps sending while its Test is held, as the
// remote does. "Each press is sent" (RF) picks a clean packet of 4, 6 or 10 copies of the code, or the code as learned
// (owner, 28 Oct: the far pinspot missed quick presses), to try while watching. "Learn the rest" walks every button not learned yet, one after another. A light also has Reset (after it
// lost power at the wall it comes on white at full: dimmed to the floor and off, so the room knows what it is doing)
// and its colours' names. "What it's doing now" puts the room's record right when it's out of step with the appliance
// (its power is one toggle button, so the room must know; owner, 28 Oct): a tap tells the room, nothing is sent.
// Remove takes it away (its learned buttons and card too). What an appliance does is chosen
// on /lights and the Room page, not here.

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Pencil, Plus, Radio, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { explainError, type RoomApi } from "@/pages/Room/api";
import { Notice, Spinner } from "@/pages/Room/bits";
import { CARD_CLASS } from "@/pages/Room/theme";
import type { RemoteButton, RemoteDevice, RemoteLearn, RemotesView } from "@/pages/Room/types";

const SMALL_BUTTON =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-xl border border-stone-200 bg-white px-3 text-[15px] hover:bg-stone-50 disabled:opacity-50";

/** A colour's name as a swatch, when the browser knows it as a colour ("Red", "Amber" doesn't). */
function swatchOf(name: string): string | null {
  const w = name.trim().toLowerCase().replace(/\s+/g, "");
  return w && typeof CSS !== "undefined" && CSS.supports("color", w) ? w : null;
}

type StateRow = NonNullable<RemoteDevice["state_rows"]>[number];

/** A row's heading without the appliance's name ("Pinspots colour" -> "Colour"; its power row -> "Power"). */
function rowWords(row: StateRow, name: string): string {
  const l = row.label.trim();
  if (l === name) return "Power";
  const rest = l.toLowerCase().startsWith(name.toLowerCase()) ? l.slice(name.length).trim() : l;
  return rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : l;
}

/** What the room believes the appliance is doing, each value one tap to correct (nothing is sent). */
function StateRows({ d, api, onChanged }: { d: RemoteDevice; api: RoomApi; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const rows = d.state_rows ?? [];
  if (!rows.length) return null;
  const put = async (t: string, v: unknown) => {
    setBusy(t);
    setErr(null);
    try {
      await api.setRemoteState(d.id, { [t]: v });
      onChanged();
    } catch (e) {
      setErr(explainError(e));
    } finally {
      setBusy(null);
    }
  };
  const chip = (sel: boolean) =>
    cn(
      "min-h-9 rounded-lg border px-2.5 text-[14px] disabled:opacity-50",
      sel ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
    );
  return (
    <div className="space-y-2 rounded-xl bg-stone-50 p-3" data-testid={`remote-state-${d.id}`}>
      <p className="text-base font-medium">What it's doing now</p>
      <p className="text-sm text-muted-foreground">
        If the room has it wrong, tap what it's really doing. This only tells the room; nothing is sent.
      </p>
      {rows.map((row) => {
        const words = row.schema["x-ui"]?.labels ?? {};
        const lo = row.schema.minimum;
        const hi = row.schema.maximum;
        return (
          <div key={row.target} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="w-24 shrink-0 text-[15px] text-stone-700">{rowWords(row, d.label)}</span>
            {row.schema.enum ? (
              <div className="flex flex-1 flex-wrap gap-1.5">
                {row.schema.enum.map((v) => (
                  <button key={v} type="button" disabled={!!busy} onClick={() => void put(row.target, v)} className={chip(row.value === v)}>
                    {words[v] ?? v}
                  </button>
                ))}
              </div>
            ) : typeof lo === "number" && typeof hi === "number" ? (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  aria-label="Less"
                  disabled={!!busy || row.value === lo}
                  onClick={() => void put(row.target, typeof row.value === "number" ? row.value - 1 : lo)}
                  className={chip(false)}
                >
                  −
                </button>
                <span className="w-16 text-center font-mono text-[15px] tabular-nums">
                  {typeof row.value === "number" ? `${row.value} of ${hi}` : "?"}
                </span>
                <button
                  type="button"
                  aria-label="More"
                  disabled={!!busy || row.value === hi}
                  onClick={() => void put(row.target, typeof row.value === "number" ? row.value + 1 : hi)}
                  className={chip(false)}
                >
                  +
                </button>
              </div>
            ) : null}
            {busy === row.target && <Spinner />}
          </div>
        );
      })}
      {err && <p className="text-sm text-rose-700">{err}</p>}
    </div>
  );
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
  onHold,
  onRename,
}: {
  b: RemoteButton;
  disabled: boolean;
  busy: boolean;
  colour: boolean;
  onLearn: () => void;
  onTest: () => void;
  /** A repeating button: start sending while held (true), stop on letting go (false). */
  onHold?: (held: boolean) => void;
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
          {onHold ? (
            <button
              type="button"
              disabled={(disabled && !busy) || !b.learned}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                onHold(true);
              }}
              onPointerUp={() => onHold(false)}
              onPointerCancel={() => onHold(false)}
              onKeyDown={(e) => (e.key === " " || e.key === "Enter") && !e.repeat && onHold(true)}
              onKeyUp={() => onHold(false)}
              onContextMenu={(e) => e.preventDefault()}
              className={cn(SMALL_BUTTON, "select-none touch-none", busy && "border-teal-300 bg-teal-50")}
            >
              {busy ? <Spinner /> : <Send className="h-4 w-4" aria-hidden />} Hold
            </button>
          ) : (
            <button type="button" disabled={disabled || !b.learned} onClick={onTest} className={SMALL_BUTTON}>
              {busy ? <Spinner /> : <Send className="h-4 w-4" aria-hidden />} Test
            </button>
          )}
        </div>
      )}
    </li>
  );
}

function RemoteCard({
  d,
  api,
  roomName,
  open,
  onToggle,
  onChanged,
}: {
  d: RemoteDevice;
  api: RoomApi;
  roomName: string | null;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const [removing, setRemoving] = useState(false);
  const [job, setJob] = useState<RemoteLearn | null>(d.job?.running ? d.job : null);
  const [last, setLast] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [sweep, setSweep] = useState(false);
  const queue = useRef<string[]>([]);
  const held = useRef(false);

  /** Hold-to-repeat: one press at once, then one every gap_s until let go (each waits for the last to be sent). */
  const hold = async (button: string, on: boolean) => {
    if (!on) {
      held.current = false;
      return;
    }
    if (held.current) return;
    held.current = true;
    setBusy(button);
    setErr(null);
    setLast(null);
    let n = 0;
    try {
      while (held.current) {
        await api.sendButton(d.id, button);
        n += 1;
        if (held.current) await new Promise((r) => window.setTimeout(r, Math.max(100, (d.gap_s ?? 0.3) * 1000)));
      }
      setLast(`Sent “${button}” ${n} time${n === 1 ? "" : "s"}.`);
    } catch (e) {
      setErr(explainError(e));
    } finally {
      held.current = false;
      setBusy(null);
      onChanged();
    }
  };

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
  const disabled = running || !!busy;

  const head = (
    <button
      type="button"
      aria-expanded={open}
      onClick={onToggle}
      className="flex w-full items-center gap-3 rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-lg font-semibold text-stone-800">{d.label}</span>
        <span className="block text-[15px] text-muted-foreground">
          {[roomName, `${learnedCount} of ${d.buttons.length} buttons learned`].filter(Boolean).join(" · ")}
        </span>
      </span>
      <ChevronDown aria-hidden className={cn("h-5 w-5 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
    </button>
  );
  if (!open) {
    return (
      <div className={cn(CARD_CLASS, "px-3 py-3 sm:px-4")} data-testid={`remote-${d.id}`}>
        {head}
      </div>
    );
  }

  return (
    <div className={cn(CARD_CLASS, "space-y-3 px-3 py-3 sm:px-4")} data-testid={`remote-${d.id}`}>
      <div>
        {head}
        <p className="mt-1 text-[15px] text-muted-foreground">{how}</p>
      </div>

      <StateRows d={d} api={api} onChanged={onChanged} />

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
            disabled={disabled || missing.includes("down") || !d.buttons.some((b) => b.learned && ["off", "toggle"].includes(b.id))}
            onClick={() => void act("reset", () => api.resetLight(d.id))}
            className="h-11 rounded-xl"
          >
            {busy === "reset" && <Spinner />} Reset
          </Button>
        )}
      </div>
      {d.light && (
        <p className="text-sm text-muted-foreground">
          Reset is for after it was off at the wall (it comes back white at full): it dims to black and switches off,
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
                onHold={(d.repeat ?? []).includes(b.id) ? (on) => void hold(b.id, on) : undefined}
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

      {d.medium === "rf" && (
        <div className="space-y-1">
          <p className="text-[15px] font-medium">Each press is sent</p>
          <div className="flex flex-wrap gap-1.5">
            {([
              ["As learned", null],
              ["Clean ×4", 4],
              ["Clean ×6", 6],
              ["Clean ×10", 10],
            ] as [string, number | null][]).map(([words, frames]) => {
              const sel = frames === null ? !d.clean : d.clean?.frames === frames;
              return (
                <button
                  key={words}
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    void act("clean", async () => {
                      await api.setRemoteClean(d.id, frames === null ? null : { frames });
                      return { status: "ok", detail: `each press now sent ${frames === null ? "as learned" : `as ${frames} clean copies`}` };
                    })
                  }
                  className={cn(
                    "min-h-9 rounded-lg border px-2.5 text-[14px] disabled:opacity-50",
                    sel ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
                  )}
                >
                  {words}
                </button>
              );
            })}
          </div>
          <p className="text-sm text-muted-foreground">
            Clean sends just the code, several times over, then a pause, so quick presses stay separate. More copies
            carry further but take longer.
          </p>
        </div>
      )}

      {d.medium === "rf" && d.freq_mhz && (
        <label className="flex items-center gap-2 text-sm text-stone-600">
          <input type="checkbox" checked={sweep} onChange={(e) => setSweep(e.target.checked)} />
          Find the frequency again for the next button (if a button won't learn)
        </label>
      )}

      <div className="border-t border-stone-100 pt-2">
        {removing ? (
          <div className="space-y-2">
            <p className="text-base">Remove {d.label}? Its learned buttons and its card on the Lights and Room pages go too.</p>
            <div className="flex gap-2">
              <Button
                variant="destructive"
                disabled={disabled}
                className="h-11 rounded-xl"
                onClick={() =>
                  void act("remove", async () => {
                    await api.removeRemote(d.id);
                    return { status: "ok", detail: "" };
                  })
                }
              >
                {busy === "remove" && <Spinner />} Remove
              </Button>
              <Button variant="outline" className="h-11 rounded-xl" onClick={() => setRemoving(false)}>
                Keep it
              </Button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            disabled={disabled}
            onClick={() => setRemoving(true)}
            className="inline-flex min-h-10 items-center gap-1.5 text-sm text-stone-600 hover:text-rose-700 disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" aria-hidden /> Remove this appliance
          </button>
        )}
      </div>
    </div>
  );
}

/** Add an appliance: its kind, its name, its room (and the RM4 Pro, when the room doesn't know one yet). */
function AddAppliance({ api, view, onAdded }: { api: RoomApi; view: RemotesView; onAdded: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState<string>(view.types[0]?.id ?? "");
  const type = view.types.find((t) => t.id === model);
  const [label, setLabel] = useState("");
  const [room, setRoom] = useState<string>(view.rooms[0]?.id ?? "");
  const [host, setHost] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const needHost = view.hosts.length !== 1;
  if (!view.types.length) return null;
  if (!open) {
    return (
      <Button onClick={() => setOpen(true)} className="h-11 w-full rounded-xl px-5 text-base sm:w-auto" data-testid="add-appliance">
        <Plus aria-hidden /> Add an appliance
      </Button>
    );
  }
  const field = "min-h-11 w-full rounded-xl border border-stone-300 bg-white px-3 text-base";
  return (
    <form
      className={cn(CARD_CLASS, "space-y-3 px-3 py-3 sm:px-4")}
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setErr(null);
        api
          .addRemote(model, label.trim() || type?.label || model, room, needHost ? host.trim() : undefined)
          .then((d) => {
            setOpen(false);
            setLabel("");
            onAdded(d.id);
          })
          .catch((x) => setErr(explainError(x)))
          .finally(() => setBusy(false));
      }}
    >
      <h3 className="text-lg font-semibold text-stone-800">Add an appliance</h3>
      <div role="radiogroup" aria-label="What is it?" className="grid gap-2 sm:grid-cols-2">
        {view.types.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={model === t.id}
            onClick={() => setModel(t.id)}
            className={cn(
              "rounded-xl border px-3 py-2 text-left",
              model === t.id ? "border-teal-300 bg-teal-50" : "border-stone-200 bg-white hover:bg-stone-50",
            )}
          >
            <span className="block text-base font-semibold">{t.label}</span>
            {t.description && <span className="block text-sm text-muted-foreground">{t.description}</span>}
          </button>
        ))}
      </div>
      <label className="block space-y-1">
        <span className="text-base font-medium">Name</span>
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={type?.label} maxLength={40} className={field} />
      </label>
      {view.rooms.length > 1 && (
        <label className="block space-y-1">
          <span className="text-base font-medium">Room</span>
          <select value={room} onChange={(e) => setRoom(e.target.value)} className={field}>
            {view.rooms.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {needHost && (
        <label className="block space-y-1">
          <span className="text-base font-medium">RM4 Pro's address</span>
          {view.hosts.length > 1 ? (
            <select value={host} onChange={(e) => setHost(e.target.value)} className={field}>
              <option value="">Choose…</option>
              {view.hosts.map((h) => (
                <option key={h} value={h}>
                  {h}
                </option>
              ))}
            </select>
          ) : (
            <input value={host} onChange={(e) => setHost(e.target.value)} inputMode="decimal" className={field} />
          )}
        </label>
      )}
      {err && <Notice tone="bad" className="text-base">{err}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" disabled={busy || !model || !room || (needHost && !host.trim())} className="h-11 rounded-xl px-5">
          {busy && <Spinner />} Add
        </Button>
        <Button type="button" variant="outline" onClick={() => setOpen(false)} className="h-11 rounded-xl">
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** The appliances worked by remote: each one added, and Add an appliance. Nothing without the route (an older room). */
export function RemoteControls({ api }: { api: RoomApi }) {
  const [view, setView] = useState<RemotesView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const load = useCallback(() => {
    api
      .remotes()
      .then((v) => {
        setView(v);
        setErr(null);
      })
      .catch((e) => setErr(explainError(e)));
  }, [api]);
  useEffect(load, [load]);

  if (!view && !err) return null;
  const roomName = (id?: string | null) => view?.rooms.find((r) => r.id === id)?.name ?? null;
  return (
    <section aria-label="Appliances worked by remote" className="space-y-2" data-testid="remote-controls">
      <h2 className="text-lg font-semibold text-stone-800">Appliances worked by remote</h2>
      <p className="text-sm text-muted-foreground">
        Lights and effects with their own remote (a glitterball, pinspots), worked through the RM4 Pro. Add one, then
        learn each of its buttons once and Test it while watching.
      </p>
      {err && <Notice tone="bad" className="text-base">{err}</Notice>}
      {view?.remotes.map((d) => (
        <RemoteCard
          key={d.id}
          d={d}
          api={api}
          roomName={roomName(d.room)}
          open={openId === d.id}
          onToggle={() => setOpenId((o) => (o === d.id ? null : d.id))}
          onChanged={load}
        />
      ))}
      {view && (
        <AddAppliance
          api={api}
          view={view}
          onAdded={(id) => {
            setOpenId(id);
            load();
          }}
        />
      )}
    </section>
  );
}
