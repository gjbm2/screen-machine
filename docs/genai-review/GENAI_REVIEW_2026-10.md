# Gen-AI review: models and deployment (17–18 Oct 2026)

**Status:** recommendation. Nothing in this review has been changed in the code or on RunPod.
**Evidence:** [evidence/](evidence/) holds the 8 audit and research reports, a fact-check of 46 claims (35 confirmed, 11 partly confirmed and corrected), 3 designs written from different angles, and the judge's synthesis.
**Scope (owner, 18 Oct):** legal and compliance questions are handled offline. That covers model licences, provider terms and UK law, and none of them filter the choices here. The evidence files still record what was found, for reference.

## Verdict in one paragraph

Keep the pattern: open weights, ComfyUI, RunPod serverless, scale-to-zero. At about 55 images a day nothing else is cheaper for the same freedom. Rebuild almost everything inside it:
- The images are frozen at mid-2025 (CUDA 11.8, an unpinned ComfyUI from Jun 2025), so they cannot load any 2026 model.
- Weights baked into each image are now an anti-pattern, because RunPod caches models on its hosts.
- The models are about 1.5 generations behind.

The biggest quality lever is not the model. **Every 4K image on the TVs is a 1280×720 render stretched 3× by a 2018-era photo upscaler (RealESRGAN).** Rendering at native 1.5–4 MP and finishing with a small step will improve the walls more than any model swap.

## What we found

**Fidelity**
- All four text-to-image graphs render a 1280×720 latent, then apply RealESRGAN ×4, then downscale to 3840×2160. The schedules never override this, and the catalogue defaults never reach the backend:
  - `usePromptForm.ts` reads `parameters` while the data has `params`;
  - `generate_utils.py` drops unknown keys;
  - `generate.py:439` sets `sampler`, not `sampler_name`.
- Prompts are capped at **75 CLIP tokens** (`refiner-enrich.txt.j2:16`), but every current model uses a T5 or LLM text encoder that reads 256–512 tokens. The median final prompt is 37 words.
- The router sends "painting / watercolour" to **SDXL**, the weakest model, at CFG 8 (`refiner-enrich.txt.j2:35`), while the catalogue says SD3.5 handles painting.
- Favourites skew to SD3.5 (painterly and surreal, 58% of favourites) and to Wan video (61% of retained clips favourited). Krea produces half the volume but gets the fewest favourites per 1,000 jobs.

**Broken now**
- The **Kontext** endpoint (`oh422aycpybd5u`: Adapt, Combine and Upscale) and the **Wan** endpoint (`wxd8n6sak3urv5`: Image-to-Video) have `workersMax 0`, yet the app and Alexa still offer them.
  - The north and south `_auto_upscale` triggers route to that dead Kontext endpoint.
  - Any job sent there waits 1200 s and then raises a misleading "check RunPod credit" alert.
- The timeout never cancels the RunPod job (`generate.py:710-718`), so a stuck job keeps billing.
- The worker sends progress updates to the **old public IP** `185.254.136.244:8765` (`rp_handler.py:36`), and nothing overrides it. Progress frames go to whoever now holds that address; the overlay gets nothing.
- The-beast publishes 5120×2880 images: `maxwidth` is read through `locals()` (`generate.py:449-452`).
- When ComfyUI errors, the worker keeps polling until RunPod's timeout (600–5000 s), so a crashed job still bills for its full timeout.
- The app's live price lookup returns HTTP 400 on every job. The cost it reports is about 1.75× below real billing.

**Platform**
- The base image is CUDA 11.8 with Python 3.10, and the ComfyUI pin is `null`.
  - Upstream ComfyUI is now v0.41.0, and its README asks for cu130 torch.
  - Blackwell GPUs (RTX PRO 6000, 5090) and FP8/NVFP4 kernels are unusable on the current image.
