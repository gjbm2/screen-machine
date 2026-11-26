// The karaoke panel's mic check, rolled up under Settings (owner, 25 Nov: "how do I know the mic is working? we had an
// (optional) levels indicator on the other screen"; then "let's move it somewhere for debugging"). Mounted only while
// open, so nothing is polled or drawn until then. The room mic is what the live picture and lights react to; it runs only while
// something needs it, and a karaoke song playing is one of those (show_service/karaoke_feed.py, mic.py add_want).
//
//   - The line: show_service's own words for the mic (GET /show/api/v1/mic: "Listening for Karaoke", "Off: nothing
//     needs it", "Switched off" when the Room page muted it), and each room mic with its state and a rough level
//     (GET /show/api/v1/inputs), read every 2 s.
//   - Levels: the Room page's live meter and spectrum (LevelTrace, WS /show/v1/levels).

import { useEffect, useMemo, useState } from "react";
import { resolveRoomUrl } from "@/pages/Room/api";
import { LevelTrace } from "@/pages/Room/LevelTrace";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";

type MicState = { on: boolean; muted: boolean; why: string };
type Input = { stream: string; label?: string; device?: string | null; state?: string; rms_dbfs?: number | null; silent?: boolean };

const POLL_MS = 2000;
const LIVE = new Set(["streaming", "quiet"]); // show_service/mic.py LIVE

export function RoomMicCheck() {
  const [baseUrl] = useState(() => resolveRoomUrl(window.location.search));
  const [mic, setMic] = useState<MicState | null>(null);
  const [inputs, setInputs] = useState<Input[] | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    let dead = false;
    const get = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const [m, i] = await Promise.all([
          fetch(`${baseUrl}/show/api/v1/mic`).then((r) => (r.ok ? r.json() : Promise.reject(r.status))),
          fetch(`${baseUrl}/show/api/v1/inputs`).then((r) => (r.ok ? r.json() : Promise.reject(r.status))),
        ]);
        if (dead) return;
        setMic(m as MicState);
        setInputs(Array.isArray(i?.inputs) ? (i.inputs as Input[]) : []);
        setDown(false);
      } catch {
        if (!dead) setDown(true);
      }
    };
    void get();
    const t = window.setInterval(get, POLL_MS);
    return () => {
      dead = true;
      window.clearInterval(t);
    };
  }, [baseUrl]);

  const levelsUrl = useMemo(() => `${baseUrl.replace(/^http/i, "ws")}/show/v1/levels`, [baseUrl]);
  const tone = !mic || down ? "idle" : mic.on ? "on" : mic.muted ? "bad" : "idle";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-end">
        <span
          className={cn(
            "rounded-full px-2.5 py-0.5 text-xs font-medium",
            tone === "on" && "bg-emerald-100 text-emerald-800",
            tone === "bad" && "bg-red-100 text-red-800",
            tone === "idle" && "bg-stone-100 text-stone-600",
          )}
        >
          {down ? "Not answering" : !mic ? "…" : mic.on ? "Listening" : mic.muted ? "Switched off" : "Off"}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        {down
          ? "The show side isn't answering; the page keeps trying."
          : mic
            ? mic.on
              ? `${mic.why}. The picture and lights react to what it hears.`
              : `${mic.why}. It comes on by itself while a song plays.`
            : "Reading the mic…"}
      </p>
      {inputs && inputs.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {inputs.map((i) => (
            <InputRow key={i.stream} input={i} on={!!mic?.on} />
          ))}
        </ul>
      )}
      {inputs && inputs.length === 0 && !down && <p className="mt-2 text-sm text-muted-foreground">No room mic is set up on this machine.</p>}
      <LevelTrace url={levelsUrl} />
    </div>
  );
}

function InputRow({ input, on }: { input: Input; on: boolean }) {
  const live = LIVE.has(String(input.state ?? ""));
  const db = typeof input.rms_dbfs === "number" ? input.rms_dbfs : null;
  // A rough level from the 2 s read: -70 dBFS (empty) .. -10 (loud). The live meter is under Levels.
  const pct = live && db != null ? Math.max(0, Math.min(100, ((db + 70) / 60) * 100)) : 0;
  return (
    <li className="text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate">
          <span className="font-medium">{input.label ?? input.stream}</span>
          {input.device ? <span className="text-muted-foreground"> · {input.device}</span> : null}
        </span>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {live ? (db != null ? `${db.toFixed(0)} dBFS` : "live") : on ? "not sending" : input.state ?? "off"}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-stone-200">
        <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
    </li>
  );
}

/** The audio agent's loopback (tools/show_sender/pc22_agent.py LOOPBACK): the device name it offers in its list. */
export const LOOPBACK = "What this computer plays (loopback)";

/**
 * Loopback on/off (owner, 26 Nov: "I don't want to have to listen to music all the time while I'm testing";
 * "loopback on/off should be in karaoke settings"): the room's ears hear this computer's own sound, captured
 * digitally, so the lights and the live picture follow the song with the volume down. It is the Room page's mic
 * choice (PUT /show/api/v1/input {stream, device}), set to the agent's loopback entry or back to that sender's
 * default mic. Shown only when a room mic offers the loopback. Off for the party: the lights should hear the room.
 */
export function LoopbackToggle() {
  const [baseUrl] = useState(() => resolveRoomUrl(window.location.search));
  const [stream, setStream] = useState<string | null>(null);
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const read = useMemo(
    () => async () => {
      const r = await fetch(`${baseUrl}/show/api/v1/inputs`);
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      const ins: (Input & { devices?: string[] })[] = Array.isArray(d?.inputs) ? d.inputs : [];
      const hit = ins.find((i) => Array.isArray(i.devices) && i.devices.includes(LOOPBACK));
      setStream(hit ? hit.stream : null);
      setOn(!!hit && hit.device === LOOPBACK);
    },
    [baseUrl],
  );

  useEffect(() => {
    let dead = false;
    const tick = () => {
      if (document.visibilityState === "visible") read().catch(() => !dead && setStream(null));
    };
    tick();
    const t = window.setInterval(tick, POLL_MS * 2);
    return () => {
      dead = true;
      window.clearInterval(t);
    };
  }, [read]);

  if (!stream) return null;
  const flip = async (want: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`${baseUrl}/show/api/v1/input`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stream, device: want ? LOOPBACK : null }),
      });
      if (!r.ok) throw new Error(`${r.status}`);
      setOn(want);
      window.setTimeout(() => void read().catch(() => undefined), 3000);   // the agent switches within ~2 s
    } catch (e) {
      setErr(`That didn't work (${e instanceof Error ? e.message : String(e)}).`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-1" data-testid="karaoke-loopback">
      <label className="flex items-center justify-between gap-3">
        <span className="min-w-0">
          Hear this laptop's own sound <span className="text-muted-foreground">(loopback)</span>
        </span>
        <Switch checked={on} disabled={busy} onCheckedChange={(v) => void flip(v)} />
      </label>
      <p className={cn("text-xs", on ? "text-amber-700" : "text-muted-foreground")}>
        {on
          ? "On: the lights and picture follow what this laptop plays, even with the volume down. Turn it off for the party, so they hear the room."
          : "For testing with the volume down. Off: the lights and picture listen to the room mic."}
      </p>
      {err && <p className="text-xs text-red-700">{err}</p>}
    </div>
  );
}
