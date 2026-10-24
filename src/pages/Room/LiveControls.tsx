// A screen's live controls, as four pieces over one state (useScreenControls):
//   - CoreControls: inline on the card while live: scene, the "basic" parameters, Back to art;
//   - LiveSettingsPanel: in "More" while live: every parameter, reset, transitions and quality;
//   - GoLiveRow: on the card's face while showing art: the scene chip and "Go live" (24 Oct walkthrough: going live
//     was only inside More);
//   - ScenePanel: in "More" while showing art, "Scene and picture": the chosen scene's words, its settings,
//     transitions and quality (no second Go live).
// A transition shows by its label only; its parameters are under Technical details (LiveDetails.tsx).

import { useState } from "react";
import { Play, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { RoomApi } from "./api";
import { Notice, SectionTitle, Spinner, Unroll } from "./bits";
import { bothWords, sceneInfo, type LookScope } from "./liveLib";
import { LookControls, LookSettings, SceneButton, ScenePicker } from "./LivePanel";
import { profileLabel, transitionSpecKey, visibleProperties, type TransitionChoice } from "./catalog";
import { ParamForm } from "./ParamForm";
import { TAP_BUTTON_CLASS, THEME_CLASS } from "./theme";
import type { Library, ParamSchema, SchemaProperty, TransitionSpec, XUi } from "./types";
import type { LiveLook } from "./useLiveLook";
import type { LiveEditState, ScreenControls } from "./useScreenControls";

/** The live routes' pieces (room_service live_api.py). Absent on a room_service without them. */
export interface LiveBits {
  lib: Library;
  api: RoomApi;
  lv: LiveLook;
  /** Its room's live card, when the room has one (the room-wide controls live there). */
  scope?: LookScope | null;
}

const DEFAULT_PROFILE = "__default";
const TRIGGER = "h-11 rounded-xl bg-white text-left text-base";
const ITEM = "min-h-11 rounded-lg text-base";

/**
 * The fields a schema marks x-ui.basic: true, shown inline on a live screen. Null if there are none.
 * While the session has its own colours, a source ignores its x-palette param, so that one is hidden
 * (the Colours row shows the session's instead).
 */
function basicSchema(schema?: ParamSchema, sessionPalette = false): ParamSchema | null {
  const props = visibleProperties(schema).filter(
    ([, p]) =>
      (p["x-ui"] as (XUi & { basic?: boolean }) | undefined)?.basic === true &&
      !(sessionPalette && (p as SchemaProperty & { "x-palette"?: boolean })["x-palette"]),
  );
  if (!props.length) return null;
  return { ...schema, properties: Object.fromEntries(props) as Record<string, SchemaProperty> };
}

// ── Inline, while live ─────────────────────────────────────────────────────────

export function CoreControls({ screenId, sc, live }: { screenId: string; sc: ScreenControls; live?: LiveBits }) {
  const basic = sc.liveNow ? basicSchema(sc.choice?.schema, !!live?.lv.look?.palette) : null;
  const [pickOpen, setPickOpen] = useState(false);
  const backToArt = (
    <Button
      variant="outline"
      data-testid={`art-${screenId}`}
      onClick={sc.backToArt}
      disabled={!sc.canBackToArt}
      className={cn(TAP_BUTTON_CLASS, "shrink-0 bg-white px-4")}
    >
      {sc.busy === "back to art" && <Spinner />}
      Back to art
    </Button>
  );
  if (!sc.liveNow) {
    // Going live, or a failed try: the one useful thing here is to stop and go back to art.
    return (
      <div className="flex justify-end px-4 pb-4">
        <Button
          variant="outline"
          data-testid={`art-${screenId}`}
          onClick={sc.backToArt}
          disabled={!sc.canBackToArt}
          className={cn(TAP_BUTTON_CLASS, "bg-white")}
        >
          {/* No stop-square icon: at this size it reads as an empty tick box. */}
          {sc.busy === "back to art" ? <Spinner /> : sc.alreadyLive ? <X /> : null}
          {sc.alreadyLive ? "Cancel" : "Back to art"}
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-3 px-4 pb-4">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          {live ? (
            <>
              <SceneButton
                info={sceneInfo(live.lib, live.api, live.lv.look?.current_scene ?? sc.liveSceneKey ?? sc.form.sceneKey)}
                caption={sc.busy === "switch scene" ? "Changing scene…" : live.scope?.shared.length ? "Change scene · this screen" : "Change scene"}
                busy={sc.busy === "switch scene"}
                onClick={() => setPickOpen(true)}
                disabled={sc.pickersDisabled || !!sc.busy}
                testId={`scene-${screenId}`}
              />
              <ScenePicker
                open={pickOpen}
                onOpenChange={setPickOpen}
                lib={live.lib}
                api={live.api}
                mode="cue"
                currentKey={sceneInfo(live.lib, live.api, live.lv.look?.current_scene ?? sc.liveSceneKey)?.key ?? null}
                onPick={(key, how) => sc.switchSceneTo(key, { cue: true, ...how })}
                testId={`scenepicker-${screenId}`}
              />
            </>
          ) : (
            <>
              <Label htmlFor={`${screenId}-scene-now`} className="sr-only">
                Scene
              </Label>
              <ScenePickerSelect
                id={`${screenId}-scene-now`}
                testId={`scene-${screenId}`}
                sc={sc}
                onChange={sc.switchSceneTo}
                disabled={sc.pickersDisabled || !!sc.busy}
                presetsOnly
              />
            </>
          )}
        </div>
        {/* With the scene thumbnail the row is the scene's alone; Back to art closes the card. */}
        {!live && backToArt}
      </div>
      {sc.busy === "switch scene" && !live && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> Changing scene…
        </p>
      )}
      {basic && (
        <ParamForm
          idPrefix={`${screenId}-basic`}
          schema={basic}
          values={sc.values}
          overridden={sc.overridden}
          onChange={sc.onParamChange}
          onReset={sc.onParamReset}
          disabled={sc.pickersDisabled}
          emptyText={null}
          compact
        />
      )}
      {sc.liveEdit.state === "error" && sc.editsGoLive && <Notice tone="bad">{sc.liveEdit.msg}</Notice>}
      {live && live.lv.available !== false && (
        <LookControls screenId={screenId} lv={live.lv} lib={live.lib} api={live.api} scope={live.scope} />
      )}
      {live && <div className="flex justify-end pt-1">{backToArt}</div>}
    </div>
  );
}

