// Types for the room_service API, after docs/av/contracts/LIVE_V1.md.
// §1.1 is the catalog and parameter schema; §3 is room_service's HTTP API and events.
//
// Everything that comes off the wire is optional or loosely typed on purpose.
// The contract allows additive changes, so the page has to tolerate new
// fields and missing ones, and never crash on them.

export type Params = Record<string, unknown>;

// ── §1.1 parameter schema (JSON Schema subset + x-ui hints) ────────────────

export type Widget = "slider" | "number" | "select" | "checkbox" | "color";

export interface XUi {
  label?: string;
  step?: number;
  unit?: string;
  widget?: Widget | string;
  /** Not in v1. Read if present: help text under the field. */
  help?: string;
  /** Not in v1. Read if present: hides the field. */
  hidden?: boolean;
}

export interface SchemaProperty {
  type?: "number" | "integer" | "string" | "boolean" | string;
  title?: string;
  description?: string;
  minimum?: number;
  maximum?: number;
  multipleOf?: number;
  enum?: Array<string | number>;
  default?: unknown;
  "x-ui"?: XUi;
}

export interface ParamSchema {
  type?: "object";
  properties?: Record<string, SchemaProperty>;
}

// ── §1.1 catalog ───────────────────────────────────────────────────────────

export interface CatalogSource {
  name: string;
  label?: string;
  description?: string;
  internal?: boolean;
  schema?: ParamSchema;
}

export interface CatalogTransition {
  name: string;
  label?: string;
  description?: string;
  schema?: ParamSchema;
}

export interface Profile {
  codec?: string;
  w?: number;
  h?: number;
  fps?: number;
  bitrate?: string;
  gop_s?: number;
}

export interface ScenePreset {
  label?: string;
  source: string;
  params?: Params;
}

export interface TransitionPreset {
  label?: string;
  transition: string;
  params?: Params;
}

export interface Catalog {
  sources?: CatalogSource[];
  transitions?: CatalogTransition[];
  filters?: unknown[];
  profiles?: Record<string, Profile>;
  presets?: {
    scenes?: Record<string, ScenePreset>;
    transitions?: Record<string, TransitionPreset>;
  };
}

// ── Spec forms (§1.1): a preset name, {preset, params}, or a raw plug-in ───

export type SceneSpec =
  | string
  | { preset: string; params?: Params }
  | { source: string; params?: Params };

export type TransitionSpec =
  | string
  | { preset: string; params?: Params }
  | { transition: string; params?: Params };

export interface LiveDesired {
  scene: SceneSpec;
  transitions?: { in?: TransitionSpec; out?: TransitionSpec };
  params?: Params;
  profile?: string;
}

export type Desired = "art" | { live: LiveDesired };

// ── §3 status ──────────────────────────────────────────────────────────────

export type StepStatus = "pending" | "running" | "ok" | "failed" | "unconfirmed" | "cancelled";

export interface TransitionStep {
  /** Not in v1. room_service sends one; matched on when present. */
  id?: string;
  subsystem: string;
  action: string;
  status: StepStatus | string;
  detail?: string | null;
}

export interface ModeTransition {
  id: string;
  from?: string | null;
  to?: string | null;
  steps?: TransitionStep[];
  /** Not in v1 status; room_service includes it (started|running|done|cancelled). */
  phase?: string;
  /** Not in v1 status; room_service includes it (ISO time). */
  finished_at?: string | null;
  /** Who asked: "web" (a person), "autopilot" (Auto, show_brain), or room_service's own "boot" | "resume" | "rollback". */
  source?: string | null;
  /** Auto's reason, in words ("the piano is playing"); only on an Auto switch. */
  reason?: string | null;
  /** The targets an Auto switch leaves alone (not stored, not set). */
  skip?: string[] | null;
  /** How the switch ended (ok | partial | failed), and room_service's note on it; once it's finished. */
  status?: string | null;
  detail?: string | null;
}

export interface ModeStatus {
  /** The mode the room starts in (modes.json "default"). */
  default?: string | null;
  current?: string | null;
  target?: string | null;
  transition?: ModeTransition | null;
  /** Not in v1 status. room_service keeps the finished transition here; read if present. */
  last_transition?: ModeTransition | null;
}

export type Phase =
  | "idle"
  | "freezing"
  | "snapshot"
  | "starting"
  | "attaching"
  | "entering"
  | "live"
  | "exiting"
  | "leaving"
  | "failed";

export interface KioskLiveStats {
  session?: string;
  fps?: number;
  presented?: number;
  dropped?: number;
  stalls?: number;
  buffered_ms?: number;
  edge_jumps?: number;
}

export interface MaskValues {
  brightness?: number;
  warm_hex?: string;
  warm_alpha?: number;
}

export interface KioskState {
  layer?: "art" | "live" | string;
  frozen?: boolean;
  base_url?: string;
  mask?: MaskValues;
  live?: KioskLiveStats | null;
}

