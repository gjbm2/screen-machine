// Settings › Sound & timing (#sound; docs/SOUND.md): every place sound can play and how late the room hears it, in one
// list (owner, 27 Nov: "latency calibration itself should also be generalised across the app and sensibly exposed").
// Each output: its delay (measured + trim, or why there is none), Calibrate (screens and Sonos: chirps timed with the
// room mic) and a trim. Under them, read only, the other timing the show runs on (screens' picture-to-light, each
// light's response, the room's sound sources), each with what it measures and the tool that measures it. One rule
// throughout: milliseconds, + = the room hears or sees it that much after it was scheduled.

import { useCallback, useEffect, useState } from "react";
import { Timer } from "lucide-react";
import { OutputTiming } from "@/components/sound/OutputTiming";
import { OutputEq } from "@/components/sound/OutputEq";
import { CameraCalibration } from "@/components/sound/CameraCalibration";
import { msText, sound, type LatencyOverview, type LatencyRow } from "@/shared/api/sound";
import { Group, Hint, ReadRow, Sub } from "./Group";

export function SoundGroup() {
  return (
    <Group id="sound" title="Sound & timing" icon={<Timer />} summary="Where sound plays, and each place's delay">
      <SoundBody />
    </Group>
  );
}

/** Mounted only while the group is open. */
function SoundBody() {
  const [o, setO] = useState<LatencyOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    sound.latency().then(
      (v) => {
        setO(v);
        setErr(null);
      },
      (e) => setErr(String((e as Error).message || e)),
    );
  }, []);
  useEffect(load, [load]);

  if (err) return <Hint>Can't read the timing: {err}</Hint>;
  if (!o) return <Hint>Reading…</Hint>;
  const other = (rows: LatencyRow[], title: string) =>
    rows.length > 0 && (
      <>
        {title && <Sub>{title}</Sub>}
        {rows.map((r) => (
          <ReadRow
            key={r.key}
            label={r.label}
            value={`${msText(r.value_ms)}${r.trim_ms ? ` (${r.trim_ms > 0 ? "+" : ""}${Math.round(r.trim_ms)} trim)` : ""}`}
            hint={`${r.measures}. Measured with ${r.how}${r.measured_at ? `, ${new Date(r.measured_at).toLocaleDateString()}` : ""}. Used by ${r.used_by}.`}
          />
        ))}
      </>
    );
  return (
    <>
      <Sub>Where sound plays</Sub>
      {o.sound.map((r) => (
        <div key={r.id} className="space-y-1 border-b border-stone-200/70 pb-3 last:border-0" data-testid={`sound-${r.id}`}>
          <p className="text-base font-medium leading-snug">
            {r.label} <span className="text-sm font-normal text-muted-foreground">· {r.kind_label}</span>
          </p>
          {r.hint && <p className="text-sm leading-snug text-muted-foreground">{r.hint}</p>}
          <OutputTiming output={r} onChanged={load} />
          <OutputEq output={r.id} folded />
        </div>
      ))}
      <Hint>
        Calibrate plays ten chirps on that speaker and times them with the room mic; the trim nudges the result by ear.
        Karaoke's lyrics and the piano on Sonos follow these figures.
      </Hint>
      {other(o.screens, "Screens: picture to light")}
      <Sub>Lights: command to light</Sub>
      <CameraCalibration onSaved={load} />
      {other(o.lights, "")}
      {other(o.sources, "Sound coming in")}
      <Hint className="text-sm">
        All in {o.file}: {o.rule}.
      </Hint>
    </>
  );
}