// ── In "More", while live ─────────────────────────────────────────────────────

export function LiveSettingsPanel({ screenId, sc, live }: { screenId: string; sc: ScreenControls; live?: LiveBits }) {
  const changed = sc.overridden.size;
  return (
    <div className="space-y-3">
      <SectionTitle
        right={
          changed > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={sc.resetAll}
              className="-my-1 h-11 rounded-xl text-[15px]"
              title="Put every setting back to the scene's own values"
            >
              <RotateCcw /> Reset all
            </Button>
          )
        }
      >
        All scene settings
      </SectionTitle>
      <div className="-mt-1 flex items-baseline justify-between gap-3 text-sm">
        <p className="text-muted-foreground">
          Changes show on the screen straight away{changed ? ` · ${changed} changed` : ""}.
        </p>
        {sc.editsGoLive && <LiveEditIndicator state={sc.liveEdit.state} />}
      </div>
      {sc.choice ? (
        <ParamForm
          idPrefix={`${screenId}-p`}
          schema={sc.choice.schema}
          values={sc.values}
          overridden={sc.overridden}
          onChange={sc.onParamChange}
          onReset={sc.onParamReset}
          disabled={sc.pickersDisabled}
        />
      ) : (
        <p className="text-sm text-muted-foreground">Waiting for the list of scenes…</p>
      )}
      {sc.liveEdit.state === "error" && sc.editsGoLive && <Notice tone="bad">{sc.liveEdit.msg}</Notice>}
      {live && live.lv.look && (
        // In a room with a live card, picking is the room's (it's there); the feel and fades stay here.
        <Unroll title={live.scope?.hosted ? "The feel and fades" : "Picking, the feel and fades"} className="pt-1" testId={`lookmore-${screenId}`}>
          <LookSettings
            screenId={screenId}
            lv={live.lv}
            lib={live.lib}
            api={live.api}
            parts={live.scope?.hosted ? "feel" : "all"}
            words={
              live.scope?.hosted && live.scope.shared.length
                ? live.lv.scoped
                  ? live.scope.own.includes("direction")
                    ? "This screen has its own feel. These change this screen only."
                    : "These change this screen only."
                  : `These change ${bothWords(live.scope.shared)} for now.`
                : null
            }
          />
        </Unroll>
      )}
      <Unroll title="Transitions and picture quality" className="pt-1">
        <p className="mb-3 text-sm text-muted-foreground">
          The “going live” transition is also used when you change scene. The rest applies the next time this screen goes
          live.
        </p>
        <TransitionAndQuality screenId={screenId} sc={sc} />
      </Unroll>
    </div>
  );
}

function LiveEditIndicator({ state }: { state: LiveEditState }) {
  const text = { idle: "Live", pending: "Sending…", ok: "Applied", error: "Not applied" }[state];
  const cls = {
    idle: "text-teal-700",
    pending: "text-amber-700",
    ok: "text-emerald-700",
    error: "text-rose-700",
  }[state];
  return (
    <span data-testid="live-edit-state" className={cn("shrink-0 text-sm font-medium", cls)}>
      {text}
    </span>
  );
}