export interface Observed {
  layer?: "art" | "live" | string;
  phase?: Phase | string;
  /** Not in v1 status; room_service includes it (e.g. "waiting for the page", "retry in 3.0 s"). */
  detail?: string | null;
  session?: string | null;
  page_connected?: boolean;
  page_role?: "kiosk" | "lab" | "viewer" | string | null;
  kiosk_state?: KioskState | null;
  last_ack_at?: string | null;
  /** Seconds until room_service tries going live again, while it waits to retry. */
  retry_in_s?: number | null;
}

export interface ScreenMask extends MaskValues {
  suspended?: boolean;
  /** Where the pushed value came from: "sensor", "astral", "blend", "fixed", "manual_override", "maskoff", "flask". */
  source?: string;
}

export interface ScreenStatus {
  /** The screen's name from the site config (publish-destinations.json), e.g. "North screen". */
  name?: string;
  /** Live diagnostics burned into the stream (diag.<screen>): wanted, on the live session now, and why it stopped. */
  hud?: { wanted: boolean; applied: boolean; failed: string | null };
  desired?: Desired;
  observed?: Observed;
  mask?: ScreenMask | null;
  last_error?: string | null;
  /** Not in v1 status. Read if room_service adds it (mirrors the screen's live settings / welcome.policy). */
  policy?: { live_enabled?: boolean; mask_push?: boolean };
  /** Same, flat form. */
  live_enabled?: boolean;
  /** Not in v1 status. room_service says whether each PNG exists; read if present. */
  snapshots?: { anchor?: boolean; target?: boolean };
}

/** A device card: an on/off target and its setting targets (room-state targets, e.g. the glitterball's
 *  "mirror_ball.main" with "mirror_ball_motor.speed" and ".direction"). */
export interface SiteEffect {
  power: string;
  settings: string[];
}

/** A device worked by the RM4 Pro's remote (room_service remotes_api.py): its buttons for learning and testing. */
export interface RemoteButton {
  id: string;
  label: string;
  /** "Power", "Colour", "Brightness", "Modes", a setting's name, or "Other". */
  group: string;
  learned: boolean;
  /** When it was learned ("2026-10-28 14:02"). */
  at?: string | null;
}

/** A learn in progress (or the last one): phase starting | sweep | found | press | done | failed | stopped. */
export interface RemoteLearn {
  button: string;
  phase: string;
  words: string;
  running: boolean;
  medium?: "rf" | "ir";
  freq_mhz?: number | null;
  timeout_s?: number;
}

/** A light's record of what it is doing (null: unknown) and settings kept for when it next comes on. */
export interface RemoteLightState {
  /** main, colour, level (0 = black), then each of its toggles (a pinspot's flash): on | off. */
  now: { main: string | null; colour: string | null; level: number | null; [toggle: string]: string | number | null };
  kept: Record<string, unknown>;
}

export interface RemoteDevice {
  id: string;
  label: string;
  /** Its device type (config/room/rf/types/<model>.json). */
  model?: string | null;
  /** The room whose card it is on (rooms.json). */
  room?: string | null;
  medium: "rf" | "ir";
  host: string;
  freq_mhz: number | null;
  subsystems: string[];
  light: boolean;
  steps: number | null;
  buttons: RemoteButton[];
  job: RemoteLearn | null;
  state: RemoteLightState | null;
  /** Every target with what the room believes it is doing now (null: unknown), to put right when out of step. */
  state_rows?: {
    target: string;
    label: string;
    schema: { enum?: string[]; minimum?: number; maximum?: number; "x-ui"?: { labels?: Record<string, string> } };
    value: string | number | null;
  }[];
}

/** A kind of appliance that can be added (a pinspot, a glitterball). */
export interface RemoteType {
  id: string;
  label: string;
  description?: string | null;
  medium: "rf" | "ir";
}

/** GET /api/v1/remotes: the appliances added, what can be added, the RM4 Pros in use, and the rooms. */
export interface RemotesView {
  remotes: RemoteDevice[];
  types: RemoteType[];
  hosts: string[];
  rooms: { id: string; name: string }[];
}

/** One room of the site (routes/data/rooms.json), in file order. Ids refer to screens, lamps, blinds, sensors;
 *  effects are device cards. */
export interface SiteRoom {
  id: string;
  name: string;
  screens: string[];
  lights: string[];
  blinds: string[];
  sensors: string[];
  /** Absent from an older room_service. */
  effects?: SiteEffect[];
}

export interface RoomStatus {
  mode?: ModeStatus;
  /** The modes config's revision: changes when modes.json is edited. */
  config?: { revision?: string };
  screens?: Record<string, ScreenStatus>;
  /** The site's rooms; absent from an older room_service (the page then shows one flat list). */
  rooms?: SiteRoom[];
  stream_service?: { reachable?: boolean; url?: string };
  subsystems?: Record<string, SubsystemStatus>;
}

