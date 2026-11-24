# Film mode: proposal

Status: **proposal for the owner's review** (23 Nov 2026). Only "Stage 1: slots" below is being built now; everything
else waits for a go. Research notes and links are at the end; nothing here has been tested on our own endpoints unless
it says so.

## What it is for

A way for someone to tell a short story as a film (about 30 seconds, six or so shots) and cast it with their actual
friends, stuffed toys, guinea pigs, or whatever they photograph. It is made at a desk, shared from the person's own
device (where there is sound), and can be put on a screen (silent). It is not a movie studio.

The owner's rulings (23 Nov):

1. **The story is the hard part.** "No production values will fix a bad story." The tool's job is to enable the story
   pathway; video generation is the last step, not the point.
2. **Follow the film process:** start with a story, produce a storyboard, generate stills, connect them in video.
3. **The bar:** easy enough that a motivated 10-year-old can do it.
4. **Work on a rough cut in near-real time** (rough stills, motion, sound) to test whether the story is told: beats,
   pacing. Casting, locations and art direction are changed afterwards without touching the story.
5. **Rough cut first, final render on request,** and the final must be the approved shot finished, not drawn again.
6. **Three needs:** casting consistency, voice consistency, and rough-cut storyboarding and story-assembly tools.
7. **Reuse open-source libraries** where they fit.
8. **Who:** probably signed-in people only, for cost. Any Google account signs in as a guest, so the real limit is the
   listed accounts (approved and admin): one rule in `config/auth/gates.yaml`.
9. **Workers:** three video workers so segments render side by side. The account has 10; Krea and Qwen sit at 3 each
   with no measured reason, so two come from them.

## The pathway

One page, four tabs. Every tab arrives already filled in; the person only changes what they don't like. Going back
never loses work: a changed card marks only its own picture and shot as out of date.

### 1. Story

