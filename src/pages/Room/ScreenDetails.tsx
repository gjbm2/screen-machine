// A screen's technical readout, for the unrolled "Technical details": desired vs
// observed state with phase names, session and acks, the brightness mask, and the
// kiosk's live playback stats (LIVE_V1 §3).

import { KV, Pill, SectionTitle, Stat } from "./bits";
import { describeSceneSpec, describeTransitionSpec, type SceneChoice, type TransitionChoice } from "./catalog";
import { BUSY_PHASES, phaseTone } from "./tones";
import type { KioskState, ScreenMask, ScreenStatus } from "./types";

function fmtTime(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleTimeString();
}

function fmtInt(n?: number): string {
  return typeof n === "number" && Number.isFinite(n) ? Math.round(n).toLocaleString() : "—";
}

export function ScreenDetails({
  screenId,
  screen,
  scenes,
  transitions,
}: {
  screenId: string;
  screen: ScreenStatus;
  scenes: SceneChoice[];
  transitions: TransitionChoice[];
}) {
  const obs = screen.observed ?? {};
  const phase = obs.phase ?? null;
  const desired = screen.desired;
  const desiredLive = desired && typeof desired === "object" && "live" in desired ? desired.live : null;
  const policyLive = screen.policy?.live_enabled ?? screen.live_enabled;

  return (
    <div className="space-y-5 text-sm">
      <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5">
        <KV k="Screen id">
          <span className="font-mono">{screenId}</span>
        </KV>
        <KV k="Desired">
          {desiredLive ? (
            <span>
              <span className="font-medium text-teal-700">live</span> · {describeSceneSpec(desiredLive.scene, scenes)}
              <span className="text-muted-foreground">
                {" "}
                · in {describeTransitionSpec(desiredLive.transitions?.in, transitions)} · out{" "}
                {describeTransitionSpec(desiredLive.transitions?.out, transitions)} · {desiredLive.profile ?? "default profile"}
              </span>
            </span>
          ) : desired === "art" ? (
            <span className="font-medium">art</span>
          ) : (
            <span className="text-muted-foreground">{desired === undefined ? "—" : JSON.stringify(desired)}</span>
          )}
        </KV>
        <KV k="Observed">
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span>{obs.layer ?? "?"}</span>
            <Pill tone={phaseTone(phase)} pulse={BUSY_PHASES.has(phase ?? "")}>
              <span data-testid={`phase-${screenId}`}>{phase ?? "?"}</span>
            </Pill>
            {/* Not in v1 status; room_service says why a phase is held (e.g. "waiting for the page"). */}
            {obs.detail && (
              <span className="text-muted-foreground" data-testid={`detail-${screenId}`}>
                ({obs.detail})
              </span>
            )}
          </span>
        </KV>
        <KV k="Page">
          {obs.page_connected ? `connected (${obs.page_role ?? "?"})` : <span className="text-rose-700">not connected</span>}
        </KV>
        <KV k="Live allowed">{policyLive === undefined ? "unknown" : policyLive ? "yes" : "no (live_enabled: false)"}</KV>
        <KV k="Session">
          <span className="font-mono">{obs.session ?? "—"}</span>
        </KV>
        <KV k="Last ack">{fmtTime(obs.last_ack_at)}</KV>
        {screen.last_error && (
          <KV k="Last error">
            <span className="break-words text-rose-700">{screen.last_error}</span>
          </KV>
        )}
      </dl>

      <MaskReadout mask={screen.mask} drawn={obs.kiosk_state?.mask} layer={obs.layer} />
      <KioskStats ks={obs.kiosk_state} />
    </div>
  );
}