/** GET /api/v1/room/describe, the parts the page reads: every target's label and value schema (from the device
 *  config and the modes.json labels) and every mode's resolved entries. */
export interface RoomDescribe {
  revision?: string;
  targets: Record<
    string,
    {
      label?: string;
      kind?: string;
      schema?: {
        type?: string;
        enum?: string[];
        default?: string | number;
        minimum?: number;
        maximum?: number;
        "x-ui"?: {
          label?: string;
          labels?: Record<string, string>;
          switch?: { on: string; off: string };
          ends?: { min?: string; max?: string };
        };
      };
    }
  >;
  /** Per subsystem: its one-shot actions ("reset" on a remote-controlled light), among others. */
  subsystems?: Record<string, { label?: string; actions?: Record<string, { label?: string }> }>;
  modes: Record<
    string,
    {
      label?: string;
      abstract?: boolean;
      order?: number;
      extends?: string | null;
      /** Per target: what the mode does with it, which mode said so, and under which modes.json key. */
      entries?: Record<string, { verb?: string; value?: unknown; from?: string; selector?: string; policy?: string }>;
    }
  >;
  /** Group name -> its targets (modes.json groups and the "<room>:<kind>" ones from rooms.json). */
  groups?: Record<string, string[]>;
}

/** One mode as modes.json stores it. */
export interface RawMode {
  label?: string;
  description?: string;
  /** The mode's short name for Alexa ("party"); none = not reachable by voice. */
  voice?: string;
  extends?: string;
  abstract?: boolean;
  state?: Record<string, unknown>;
  [k: string]: unknown;
}

/** modes.json as GET /api/v1/room/config returns it. */
export interface RawRoomConfig {
  modes?: Record<string, RawMode>;
  [k: string]: unknown;
}

/** One member of a room-state subsystem (coordinator status). */
export interface MemberStatus {
  believed?: unknown;
  /** confirmed (read back), unconfirmed (sent, nothing reads it back), stale, unknown. */
  confidence?: string;
  desired?: unknown;
  inflight?: { value: unknown; until_s: number } | null;
}

export interface SubsystemStatus {
  kind?: string;
  available?: boolean;
  dry_run?: boolean;
  last_error?: string | null;
  believed?: unknown;
  members?: Record<string, MemberStatus>;
}

export interface ModeDef {
  label?: string;
  state?: Record<string, unknown>;
  on_enter?: unknown;
  on_exit?: unknown;
  sequence?: unknown;
}

// ── §3 events ──────────────────────────────────────────────────────────────

export interface RoomEvent {
  type: "status" | "mode.transition" | "screen.phase" | "screen.mask" | "stream" | "log" | string;
  data?: unknown;
}

export interface ModeTransitionEventData {
  id?: string;
  phase?: "started" | "step" | "done" | "cancelled" | string;
  /** null on started/done/cancelled (room_service). */
  step?: TransitionStep | null;
  /** Not in v1; room_service sends them on "started". */
  from?: string | null;
  to?: string | null;
  /** Who switched ("autopilot" = Auto), and Auto's reason and skipped targets, when the event carries them. */
  source?: string | null;
  reason?: string | null;
  skip?: string[] | null;
  /** On done and cancelled: how it ended (ok | partial | failed) and room_service's note on it. */
  status?: string | null;
  detail?: string | null;
}

/** What the page remembers about the most recent mode transition, so its outcome stays visible after status drops it. */
export interface TrackedTransition extends ModeTransition {
  phase: "started" | "step" | "done" | "cancelled" | string;
  finishedAt?: number;
}

// ── Event log ──────────────────────────────────────────────────────────────

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LogEntry {
  id: number;
  /** Local receive time (ms since epoch). Wall-clock times never cross hosts. */
  t: number;
  /** An event type from §3, or "ui" (this page's actions) or "conn" (connection changes). */
  type: string;
  screen?: string;
  level: LogLevel;
  summary: string;
  data?: unknown;
  /** Coalesced repeats (screen.mask is pushed at up to ~10 Hz). */
  count: number;
}

// ── Live controls (room_service live_api.py) ─────────────────────────────────

export interface LibraryScene {
  name: string;
  label: string;
  source: string;
  /** Every facet the renderer has (modes incl. the owner's Library tags, moods, family, technique, dims; energy is a range). */
  tags?: { family?: string; moods?: string[]; dims?: string; technique?: string[]; energy?: number[]; modes?: string[] } & Record<string, unknown>;
  in_auto?: boolean;
  /** "core" (the room's own plug-ins) or "imported" (shader ports). */
  origin?: string;
  basic?: Params;
  thumb?: string | null;
}