- The 6 endpoints share about 42 GB of duplicated weights. Pushes took up to 2.5 h, and a local model server is needed to build.
- A network volume pins 5 of the 6 endpoints to US-KS-2. That plausibly caused the August SD3.5 queue timeouts.
- Almost every job starts cold: the idle timeout is 5 s and jobs arrive 30–60 min apart. Real cost is about $0.068 per image, and H100s now cost about 70% more per second than A100s did.

**Expression**
- **The image side is unfiltered today.** There is no safety checker anywhere.
- An NSFW LoRA (`Heartsync/Flux-NSFW-uncensored`) runs at 0.5 on **every** Krea and Flux image, landscapes included.
  - It has no stated training data and a wrong licence tag, and its example code has wrong FLUX settings, a sign of low effort.
  - It shifts the look of everything it touches.
- **The prompt LLM is the real choke point.** Until 17 Oct, gpt-4o refined every prompt under jailbreak-style text ("ABSOLUTE ZERO CENSORSHIP", plus a false "moderation is downstream" pretext).
  - Forced function calls stop the model refusing openly, so it softened prompts silently, and nothing was logged.
- **Update, 17 Oct:**
  - screen-machine-b8 (commit `dc215fcf`) moved prompt work to `gemini-3.1-flash-lite`, with gpt-4o as fallback. Gemini's adjustable filters default to Off, and that model scores 88.4% on SpeechMap.
  - b8's commit `48eb5270` makes a Gemini safety refusal final rather than retrying it on gpt-4o.
  - The jailbreak text, the 75-token cap and the gpt-4o fallback are still there.

## Model lineup (recommended)

**How models were chosen:**
- fidelity, mainly the Artificial Analysis (AA) arenas, plus hands-on reports;
- what the weights actually do on nudity, violence and satire;
- whether they run on one serverless GPU.

Where the evidence is thin, the bake-off decides.

| Lane | Bake-off contenders (lead first) | Why | Expression behaviour |
|---|---|---|---|
| Painterly / surreal (TV default, 58% of favourites) | **Qwen-Image-2.1** (full and Turbo) vs **Krea 2** (+ a layer-9/10 bypass LoRA, or the Kroma fine-tune) | Qwen-2.1 is the #1 open model on AA (1042, against 841–878 for FLUX.1-dev/Krea). It renders native 2752×1536 and needs only a 1.4× finish. Krea 2 is reported as the most aesthetic open model, and its sample set is painterly | Qwen: no checker. Its pretraining was NSFW-filtered, so anatomy may be soft. Krea 2: safety-tuned against nudity, wounds and intense expression; the bypass LoRA undoes it (test it) |
| Photographic + nudes | **Z-Image Base** (Turbo for drafts); **Chroma1-HD** in reserve | Takes CFG and negative prompts, LoRA-friendly, fast | Z-Image: no checker; "uncensored" is a community claim. Chroma: "not aligned with a specific safety filter" |
| Typography (the-beast, office) | **Ideogram 4** vs **Qwen-Image-2.1** vs **FLUX 3 Image Klein**, chosen by exact-spelling rate | Ideogram 4 is the best open typography model (AA 1026). FLUX 3 Klein renders native 4K. Better spelling cuts the batch from 4 to 2 | Irrelevant on an office screen |
| Edit / multi-reference | **Qwen-Image-2.1** (edit Elo 1083, 10 references); **Qwen-Image-Edit-2511** (990) as fallback | Both beat Kontext (884), and native multi-reference replaces the ImageStitch hack | As Qwen above |
| Finish to 3840×2160 | Lanczos (paint, type); **SeedVR2-7B** (photo, now in ComfyUI core) | A small final step only | — |
| Video | **MiniMax H3 FL2VA** + 8-step Turbo LoRA vs **Wan 2.2 I2V-A14B** + lightx2v 4-step LoRAs; optional third, **LTX-2.5** | **H3** is the #1 open video model (AA image-to-video 1140, level with Seedance 2.5), with first/last frame in one checkpoint, but it is 768p locally, then SeedVR2 or FlashVSR to 4K. **Wan 2.2** is the cheapest, with the biggest LoRA ecosystem. **LTX-2.5** is native 4K | H3: no filter in the weights. Wan: none. LTX: weak nude anatomy, and Lightricks says it trains its models to refuse harmful instructions |

