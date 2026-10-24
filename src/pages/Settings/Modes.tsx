// Settings › Room & modes (#modes): the modes as a list (each with Edit, which opens the Room page's mode editor, Start
// here, Duplicate and Move), how long Auto waits before it changes mode again, what each mode does and the room's own
// order for switching (read only), and the rooms and what each holds (read only, from rooms.json via the status).
// Every write is the room's own config save (PUT /api/v1/room/config: the whole modes file, refused if someone saved
// meanwhile; room_service reloads it at once). Deleting a mode stays with the mode's own ⋯ on the Room page, which
// tidies everything that names it.

import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Copy, LayoutGrid, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { explainError, RoomApiError, type RoomApi } from "@/pages/Room/api";
import { Notice, Spinner, Unroll } from "@/pages/Room/bits";
import { modeChanges, notConnectedWords } from "@/pages/Room/modeWords";
import type { ModeDef, RawMode, RawRoomConfig, RoomDescribe, RoomStatus } from "@/pages/Room/types";
import type { NewLogEntry } from "@/pages/Room/useRoomConnection";
import { friendlyName, modeDescription, modeLabel } from "@/pages/Room/words";
import { Group, Hint, LinkRow, ReadRow, Row, Sub } from "./Group";

type Json = Record<string, unknown>;
const isObj = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

const ICON_BTN =
  "grid h-10 w-10 shrink-0 place-items-center rounded-full text-stone-600 hover:bg-stone-200/60 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function newModeId(label: string, taken: Record<string, unknown>): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "mode";
  let id = base;
  for (let n = 2; id in taken; n++) id = `${base}-${n}`;
  return id;
}

/** A full copy (modes are flat), without its comments and without the triggers that switch it on by itself, so a copy
 *  never takes over the room before someone says so (as the Room page's Duplicate). */
function copyOf(src: RawMode, label: string): RawMode {
  const copy = clone(src);
  for (const k of Object.keys(copy)) if (k.startsWith("_")) delete copy[k];
  if (isObj(copy.autopilot)) {
    delete copy.autopilot.enter_when;
    delete copy.autopilot.otherwise_suggest_when;
    if (!Object.keys(copy.autopilot).length) delete copy.autopilot;
  }
  return { ...copy, label };
}

function saveWords(e: unknown): string {
  if (e instanceof RoomApiError && e.status === 0) return "The room isn't answering. Try again in a moment.";
  if (e instanceof RoomApiError && e.status === 409) return "Someone else changed the modes just now. Try again.";
  if (e instanceof RoomApiError && e.errors?.length) return "The room's checks found a problem with that, so nothing changed.";
  return explainError(e);
}

/** The room's own switching-order rules, in words: each rule's note, else from → to. */
function ruleWords(r: unknown, modes: Record<string, ModeDef> | null): string | null {
  if (!isObj(r)) return null;
  if (typeof r._note === "string" && r._note.trim()) return r._note.trim();
  const side = (v: unknown) =>
    v === "*" || v === undefined ? "any mode" : Array.isArray(v) ? v.map((x) => modeLabel(String(x).replace(/^@/, ""), modes)).join(", ") : modeLabel(String(v).replace(/^@/, ""), modes);
  return `From ${side(r.from)} to ${side(r.to)}`;
}

type ModesProps = {
  api: RoomApi;
  status: RoomStatus | null;
  modes: Record<string, ModeDef> | null;
  describe: RoomDescribe | null;
  names: Record<string, string>;
  onEdit: (name: string) => void;
  onChanged: () => void;
  addLog: (e: NewLogEntry) => void;
};