export interface LibraryTransition {
  name: string;
  label: string;
  transition: string;
  duration_s?: number | null;
  tags?: { transition_style?: string[] | string };
  thumb?: string | null;
}

export interface LibraryEffect {
  name: string;
  label: string;
  effect: string;
  stage?: string | null;
  tags?: { family?: string; moods?: string[] };
  basic?: string[];
  thumb?: string | null;
}

export interface LibraryPalette {
  name: string;
  label: string;
  swatch: string[];
  family?: string | null;
  moods?: string[];
}

export interface Facet {
  value: string;
  count: number;
}

export interface Library {
  rev?: string;
  features?: string[];
  counts?: Record<string, number>;
  sources?: Record<string, { label?: string; description?: string; basic?: Record<string, SchemaProperty>; thumb?: string | null }>;
  scenes: LibraryScene[];
  transitions: LibraryTransition[];
  effects: LibraryEffect[];
  palettes: LibraryPalette[];
  stages?: string[];
  facets?: {
    scenes?: { family?: Facet[]; moods?: Facet[] };
    effects?: { stage?: Facet[] };
  };
  vocab?: { moods?: string[]; genre?: string[]; transition_style?: string[]; palette_family?: string[] };
  direction?: { defaults?: Direction };
  auto?: { defaults?: AutoBlock };
  /** The effects bus: max_slots is the renderer's per-screen limit (6). */
  fx_bus?: { max_slots?: number; page_max?: number; fade_s?: { default?: number; max?: number } };
}

export interface Direction {
  energy?: number;
  pace?: number;
  intensity?: number;
  change_rate?: number;
  moods?: string[];
  genre?: string | null;
  transition_style?: string | null;
  palette_family?: string | null;
  source?: string | null;
  note?: string | null;
  /** follow: how much the song's events (a drop, a new track) move the effects, 0-1; null = the director's 0.8. */
  fx?: { density?: number; rate?: number; follow?: number | null; stages?: string[] | null; moods?: string[] | null };
}

export type Pool = "all" | string[] | { tags: Record<string, unknown> };

export interface AutoBlock {
  enabled?: boolean;
  pool?: Pool;
  transitions?: Pool;
  dwell_s?: [number, number] | null;
  snap?: "now" | "beat" | string;
  avoid_repeat?: number;
  armed?: boolean;
  dwell_s_effective?: [number, number] | null;
  dwell_from?: string;
  fx?: { enabled?: boolean; pool?: Pool; max?: number; armed?: boolean };
}

export interface FxEntry {
  id: string;
  effect: string;
  preset?: string | null;
  label?: string;
  stage?: string;
  level?: number;
  bypass?: boolean;
  origin?: "manual" | "auto" | string;
  params?: Params;
  bindings?: Record<string, unknown>;
}

export interface FxBus {
  rev?: number;
  open?: boolean;
  fade_s?: number;
  stack?: FxEntry[];
  master?: { amount?: unknown; pulse?: unknown; level_now?: number | null };
}

/** GET /api/v1/screens/{id}/look: the live session's look. */
export interface Look {
  screen?: string;
  session?: string;
  state?: string;
  scene?: string;
  current_scene?: string;
  auto?: AutoBlock | null;
  direction?: Direction | null;
  palette?: string | null;
  palette_transition_s?: number;
  beat_follow?: string;
  fx?: FxBus;
  cue?: { pending?: unknown; last?: { scene?: string; cause?: string; transition?: string } | null };
  /** Effects that come with the scene (a Gallery look's own); they change with it and aren't on the bus. */
  current_scene_effects?: Array<{ effect: string; preset?: string | null; stage?: string | null }> | null;
}

/** The renderer's room for a session (rooms.py): one leader decides, the others follow. "solo": not in one. */
export interface RendererRoom {
  id: string;
  role: "solo" | "leader" | "follower" | string;
  leader: string | null;
  screens: string[];
}

/** GET /renderer/api/sessions/{sid}: the session's whole summary (the look's fields, its room and its Up next). */
export interface SessionView extends Look {
  room?: RendererRoom | null;
  upnext?: UpNextPlan;
  /** The parts of the look this screen set for itself (scope "screen": direction, palette, beat_follow, fx); the rest
   *  is the room's. Absent from a renderer without look_scope. */
  own?: string[];
}

export interface LookPatch {
  auto?: Partial<AutoBlock> | boolean | null;
  direction?: Partial<Direction>;
  palette?: string | null;
  palette_transition_s?: number;
  beat_follow?: string;
  /** "screen": this screen only (needs the renderer's look_scope feature and a room_service that takes it). */
  scope?: "screen" | "room";
}

/** POST …/fx/next (screen-machine-04, 17 Oct): all optional. effect: put this preset on; replace: the slot it swaps
 *  out ("none": add); only: every other picked effect comes off. An older renderer ignores the body. */