// ── On the card's face, while showing art ─────────────────────────────────────

export function GoLiveRow({
  screenId,
  sc,
  streamReachable,
  live,
}: {
  screenId: string;
  sc: ScreenControls;
  streamReachable: boolean | undefined;
  live?: Omit<LiveBits, "lv">;
}) {
  const [pickOpen, setPickOpen] = useState(false);
  return (
    <div className="space-y-2 px-4 pb-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[12rem] flex-1">
          {live ? (
            <>
              <SceneButton
                info={sceneInfo(live.lib, live.api, sc.form.sceneKey)}
                caption="Scene · tap to change"
                onClick={() => setPickOpen(true)}
                disabled={sc.pickersDisabled}
                testId={`scene-${screenId}`}
              />
              <ScenePicker
                open={pickOpen}
                onOpenChange={setPickOpen}
                lib={live.lib}
                api={live.api}
                mode="choose"
                currentKey={sc.form.sceneKey}
                onPick={(key) => sc.setScene(key)}
                testId={`scenepicker-${screenId}`}
              />
            </>
          ) : (
            <>
              <Label htmlFor={`${screenId}-scene`} className="sr-only">
                Scene
              </Label>
              <ScenePickerSelect id={`${screenId}-scene`} testId={`scene-${screenId}`} sc={sc} onChange={sc.setScene} disabled={sc.pickersDisabled} />
            </>
          )}
        </div>
        <Button
          data-testid={`golive-${screenId}`}
          onClick={sc.goLive}
          disabled={!sc.canGoLive}
          className={cn(TAP_BUTTON_CLASS, "shrink-0 px-5")}
          title={sc.liveDisabled ? "Live is switched off for this screen" : sc.alreadyLive ? "Already live" : undefined}
        >
          {sc.busy === "go live" ? <Spinner /> : <Play />} Go live
        </Button>
      </div>
      {sc.liveDisabled === "refused" && (
        <Notice tone="warn">
          <strong className="font-semibold">Live is switched off for this screen.</strong> The room refused it. The owner
          turns it on in the room setup.{" "}
          <button type="button" className="font-medium underline underline-offset-2" onClick={sc.clearRefused}>
            Try again
          </button>
        </Notice>
      )}
      {streamReachable === false && (
        <Notice tone="warn">The live pictures aren't answering right now. Going live starts by itself as soon as they're back.</Notice>
      )}
    </div>
  );
}

// ── In "More", while showing art ──────────────────────────────────────────────

