# Karaoke

Ask for a song by name or link. The machine downloads it, removes the lead vocal, fetches the lyrics, and the screen's
display page plays the backing track with the lyrics over whatever the screen shows (art or live). Built 25 Nov 2026
for the laptop rig (owner: the birthday karaoke on 5 Dec). Capability `karaoke`; on for `laptop` and `finsbury` (PC22, 27 Nov: its Karaoke mode).

## Using it

- **The panel:** `http://<laptop>:5000/karaoke` (over Tailscale, or the same Wi-Fi). Under the queue, one search
  finds songs in the library and on the web as you type (spelling forgiven: "hapy farell" finds Pharrell's Happy), or
  takes a pasted YouTube or Spotify link. **Add** puts a song at the end of the queue (singers pick a song and wait
  their turn); **Up next** jumps the queue; **Library only** gets it ready for later. An artist's name anywhere
  opens their songs, most popular first. Each song shows its progress: Getting the song, Finding the lyrics,
  Removing the lead vocal, Ready (about a minute). The queue is the singing order; songs already sung are tucked
  away under a line at its top. **Clear** empties the queue (the song playing stays). With nothing playing
  and nothing waiting, a song added plays at once (or as soon as it's ready). The library sorts most popular first (or by title, artist, year, when added) and
  filters by tag (genre, decade). Now playing has Pause/Resume, Restart, Next, Stop, the singer's voice (the lead
  vocal under the backing: Off, Quiet, Full), a nudge for the lyrics' timing, and "Lyrics off? Time them
  carefully" (the careful pass, below; also the wand on a library row).
- **Where it plays** (Settings → Screen, Sound): the karaoke screen is any screen that can go live (unset: the first in
  the deployment's default room). Only its display page shows the lyrics; every other screen's page leaves karaoke
  alone. Sound: from that screen's own page (its browser: speakers, HDMI, Bluetooth to a speaker), none, or a Sonos
  room (where the installation has them: the Music page's Play on Sonos rooms). On a Sonos room the Sonos plays a mix
  of the backing track and the guide vocal (child mode's mutes in it) through exactly the Music page's path
  (`routes/karaoke/sonos_out.py` -> piano_render/sonos.py SonosOutput -> room_service; the file served by show_service
  through room_service's LAN door), and the player's clock follows what the room hears (the Sonos's start delay from
  calibration/latency.json, then its own position as it plays). Changing the voice level or child mode mid-song mixes
  again from where the song is. Calibrate is for the screen's own sound only. The picture card on the panel follows
  the same screen.
- **The live picture follows the player** (`routes/karaoke/live_view.py`): a song starting from stopped puts the
  karaoke screen live (the art director picking its scenes), the panel's Stop puts it back to art. The queue running
  out leaves it live (the next singer is choosing); pause, song changes and a Go live / Back to art by hand in between
  are left alone.
- **The output window:** any browser window on `http://localhost:5000/display/vj` plays the backing track and shows
  the lyrics over the screen's art. It takes no input. Any number can be open, or none: the server runs the player.
  Browsers only let a page make sound once it has been clicked or the site is allowed to autoplay, so either click the
  output window once after opening it, allow autoplay for `http://localhost:5000` in the browser's site settings, or
  open it with `tools/karaoke/windows/karaoke-screen.cmd` (Chrome with sound allowed).
- **Speaker delay** (Settings): the whole delay from the player to the room (Bluetooth, a Sonos's buffering); the
  lyrics wait that long. For the lyrics only: the picture and lights follow the live mic's own beat clock.
  **Calibrate** measures it (`routes/karaoke/calibrate.py`): with a song playing it listens to the song for 10 s and
  matches it against the backing track (no pause, nothing extra plays: the best way, the speaker as it is while songs
  play); with nothing playing, every output window plays ten different chirps over a quiet noise bed and the mic times
  each on its own (up to 3 s). An output window opened before a change to the page needs reloading (F5) to chirp.
- **Child mode** (Settings): obvious profanity (`routes/karaoke/profanity.py`: the strong swear words and slurs and
  their forms; damn, hell and crap stay) is served as dashes by the lyrics API itself, so the output window and the
  lyric effects alike get clean words ("you're so ------- special"); the playing song's title and artist too. The
  lead vocal is muted over each such word (its word timing, and anything Whisper heard, padded 80 ms). The
  instrumental isn't touched (backing vocals in it can still carry a word).
- **Each song's start and end:** an intro card (cover, title, artist, writers from MusicBrainz, album and year) over
  a scene fading up, and the song named in the corner as the scene fades out at the end. A song that starts singing
  within 8 s gets silence before its music (`lead_in_s`: the player starts it at a negative position), so its title
  is up at least 8 s before the first line: time for singers to change over.
- **The room mic** (Settings → Room mic and levels, rolled up): it comes on by itself while a song plays (and during a
  calibration), so the art director and the lights hear the room. On the laptop the audio agent runs with `--raw`
  (WASAPI exclusive): Windows' own echo cancelling and noise suppression otherwise removed the speakers' sound and
  key taps from the mic (25 Nov: shared mode -89 dBFS with a song playing; exclusive -55 dBFS with the song in it).

## How it works

| Piece | Where |
|---|---|
| API, library, queue, player state | `routes/karaoke/` (`api.py`, `store.py`); state in `~/.local/state/screen-machine/karaoke/` |
| Worker (one song at a time) | `routes/karaoke/worker.py`: fetching → lyrics → separating → ready |
| Finding songs | `routes/karaoke/sources.py`: `catalog_search` (iTunes + Deezer at once, YouTube's guesses put the song meant first and correct spelling), `artist_top` (Deezer's top tracks), `song_facts` (genre, year, popularity, cover); LRCLIB lyrics; Spotify track page; yt-dlp |
| The art director | `show_service/karaoke_feed.py`: the karaoke player is a music source like the Music page's (its song, position and length in `ctx.now_playing`): each song's end is armed and fires the director's transition, and a new song gets its own treatment |
| Speaker delay | `routes/karaoke/calibrate.py`: chirps from the output windows, timed by show_service's live mic samples (WS `/v1/levels?wave`) |
| Lyrics and alignment | `routes/karaoke/lyrics.py`: LRC → lines; `best_fit`: the upload that fits what is sung, timed to it |
| Hearing the vocal | `tools/karaoke/transcribe.py` (Whisper turbo on the lead vocal: every sung word and when) |
| Word timing | `tools/karaoke/align.py` (MMS forced alignment, each line's words within the line) |
| The careful pass | `tools/karaoke/refine.py` (every voice out, heard, the whole lyrics aligned at once); `worker.refine` |
| Sync eval | `tools/karaoke/evals/sync_eval.py`: a headless display page on a fake clock; sound vs lyrics, and (with a Gemini key) the model's verdict on 10 s clips |
| Vocal removal | `tools/karaoke/separate.py`, run in the karaoke venv (never in Flask) |
| Display layer | `src/pages/DisplayPage/karaoke/KaraokeLayer.tsx`: Web Audio (backing + vocal at the guide gain), lyrics |
| Control panel | `src/pages/Karaoke/index.tsx` (`fuzzy.ts`: the library's spelling-forgiving match; `RoomMic.tsx`), `src/shared/api/karaoke.ts` |
| Artist pictures | `routes/karaoke/pictures.py`: each song's artists' pictures, found, vetted and kept per artist (below); `src/pages/Karaoke/SongPictures.tsx` hides one |

- **The recording:** LRCLIB's artist, title and length choose the YouTube recording (closest length, no covers,
  karaoke, live or sped-up versions), so the lyrics' timings fit. Spotify audio is DRM'd: a Spotify link gives the
  title and artist and the audio comes from YouTube. A YouTube link is used as is.
- **The vocal:** `mel_band_roformer_karaoke_aufr33_viperx` removes the lead and keeps the backing vocals. Overlap 2 with
  fp16: 31 s for a 3:51 song with the 4K live render running, and the render dropped no frames (25 Nov). Don't run it
  on the CPU: that slowed the render (205 late frames).
- **The lyrics** (25 Nov: Happy's lyrics had nothing to do with its sound). LRCLIB is people's uploads, a dozen
  for a big song, for different edits, and some plain wrong, so none is trusted as it comes: Whisper hears the
  separated lead vocal (`sung.json` beside the song), every upload's words are matched to it in order (the longest
  common run of words, so each chorus goes to its own repeat), the one matching the most sung words wins (at least
  35% of them), and each line starts when it is sung; lines nobody was heard singing go between their neighbours.
  None fits: the lines are what Whisper heard. Then each word is timed and the line starts at its first word.
  Retry redoes a song through all of it. Lines nobody was heard singing keep the upload's own timing, moved by the
  shift of the heard lines round them (past the last heard line, the median shift of the last five), and a heard
  line far from its neighbours' shift (over 4 s) is a stray match and is placed the same way (27 Nov: Hey Jude's
  four-minute coda is the choir in the backing track, so the lead stem had nothing heard there and the even spread
  squeezed it into half its length). A line the upload's timing puts past the recording's end is left out, not
  piled up at the end. Of the uploads matching at least 97% as many sung words as the best, the one with the
  fewest words nobody sings wins (27 Nov: I Will Survive's album-version upload matched 403 sung words and had 232
  more nobody sings; the single edit's matched 398 with 33). Credit lines ("Written by：...") and the song's own
  name as a line are never lyrics.
- **Lyrics off? The careful pass** (owner, 27 Nov: "a way to refer a song for 2ndary processing -- e.g. more
  carefully time lyrics"). The wand on a library row, or "Lyrics off? Time them carefully" under the playing song,
  refers it (`POST /api/karaoke/songs/<id>/refine`). It first chooses the upload again (LRCLIB's uploads against
  what was heard, as above), so a song prepared before a better rule gets it. The worker runs it when no song is waiting to be prepared (a
  song added meanwhile ends it and it waits again); the song stays playable throughout. `tools/karaoke/refine.py`
  sums the two stems back into the song, takes EVERY voice out (the BS-Roformer all-voices model: choir, backing
  and lead), has Whisper hear that, and force-aligns the whole lyrics to it in one CTC path (MMS_FA, a star before
  each line for what isn't in the lyrics), so every line is placed in order against the others. It replaces the
  lyrics only if, of the lines with real words in them (not "na na na", "yeah"), at least 60% start within 2 s of
  where they were heard (`lyrics.agreement`); else the song says "didn't fit the singing better" and keeps its
  lyrics. The display pages and the renderer's lyric feed take the new timing at once, mid-song too (the song's
  `lyrics_rev`); the speaker-delay nudge for the song goes back to 0. Undo puts the first pass's back
  (`lyrics.first.json`). About 7 min for a 7-minute song with the live render running. Hey Jude, 27 Nov: all 68
  lines placed, 27 of 29 heard lines agree (median 1 s); the coda runs to 7:00, mostly 1-4 s from LRCLIB's upload
  (which isn't exact either), where the first pass had it squeezed into 3:04-5:52.
- **The player:** one clock on the server (`store.py`): a song, a state, and its position at a server time. Play,
  pause, next and the end of a song (checked twice a second against the song's exact length) all happen there. Each
  display page polls `/api/karaoke/now` and places the sound at the server's position whenever the player moves (a
  new rev: play, seek, next); in between the sound runs on the browser's own clock, re-placed only after a stall of
  more than 1 s. The lyrics follow the sound itself. (25 Nov: chasing the server's clock jumped the audio every few
  seconds: the laptop's WSL clock ran 7.6% slow, the PC22 fault of 11 Oct, fixed the same way:
  docs/KIOSK_INFRASTRUCTURE_NOTES.md §6. `python3 scripts/wsl_clock_check.py` checks it.)

- **The pictures** (owner, 26 Nov: the photo scenes, "publicly-available-from-the-web images of the singer and/or
  band ... cached for any taylor swift track"). A thread (`karaoke-pictures`) works through the playing song's
  artists, then the queue's, then the library's, one artist at a time, and keeps each artist's pictures in
  `~/.local/state/screen-machine/karaoke/pictures/<artist>/` (never a screen's bucket, never in git):
  - from Deezer (the artist's photo), Wikimedia Commons (freely licensed photos: them in concert / live /
    performing first, in the song's year first, so a 1985 song can show 1985) and fanart.tv when `FANARTTV_API_KEY`
    is in `.env` (its free personal key: screen-shaped backgrounds). By catalogue identity, never a loose web image
    search. People only (owner, 26 Nov: "album cover art isn't a very charismatic thing to show ... we need artists
    performing"): performing and the band, at most 5 posed portraits; no covers.
  - Each picture gets one Gemini look (`song_picture` in config/llm.yaml, about $0.001): is it really this artist,
    what kind (performing, the band, a portrait), is it good, how ENGAGING is it on a big screen (appeal: a striking
    moment on stage high, a distant or ordinary snapshot low; under 0.35 not kept), is it fine for a children's
    party (strict: no alcohol as the subject, no gestures, no near-nudity), where are the heads, what era, which
    moods. One that fails is deleted and remembered. Without the AI, only Deezer's own artist photo.
  - Commons serves only its standard thumbnail widths to us without a 10-minute 429, and only slowly: one download
    every 12 s, so an artist's Commons photos arrive over a few minutes; a 429 pauses Commons and the artist is
    finished later.
  - `GET /api/karaoke/songs/<id>/pictures` (the song's artists merged, its era first), `GET
    /api/karaoke/pictures/<artist>/<file>`, `POST .../hide {hidden}`.
  - The renderer reads the playing song's list (`stream_service/lyric_feed.py`) and the photo scenes
    (`song-photos-*` in config/live/scenes.yaml: sketchbook, video wall, cube wall, globe, landscape, echo, pop
    art, framed) only play while it has at least 4 ENGAGING ones (appeal 0.6 and up); then about 40% of the looks
    involve the artist with 10 or more of them (owner: "around 30-50% ... providing the pics are ok"), fewer with a
    handful; verse looks in verses, chorus looks in choruses; the most engaging pictures most often.

- **Video clips** (owner, 26 Nov: "live youtube feed would be fun ... only moving surfaces ... slowed / styled"):
  after an artist's photos, three 30 s silent 720p clips from their concert films on YouTube (yt-dlp fetches only
  that section, ~40 s for all three, in parallel), each checked by three of its frames (the artist on stage, fit
  for children) and its baked-in black bars measured (ffmpeg cropdetect), kept in `<artist>/clips/`. The renderer's
  `song-video-*` looks (`stream_service/plugins/sources/song_video.py`: slowed, frames blended, drifting, with an
  effect on top) play only with the song's clips, and share the artist's share with the photo looks.

- **Duets** (owner, 28 Nov: "detect who sings which line, and show it on the karaoke screen"; routes/karaoke/duet.py,
  the worker's `karaoke-duets` thread). Every ready song once, and again when its lyrics' words change:
  - The AI (`duet` in config/llm.yaml, about $0.007 a song) says whether the recording is a duet, its two voices
    (names; man, woman or child) and each line's part: A (sings first), B, both, backing. A lyric line's own tag
    ("[Elton:]", "Him:", "(Both)") wins over it. It knows the famous duets but gets lines wrong (28 Nov, Ain't No
    Mountain High Enough: a third of them), so it is a prior, never the truth.
  - Only a song it calls a duet is listened to: `tools/karaoke/voices.py` (karaoke venv, CPU, nice 19, ~20-40 s)
    gives each line of the lead stem a speaker embedding (WeSpeaker ResNet34, 26 MB, fetched once into
    `~/.cache/screen-machine/karaoke-models/speaker/`) and a median pitch. Pitch alone fails: Gaga sings Shallow's
    second verse in Cooper's register; the embedding still tells them apart (~0.5 alike within a verse, ~0.15
    across).
  - `decide`: the lines not sung together split into two sounds, starting from the AI's A and B and letting the
    sound move the lines that sound like the other voice (from the two least alike lines instead, a split could
    latch onto something that wasn't the singers: Breaking Free, You're The One That I Want). A is whoever sings
    first; the names follow the pitch where the AI's man sings 5+ semitones above its woman (Endless Love).
    It is a duet only when the two sound clearly apart, each voice is confirmed (the AI and the sound agree) on 3+
    lines, and the AI's solo lines agree with the sound 60%+ of the time. A line is marked when the two agree, or
    when the sound is clear on its own (margin 0.3+, and only on a line with real words: a wail doesn't sound like
    its singer). Both and backing keep the AI's word. A wrong duet display is worse than none: anything unsure shows
    as before. The sound can't tell one voice from two by itself: one singer's verses and choruses (or a choir
    coda) split as far apart as two singers (28 Nov: Hey Jude 0.38, Ain't No Mountain 0.41), so whether a song IS
    a duet is the AI's call (13 of 13 non-duets right on 28 Nov); the sound decides its lines.
  - Two answers: `duet` (it is one: the AI's word, which tags it) and `shown` (its parts are sure enough for the
    screen). 28 Nov, Somethin' Stupid: sung together nearly throughout, so tagged and shown as one voice.
  - The sound of each line is kept (`voices.json`), so a better `decide` judges a song again without asking or
    listening (`duet.redecide`).
  - Kept as `duet.json` beside the song (for the lyrics it was made from); `GET .../lyrics` puts each line's `voice`
    on (and `voices` per word where its brackets are another part's) and `duet: {voices}`. The song's `duet` field
    is what was found; `duet_set` (`POST /api/karaoke/songs/<id>/duet {duet: true | false | null}`, the Karaoke
    page's people button) is a person's word, and wins. Either way the library tags it "Duets".
  - The screen, for a duet only: A's lines on the left, B's on the right, both centred; each part its own colour
    (blue, pink, violet for both), sung as that colour at full brightness. With two or more singers named on the
    queue entry, the name of whoever is up shows over the line where the part changes (the first named takes A).

## The karaoke venv (per machine)

```
python3 -m venv ~/.venvs/sm-karaoke
~/.venvs/sm-karaoke/bin/pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu128
~/.venvs/sm-karaoke/bin/pip install "audio-separator[gpu]" audioread "yt-dlp[default]" openai-whisper
~/.venvs/sm-karaoke/bin/pip install --force-reinstall --no-deps torchvision --index-url https://download.pytorch.org/whl/cu128
```

torchvision must come from the cu128 index (PyPI's breaks onnx2torch). The deployment names the venv and yt-dlp's JS
runtime in `deployment.yaml` `host.karaoke_python` and `host.karaoke_js_runtime`. Models are cached in
`~/.cache/screen-machine/karaoke-models/` (the first song downloads it). Keep yt-dlp current
(`pip install -U yt-dlp`): YouTube changes break old versions.

## Not yet

Spotify albums and playlists. Backing-vocal words in a line ("(Because I'm happy)") are timed against the lead
vocal, which doesn't sing them.