export interface FxNextBody {
  effect?: string;
  replace?: string;
  only?: boolean;
  /** With look_scope: "screen" acts on this screen only (its effects become its own); "room" on the room's. */
  scope?: "screen" | "room";
}

export interface FxNextResult {
  op?: string;
  preset?: string | null;
  removed?: string | null;
  why?: string;
}

export interface CueBody {
  to: SceneSpec | "auto";
  transition?: TransitionSpec;
  at?: "now" | "beat";
}

export interface FxPut {
  stack: Array<string | Partial<FxEntry>>;
  fade_s?: number;
  master?: { amount?: number };
  if_rev?: number;
}

// ── Up next (the renderer's upnext.py, through the /renderer pass-through) ──

/** "auto": the art director's pick (it may re-pick it); "you": added, changed or kept by hand (it stays). */
export type UpNextOrigin = "auto" | "you";

export interface UpNextScene {
  id: string;
  /** A scene preset's name (or a plain source's, when `spec` names one). */
  scene: string;
  spec?: SceneSpec | null;
  /** The transition preset it comes in by; null: picked when it plays. */
  transition: string | null;
  origin: UpNextOrigin;
  transition_origin: UpNextOrigin | null;
  why?: string | null;
  /** Why it's here, in page words ("Fits calm, dreamy · you rated it 4★", "You queued it"); `why` is technical. */
  why_words?: string | null;
  /** Seconds (at read time) until it may / must play; null: only on Next (scene picking off). */
  eta_s: [number, number] | null;
}

export interface UpNextEffect {
  id: string;
  /** An effect preset's name. */
  effect: string;
  origin: UpNextOrigin;
  why?: string | null;
  /** Why it's here, in page words ("Fits calm, dreamy · you rated it 4★", "You queued it"); `why` is technical. */
  why_words?: string | null;
  /** Seconds (at read time) until it comes in; null: only on Next effect. */
  eta_s: number | null;
}

/** GET /renderer/api/sessions/{sid}/upnext (and the session.upnext event): a follower shows its leader's plan. */
export interface UpNextPlan {
  rev: number;
  /** The screen whose session plans it (a room's leader). */
  planned_by?: string;
  scenes: UpNextScene[];
  effects: UpNextEffect[];
}

export type UpNextEdit =
  | { op: "add"; kind: "scene"; scene: SceneSpec; transition?: string | null; at?: number }
  | { op: "add"; kind: "effect"; effect: string; at?: number }
  | { op: "play" | "keep" | "remove"; id: string }
  | { op: "move"; id: string; to: number }
  | { op: "set"; id: string; scene?: SceneSpec; transition?: string | null; effect?: string }
  | { op: "reroll"; id: string }
  | { op: "reroll"; kind: "scene" | "effect" };

export interface UpNextEditResult {
  upnext: UpNextPlan;
  id?: string;
  cue?: unknown;
  fx?: unknown;
}

// ── Virtual DMX fixtures (room_service dmx/api.py: GET /api/v1/fixtures) ──

/** A Hue lamp's state as a room target value: "dmx" follows Maestro; a state is {on, bri, xy | mirek}. */
export type HueValue = "dmx" | "off" | "taken_over" | { on: boolean; bri: number; xy?: [number, number]; mirek?: number; hex?: string };

export interface FixtureChannel {
  offset: number;
  address: number;
  attr: string;
  field: string;
}

export interface Fixture {
  id: string;
  label: string;
  kind: "screen" | "hue" | "other";
  profile: string;
  profile_label: string;
  address: number;
  end: number;
  footprint: number;
  channels: FixtureChannel[];
  screen?: string;
  placement?: string;
  light_id?: string;
  /** The bridge's reading of the lamp. */
  light?: { on?: boolean; bri?: number; mode?: string; value?: HueValue; hex?: string | null } | null;
  /** The latest values Maestro sent (null before any Art-Net). */
  dmx: Record<string, unknown> | null;
  dmx_hex: string | null;
  status?: string;
  status_text?: string;
  target?: string;
  control?: "dmx" | "house";
  desired?: HueValue | null;
  house_value?: HueValue | null;
  writable?: boolean;
}

export interface FixturesView {
  enabled: boolean;
  artnet?: {
    online: boolean;
    pps: number;
    age_ms: number | null;
    packets: number;
    last_from: string | null;
    universe: number;
    bind: string | null;
    bind_error: string | null;
    other_universes: Record<string, number>;
    source: string | null;
  };
  hue?: { state: string; detail?: string | null; bridge?: string; host?: string; model?: string; reachable?: boolean | null; error?: string | null };
  bus?: { url: string; clients: { client_id: string; role: string }[] } | null;
  patch_error?: string | null;
  fixtures: Fixture[];
}

/** show_service's test sources (GET/POST/DELETE /show/api/v1/test): recorded music and the test piano, played
 *  through the show engine, so the screens, lamps and beat clock all hear them. */
