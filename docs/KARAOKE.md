# Karaoke

Ask for a song by name or link. The machine downloads it, removes the lead vocal, fetches the lyrics, and the screen's
display page plays the backing track with the lyrics over whatever the screen shows (art or live). Built 25 Nov 2026
for the laptop rig (owner: the birthday karaoke on 5 Dec). Capability `karaoke`; on for `laptop`, off elsewhere.

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
  vocal under the backing: Off, Quiet, Full) and a nudge for the lyrics' timing.
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
  Retry redoes a song through all of it.
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
  - from Deezer (the artist's photo, the song's album cover and up to 3 more of their albums' covers, one per album),
    Wikimedia Commons (freely licensed photos, searched by name and by the song's year, so a 1985 song can show
    1985 pictures) and fanart.tv when `FANARTTV_API_KEY` is in `.env` (its free personal key: screen-shaped
    backgrounds). By catalogue identity, never a loose web image search.
  - Each picture gets one Gemini look (`song_picture` in config/llm.yaml, about $0.001): is it really this artist or
    their album's cover, is it good, is it fine for a children's party (strict: no alcohol as the subject, no
    gestures, no near-nudity), where are the heads, what era, which moods. One that fails is deleted and
    remembered. Without the AI, only Deezer's own artist photo and covers it marks clean.
  - Commons serves only its standard thumbnail widths to us without a 10-minute 429, and only slowly: one download
    every 12 s, so an artist's Commons photos arrive over a few minutes; a 429 pauses Commons and the artist is
    finished later.
  - `GET /api/karaoke/songs/<id>/pictures` (the song's artists merged, its era first), `GET
    /api/karaoke/pictures/<artist>/<file>`, `POST .../hide {hidden}`.
  - The renderer reads the playing song's list (`stream_service/lyric_feed.py`) and the photo scenes
    (`song-photos-*` in config/live/scenes.yaml: sketchbook, video wall, cube wall, globe, landscape, echo, pop
    art, framed) only play while it has at least 4; then they get about a fifth to a third of the scene picks,
    the verse looks in verses and the chorus looks in choruses.

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