export function ModesGroup(props: ModesProps) {
  const { modes, status } = props;
  return (
    <Group
      id="modes"
      title="Room & modes"
      icon={<LayoutGrid />}
      summary={modes ? `${Object.keys(modes).length} modes · starts in ${modeLabel(status?.mode?.default ?? null, modes)}` : "Modes"}
    >
      <ModesBody {...props} />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function ModesBody({
  api,
  status,
  modes,
  describe,
  names,
  onEdit,
  onChanged,
  addLog,
}: ModesProps) {
  const [cfg, setCfg] = useState<{ config: RawRoomConfig; revision: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dwell, setDwell] = useState("");
  const revision = status?.config?.revision;

  // the file itself (for the start mode, Auto's wait and the switching rules), read again when it changes
  useEffect(() => {
    let alive = true;
    api
      .roomConfig()
      .then((c) => alive && setCfg({ config: c.config, revision: c.revision }))
      .catch(() => alive && setCfg(null));
    return () => {
      alive = false;
    };
  }, [api, revision]);

  const ap = isObj(cfg?.config.autopilot) ? (cfg?.config.autopilot as Json) : null;
  const dwellNow = typeof ap?.min_dwell_s === "number" ? (ap.min_dwell_s as number) : null;
  useEffect(() => setDwell(dwellNow === null ? "" : String(dwellNow)), [dwellNow]);

  const startMode = status?.mode?.default ?? (typeof cfg?.config.default === "string" ? cfg.config.default : null);
  const current = status?.mode?.current ?? null;
  const entries = Object.entries(modes ?? {});
  const wordsOpts = { names, live: status?.subsystems };

  const edit = async (what: string, fn: (c: RawRoomConfig) => void, note: string): Promise<boolean> => {
    setBusy(what);
    setError(null);
    try {
      const { config, revision: rev } = await api.roomConfig();
      const next = clone(config);
      fn(next);
      const saved = await api.saveRoomConfig(next, rev, note);
      setCfg({ config: next, revision: saved?.revision ?? rev });
      addLog({ type: "ui", summary: note });
      onChanged();
      return true;
    } catch (e) {
      setError(saveWords(e));
      addLog({ type: "ui", level: "error", summary: `${note}: refused: ${explainError(e)}` });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const move = (name: string, by: -1 | 1) =>
    void edit(
      `move-${name}`,
      (c) => {
        const keys = Object.keys(c.modes ?? {});
        const shown = keys.filter((k) => !!modes && k in modes);
        const i = shown.indexOf(name);
        const j = i + by;
        if (i < 0 || j < 0 || j >= shown.length) return;
        const a = keys.indexOf(name);
        const b = keys.indexOf(shown[j]);
        [keys[a], keys[b]] = [keys[b], keys[a]];
        const all = c.modes ?? {};
        c.modes = Object.fromEntries(keys.map((k) => [k, all[k]]));
      },
      `mode ${name} moved ${by < 0 ? "earlier" : "later"} in Settings`,
    );

  const duplicate = (name: string) => {
    let id = "";
    void edit(
      `copy-${name}`,
      (c) => {
        const all = c.modes ?? {};
        const label = `${all[name]?.label || modeLabel(name, modes)} (copy)`;
        id = newModeId(label, all);
        const out: Record<string, RawMode> = {};
        for (const [k, v] of Object.entries(all)) {
          out[k] = v;
          if (k === name) out[id] = copyOf(all[name] ?? {}, label);
        }
        c.modes = out;
      },
      `mode ${name} copied in Settings`,
    ).then((ok) => ok && id && onEdit(id));
  };

  const startHere = (name: string) => void edit(`start-${name}`, (c) => void (c.default = name), `start mode → ${name} in Settings`);

  const dwellN = Number(dwell);
  const dwellOk = dwell.trim() !== "" && Number.isFinite(dwellN) && dwellN >= 0 && dwellN !== dwellNow;
  const saveDwell = () =>
    void edit(
      "dwell",
      (c) => {
        c.autopilot = { ...(isObj(c.autopilot) ? c.autopilot : {}), min_dwell_s: Math.round(dwellN) };
      },
      `Auto's wait between mode changes → ${Math.round(dwellN)} s in Settings`,
    );

  const rules = Array.isArray(cfg?.config.transitions) ? (cfg?.config.transitions as unknown[]) : [];
  const notConnected = notConnectedWords(describe, status?.subsystems);
  const rooms = status?.rooms ?? [];
  const screenName = (id: string) => status?.screens?.[id]?.name || friendlyName(id);

  return (
    <>
      {error && <Notice tone="bad">{error}</Notice>}

      <Sub>Modes</Sub>
      <Hint>The tiles on the Room page, in this order. Tap a tile there to switch the room.</Hint>
      {modes === null ? (
        <p className="flex items-center gap-2 text-base text-muted-foreground">
          <Spinner /> Loading modes…
        </p>
      ) : (
        <ul className="divide-y divide-stone-200/80 rounded-2xl border border-stone-200/80 bg-card" data-testid="settings-modes-list">
          {entries.map(([name, def], i) => {
            const label = def.label || modeLabel(name, modes);
            return (
              <li key={name} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2">
                <div className="min-w-0 flex-1 basis-40">
                  <p className="text-base font-semibold leading-snug">
                    {label}
                    {name === current && <span className="ml-2 text-[13px] font-semibold text-teal-700">Now</span>}
                    {name === startMode && <span className="ml-2 text-[13px] font-normal text-muted-foreground">Starts here</span>}
                  </p>
                  {modeDescription(def) && <p className="line-clamp-2 text-sm text-muted-foreground">{modeDescription(def)}</p>}
                </div>
                <div className="flex shrink-0 items-center">
                  {busy?.endsWith(`-${name}`) && <Spinner className="mr-1" />}
                  <button type="button" className={ICON_BTN} onClick={() => onEdit(name)} aria-label={`Edit ${label}`} title="Edit">
                    <Pencil className="h-4 w-4" aria-hidden />
                  </button>
                  <button type="button" className={ICON_BTN} onClick={() => duplicate(name)} aria-label={`Duplicate ${label}`} title="Duplicate" disabled={!!busy}>
                    <Copy className="h-4 w-4" aria-hidden />
                  </button>
                  <button type="button" className={ICON_BTN} onClick={() => move(name, -1)} aria-label={`Move ${label} earlier`} title="Move earlier" disabled={!!busy || i === 0}>
                    <ArrowUp className="h-4 w-4" aria-hidden />
                  </button>
                  <button
                    type="button"
                    className={ICON_BTN}
                    onClick={() => move(name, 1)}
                    aria-label={`Move ${label} later`}
                    title="Move later"
                    disabled={!!busy || i === entries.length - 1}
                  >
                    <ArrowDown className="h-4 w-4" aria-hidden />
                  </button>
                </div>
                {name !== startMode && (
                  <div className="basis-full">
                    <button
                      type="button"
                      onClick={() => startHere(name)}
                      disabled={!!busy}
                      className="min-h-9 rounded-md text-sm font-medium text-teal-800 underline-offset-4 hover:underline disabled:opacity-50"
                    >
                      Start here
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <Hint>To delete a mode, use its ⋯ on the Room page: it tidies everything else that names it.</Hint>

      <Row
        label="Auto waits before changing mode again"
        hint={dwellNow === null ? "Not set in the modes file: Auto uses its own." : `Now ${dwellNow} s.`}
        stack
        testId="settings-modes-dwell"
      >
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (dwellOk) saveDwell();
          }}
        >
          <Input
            type="number"
            inputMode="numeric"
            min={0}
            step={5}
            value={dwell}
            onChange={(e) => setDwell(e.target.value)}
            aria-label="Seconds"
            className="h-11 w-28 rounded-xl bg-white text-base"
          />
          <span className="text-[15px] text-muted-foreground">seconds</span>
          <Button type="submit" disabled={!dwellOk || !!busy} className="h-11 rounded-xl px-5 text-base">
            Save
          </Button>
        </form>
      </Row>

      <Unroll title="What each mode does" testId="settings-modes-does">
        <ul className="space-y-2 pb-2">
          {entries.map(([name, def]) => {
            const changes = modeChanges(describe, name, wordsOpts);
            return (
              <li key={name} className="rounded-xl bg-white px-3 py-2">
                <p className="text-[15px] font-medium">{def.label || modeLabel(name, modes)}</p>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {changes || (describe ? "Changes nothing in the room." : "Reading what it does…")}
                </p>
              </li>
            );
          })}
        </ul>
        {notConnected.map((s) => (
          <p key={s} className="pb-2 text-sm text-muted-foreground">
            {s}
          </p>
        ))}
      </Unroll>

      <Unroll title="The order things change in" hint={rules.length ? `${rules.length} rules` : undefined} testId="settings-modes-rules">
        {rules.length ? (
          <ol className="list-decimal space-y-1.5 pb-2 pl-6 text-[15px] leading-snug">
            {rules.map((r, i) => {
              const w = ruleWords(r, modes);
              return w ? <li key={i}>{w}</li> : null;
            })}
          </ol>
        ) : (
          <Hint className="pb-2">{cfg ? "The room's usual order, with no rules of its own." : "Reading the modes file…"}</Hint>
        )}
        <Hint className="pb-2">Read only here for now.</Hint>
      </Unroll>

      <Sub>Rooms</Sub>
      {rooms.length ? (
        <div className="space-y-3" data-testid="settings-rooms">
          {rooms.map((r) => (
            <div key={r.id} className={cn("space-y-1 rounded-2xl border border-stone-200/80 bg-card px-3 py-2")}>
              <p className="text-base font-semibold">{r.name || r.id}</p>
              <ReadRow label="Screens" value={(r.screens ?? []).map(screenName).join(", ") || "None"} />
              <ReadRow label="Lights" value={r.lights?.length ? `${r.lights.length}` : "None"} />
              {!!r.blinds?.length && <ReadRow label="Blinds" value={`${r.blinds.length}`} />}
              {!!r.sensors?.length && <ReadRow label="Light sensors" value={r.sensors.join(", ")} />}
              {!!r.effects?.length && <ReadRow label="Room effects" value={`${r.effects.length}`} />}
            </div>
          ))}
          <Hint>What each room holds is read only here.</Hint>
        </div>
      ) : (
        <Hint>{status ? "No rooms are set up." : "Reading the room…"}</Hint>
      )}
      <LinkRow href="/room">Switch modes on the Room page</LinkRow>
    </>
  );
}