export type ShowTestSource = "audio" | "piano";
export interface ShowTestPiece {
  id: string;
  label: string;
  kind: "file" | "phrase";
  genre?: string;
  artist?: string;
  license?: string;
  duration_s?: number;
}
export interface ShowTestState {
  source: ShowTestSource;
  /** "preparing": rendering the chosen sound before it plays; "finished": a play-once piece ended by itself. */
  state: "off" | "preparing" | "starting" | "playing" | "stopping" | "finished" | "failed" | string;
  piece: string | null;
  label: string | null;
  error?: string | null;
  /** The piano's sound and where it plays (ids from ShowTestView.instruments / outputs); absent on an older show_service. */
  instrument?: string | null;
  output?: string | null;
  once?: boolean;
  /** 0..1 while the sound is got ready, else null. */
  progress?: number | null;
  /** The speed it plays at, and where in the piece it is (seconds at its own tempo); a newer show_service's. */
  speed?: number | null;
  pos_s?: number | null;
}
/** A virtual piano sound show_service can play the test piano with (only installed ones are listed). */
export interface ShowInstrument {
  id: string;
  label: string;
  /** "rendered": made into a sound file first (state "preparing"); "live": played note by note. */
  kind: "rendered" | "live" | string;
}
/** Where the test piano's sound comes out ("pc22": PC22's speakers, "none": the screens only). */
export interface ShowOutput {
  id: string;
  label: string;
  /** A browser that plays PC22's stream ("device:<id>", listenStream.ts), seen in the last 12 h. */
  device?: boolean;
}
/** POST /show/api/v1/test's optional piano fields. */
export interface ShowTestStartOpts {
  instrument?: string | null;
  output?: string | null;
  once?: boolean;
  /** The piece's tempo times this (0.25-4; 1 as written). */
  speed?: number;
  /** Start this far into the piece (seconds at its own tempo): a speed change carries on from pos_s. */
  start_s?: number;
}
export interface ShowTestView {
  sources: Record<ShowTestSource, ShowTestState>;
  pieces: Record<ShowTestSource, ShowTestPiece[]>;
  detail?: string;
  /** Absent on an older show_service: the card then offers no choice of sound. */
  instruments?: ShowInstrument[];
  default_instrument?: string;
  outputs?: ShowOutput[];
}

/** One saved take of the Steinway (GET /show/api/v1/piano/recordings); it plays as test piece "rec:<id>". */
export interface PianoRecording {
  id: string;
  title: string;
  /** Whose piece it is, and its genre, when the player said (printed on its score). */
  composer?: string | null;
  genre?: string | null;
  /** ISO 8601 with offset. */
  recorded_at: string;
  duration_s: number;
  notes: number;
  /** The library id of the piece it was played for (Record on a score), or the piece a section of it was spliced
   *  into (a new version: the piece itself is untouched). */
  of?: string | null;
  version_of?: string | null;
}
/** Stop for a piece (the score page): the take named after it, or (with a section, in the piece's own seconds from
 *  its score's bar map) spliced into that section as a new recording. */
export interface PianoRecordAsk {
  piece: string;
  section?: { from_s: number; to_s: number; from_bar?: number; to_bar?: number };
}
export interface PianoRecordingState {
  state: "off" | "recording" | string;
  /** How long it has been recording, while it is. */
  since_s: number | null;
  notes: number;
  duration_s: number;
}
/** GET /show/api/v1/piano/recordings and POST …/piano/record {on}: the recorder and the takes, newest first. */
export interface PianoRecordingsView {
  recording: PianoRecordingState;
  recordings: PianoRecording[];
  /** POST {on: false} only: the take just saved, or null when nothing was played. */
  last?: PianoRecording | null;
  /** POST {on: false} only: "take", or "version" (a section spliced into the piece). */
  made?: "take" | "version" | string;
  /** Why it was kept differently (a section that couldn't go in), or why it couldn't be saved. */
  error?: string;
}

/** The test piano: the renderer plays a piece into every screen's sound view (GET/POST /renderer/api/sound/test). */
export interface TestPianoView {
  available: boolean;
  running: boolean;
  piece: string | null;
  playing_s?: number | null;
  pieces: { id: string; label: string; kind: "phrase" | "file" }[];
}

/** The test show: room_service's fake Art-Net, a stand-in for Maestro (GET/POST /api/v1/dmx/fake). */
export interface TestShowView {
  available: boolean;
  running?: boolean;
  packets?: number;
  rate_hz?: number;
  stopped?: string | null;
  running_s?: number | null;
  song?: {
    song?: string;
    bpm?: number;
    target_bpm?: number;
    pulses?: boolean;
    section?: string;
    bar?: number;
    pending_song?: { song: string; at_bar: number } | null;
    songs: string[];
    bpm_range: [number, number];
  };
  lamps?: { total: number; armed: number; following: number; state: string };
  renderers?: number;
  artnet?: { online: boolean; source: string | null; last_from: string | null };
}