export function ScenePanel({ screenId, sc }: { screenId: string; sc: ScreenControls }) {
  if (sc.liveDisabled === "policy") {
    return (
      <Notice tone="muted">
        <strong className="font-semibold">This screen shows art only.</strong> Live isn't switched on for it. The owner can
        turn it on in the room setup.
      </Notice>
    );
  }
  const changed = sc.overridden.size;
  return (
    <div className="space-y-3">
      <SectionTitle>Scene and picture</SectionTitle>
      <p className="-mt-1 text-sm text-muted-foreground">
        {sc.choice ? (
          <>
            <span className="font-medium text-stone-700">{sc.choice.label}</span>
            {sc.choice.description ? ` · ${sc.choice.description}` : ""}
          </>
        ) : (
          "Choose a scene on the card above."
        )}{" "}
        These apply when it goes live.
      </p>

      <Unroll title="Scene settings" hint={changed ? `${changed} changed` : undefined}>
        {sc.choice ? (
          <div className="space-y-3 pb-1">
            <ParamForm
              idPrefix={`${screenId}-p`}
              schema={sc.choice.schema}
              values={sc.values}
              overridden={sc.overridden}
              onChange={sc.onParamChange}
              onReset={sc.onParamReset}
              disabled={sc.pickersDisabled}
            />
            {changed > 0 && (
              <Button variant="ghost" size="sm" onClick={sc.resetAll} className="h-11 rounded-xl text-[15px]">
                <RotateCcw /> {sc.choice.kind === "source" ? "Reset to defaults" : "Reset to preset"}
              </Button>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Choose a scene first.</p>
        )}
      </Unroll>

      <Unroll title="Transitions and picture quality">
        <TransitionAndQuality screenId={screenId} sc={sc} />
      </Unroll>
    </div>
  );
}

// ── Pickers ─────────────────────────────────────────────────────────────────

/** The plain select (a room_service without the library). */
function ScenePickerSelect({
  id,
  testId,
  sc,
  onChange,
  disabled,
  presetsOnly = false,
}: {
  id: string;
  testId: string;
  sc: ScreenControls;
  onChange: (key: string) => void;
  disabled?: boolean;
  /** The quick picker: the named scenes only (plus the current one), not the raw sources. */
  presetsOnly?: boolean;
}) {
  const key = sc.form.sceneKey;
  const presets = sc.scenes.filter((s) => s.kind === "preset");
  const sources = sc.scenes.filter(
    (s) => s.kind === "source" && (!presetsOnly || !presets.length || s.key === key),
  );
  return (
    <Select value={key ?? ""} disabled={disabled} onValueChange={onChange}>
      <SelectTrigger id={id} data-testid={testId} className={TRIGGER}>
        <SelectValue placeholder={sc.scenes.length ? "Choose a scene" : "Loading scenes…"} />
      </SelectTrigger>
      <SelectContent className={cn(THEME_CLASS, "rounded-xl")}>
        {presets.length > 0 && (
          <SelectGroup>
            <SelectLabel className="text-xs font-medium text-muted-foreground">Scenes</SelectLabel>
            {presets.map((s) => (
              <SelectItem key={s.key} value={s.key} className={ITEM}>
                {s.label}
              </SelectItem>
            ))}
          </SelectGroup>
        )}
        {presets.length > 0 && sources.length > 0 && <SelectSeparator />}
        {sources.length > 0 && (
          <SelectGroup>
            <SelectLabel className="text-xs font-medium text-muted-foreground">Plain sources (default settings)</SelectLabel>
            {sources.map((s) => (
              <SelectItem key={s.key} value={s.key} className={ITEM}>
                {s.label}
              </SelectItem>
            ))}
          </SelectGroup>
        )}
        {key && !sc.choice && (
          <SelectItem value={key} disabled className={ITEM}>
            Not in the list: {key.replace(/^(preset|source):/, "")}
          </SelectItem>
        )}
      </SelectContent>
    </Select>
  );
}

function TransitionAndQuality({ screenId, sc }: { screenId: string; sc: ScreenControls }) {
  return (
    <div className="grid grid-cols-1 gap-3 pb-1 sm:grid-cols-2">
      <TransitionPicker
        id={`${screenId}-in`}
        label="Going live"
        spec={sc.form.inSpec}
        choices={sc.transitions}
        disabled={sc.pickersDisabled}
        onChange={sc.setInSpec}
      />
      <TransitionPicker
        id={`${screenId}-out`}
        label="Back to art"
        spec={sc.form.outSpec}
        choices={sc.transitions}
        disabled={sc.pickersDisabled}
        onChange={sc.setOutSpec}
      />
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor={`${screenId}-profile`} className="text-sm font-normal text-muted-foreground">
          Picture quality
        </Label>
        <Select
          value={sc.form.profile || DEFAULT_PROFILE}
          disabled={sc.pickersDisabled}
          onValueChange={(v) => sc.setProfile(v === DEFAULT_PROFILE ? "" : v)}
        >
          <SelectTrigger id={`${screenId}-profile`} data-testid={`profile-${screenId}`} className={TRIGGER}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent className={cn(THEME_CLASS, "rounded-xl")}>
            <SelectItem value={DEFAULT_PROFILE} className={ITEM}>
              Automatic
            </SelectItem>
            {Object.entries(sc.profiles).map(([name, p]) => (
              <SelectItem key={name} value={name} className={ITEM} title={name}>
                {profileLabel(name, p)}
              </SelectItem>
            ))}
            {sc.form.profile && !(sc.form.profile in sc.profiles) && (
              <SelectItem value={sc.form.profile} disabled className={ITEM}>
                Not in the list: {sc.form.profile}
              </SelectItem>
            )}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

function TransitionPicker({
  id,
  label,
  spec,
  choices,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  spec: TransitionSpec | null;
  choices: TransitionChoice[];
  disabled?: boolean;
  onChange: (spec: TransitionSpec) => void;
}) {
  const key = transitionSpecKey(spec);
  const known = key ? choices.some((c) => c.key === key) : false;
  return (
    <div className="min-w-0 space-y-1.5">
      <Label htmlFor={id} className="text-sm font-normal text-muted-foreground">
        {label}
      </Label>
      <Select
        value={key ?? ""}
        disabled={disabled || (!choices.length && !key)}
        // Picking an entry selects that preset by name. The "custom" entry keeps the original spec.
        onValueChange={(k) => onChange(k === key && spec ? spec : k)}
      >
        <SelectTrigger id={id} className={TRIGGER}>
          <SelectValue placeholder={choices.length ? "Choose…" : "No transitions"} />
        </SelectTrigger>
        <SelectContent className={cn(THEME_CLASS, "rounded-xl")}>
          {choices.map((c) => (
            <SelectItem key={c.key} value={c.key} className={ITEM}>
              {c.label}
            </SelectItem>
          ))}
          {key && !known && (
            <SelectItem value={key} className={ITEM}>
              Custom: {key.replace(/^raw:/, "")}
            </SelectItem>
          )}
        </SelectContent>
      </Select>
    </div>
  );
}