- The person types or says an idea.
- The app asks specific questions, one at a time, in four stages: Start, Journey, Climax, End ("What happens next?
  Why?"). A study of a children's story tool found questions like these drew story contributions about 90-100% of the
  time, against 37% for open "anything to add?" prompts (Tinker Tales; 10 children aged 6-8, so a small study).
- The result is a story spine of five to seven sentences ("Once upon a time... Every day... Until one day... Because of
  that... Until finally..."), each a beat card. The child's own words are kept visibly.
- Characters and places are named here as **roles**, with stand-in looks. Nothing is cast yet.

### 2. Reel (the storyboard)

The studio pattern is the story reel, or animatic: rough panels with a length each, scratch voices and temp music,
played through. The main button is **Watch it**.

- Each beat becomes one or more **shot cards**: a pencil-sketch picture, one line of what happens, an optional spoken
  line, a length.
- **Rough on purpose.** Sketches arrive in seconds, read as a plan, and stop anyone polishing early.
- **Voices:** the person records each line themselves (the studio's scratch dialogue), or a text-to-speech voice per
  role speaks it. A spoken line sets the minimum length of its shot.
- **Motion** is done in the browser: a slow drift on each picture, and a cut or a dissolve at each join. No video model
  is in this loop, so it plays at once.
- **Pacing edits are the core verbs:** split a beat, merge two, hold longer, cut sooner, reorder, change a line.
- **Versions:** any reel can be saved and compared.
- **Notes, not fixes:** an optional "story friend" watches the reel and asks two or three questions ("Why does Max want
  the map?", "Nothing goes wrong. What could?"). The rough cut can be shared as a file for a real audience.

### 3. Look (cast, places, style)

- **A cast sheet per role:** one to three photos, a line of what never changes, a line of what can (clothes, props),
  and a voice recording.
- **Places** and **art direction** (photograph, watercolour, clay, comic) are chosen the same way.
- Each sketch is redrawn as a real still from the cast photos and the sketch. A still is fixed by saying what is wrong
  ("make it night", "Max should be bigger"). Shot size and angle are chips (wide, medium, close; eye level, low, high).
- Every still is drawn from the cast sheet, never from the previous still: chained edits build up grain by the third or
  fourth hop.
- Changing the cast, a place or the style redraws pictures. It never edits beats, lines or timing.

### 4. Film

- **One track.** Stills sit along it as thumbnails; each still owns the segment that starts at it.
- **Length:** drag a segment's edge, snapping to the lengths the model allows.
- **Cut or Move** on each join. A move draws as one continuous bar into the next still (first-to-last video); a cut
  draws as a break (video from that still alone, then a hard cut). No product found offers this as a switch (Pika makes
  every join a move, Kling every join a cut), but it maps onto the two modes we have.
- **Camera:** on a cut, one chip (still, push in, pull back, pan, rise, orbit, handheld). On a move the two stills
  already fix the camera, so the camera sentence is written from them and can be edited.
- **Draft, then final:**
  1. Draft shots are drawn small with their sound, each its own job, side by side on three workers. Any one can be
     redone; every take is kept.
  2. On approval, a video upscaler enlarges the same frames and they are frame-interpolated, with the draft's sound
     untouched. A seed only repeats a result at the same size and length, and the open H3 release has no refine
     stage, so re-rendering at a higher size would be a different shot.
- **Finish:** join, optional music bed under the speech, then Share (the device's share sheet), Download, Save to
  Generated, Publish to a screen.

## The film file

Two layers, so the look can change without touching the story.

- **Story layer:** beats; shots (what happens, spoken line and who says it, length, cut or move, camera); order;
  versions.
- **Look layer:** cast (photos, fixed and changeable features, voice), places, style; per shot, its sketch, its still,
  its video takes and which take is chosen.

One JSON file per film in the person's own space. ViMax's planner schema (MIT) is the model to borrow: characters
with `static_features` and `dynamic_features`, shots with first-frame and last-frame descriptions, motion and audio.
OpenTimelineIO is an interchange format; export to it if ever wanted, don't use it as the project file.

## Models and workflows

| Need | What we have | What would be new |
|---|---|---|
| Sketch and still pictures | Qwen-Image 2.1 Turbo, Z-Image, Krea 2; Qwen edit and combine | A sketch style and a "from the cast sheet" prompt; perhaps an angle-change edit (a Multiple-Angles LoRA exists for another Qwen edit version; untested on ours) |
| Move between two stills | `h3-fl2v.json` (MiniMax H3, first-to-last), slowed for the TVs | The same at real speed with a chosen length: `h3-film.json` (Stage 1) |
| Sound with the picture | H3 draws it; the live graphs discard it (two nodes, in the 19 Nov lab graph) | Keep it in film segments |
| Cast and voice in video | Nothing | H3's **Ref2VA** checkpoint (see below) |
| Finish a draft | RealESRGAN x2 and RIFE on the drawn frames | A video upscaler pass: FlashVSR (about 20 s for a 5 s clip, licence not found) or SeedVR2 (about 2.5 min, Apache-2.0, rated most accurate) |
| Scratch voices | Nothing | The browser's recorder; Pocket TTS (MIT, clones a voice) or Kokoro (Apache-2.0) on the CPU |
| Music bed | Nothing | ACE-Step 1.5 (MIT, under 4 GB), in the video bundle |
| Join | ffmpeg on the server | A join step (Stage 2) |

### H3 Ref2VA: the main lead

The open H3 release has a second checkpoint, reference to video and audio, that we do not run. Its guide describes
our three needs:

- subjects are "people, animals, or objects" built from photos (the example makes a pet from three);
- a short recording is bound to a subject as its voice;
- a supplied audio track can be reused as the shot's sound, so a child's recorded line can be the real line;
- a picture can be the shot's first, key or last frame.

How well it holds is not documented. One blogger's timings on a hobby GPU: identity and pose drift at 4 steps, and
voice is weakest under about 12 steps, so it will be slower than our 8-step graph. The published file is
`int8_convrot`, the format we avoided on 19 Nov because the worker's torch is cu128; that needs checking first.

## What is solved, and what is not

| | Status |
|---|---|
| One person recognisable across stills | Mostly solved (vendor claims) |
| Toys and pets across stills and video | Half: models accept them; no evidence on how well |
| Two or three cast in one shot | Half: works, but physical contact breaks it |
| Identity through a clip | Half: drifts at low step counts |
| A voice per character across shots | Half: the inputs exist; reliability unknown |
| Several speakers in one shot | Unsolved: the wrong character speaks |
| Eyelines, props, wardrobe continuity | Unsolved |
| The pause at each still in first-to-last video | Known flaw: the picture matches at the join, the motion does not |
| Children's voices, talking toys | Unknown |

Designing around the gaps:

1. People record their own lines, so a character's voice is the same by construction.
2. One speaker per shot by default (ordinary film grammar).
3. Toys and pets speak off-screen or through a narrator by default.
4. Cuts carry scene changes; moves are for one continuous action. The slow tail of a move is trimmed at the join.
5. The planner avoids physical contact between cast members.

The face-identity add-ons used for people (PuLID, InstantID, InfiniteYou) are built on human face detection and are
no use for a toy or a guinea pig.

## Tests before each stage commits

1. **Cast (make-or-break for stage 4):** Ref2VA with photos of a child, a plush toy and a guinea pig, six to ten shots,
   two or three in frame, a child's recorded voice.
2. **Sketch to still:** does a still drawn from a sketch plus cast photos keep the sketch's composition? If not, the
   reel uses real stills from the start.
3. **The pause:** three stills pairwise, with and without trimmed tails; and three stills in one pass.
4. **Draft size:** H3's time against size and length on our GPUs; the smallest size at which faces survive; the
   shortest clip it will draw (its docs say 4-15 s; 2.3 s and 3.0 s drew fine on 23 Nov).
5. **Finish:** FlashVSR and SeedVR2 on a small draft, against today's full-size draw plus RealESRGAN.
6. **Speech:** how good H3's own speech is, and plain-language camera wording.

## Stages

### Stage 1: slots (built 23 Nov)

A film specialisation of the generator's top section: the plain A > B > C rendering pipeline, without the story tools.
The top section has a **Create / Film** switch (Create is the prompt form, and the default; the choice is kept per
browser).

- A row of slots, two to start with, more with the + at the end; the row scrolls sideways when it outgrows the form.
  A picture goes into a slot by:
  - dragging it from Generated or a screen's pictures (a clip gives its first frame);
  - dropping files on it;
  - the slot's own menu, which offers what the form's Upload offers (this computer, the camera on a phone, Famous
    artworks); several pictures chosen at once fill the slots from there on;
  - the toolbar's Upload, or "Use as prompt" on any picture while Film is open: the next empty slot.
- Between each pair of slots: what happens (the person narrates the transition) and a slider for how many seconds
  (2 to 10: one pass of the model).
- The form's own toolbar and button, as in Create: Upload, the prompt helper (the film's own, or none), Advanced, and
  the generate button with about how long the film takes under it. No workflow or screen to choose.
- The button asks for one video per pair of filled neighbours, from the first picture to the second, at real speed
  with sound. The videos are ordinary jobs, made side by side (three video workers), and land in Generated as one
  batch in slot order. Each link shows its video's progress.
- Every video is drawn in the film's shape: the one chosen in Advanced, else its first picture's. A picture of another
  shape is fitted as a picture is fitted to a screen (the AI plans a crop and an extension; Advanced can say crop or
  extend).
- The links, the settings and the app's own pictures are kept across a reload; a file from the device is not.
- No explaining text on the page (owner: "text = bad design"): letters on the slots, a placeholder in each box, icons.
- The two natural uses:
  1. photograph something before, during and after; put the photos in A, B, C; describe each transition;
  2. generate a picture (or start from a photo), adapt it from prompts, put the versions in the slots, and have the
     transitions drawn.

How it is made:

| Piece | Where |
|---|---|
| The slots, links and time estimate (pure, tested) | `src/components/film/film.ts`, `film.test.ts` |
| The film held by the form, and the requests that make it | `src/components/film/useFilm.ts` |
| The strip | `src/components/film/FilmStrip.tsx` |
| The switch, the toolbar and Advanced in Film, "Use as prompt" into the film | `src/components/prompt-form/PromptForm.tsx`, `PromptFormToolbar.tsx` |
| A picture dropped on a slot | `src/pages/Index.tsx` (`DROP_ZONES.FILM_SLOT_PREFIX`) |
| The segment's graph | `routes/workflows/h3-film.json` (`h3-fl2v` at real speed, with H3's sound; catalogue entry hidden) |
| Its refiner | `routes/data/refiner-film.txt` (refiner `film`, hidden from the pickers) |
| Pictures in slot order, links and files mixed | `image_order` in the request; `routes/generate_api.py` `in_page_order` |
| The film's shape, both pictures fitted, one pass, the length | `video_aspect`, `video_shape`, `video_fit`, `video_seconds` in `routes/generate.py` |

What the model does with a length: H3 draws 17k + 5 frames (56 is 2.3 s, 73 is 3.0 s, 124 is 5.2 s, 243 is 10.1 s) and
rounds anything else up, so a segment asks for the count nearest its seconds. On a warm PRO 6000: 45 s to make 2.3 s
of video, 76 s for 3 s, about 100 s for 5 s.

Checks: `tests/integration/test_generate_roundtrip.py` (film segments), `node --experimental-strip-types --test
src/components/film/film.test.ts`, and on the live page `tools/ui_evals/film_strip.py` (`--make` makes a real film).

Added 24 Nov (owner's notes on the first version):

- **Image / Film** (was Create / Film).
- **On a phone** each slot is a small thumbnail beside the link that leaves it; tapping a picture opens it in the
  Loope.
- **Suggestion rows** under the strip, as under Image's prompt (Change, Camera, Sound;
  `src/data/example-prompts.json` `contextPrompts.film`): a chip adds its words to the link being written. On a phone
  each row is one line that scrolls sideways.
- **The prompt helper** (`refiner-film.txt`) is told each link's seconds and the Sound setting: it paces the action to
  the time, and with an empty link describes the most natural change from one picture to the next.
- **Advanced: Sound**: as the model draws it, no music (a line added after any rewrite), or silent (the graph's sound
  is taken out: `silence()` in `routes/generate.py`).
- **One film, published**: when the last segment lands the page asks for them joined (`POST /api/film/join`,
  `routes/film.py`: ffmpeg, slot order, each segment's sound or silence) into one clip kept in Generated with them, then
  publishes it to the screens chosen in the toolbar's Publish menu. A film of one link publishes that clip.
- **Start again** reads what every result keeps (its words as written, workflow, settings, prompt helper and pictures
  in order; `src/components/recent/recipe.ts`): a still comes back to Image, a segment or a joined film comes back to
  Film with its pictures, words and seconds, and from its batch the whole film. Each segment keeps its place in the film
  (`film_link`): the stored batch index is 0 for every one.
- Fitting a picture with a plain grey backdrop: its extension is no longer refused as "left grey"
  (`routes/reframe.py` `edge_grey`); it had fallen back to a crop of the face.

Known: two unrelated scenes joined by a move come out as a picture-in-picture card that zooms in (the model's way of
joining them). A cut is the answer; Cut / Move per join is next. The join runs from the page, so a page closed before
the last segment lands leaves the segments unjoined.

Not in this stage: cut or move, redoing one segment, a saved film.

### Stage 2: one film

Join the segments into one clip with its sound; redo one segment; cut or move per join; the track with draggable
lengths; a film kept as a file that can be reopened.

### Stage 3: story and reel

The Story and Reel tabs: questions, beat cards, sketch panels, recorded lines, the in-browser animatic, versions,
the story friend.

### Stage 4: cast and look

Cast sheets, places, style; stills from the cast sheet; Ref2VA for video if test 1 passes.

### Stage 5: draft and final, music

Small drafts and the finish pass if tests 4 and 5 say they pay; the music bed.

## Open-source pieces

| Piece | Licence | Use |
|---|---|---|
| react-timeline-editor | MIT | The track |
| wavesurfer.js | BSD-3 | Waveform of a recorded line |
| Mediabunny | MPL-2.0 | Reading and writing media in the browser, if the animatic needs more than the page's own audio and CSS |
| ViMax | MIT | Planner prompts and the cast and shot schemas |
| Pocket TTS, Kokoro | MIT, Apache-2.0 | Scratch voices on the CPU |
| ACE-Step 1.5 | MIT | Music bed |
| ai-toolkit | MIT | A LoRA per cast member, only if references alone don't hold |
| OpenCut, omniclip | MIT | Whole editors: to read, not to embed |

Restrictive licences, left alone unless the owner says otherwise: Remotion, Twick, Etro. Licence terms for H3 and the
foley models conflict between sources; that is with the owner's offline review.

## Cost and time, as known today

- A still: about 1-3p, seconds on a warm worker.
- A 5 s video segment at 1344x768: about 100 s and 5-10p on a PRO 6000 (19 Nov test).
- A six-shot film: five or six segments, about 30-60p; about 10 minutes one at a time, about 4 with three workers.
- The story and reel stages cost almost nothing, which is the reason for the order.

## Sources

Prior art and patterns:
- Pikaframes (keyframes in a row, a prompt and 1-10 s per transition): https://pika.art/faq
- Reigh worker (one task per segment, then stitch): https://github.com/banodoco/reigh-worker
- Hypernatural, storyboard then timeline: https://intercom.help/hypernatural/en/articles/15997754-storyboard-vs-timeline
- Higgsfield Popcorn: https://higgsfield.ai/creator-hub/help-center/ai-models/how-do-i-use-popcorn
- Kling 3.0 multi-shot: https://kling.ai/blog/kling-video-3-multi-shot-guide
- ViMax: https://github.com/HKUDS/ViMax
- Tinker Tales: https://arxiv.org/html/2602.04109v1
- The pause at each join: https://hackernoon.com/how-to-chain-ai-video-clips-into-one-shot-without-the-hidden-stutter-at-every-cut
- Draft mode economics: https://hackernoon.com/ai-video-draft-mode-doesnt-make-renders-cheaper-it-makes-mistakes-cheaper

MiniMax H3:
- Reference guide (Ref2VA): https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md
- Comfy prompt guide: https://docs.comfy.org/tutorials/video/minimax/minimax-h3-prompt-guide
- Local deploy (sizes, lengths): https://platform.minimax.io/docs/guides/local-deploy-h3
- Community thread on sizes and speed: https://huggingface.co/Comfy-Org/MiniMax-H3/discussions/30

Other models and libraries:
- Video upscalers: https://docs.comfy.org/tutorials/utility/video-upscale
- Multiple-Angles LoRA: https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA
- ACE-Step 1.5: https://github.com/ACE-Step/ACE-Step-1.5
- react-timeline-editor: https://github.com/xzdarcy/react-timeline-editor
- wavesurfer.js: https://github.com/katspaugh/wavesurfer.js
- Mediabunny: https://github.com/Vanilagy/mediabunny
- Pocket TTS: https://github.com/kyutai-labs/pocket-tts · Kokoro: https://github.com/hexgrad/kokoro

Not found by the research: written breakdowns from working AI film-makers, any evidence on toys or pets holding
across shots, any public data on what casual users finish. The studio story practice (story spine, story reel,
scratch voices, notes) is from general knowledge of how animation story teams work, not from a fresh source.
