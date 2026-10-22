# Room page (`/room`)

The room's control panel (AV_PROGRAMME §5), used mostly from a phone on the
house Wi-Fi, by the owner and by guests. It talks only to **room_service**
(LIVE_V1 §3) over HTTP and its events socket.

## What's on the page

Operating the room is open to anyone on the house Wi-Fi: the page connects and works
with no key. Only setup edits need the admin token (see **Connection** below).

### The main screen

It answers "what's the room doing, and change it", in plain words, with no phase names,
URLs, JSON or ids:

1. **Header**: "Room", a status pill with a dot and one word (Connected, Reconnecting,
   Connecting, Offline, Locked), and the menu button. Tapping the pill opens the menu too,
   whose first section says the same thing in a full sentence.
2. **Mode**: one big tile per mode from `GET /modes`, in the file's order, with its `label`, its
   `description` if it has one, and one line saying what it changes in the room, in words from
   `GET /room/describe` (target labels, `x-ui` labels, presets and switch words; no device names in
   code): "Glitterball spinning · Hue follows the show · Blinds lowered (not connected yet)". Only
   devices are listed (not the page's reload policy or the sensor hold); working devices come first,
   then the screens, then things not connected yet (a placeholder, unreachable or a dry run). The
   current mode is teal with "✓ Now"; the room's start mode says "Starts here". With four or more
   modes, a phone shows them two to a row, each with one 13 px line, so the modes and every screen
   still fit on one screen. Tiles never go dead: a tap while a switch runs wins (room_service: the
   last request wins), and a tap while the room is out of reach says "The room isn't answering;
   trying…" and sends it again every 2 s for a minute.
3. **The progress line** under the tiles, only while it matters: "Switching to Disco · glitterball
   starting, glitterball speed to high" during a switch (what the target mode still has to change,
   from its describe entries, the steps and what the devices are doing now; "screens changing" or
   "nearly there" when that's all), "Now Gallery" for 15 s after a clean one. A switch Auto made
   starts "Auto:", with Auto's reason when it gives one. If steps failed it stays for up to 10 minutes
   and names the failure ("Disco is on, but North screen couldn't change to live"), amber when most
   of the switch worked and rose when none of it did. **Details** unrolls the steps in words (a step
   skipped because its device isn't connected says "Not connected yet"; a failed one shows the
   room's reason).
4. **Rooms**: below the modes (which stay room-wide), one section per room of the site, in the order of
   `routes/data/rooms.json` (room_service sends it as `status.rooms`): the room's name as the heading, its **live
   card** (below; a room of two or more screens, while any of them is live), its screen cards, then its **Lights**
   (the lamps it lists, from one shared fixtures poll). Screens or lamps in no room go in an
   **Other** section at the end; a room with nothing to show is left out. Without `status.rooms` (an older
   room_service) the page shows one flat **Screens** list and **Lights**, as before. There is no blinds view yet.
   **Screens**: one card per screen with its name (`status.screens.<id>.name`, the site config's) and its state in words: "Showing
   art", "Live · Fractal zoom", "Switching to live…", "Going back to art…", "Waiting to go
   live… · Can't go live yet", "Problem — tap for details". The state wraps rather than
   truncates on a narrow phone. An art screen shows just "Showing art", even when its page
   isn't on room control or live is off for it: that only matters for going live, so it is
   explained under **More**.
   - A **live** screen has its core controls inline (the live controls, below, when
     room_service has the live routes; otherwise a plain scene select):
     - **Scene**: a button with the scene on glass (thumbnail and name, following the
       scenes it picks by itself). It opens the **scene picker**: a sheet from the bottom on a phone, a centred
       dialog on a desktop, with a thumbnail grid (the scene on now first, then the room's own
       scenes, then imported shader ports), search, and one row of tag chips (families, moods,
       "Plain sources"). Tapping a scene switches to it at once. The picker's footer has
       **Transition** ("The room's choice", Cut, and every transition preset) and **On the
       beat**; both are remembered on this device.
     - the scene's `x-ui.basic` parameters, one compact row each, as before. While the
       screen has its own colours (below), an `x-palette` parameter is hidden: the source
       ignores it then.
     - **Scope follows where a control sits** (owner, 17 Oct): in a room of two or more screens the room's
       controls (the rows below, effect picking, How many, energy, Up next) are on the room's live card, not here,
       and act on every live screen in it; the screen's card keeps its own scene, settings, colours, feel and
       effects. Its look controls (colours, the feel) change only this screen once the renderer has `look_scope`;
       until then they say "Both screens" (the renderer applies a look to the whole room). On a screen in no room,
       or the only screen of its room, the rows below are on its card.
     - **Scenes**: "Picked by itself" or "You pick", with a switch (**Pick scenes by itself**: the
       renderer's own scene picking, `look.auto`) and **Next scene** (always shown: its next pick now; with
       picking off, that one then stays). The word "Auto" is kept for the room-level Auto, which picks the mode
       (SHOW_ENGINE §8.9).
     - **Effects**: the same shape: **Next effect** (swaps the one it picked for another; yours stay) and **Pick
       effects by itself** (`auto.fx.enabled`; off is the pause: what's on stays, nothing changes by itself; it
       never depends on scene picking, and a mode change doesn't turn it back on). A status line says what that
       means now ("Picked by itself · next change in ~40 s · 1 or 2 at a time", "You pick · these stay on").
       While on, **How many**: One / Two / Three (`auto.fx.max` n with `direction.fx.density` 1, so exactly n).
       Energy chips Calm, Steady, Lively, Wild (`direction.energy`, plus `transition_style` gentle / any /
       lively / wild) show while either picking is on.
     - **Up next** (`UpNext.tsx`, `useUpNext.ts`; the renderer's `GET/POST /renderer/api/sessions/{sid}/upnext`):
       the next scenes (up to 4, each "via" the transition it comes in by) and effects (up to 3), with
       rough times ("next", "in ~25 s", "in ~3 min", "on Next only" while picking is off) that count down
       on the page, and "yours" on the ones you added, changed or kept. Each row has **Play** up front;
       tapping it unrolls **Comes in by** (scenes: "Picked by itself" or any transition), **Move up**,
       **Keep** (it won't swap it), **Pick another**, **Choose…** (the thumbnail picker) and **Remove**;
       **+ Scene** / **+ Effect** put one at the top, and **Pick again** re-picks its own entries (yours
       stay). In a room either screen shows and edits the leader's list ("Shared with …"). A phone shows
       the next scene and the next effect with **Show all**. Read on `session.upnext` events, after each
       change and every 10 s; nothing is ever disabled: a change that can't get through says why in
       words, with **Try again** when it never reached the renderer.
     - **Colours**: swatches for the session palettes, plus "the scene's own".
     - **Effect chips**: one per effect on the screen's bus. Dashed: the room's pick; solid: yours (this screen);
       grey "· with the scene": a Gallery look's own. A tap opens a sheet, never deletes: yours has **Showing**
       (off: paused, kept in its place; `bypass`), its own **Strength** (`level`), **Choose another…** and
       **Take it off**; the room's pick has **Swap it** and **Take it off** (both screens); the scene's has **Next
       scene**. **Change effect** (where the rows are) is the twin of Change scene: tap one to put it on now in
       place of the one it picked; its first tile, **No effects**, turns effect picking off and takes every effect
       off; **Add on top** (remembered on this phone) keeps what's on and adds it as yours. On a room's screen card
       it is **Add an effect** (on top, this screen only). Nothing is disabled: at the renderer's limit
       (`fx_bus.max_slots`, 6) your oldest comes off. **Strength** is the screen's master (`master.amount`).
     - **Back to art**, at the end of the card.
   - **A room's live card** (`RoomLive.tsx`, `useRoomLive.ts`), above its screens' cards while any is live:
     "Both screens · Live together: a change here changes both screens" (or the one screen live). **Change scene**
     (on every live screen here), the Scenes and Effects rows, the room's picked effects and **Change effect**, How
     many, energy, **Up next** (the room's plan), and under **What it picks from, and how often**: the scenes it
     picks from, how long each stays, how often it changes scene, on the beat, how often effects change and
     **Change with the music** (`direction.fx.rate`, `direction.fx.follow`). It reads each live screen's session
     (`GET /renderer/api/sessions/{sid}`: the look, its renderer room and Up next) and sends a room-wide change once
     per renderer room (to its leader, which the others follow) and a per-screen one (a scene by hand, the effects
     bus) to every live screen.
   - A screen on its way to live, or one whose last change failed, gets **Cancel** /
     **Back to art** inline, except while a mode change is the thing taking it live: a
     Cancel there would fight the mode just chosen, so it stays under **More**.
   - Every card has a visible **More** (with a chevron) that unrolls the rest.

### Behind each screen's "More"

- **Going live** (an art screen): the scene, its settings, transitions in and out, picture
  quality, and **Go live**. If live is off for the screen (`policy.live_enabled` false, or a
  409 `live_disabled`), this says so instead, and how the owner turns it on.
- **While live**: every parameter, **Reset all** and per-field reset, a Sending/Applied
  indicator; **Picking, the feel and fades**: scene picking (what it picks from, how long each scene stays, how
  often it changes, on the beat) and effect picking (how often they change, change with the music), none of it
  greyed; the feel (speed, movement with the music, moods, music, transition style, colour family, **Back to
  neutral**); how long colour changes and effects take to blend; and what the renderer hears of the music; then
  transitions and quality for next time. On a room's screen card it is **The feel and fades** (picking is on the
  room's live card).
- **Going live** uses the same scene picker (choose mode: no transition footer).
- Explanations when they apply: the last change failed (with the raw error), the screen's
  page isn't on room control (`?ctl=`), the scene list won't load, the stream service is
  down.
- **Snapshots**: the anchor and target stills of the last cut, full size on tap.
- **Technical details**: desired vs observed, phase, session, acks, the brightness mask,
  playback stats.

### Behind the menu

A side sheet with: the connection in a sentence, last update, retry and version, and
**Refresh**; the **room service address** (change / use default); the **stream service**; the
**last mode change** with its steps in words; **what each mode does** (its name, "Starts here" on
the start mode, its description, and what it changes in the room in the tiles' words, in full), then
which devices aren't connected yet, in a sentence from describe's subsystems ("The TVs and the DMX
power aren't connected yet, so modes skip them."; a dry run gets its own: "…modes set them, but
nothing moves."); and the **Activity log**, which scrolls into view when opened.

### Edit, duplicate, delete

The "⋯" in a tile's corner (a 44 px target) has **Edit**, **Duplicate**, **Move earlier**, **Move later** and
**Delete…**. Every one saves the whole modes file through `PUT /api/v1/room/config` with the revision it read (a
409 says someone else saved just now; a 400 says the room's checks found a problem, with the list in the activity
log). They stay available offline: a save the room doesn't answer says so.

- **Edit** opens the mode editor (`ModeEditor.tsx`). Modes are flat: each one lists everything it sets.
- **Duplicate** (and **Add a mode**, a copy of the mode the room is in, named "New mode") inserts a full copy right
  after the original and opens it in the editor. The copy leaves out the mode's comments (`_` keys) and the triggers
  that switch it on by itself (`autopilot.enter_when`, `autopilot.otherwise_suggest_when`), so a copy never switches
  the room by itself; if it had one, a note says "Copied without its switch-by-itself trigger".
- **Move earlier / later** swaps the mode with its neighbour in `modes` (the tiles follow the file's order). The first
  tile has no "Move earlier", the last no "Move later".
- **Delete…** is never refused. The dialog says "<Mode> goes for everyone. You can undo for a few seconds."
  - The mode the room is in: "Switch to <the start mode>, then delete" (`PUT /mode` first, then the save).
  - The start mode: "Start in [<mode> ▾] instead, then delete" sets the file's `default` to the one picked.
  - In the same save, whatever names the mode goes too: its name comes out of other modes' `autopilot.from`
    (emptied rather than removed, so Auto never gains a way in) and `leave_to` (back to `previous`), and out of
    switching-order rules (`transitions` from/to). A legacy store/retrieve pair is never left half: a retrieve of what
    only this mode saved becomes what it did with nothing saved (its `else`, or nothing). The dialog lists these
    changes before you confirm.
  - Afterwards a note at the foot of the page says "<Mode> deleted", with the same list and **Undo** for 15 s. Undo
    saves the file as it was, on top of the delete's revision.

### The skin

Light and warm, never dark: a soft off-white page, white cards with gentle shadows and
generous radius, one calm teal accent, and muted status tints (emerald, amber, rose, sky)
with dark text. Every text/background pair is at least 4.5:1 (WCAG AA). Body text on the
main screen is 16 px or more (17 px names; 15 px only for secondary labels: "Now", "More",
the header word, mode descriptions), and every tap target on it is at least 44 px. Icons (lucide) are used
sparingly: the menu, the modes, the screens, status. Animations stop under
`prefers-reduced-motion`.

The page defines its own colour variables on its root element and on each portal it opens
(`theme.tsx`), so the shared shadcn components follow it, and `<html>` is never touched (no
`dark` class). Leaving `/room` leaves the document as it was: only the tab title is set,
and it is put back.

At 390 px wide (16 px gutters, no horizontal scroll) the modes and every screen's state fit
on a 390×844 screen with 2–5 modes and 3 art screens. A live screen's card now carries the
live controls too (about 720 CSS px tall with two basic parameters, scene picking on and one effect),
so the page scrolls while a screen is live. On a desktop the page is a centred column of
about 720 px.

**Thumbnails** load only when their tile nears the visible part of its scroll area (an
IntersectionObserver per tile, 240 px ahead), over a neutral placeholder, and the picker grid
renders 36 tiles at a time, adding more as its end scrolls into view. room_service serves them
from a small cache with `max-age=300`, so a phone never asks for hundreds at once.

## Run it (dev)

```bash
# never `npm run build`: dist/ is what the TVs load
VITE_API_URL=http://127.0.0.1:5000 npx vite --port 5175 --strictPort
# then open http://<host>:5175/room
```

Use it from the LAN address. room_service is not exposed through ngrok.

## Connection

| What | How |
|---|---|
| One screen | `?screen=<id>` (the TVs' QR codes: `http://192.168.1.205:8080/room?screen=<id>`, built by `get_qr` in `routes/utils.py`). That screen's card starts unrolled and highlighted (a teal ring), and the page scrolls to it once the modes above it have loaded. The header gains a row with the screen's name and two links: **Art** (`/?publish=<id>`, the main app on that screen) and **Scheduler** (`/scheduler?destination=<id>`). An id the room doesn't have gets a short note above the screen cards. |
| room_service URL | `?room=<url>` (remembered), else `localStorage.sm_room_url`, else `${location.protocol}//${location.hostname}:8700`. Change it in the menu under **Room service address**. **Use default** clears the stored URL. |
| Token | None needed to operate the room: it is open on the LAN (LIVE_V1 §3), so the page connects and works without one. The admin token ("setup key" on the page) is only for setup edits. It is entered from the menu (**Setup access**), kept in `localStorage.sm_room_token` like the admin panel, and sent only once entered. If room_service ever answers 401, the page stops and brings up the key dialog ("This room needs a key… Ask whoever runs the room"); if that is dismissed, a "This room needs a key" card offers it again. The dialog's **Where's the key?** unroll names the file (`~/.config/screen-machine/room_service.token` on the workstation) and the server it is for. |
| Token binding | `?room=` comes from the address bar, so a crafted link could aim the page at another host. The token is therefore saved together with the URL it was entered for (`localStorage.sm_room_token_url`) and sent only there. A token with no binding (e.g. seeded by a test harness) is used only for a room_service on the page's own hostname. Anything else gets the prompt, with a note saying which server the saved token belongs to. |
| Events | `WS /api/v1/events` (with `?token=…` only when a key is saved). Reconnects with full-jitter backoff `random(0, min(10 s, 0.5 s·2^n))`; `n` resets once a message arrives. The page never sends anything on this socket. |
| Fallback | While the socket is down, `GET /api/v1/status` every 2 s. Every request times out after 10 s (snapshots after 30 s), so polls can't pile up. While it is up, a status read every 15 s acts as a liveness check (§3 has no heartbeat on this socket). If that read fails and the socket has also been silent for 15 s, the socket is closed and polling takes over. A socket that is still delivering is kept, because HTTP can fail on its own, for example on a CORS refusal. |
| Header word | Connected (events socket open), Reconnecting (socket down, polling still fresh), Connecting, Offline, Locked (401). Tapping it opens the menu, which says it in a full sentence, with the last update, the next retry and the version. When there has been no fresh status for 6 s a "Lost touch with the room" banner says the page shows the last known state; the mode tiles still take taps (they keep trying). |

Note: the browser logs a failed socket URL, including `?token=`, in the devtools
console. That comes with the contract's `?token=` design; the page itself never logs the token.

## What it sends

| Action | Call |
|---|---|
| Mode button | `PUT /api/v1/mode {mode}`; the newest tap wins, and one the room doesn't answer is sent again every 2 s for a minute. Tapping the current mode applies it again. |
| Add, duplicate, move, delete a mode | `GET /api/v1/room/config`, then `PUT /api/v1/room/config {config, base_revision, note}`; undo of a delete puts the previous file back with the delete's revision |
| Go live | `PUT /api/v1/screens/{id} {desired: {live: {scene, transitions: {in, out}, profile?}}}` |
| Back to art | `PUT /api/v1/screens/{id} {desired: "art"}` |
| Parameter edit while live | `PATCH /api/v1/screens/{id}/live {params}`. Changes are batched and sent at most every 200 ms, with **at most one request in flight**. Edits made meanwhile merge into the next request, so a slow room_service or stream_service never builds a backlog of stale values. Order is kept, and the last value always goes. **Reset** sends the preset's value back, because PATCH merges. |
| Switch scene while live | Picking a scene in the picker sends `POST /api/v1/screens/{id}/cue {to: <preset name or {source}>, transition?, at}` straight away (`transition` only when one is chosen; `at` is `beat` with **On the beat**). room_service records it in the desired state. Without the live routes the page falls back to `PATCH /api/v1/screens/{id}/live {scene, transition: <in transition>}`. The new scene starts from its preset values, and its parameters can then be changed live. If the switch is refused, the picker goes back. |
| Library, thumbnails | `GET /api/v1/library` with the catalog (and again 2 s after a `catalog.changed` stream event); thumbnails are `<img src>` to `/api/v1/thumb/<plug-in>.png?v=…`. A 404 on the library means an older room_service: the page keeps the plain selects and hides the live controls. |
| The look | `GET /api/v1/screens/{id}/look` when the screen's session changes, 350 ms after any stream event for that session (scene, cue, effects, director, palette, state), and every 20 s. |
| Scene picking, energy, feel, colours | `PATCH /api/v1/screens/{id}/look` with `{auto: {enabled}}`, `{auto: {pool \| dwell_s \| snap \| fx}}`, `{direction: {…only the changed keys, source: "panel"}}` (room_service merges it), `{palette}` (`null` = the scene's own) or `{palette_transition_s}`. Shown at once, then replaced by the reply. **Next** is `POST …/cue {to: "auto"}`. Feel sliders send on release. |
| Effects | Yours: read-change-write on `GET`/`PUT /api/v1/screens/{id}/fx`: the GET's entries are echoed back (same `id`, preset, params, bindings, level, bypass) with one added, removed or changed, `fade_s` kept (or the new one from **Fades**), `if_rev` set; a 409 `fx_conflict` is retried once. The strength slider sends `master: {amount}` at most every 200 ms with one request in flight. The room's picks (`roomFx.ts`): **Next effect** `POST /renderer/api/sessions/{sid}/fx/next`; **Swap it** the same with `{replace: <slot>}`; **Change effect** `{effect, only: true}` and **Take it off** / **No effects** `POST …/fx/clear {effect?}` (screen-machine-04, 17 Oct). Whether a renderer has those is asked once per session (`fx/clear {probe: true}`: 400 = yes, 404 = no); without them: Up next add + play, and a PUT per screen. |
| Scope | Once the library's features list `look_scope`, a room's live card sends `scope: "room"` (look PATCH once per renderer room, and fx/next, fx/clear), which gives back to the room any part a screen had set for itself; a room's screen card sends `scope: "screen"` (its colours and feel; Swap it / Take it off on the room's pick change that screen only). Without the feature no scope is sent and the card's look controls say "Both screens". If room_service turns `scope` down (it predates it), the room's card resends without it and a screen card says so and stops sending it (never falls back to changing both). The session's `own` (`GET /renderer/api/sessions/{sid}`) says what a screen set for itself: "This screen's own" on its colours, "North screen keeps its own colours and feel" on the room's card. |
| Snapshots | `GET /api/v1/screens/{id}/snapshots/{anchor,target}.png` (with the bearer token when a key is saved), shown through blob URLs. Only the kind a cut produced is re-fetched: the anchor after an entry's `snapshot` phase, the target after an exit's. If polling missed the `snapshot` phase, arriving at `live` or `idle` triggers the fetch instead. When status carries `screens.<id>.snapshots.{anchor,target}` (room_service adds it), a PNG marked absent isn't fetched, so there are no 404s, and it is fetched once it appears. A body that won't decode shows "Couldn't load". Click a thumbnail for full size. |

**Scene spec.** A preset with no changes goes as its bare name (`"fractal-zoom"`).
Changed parameters go as `{"preset": name, "params": {only the changed keys}}`.
A raw source goes as `{"source": name, "params": {…}}`. The top-level
`live.params` is not used. If a desired state carries one, it is read as overrides.

**Transitions and profile** apply when a screen goes live. While it is live,
only parameters and the scene can change.

**Errors.** `{error, detail}` bodies are shown inline. A `409 live_disabled`
on Go live marks the screen as live-disabled, with an explanation. v1 status
doesn't carry `live_enabled`, but room_service sends `screens.<id>.policy.live_enabled`,
which is used up front, and the flat `screens.<id>.live_enabled` is read too.
`409 not_live` on a live edit is explained the same way. A network failure names this
page's origin and room_service's `web_origins` (from `.env`), because to `fetch()` a CORS refusal looks
exactly like an unreachable server.

A failed modes or catalog read is shown in its panel. The last good copy is kept,
and the read is retried every 10 s, and immediately when `stream_service.reachable`
turns true again. **Refresh** re-reads modes, catalog and status through the same connection,
so a late reply from a previous server is dropped.

**Optional status fields read when present.** These are additive and are what room_service sends:

| Field | Use |
|---|---|
| `mode.last_transition` | The last finished transition with its real outcome (`done` or `cancelled`), also after a reload |
| `observed.detail` | Why a phase is held, e.g. "waiting for the page" or "retry in 3.0 s", shown next to Observed |
| `snapshots` | Avoids fetching snapshots that don't exist |
| `policy.live_enabled` | Disables Go live up front |
| step `id` | Matches `mode.transition` step events to their steps |

## The parameter form is generic

It is built from each source's JSON Schema in the catalog (LIVE_V1 §1.1), so a
new plug-in or preset appears in the pickers and gets a form with no UI change.

| Schema | Widget |
|---|---|
| `x-ui.widget: slider` (number with min and max) | slider plus number box, with `x-ui.step` and `x-ui.unit` |
| `x-ui.widget: number`, or a number without both bounds | number box. Typed values commit on Enter or blur, clamped, so typing `1000` while live never sends 1, 10 or 100. Spinner and arrow steps commit at once. Visiting a field without changing it commits nothing. |
| `enum`, or `x-ui.widget: select` | select (numeric enums stay numbers) |
| `boolean`, or `x-ui.widget: checkbox` | switch |
| `x-ui.widget: color` (`#RRGGBB`) | colour picker plus hex box |
| other strings | text box. Commits on Enter or blur, never per keystroke. |

A field shows the preset's value: the schema defaults overlaid with the
preset's `params`. A changed field gets a dot and a reset button, and
**Reset all** (live) or **Reset to preset** (before going live) clears them all. Labels come from `x-ui.label`, then `title`,
then the key. Help text comes from `x-ui.help` or `description`.

The form follows the screen's desired state, for example after a mode switch.
It waits until 3 s after the person last touched it, so a status echo of their
own live edit doesn't move a slider mid-drag.

Fields marked `x-ui.basic: true` are also shown inline on a live screen's card, one
compact row each (label and value beside the control, no per-field reset; that is under
"More"). Without them, nothing extra shows.

## Files

| File | Role |
|---|---|
| `index.tsx` | Page layout (the modes, then one section per room from `status.rooms`, then "Other"), URL and token wiring, connection banners |
| `Fixtures.tsx`, `useFixtures.ts` | A room's lights (`LightsSection`, filtered with `only`), and the one fixtures poll the page shares |
| `useRoomConnection.ts` | Events socket, backoff, polling fallback, status and event reducer, log ring (200 lines; in a run of `screen.mask` lines each screen has one line with a count, even when several screens push interleaved; `status` pushes are counted rather than listed) |
| `api.ts` | `RoomApi` fetch client, URL and token storage, error explanations |
| `types.ts` | LIVE_V1 §1.1 and §3 types (tolerant of added fields) |
| `catalog.ts` | Picker choices, schema defaults, spec building |
| `ModeSwitcher.tsx` | Mode tiles, their ⋯ menu (duplicate, move, delete with undo), the progress line and its step list (also used by the menu) |
| `modeWords.ts` | What a mode changes in the room, in words from describe: the tiles' line, the progress line's "glitterball starting", the menu's "not connected yet" sentences, steps in words |
| `ScreenCard.tsx` | One screen: status in words, inline core controls (hidden Cancel while a mode change owns the go-live), the "More" unroll |
| `useScreenControls.ts` | A screen's live-control state: form, desired-state sync, Go live / Back to art / scene switch, batched live PATCH (kept in the always-mounted card, so it survives "More" closing) |
| `LiveControls.tsx` | `CoreControls` (inline), `LiveSettingsPanel` and `GoLivePanel` (in "More"), and the plain selects |
| `LivePanel.tsx` | The live controls: `SceneButton`, `ScenePicker`, `PickingRows` (Scenes and Effects, How many, energy), the effect chips, sheet and picker, `ColoursRow`, `LookControls` (a screen's) and `LookSettings` (under "More") |
| `useLiveLook.ts` | A live screen's look as state: reads, event-driven refresh, look PATCHes (scope "screen" when it may), cues, the effects bus read-change-write and the throttled strength slider |
| `RoomLive.tsx`, `useRoomLive.ts` | A room's live card and its state: every live screen's session read, room-wide changes once per renderer room, per-screen ones on each |
| `roomFx.ts` | Effects across screens: the bus read-change-write, the limit, the probe for 04's fx routes, take off, put on, swap |
| `Picker.tsx`, `Thumb.tsx`, `liveLib.ts` | The sheet/dialog thumbnail picker and its chips; the lazy thumbnail; scene items, labels and the thumbnail scroll-root context |
| `ScreenDetails.tsx` | Technical details: desired vs observed, phase, session, mask, playback stats |
| `ParamForm.tsx` | Schema-driven form |
| `Snapshots.tsx` | Anchor and target thumbnails, full-size dialog |
| `Header.tsx`, `MenuSheet.tsx` | The header, and the menu sheet (connection, address, stream service, last change, what each mode does, activity log) |
| `EventLog.tsx` | Filterable log: type chips, level, text, pause |
| `TokenPrompt.tsx` | The setup-key (admin token) dialog |
| `connection.ts`, `words.ts` | The connection's word and sentence; plain-language wording for screens, steps (and failed steps) without describe, and modes |
| `theme.tsx`, `tones.ts`, `overlays.tsx`, `bits.tsx` | The light theme, status colours, the side sheet and modal, small shared pieces |

## Tests

These run outside the repo. The scratch harness used during the build was:

- `mock_room.py`: an aiohttp mock of LIVE_V1 §3 on :3013. It has a canned catalog, three modes (one with a refused screen and an adapterless subsystem), the phase walk for entry and exit, PNG snapshots, a drifting mask, ticking kiosk stats, and test hooks `/_mock/requests`, `/_mock/state` and `/_mock/ws_outage?s=N`.
- `test_room.py`: Python Playwright against Chrome for Testing at 1440×900 and 390×844. It covers the token prompt, a mode switch with live step statuses, live parameter edits (slider, number, select, checkbox, colour, and a typed number sent only on Enter), reset to preset, switching scene, a snapshot dialog, back to art, go live from the form, 409 `live_disabled`, a failed step and an unconfirmed step, a failed handshake with `last_error`, log filters, a socket outage leading to polling and then reconnect, a 401 re-prompt, a crafted `?room=` link that must not receive the token, the phone layout with no horizontal overflow, and a check that the page sends zero frames on the events socket.

Added in review, in the same scratch directory:

- `real_room.py`: the real `room_service` package from this repo, in-process on :3014. It is wired to room_service's own test fakes (`room_service/tests/fakes.py`): a fake stream_service, a fake Flask, a fake display page with role `lab`, and a snapshotter that returns real PNGs. Everything is on 127.0.0.1, and state and token stay in the scratch directory. Hooks on :3114 can slow stream_service's PATCH or take stream_service down and up.
- `test_real_room.py` (16 checks) runs against it. It covers:
  - the real status shape: policy, `last_transition`, `detail`, snapshot flags;
  - a mode switch, and live edits that room_service echoes into `desired.live.params` without the form snapping back;
  - visiting a field sends nothing, an integer enum stays a number, and text commits on Enter;
  - a 1.5 s stream_service PATCH: a drag gives at most 4 requests, not a backlog;
  - switching scene, back to art, and the last transition shown after a reload;
  - the catalog recovering by itself after stream_service comes back;
  - a superseded transition, mask-log coalescing, an explained CORS refusal, and the phone layout.
- `test_room_extra.py`: against the mock, with interleaved mask pushes from two screens, and the transition outcome without `last_transition`.

Redesign (14 Oct), in a sibling scratch directory (`room-ui/`):

- `run_mock.py`: runs `mock_room.py` with this session's dev origin (:5186) added. `--open` models the open-LAN
  ruling (no token needed); `--basic` marks zoom speed and palette `x-ui.basic` and gives two modes a description.
- `after.py` (25 checks, 390×844 and 1440×900): the main screen fits with no jargon and no tap target under 44 px; a
  mode switch with its progress line and details; a scene switch from the inline picker; live parameter edits from
  "More"; a snapshot; technical details; back to art; go live from "More" with a changed setting, transitions and
  quality; 409 `live_disabled`; a mode with a failed step; the menu (address, last change, activity log, setup key);
  leaving `/room` leaves `<html>` untouched; the token prompt on a 401 and the "Locked" card; an unreachable server
  (with the CORS explanation); a server that dies mid-session (banner, controls off); the open LAN with no token; and
  `x-ui.basic` fields inline on a live card.

Finisher pass (14 Oct), in `room-ui-final/` (`run_mock.py` adds `--modes5` for five modes; `final.py`, 14 checks):
the main screen with no hint, a visible "More" and 44 px targets; a mode switch with no inline Cancel; the compact
basic rows and an inline edit's PATCH; "More" while live; the menu opened from the status pill, with the activity
log scrolled into view; a partial failure (amber, naming the step); a long scene name at 360 px; the fit with five
modes and a live screen with two basic parameters; desktop width; the key dialog's wording and the "needs a key"
card; and the current mode tile staying readable offline.

Live controls (14 Oct), in `livectl/` (`smoke.py`): one pass against a real room_service on :3041 (a copy of the
preview config) and 2f's stream_service :8093, with the devtest display page as the glass at 1280×720 and this page
at 390×844: go live from "More" with the picker and 720p30, cue another scene from the picker, add an effect,
switch Auto on, pick a palette, open "Picking, the feel and fades", back to art; no page errors, no horizontal overflow.

Static checks:

```bash
npx tsc --noEmit -p tsconfig.app.json   # no errors in src/pages/Room or src/App.tsx
npx eslint src/pages/Room src/App.tsx
```

## Used by the home page (screen-machine-b5, 14 Oct)

The app's home page (`src/components/room/`, HeaderSection, ImageDisplay, BucketGridView) imports from this folder.
Keep these exports and signatures stable, or update the home page with them:
`api.ts` (`defaultRoomUrl`, `RoomApi(base, "")`, `explainError`, `storageGet`, `storageSet`), `bits` (`Spinner`),
`LivePanel` (`LookControls`, `SceneButton`, `ScenePicker`), `liveLib` (`sceneInfo`), `theme` (`RoomThemeStyle`,
`THEME_CLASS`), `useLiveLook`, and `types`.