// ── show_service's Music card (GET /show/api/v1/card, WS /show/v1/events; show_service/contract.py CardData) ──
// Every key may be missing: the socket's merge patches delete a key with null, and a missing key reads as null.

/** An active nudge (× clears it: DELETE /show/api/v1/nudge/{id}). `label` is in page words ("Calmer ×2"). */
export interface ShowChip {
  id: string;
  kind?: string;
  label?: string;
  expires_in_s?: number | null;
}

/** A palette the card offers: Auto, From the art, then the mode's first five. */
export interface ShowPaletteChoice {
  name: string;
  label?: string;
  family?: string | null;
  /** "#rrggbb" colours ([] for From the art before the first still). */
  swatch?: string[];
}

/** One of Auto's announcements: a switch ("Auto → Piano · the piano is playing", with Undo), a suggestion, a hold. */
export interface ShowAutoNote {
  kind?: string;
  text?: string;
  mode?: string | null;
  undo?: boolean;
  expires_in_s?: number | null;
}

export interface ShowCard {
  /** text: "soul · relaxed", or Quiet / Talking / No sound coming in / Music / Listening; loose: following loosely. */
  hearing?: { text?: string; level?: number; loose?: boolean; stream?: string | null; test?: boolean } | null;
  vibe?: { name?: string | null; label?: string; known?: boolean } | null;
  steps?: { value?: number; min?: number; max?: number; at_limit?: boolean } | null;
  palette?: { pick?: string; current?: string | null; choices?: ShowPaletteChoice[] } | null;
  look?: { id?: string | null; keep_in_s?: number | null; next_pending?: boolean } | null;
  house?: boolean | null;
  low?: boolean | null;
  chips?: ShowChip[] | null;
  /** The AI listening switch; `why` says (in words) why clips aren't going when they aren't. */
  listening?: {
    on?: boolean;
    effective?: boolean;
    why?: string | null;
    /** What it has cost today, and roughly what an evening costs at the current cadence and model (show_service's
     *  estimate), in US dollars; who it sends to, in words ("Google"). */
    spend_today_usd?: number | null;
    est_evening_usd?: number | null;
    provider?: string | null;
    /** Whether the room's mode lets clips go (owner, 20 Oct: set per mode in the mode editor; live modes do). */
    mode?: boolean | null;
    /** README S5.22: what the AI makes of the music in a line ("soft late-night jazz trio"); null until it has said. */
    character?: string | null;
    /** The art wish (owner, 28 Oct), when show_service puts it in the listening block: see ShowCard.art. */
    art?: ShowArtWish | null;
    /** The AI's recommended approach (intensity 0..1, how often looks change, how lively the transitions). */
    approach?: { intensity?: number | null; change?: string | null; transitions?: string | null; conf?: number | null } | null;
    /** The cue sheet: what it's ready for, each in a plain line. */
    armed?: ShowCueLine[] | null;
    /** The last round trips to the AI, newest first (up to 10; owner, 21 Oct: "see casually within the /room tab what
     *  it is returning on each roundtrip"). Skips and failures are included with ok false and the words why. */
    history?: ShowListenRound[] | null;
  } | null;
  describe?: {
    state?: "idle" | "working" | "done" | string;
    text?: string | null;
    reply?: string | null;
    suggest?: { mode: string; label?: string } | null;
  } | null;
  /** The room-level Auto: line is its words ("Auto · watching", "Auto paused · you chose Disco · Resume"). */
  auto?: { on?: boolean | null; state?: string; line?: string; chip?: ShowAutoNote | null; suggest?: ShowAutoNote | null } | null;
  /** The room mic (owner, 16 Oct: it runs only while something needs it, and Mute always wins). */
  mic_state?: ShowMicState | null;
  /** What pictures the art director is asking for (owner, 28 Oct: art tags → Ambient); absent until show_service
   *  sends it. */
  art?: ShowArtWish | null;
}

/** The art wish on room.direction (show_service; art-tags phase 2): words from config/art_tags.yaml's subject, moods
 *  and medium facets, and `theme`, at most 10 plain words ("late-night city, smoky rooms, painterly abstraction").
 *  via: listener = the AI listener's own wish; genre = the music kind's defaults (music_art); none = no music known. */
export interface ShowArtWish {
  subjects?: string[] | null;
  moods?: string[] | null;
  media?: string[] | null;
  avoid?: string[] | null;
  theme?: string | null;
  via?: "listener" | "genre" | "none" | string | null;
}

/** POST /show/api/v1/step's answer (at a limit: steps.at_limit). */
export interface ShowStepResult {
  steps?: ShowCard["steps"];
  chips?: ShowChip[];
  done?: boolean;
}