function MaskReadout({ mask, drawn, layer }: { mask?: ScreenMask | null; drawn?: KioskState["mask"]; layer?: string }) {
  if (!mask) {
    return (
      <div>
        <SectionTitle>Art brightness</SectionTitle>
        <p className="text-muted-foreground">No mask reported.</p>
      </div>
    );
  }
  const b = typeof mask.brightness === "number" ? Math.min(1, Math.max(0, mask.brightness)) : null;
  const hex = mask.warm_hex && /^#[0-9a-fA-F]{6}$/.test(mask.warm_hex) ? mask.warm_hex : "#FFFFFF";
  const alpha = typeof mask.warm_alpha === "number" ? mask.warm_alpha : 0;
  const live = layer === "live";
  const pill = live ? (
    <Pill tone="muted">not used while live</Pill>
  ) : mask.source === "fixed" ? (
    <Pill tone="info">off for this screen</Pill>
  ) : mask.source === "manual_override" || mask.source === "maskoff" ? (
    <Pill tone="info">set by hand</Pill>
  ) : mask.suspended ? (
    <Pill tone="muted">stays as it was (this mode)</Pill>
  ) : (
    <Pill tone="ok">follows the room's light</Pill>
  );
  // What the page draws, only when it isn't what was sent.
  const off = (a: number | undefined, x: number | null) => typeof a === "number" && (x === null || Math.abs(a - x) > 0.01);
  const drawnDiffers = !!drawn && (off(drawn.brightness, b) || off(drawn.warm_alpha, alpha));
  return (
    <div>
      <SectionTitle right={pill}>Art brightness</SectionTitle>
      {!live && (
        <div className="flex items-center gap-3">
          {/* What the mask does to white: a black layer at 1 − brightness, then the warm layer. */}
          <div className="relative h-11 w-11 shrink-0 overflow-hidden rounded-lg border border-stone-200 bg-white" title="White, as masked">
            <div className="absolute inset-0 bg-black" style={{ opacity: b === null ? 0 : 1 - b }} />
            <div className="absolute inset-0" style={{ background: hex, opacity: alpha }} />
          </div>
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="w-20 shrink-0 text-muted-foreground">Brightness</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-stone-200">
                <div className="h-full rounded-full bg-amber-400" style={{ width: `${(b ?? 0) * 100}%` }} />
              </div>
              <span className="w-12 text-right font-mono tabular-nums">{b === null ? "—" : b.toFixed(3)}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-20 shrink-0 text-muted-foreground">Warmth</span>
              <span className="h-3.5 w-3.5 shrink-0 rounded border border-stone-300" style={{ background: hex }} />
              <span className="font-mono">{hex.toUpperCase()}</span>
              <span className="ml-auto font-mono tabular-nums text-muted-foreground">α {alpha.toFixed(3)}</span>
            </div>
          </div>
        </div>
      )}
      {!live && drawnDiffers && drawn && typeof drawn.brightness === "number" && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          The page draws: brightness {drawn.brightness.toFixed(3)} · {drawn.warm_hex ?? "?"} α{" "}
          {typeof drawn.warm_alpha === "number" ? drawn.warm_alpha.toFixed(3) : "?"}
        </p>
      )}
    </div>
  );
}

function KioskStats({ ks }: { ks?: KioskState | null }) {
  if (!ks) {
    return (
      <div>
        <SectionTitle>Playback</SectionTitle>
        <p className="text-muted-foreground">No report from the screen yet (it reports every 5 s).</p>
      </div>
    );
  }
  const live = ks.live;
  const presented = live?.presented ?? 0;
  const dropped = live?.dropped ?? 0;
  const dropPct = presented + dropped > 0 ? (100 * dropped) / (presented + dropped) : 0;
  return (
    <div>
      <SectionTitle
        right={
          <div className="flex gap-1.5">
            {ks.frozen && <Pill tone="warn">frozen</Pill>}
            <Pill tone={ks.layer === "live" ? "live" : "muted"}>page layer: {ks.layer ?? "?"}</Pill>
          </div>
        }
      >
        Playback
      </SectionTitle>
      {live ? (
        <div className="grid grid-cols-3 gap-1.5" data-testid="kiosk-stats">
          <Stat label="FPS" value={typeof live.fps === "number" ? live.fps.toFixed(1) : "—"} />
          <Stat label="Presented" value={fmtInt(live.presented)} />
          <Stat
            label="Dropped"
            value={`${fmtInt(live.dropped)}${dropped ? ` (${dropPct.toFixed(1)}%)` : ""}`}
            tone={dropPct > 1 ? "bad" : dropped > 0 ? "warn" : undefined}
          />
          <Stat label="Stalls" value={fmtInt(live.stalls)} tone={(live.stalls ?? 0) > 0 ? "bad" : undefined} />
          <Stat label="Buffer ms" value={fmtInt(live.buffered_ms)} />
          <Stat label="Edge jumps" value={fmtInt(live.edge_jumps)} tone={(live.edge_jumps ?? 0) > 0 ? "warn" : undefined} />
        </div>
      ) : (
        <p className="text-muted-foreground">Not playing live.</p>
      )}
      {ks.base_url && (
        <p className="mt-1.5 truncate text-xs text-muted-foreground" title={ks.base_url}>
          Art image: <span className="font-mono">{ks.base_url}</span>
        </p>
      )}
    </div>
  );
}