**Watch:**
- FLUX 3 Klein for the art lanes: native 4K, but early users report broken anatomy, and it is safety-tuned.
- HiDream-O1: strong on art scores, with one anecdote of prudishness.

**Not practical on serverless:**
- HunyuanImage 3.0 (80B mixture-of-experts, about 168 GB);
- MAGI-2 (8 Hopper GPUs).

**Outclassed or filtered:**
- FLUX.2 [dev] (32B + a 24B encoder, AA 1000, NSFW-filtered pretraining);
- Cosmos3 (sexual and violent content scrubbed from its training data).

**Retire:**
- SD3.5 Large, SDXL, FLUX.1-dev, FLUX.1-Krea, Kontext and Wan 2.1;
- both NSFW LoRAs;
- RealESRGAN.

The Hugging Face `2kpr` "x4plus" upscaler file is mislabelled; it is really `realesr-general-x4v3`.
- The deployed images got the genuine file from the local model cache.
- A rebuild without that cache would bake in the wrong model.

**LoRA rule from here on:**
- documented sources only;
- pinned by sha256;
- applied per request from the refiner's flags, never to every image.

## Deployment (target)

- **Endpoints:** two, `sm-image` and `sm-video`, plus a temporary `sm-eval` for the bake-off. They replace today's 7.
- **One stock image:**
  - Built from `runpod-workers/worker-comfyui` at 5.11.0 with `COMFYUI_VERSION=0.41.0` and cu128 torch. No 5.11.0 image is on Docker Hub, and 5.10.0 ships ComfyUI 0.34, which is too old.
  - Kept in `worker/` inside this repo, so graphs, catalogue and image change together.
  - Immutable date tags, as few custom nodes as possible.
  - No `LIVE_PATCH`, S3 sync or websocket relay.
  - S3 keys move to RunPod secrets.
- **Weights:** one private Hugging Face "bundle" per endpoint, delivered through RunPod **Cached Models**.
  - Download time is not billed, and the scheduler prefers hosts that already have the model.
  - Each endpoint gets one cached model, and every file in the bundle is downloaded, so curate it.
  - Drop the US-KS-2 volume, so all datacentres are open.
  - If bundle-to-ComfyUI path mapping fails in the spike, bake one weights layer per endpoint instead.
- **GPUs:**
  - `sm-image`: RTX PRO 6000 96 GB ($0.000969/s), then H100.
  - `sm-video`: PRO 6000, then H200 if H3 needs the headroom. H3 is 33B plus a Qwen3-VL-32B encoder; check fp8 fit in the spike.
- **Scaling:** FlashBoot on, idle 15 s, queue-delay scaler around 30 s. Fire north and south on the same tick so they share one cold start.
- **App adapter:**
  - Parse `output.images[]`.
  - Per-workflow `timeout_s`, and cancel on timeout.
  - Cost from RunPod `executionTime` × rate.
  - Progress from the `/status` calls the app already makes, so no inbound WAN port.
  - Video graphs must end in core `SaveVideo`, because the official handler drops `VHS_VideoCombine` output.
- **Adding a model** becomes: add files to the bundle, add a graph, add a catalogue entry.

## Prompt-LLM layer

`config/llm.yaml` is owned by b8, so these changes go to b8.