/** POST /show/api/v1/action's answer (director words). */
export interface ShowActionResult {
  done?: boolean;
  chips?: ShowChip[];
  look?: { id?: string | null; next_pending?: boolean };
  detail?: string;
}

/** POST /show/api/v1/autopilot/undo: nothing to undo is 200 {undone: false, why, detail}. */
export interface ShowUndoResult {
  undone: boolean;
  mode?: string;
  why?: string;
  detail?: string;
}

/** POST /show/api/v1/describe (never an error: a failed AI falls back to keywords). */
export interface ShowDescribeResult {
  ok?: boolean;
  reply?: string;
  applied?: ShowChip[];
  suggest?: { mode: string; label?: string } | null;
}

/** One AI listening round trip (show_service listener history; e9, 21 Oct). Fields may be missing on skips/failures. */
export interface ShowListenRound {
  t_ms?: number | null;
  /** Wall-clock seconds (epoch). */
  at?: number | null;
  call?: string | null;
  ok?: boolean | null;
  model?: string | null;
  latency_ms?: number | null;
  cost_usd?: number | null;
  content?: string | null;
  character?: string | null;
  genre?: string | { label?: string | null; detail?: string | null } | null;
  moods?: string[] | { words?: string[] | null } | null;
  coming?: Array<{ kind?: string | null; eta_min_s?: number | null; eta_max_s?: number | null }> | null;
  why?: string | null;
  error?: string | null;
  /** The exact clip sent (show_service keeps the last few): a path through room's /show door. */
  clip_url?: string | null;
  device?: string | null;
  clip_s?: number | null;
  /** Lost audio filled with silence inside the clip. */
  gaps?: number | null;
  gaps_s?: number | null;
  /** The clip's measured loudness before normalising, and the gain applied. */
  lufs?: number | null;
  gain_db?: number | null;
  /** The pictures that call asked for (owner, 28 Oct), when the listener sent an art wish. */
  art?: ShowArtWish | null;
}

/** One armed cue (show_service CueLine): words like "Big change at the chorus, likely in 10-25 s" or "Ready: slow fade if
 *  the music fades"; window_s is seconds from the card's time (a forecast), null for a standing cue. */
export interface ShowCueLine {
  kind: string;
  words: string;
  window_s?: [number, number] | null;
  size?: string | null;
}

/** GET/PUT /show/api/v1/listening, the parts the page reads. */
export interface ShowListeningView {
  on: boolean;
  effective?: boolean;
  why?: string | null;
}

/** GET/PUT /show/api/v1/mic and the card's mic_state: whether the room mic is on, and why. */
export interface ShowMicState {
  on?: boolean;
  muted?: boolean;
  /** Something needs it (Auto, or the mode follows the music), muted or not. */
  wanted?: boolean;
  /** In words: "Listening for Auto", "Off: nothing needs it", "Muted". */
  why?: string | null;
  /** What needs it: Auto, or the mode (its label). */
  claims?: { by: "auto" | "mode" | "listening" | string; label?: string | null }[];
  mode?: string | null;
}

/** GET /show/api/v1/inputs (and PUT /show/api/v1/input's answer): every room mic show_service can hear, by sender.
 *  A sender lists its own capture devices (PC22's agent: every Windows input; the kiosk: its USB mic). */
export interface ShowInputs {
  /** The stream show_service listens to now. */
  active?: string | null;
  /** The owner's pick (null: show_service's own order). */
  choice?: { stream: string; device?: string | null } | null;
  inputs: {
    stream: string;
    sender?: string;
    /** The sender's name in words ("PC22"). */
    label?: string;
    state?: string;
    /** The device it captures now. */
    device?: string | null;
    devices?: string[];
  }[];
}

/** One message on WS /show/v1/events (seq goes up by 1 per message; a gap means ask for a fresh welcome). */
export type ShowEvent =
  | { type: "welcome"; seq?: number; card: ShowCard }
  | { type: "card"; seq?: number; patch: Record<string, unknown> }
  | { type: "auto"; seq?: number; event: ShowAutoNote }
  | { type: "describe"; seq?: number; result: ShowDescribeResult }
  | { type: "pong"; seq?: number };

/** GET /renderer/api/diag's host block (stream_service diag.host); the memory fields are optional (newer renderers). */
export interface RendererDiag {
  host?: {
    load?: number[];
    cpus?: number;
    mem_total_mb?: number | null;
    mem_available_mb?: number | null;
    swap_total_mb?: number | null;
    swap_used_mb?: number | null;
  };
}

/** GET /renderer/api/state: the live sessions' summaries; only what the "PC busy" badge reads. */
export interface RendererState {
  sessions?: Array<{
    screen?: string;
    state?: string;
    degrade?: { level?: number; why?: string | null; out_fps?: number | null; scene_div?: number | null } | null;
    fps?: number | null;
  }>;
}
