// The one sound-output picker (docs/SOUND.md): the app's outputs (GET /api/sound/outputs, smsound.outputs), the same
// places in the same words wherever sound is chosen. Ones the consumer can't play to are shown, greyed, with why.

import { cn } from "@/lib/utils";
import type { SoundOutput } from "@/shared/api/sound";

export function SoundOutputSelect({
  value,
  outputs,
  onChange,
  ariaLabel = "Sound",
  className,
  disabled,
}: {
  value: string | null | undefined;
  outputs: SoundOutput[];
  onChange: (id: string) => void;
  ariaLabel?: string;
  className?: string;
  disabled?: boolean;
}) {
  const known = outputs.some((o) => o.id === value);
  return (
    <select
      value={value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      aria-label={ariaLabel}
      className={cn("max-w-full rounded-lg border border-stone-200 bg-card px-2.5 py-1.5 text-sm", className)}
    >
      {!known && value && <option value={value}>{value}</option>}
      {outputs.map((o) => (
        <option key={o.id} value={o.id} disabled={o.available === false} title={o.available === false ? o.why : (o.hint ?? undefined)}>
          {outputLabel(o)}
          {o.available === false ? ` (${o.why ?? "not here"})` : ""}
        </option>
      ))}
    </select>
  );
}

/** "VJ screen (screen)", "Sonos (Kitchen)", "No sound". */
export function outputLabel(o: SoundOutput): string {
  if (o.kind === "screen") return `${o.label} (screen)`;
  return o.label;
}