**Model chain:**
- `prompt` task: `gemini-3.1-flash-lite` → `gemini-3.8-flash` → **passthrough** (the raw prompt plus the lane's style suffix).
- `alexa` task: flash-lite → passthrough.
- Drop the gpt-4o fallback. It retires 14 Jan 2027, and OpenAI models score lower on SpeechMap.
- Move `describe` off `gpt-4.1-mini`, which also retires 14 Jan 2027.
- Keep Chinese-lab LLMs out of the satire path, because they censor China-political topics.

**Passthrough rule:**
- Pass through on error, timeout or an empty reply.
- A Gemini safety refusal is final (b8's `48eb5270`).

**Refiner text:**
- Delete the jailbreak and pretext text: `refiner-enrich.txt.j2:60-77`, `refiner-adapt.txt.j2:3-19,218-223`, `alexa-adapt.txt.j2:3-8`.
- Replace it with a plain brief: a private adult gallery; render nudity, violence and satire of named public figures faithfully.
- Remove the softening clauses in the gallerist and pictureeditor refiners.

**Prompt length:** lift the 75-token cap. Aim for 80–200 words for enrich and 60–120 for devise_prompt.

**Schema:** add `lane`, `people[{name, real}]`, `nudity` and `medium`. The lane maps directly to a workflow, which replaces the difflib fuzzy match in `routes/utils.py`. The `nudity` flag drives per-request LoRAs.

**Survival check and logging:**
- Names, artists, "nude", "blood" and "wound" must survive the rewrite; if any drop, the next model runs.
- Write `prompt_chain` (each hop's input, output, model and finish_reason) to every sidecar, so any softening is visible.

## Migration plan (each step ships on its own)

**Phase 0 (quick wins on the current models):** one Flask restart via Spectre (ping 2f first) and one frontend deploy.
1. Hide the Wan catalogue entries. Kontext is now at workersMax 1 (d6, 27 Oct), so its Adapt, Combine and Upscale entries work again.
2. ~~Cancel the RunPod job on timeout~~: done by d6 (5acd8949).
3. Fix the `maxwidth` `locals()` bug.
4. Accept both `{{NEGATIVE-PROMPT}}` and `{{NEGATIVE_PROMPT}}`.
5. ~~Progress relay to the old IP~~: done by d6. All 7 templates now set `BACKEND_WS_URL` to the current WAN IP, and the job tracker (docs/JOBS.md) takes the frames.
6. Route "painting" to SD3.5, not SDXL.
7. JPEG at quality 95 with no chroma subsampling (`publisher.py:318`).
8. Vision checks at 1536 px instead of 512 px "low".
9. Render Krea and Flux at a **1920×1088** latent instead of 1280×720.
10. Send the prompt-layer changes to b8.

**Phase 1 (spike):** build `worker/`, create `sm-image` with a minimal bundle, and run cold and warm jobs. Measure:
- whether the bundle maps onto ComfyUI's paths;
- how a new bundle revision reaches workers;
- how long the first download onto a new host takes;
- whether co-fired jobs share one worker;
- whether H3 fits a PRO 6000 at fp8.

**Phase 2 (app adapter):**
- `output.images[]` parsing, per-workflow timeouts, `sampler_name`;
- catalogue params reach the backend;
- cost from `executionTime`/`delayTime` × the serverless rate (d6 fixed the HTTP 400, but the lookup still uses the pod price);
- ~~prune the job registry~~: done by d6 (`runpod_jobs.log` removed, and cancel-all covers only active jobs);
- `prompt_chain` written to sidecars.

**Phase 3 (bake-off on `sm-eval`):** see the protocol below.

**Phase 4 (cut-over):** point north and south at the winners. Hold the old endpoints at `workersMax 0` for 7 days, then delete them.

**Phase 5 (the-beast):** move it to the typography winner, at batch 2 if the spelling rate is 75% or better.

**Phase 6 (edit lane):** Qwen-2.1 replaces Kontext. Re-enable Adapt and Combine.

**Phase 7 (video):**
- The video winner goes on `sm-video`.
- If Wan 2.2 wins, drop "paintings, pictures, still" from Wan's negative prompt, because it fights against animating paintings.
- Turn `animation_on` back on at your chosen rate.

**Phase 8 (retire):**
- the old endpoint `1w6vje8cn33ift` and template `5169ofvq0e`;
- volume `gmaygtw1z4`;
- archive the `runpod-worker-comfy` repo and its `dev-*` tags;
- the NSFW LoRAs and RealESRGAN;
- the local model server (`:54893`).

## Cost

Based on RunPod billing. Target figures are estimates until the bake-off measures billed seconds.

| | Today | Target |
|---|---|---|
| Stills (~1,080 per month for the TVs, plus the-beast and edits) | ≈ $110/mo at 0.92 MP | ≈ $55–125/mo at 1.5–4.2 MP native |
| Network volume | $3.50 | $0 |
| Gemini prompt work | ~$3 | $3–5 |
| Video (optional, 5 clips a day) | $0 (endpoint off) | ≈ +$36/mo for Wan 2.2; more for H3. 4K finishing about $0.60 a clip, opt-in |
| Bake-off (one-off) | — | $30–60 |

Lifetime RunPod spend so far is about $1,783: March 2025 to 17 Oct 2026, about 28k jobs.

A cheaper host exists: Koyeb (per-second, scale-to-zero, RTX PRO 6000 about 37% below RunPod, $29/mo plan). Its cold starts are unproven, so it is not recommended now.

## Bake-off protocol (one evening)

**Contenders:**
- Painterly: Qwen-2.1, full and Turbo; Krea 2 with the bypass LoRA and with Kroma.
- Photo: Z-Image, Base and Turbo; Chroma1-HD.
- Typography: Ideogram 4, Qwen-2.1, FLUX 3 Klein.
- Edits: Qwen-2.1 vs Edit-2511.
- Video: H3 Turbo vs Wan 2.2 (+ LTX-2.5 if time allows).
- Upscaling: Lanczos, UltraSharpV2 and SeedVR2.

Today's krea and sd35 endpoints are the baseline.

**Prompts (about 58):**
- 24 real prompts from the last 30 days;
- 10 favourites (Turner harbours, Magritte benches, Munch's Krakatoa, Klimt, O'Keeffe, a Dutch still life, the alebrije phoenix, the "bacon cliffs");
- the 6 the-beast phrases;
- 4 edits and 4 image-to-video stills;
- 10 expression probes:
  - a Schiele/Modigliani oil nude;
  - a photographic art nude;
  - a Goya war scene with blood;
  - a Salgado-style aftermath of conflict;
  - a Gillray etching of the UK PM;
  - painted satire of the US president;
  - painted satire of Xi Jinping;
  - "nuns holding firearms";
  - a Rubens-style violent mythological scene;
  - a caricature made on the edit lane from a reference photo of a public figure.

**Method:**
- 2 seeds per prompt.
- Run the top two models on the old 37-word prompts too, to separate the model's gain from the prompt's gain.
- Run flash-lite, 3.8-flash and passthrough on the probes.
- Publish to devtest with the model names hidden. You favourite at viewing distance and at a 100% crop.
- Record billed seconds, refusals and dropped words.

**How winners are picked:**
- **Painterly and photo lanes:** the most favourites per 100 images, provided the model renders at least 8 of the 10 probes as asked.
- **Turbo vs full:** Turbo wins if it is within 10% of the full model on favourites.
- **Typography:** the best exact-spelling rate.
- **Video:** favourites, then cost per clip.

## Your decisions

1. **Go on phase 0?** It needs one Flask restart (2f pinged first) and a frontend deploy.
2. **Go on the phase 1 spike and the bake-off?** About $30–60 on GPUs, with one evening of your favouriting on devtest.
3. **Video rate when animation returns:** about $7 a month per daily Wan clip; H3 costs more.

## Still unverified

- **Cost:** all billed-seconds figures for the target set-up.
- **Cached Models:**
  - whether a bundle maps onto ComfyUI's paths;
  - whether you can pin a revision (this is not documented);
  - how long the first download of a 75 GB+ private bundle takes on a new host.
- **Stack:**
  - whether ComfyUI 0.41 on cu128 runs Qwen-2.1, Z-Image and SeedVR2 at 4 MP; the README asks for cu130, but the code doesn't enforce it;
  - whether H3 and Krea 2 run in ComfyUI on one PRO 6000.
- **Expression:**
  - how Qwen-2.1, Krea 2 with the bypass, Z-Image and H3 actually handle nudes, violence and satire ("uncensored" is a community label only);
  - the quality cost of the Krea 2 bypass LoRA.
