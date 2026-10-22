// Which mic the room listens through (owner, 16 Oct: "a drop down list of available mics ... and when we have the
// mini-PC's mic it will appear on the list too"). The list is show_service's (GET /show/api/v1/inputs): each sender
// with the capture devices it reports, so a new sender or a newly plugged mic shows up by itself. Picking one
// (PUT /show/api/v1/input) makes that stream the room's input and tells its sender to capture that device.

import { useCallback, useEffect, useState } from "react";
import { explainError, type RoomApi } from "./api";
import { Spinner } from "./bits";
import type { ShowInputs } from "./types";

const POLL_MS = 5_000;
const SEP = "\u0000";

/** Windows' input names in fewer words: "Microphone (4- Samson Meteor Mic)" -> "Samson Meteor Mic". */
export function micName(name: string): string {
  const m = /^(?:Microphone|Headset Microphone|Line In|Microphone Array)\s*\((?:\d+-\s*)?(.+)\)$/i.exec(name.trim());
  return m ? m[1] : name;
}

/** A sender's state after its name in the list: nothing while it sends (or is connected and quiet). */
function stateWords(state?: string | null): string {
  if (!state || state === "streaming" || state === "quiet") return "";
  if (state === "off") return " (not in use)";
  if (state === "no_device") return " (no mic plugged in)";
  return " (not sending)";
}

export function MicPicker({ api }: { api: RoomApi }) {
  const [inputs, setInputs] = useState<ShowInputs | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setInputs(await api.showInputs());
    } catch {
      /* show_service not answering (or older): the picker waits */
    }
  }, [api]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const list = inputs?.inputs ?? [];
  if (!list.length) return null;

  // What's picked: the owner's choice, else the stream show_service listens to, on the device its sender captures now.
  const choice = inputs?.choice ?? null;
  const active = list.find((i) => i.stream === (choice?.stream ?? inputs?.active)) ?? null;
  const value = choice ? `${choice.stream}${SEP}${choice.device ?? ""}` : active ? `${active.stream}${SEP}${active.device ?? ""}` : "";
  // The chosen mic isn't plugged in: its sender falls back to another, and the page says so (never a silent swap).
  const missing =
    choice?.device && active && !(active.devices ?? (active.device ? [active.device] : [])).includes(choice.device) ? choice.device : null;
  // The picked stream isn't sending: show_service listens through another until it's back (never a silent swap).
  const using =
    inputs?.active && choice && inputs.active !== choice.stream ? list.find((i) => i.stream === inputs.active) ?? null : null;

  const pick = async (v: string) => {
    const [stream, device] = v.split(SEP);
    setBusy(true);
    setErr(null);
    try {
      setInputs(await api.showSetInput(stream, device || null));
    } catch (e) {
      setErr(explainError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2">
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className="shrink-0">Using</span>
        <select
          className="h-10 min-w-0 flex-1 rounded-lg border border-stone-200 bg-white px-2 text-[15px] text-stone-900"
          value={value}
          onChange={(e) => void pick(e.currentTarget.value)}
        >
          {!active && <option value="">None yet</option>}
          {list.map((i) => {
            const listed = i.devices?.length ? i.devices : i.device ? [i.device] : [""];
            const devices = missing && i.stream === active?.stream ? [...listed, missing] : listed;
            return (
              <optgroup key={i.stream} label={`${i.label || i.sender || i.stream}${stateWords(i.state)}`}>
                {devices.map((d) => (
                  <option key={d} value={`${i.stream}${SEP}${d}`}>
                    {d ? micName(d) : i.stream}
                    {d && d === missing ? " (not plugged in)" : ""}
                  </option>
                ))}
              </optgroup>
            );
          })}
        </select>
        {busy && <Spinner className="shrink-0" />}
      </label>
      {missing && active && (
        <p className="mt-1 text-sm leading-snug text-amber-800">
          {micName(missing)} isn't plugged in to {active.label || active.sender || active.stream}.
          {active.device ? ` Using ${micName(active.device)} until it's back.` : ""}
        </p>
      )}
      {using && (
        <p className="mt-1 text-sm leading-snug text-amber-800">
          {active ? active.label || active.sender || active.stream : choice?.stream} isn't sending; the room listens through{" "}
          {using.label || using.sender || using.stream} until it's back.
        </p>
      )}
      {err && <p className="mt-1 text-sm text-rose-700">{err}</p>}
    </div>
  );
}
