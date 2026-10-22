## (a) Scores (1–10; for migration risk, 10 means lowest risk)

| Criterion | Quality | Ops | Expression |
|---|---|---|---|
| Artistic fidelity | **9** | 6 | 8 |
| Freedom of expression | 6 | 8 | 8 |
| Simplicity / maintainability | 6 | **9** | 5 |
| Cost | 6 | **8** | 7 |
| Migration risk | 7 | **8** | 7 |
| Factual soundness (vs verified facts) | 8 | 7 | 8 |
| **Total /60** | 42 | **46** | 43 |

**Quality design**
- Strengths:
  - The fidelity case is the strongest of the three: native ~4 MP generation, lane routing and two model "voices".
  - Its phase-0 list is the most useful: painting goes to sd35 instead of sdxl, JPEG 4:4:4, vision checks at 1536 px, and 2 MP latents on the current models.
  - The bake-off reuses the existing devtest favourites, so it needs no new tooling.
- Weaknesses:
  - It puts LTX-2.5 into production even though LTX's acceptable-use policy is binding, can be changed by Lightricks, and lets them restrict use remotely (V2). It bars graphic violence and "humiliate[s]" content, which is a model imposing guardrails, the exact thing the brief rejects.
  - Supporting LTX means two video stacks and a hand-built 4K DFR graph.
  - It is the costliest design (~$195/mo).
  - It says to "pin by commit" for Cached Models; P2 says no revision pinning is documented.
  - "40–44/45 probes" and "HiDream prudish" have no source.

**Ops design (chosen as the spine)**
- Strengths:
  - Qwen-2512 and Edit-2511 share one encoder and VAE.
  - Zero custom nodes, and adding a model takes 3 steps.
  - It spikes Cached Models before anything depends on them.
  - It uses a throwaway `sm-eval` endpoint for testing.
  - Rollback is just repointing `workflows.json`.
  - It follows the Flask restart rules (Spectre, ping 2f first).
- Weaknesses:
  - The Apache-only default caps fidelity on the lane that holds 58% of favourites: 2512 is 1.5 MP native and needs a 2.3× upscale.
  - Its LLM fallback is gpt-6-luna (SpeechMap 63.4), which brings prompt softening back.
  - It applies UltraSharpV2 (non-commercial licence) on a lane that includes the office the-beast screen.
  - Several figures are unverified: 45 s billed per job, "FlashBoot 200 ms", and "QUEUE_DELAY queues onto one warm worker".

**Expression design**
- Strengths:
  - The best legal analysis. It matches L5–L7 and the 6 Nov dates in SI 2026/1090.
  - It adds a check that key words survive the LLM rewrite, and refiner flags.
  - It catches that a passthrough fallback would skip past a Gemini child-safety refusal.
  - It spots that Wan's negative prompt suppresses "paintings".
- Weaknesses:
  - It recommends shipping an output classifier with face-age estimation that drops images. That is a gate, which house rule 5 forbids unless asked, and it has known false positives on putti and painted faces.
  - It adds a backend abstraction and a Koyeb drill.
  - It makes Turbo the schedule default before the bake-off has run.
  - Several claims are unverified: "Krea 2 tuned against wounds/intense emotion", "SD3.5 AUP bans gore", and that rp_handler logs prompts.

---

## (b) Final recommendation

**Structure:** the Ops design is the spine (architecture, packaging, migration). The lineup and fidelity grafts come from Quality; the prompt layer and legal analysis come from Expression.

### Executive summary
1. **Keep the pattern** of self-hosted open weights in ComfyUI on RunPod serverless, and **rebuild the packaging**:
   - 6 endpoints become 2 (`sm-image`, `sm-video`), both running one stock `worker-comfyui` image built at ComfyUI 0.41.0 on cu128.
   - Weights come from one private Hugging Face bundle per endpoint via Cached Models.
   - No network volume, so no US-KS-2 pin.
2. **Production weights are Apache-2.0 or MIT only, with one owner-decided exception: Qwen-Image-2.1.** Its research licence has no acceptable-use policy, no filter clause and no anti-circumvention clause (I2). Excluded:
   - every licence that requires filters or review or bans circumvention: FLUX.2 [dev], FLUX 3 Klein, Krea 2, Ideogram 4;
   - every licence that binds you to a changeable AUP: LTX-2.5;
   - every licence that excludes the UK: HunyuanImage 3, HunyuanVideo 1.5, MiniMax H3.
3. **Lineup:**
   - Painterly and edit: Qwen-2.1 if the owner says yes; otherwise the bake-off winner of Qwen-2512 vs HiDream-O1, plus Edit-2511.
   - Photographic and nudes: Z-Image Base.
   - Typography (office screen): Qwen-2512 or Z-Image Turbo, whichever spells better.
   - Video: Wan 2.2 only.
4. **Generate at native 1.5–4 MP and finish with one small step.** The 1280×720 → RealESRGAN ×4 path retires.
5. **Prompt chain:** gemini-3.1-flash-lite → gemini-3.8-flash → the raw prompt passed through unchanged.
   - Drop gpt-4o; it retires 14 Jan 2027.
   - Delete the "ZERO CENSORSHIP" and "moderation is downstream" pretext text.
   - Lift the 75-token cap.
   - Add a `lane` field and a check that key words survive the rewrite.
   - Write `prompt_chain` to every sidecar.
6. **No output gate.** The legal lines are met by design:
   - no NSFW LoRA on any edit graph;
   - an intimate brief about a real person is restyled into a painted medium;
   - a hard Gemini child-safety block ends that job and does not pass through.
7. **Phase 0 ships today on the current models:**
   - remove the dead endpoints from use and cancel jobs on timeout;
   - fix the downscaler so the-beast stops publishing 5120×2880;
   - the new prompt layer;
   - JPEG 4:4:4 and 1536 px vision checks;
   - 2 MP latents on Krea and Flux.
8. **One-evening bake-off** on the owner's own prompts and favourites, judged blind on devtest using the existing favourites.
9. **Cost:** images about $55–125/mo, against about $110 today at 0.92 MP. Optional Wan video adds about $36/mo at 5 clips a day.
10. **RunPod's terms** are accepted as a paper risk: it is boilerplate, unmonitored, and no enforcement against private serverless use was found (P1). The image stays portable, and Koyeb is the documented exit, not built now.

### Disagreements resolved

| Issue | Quality / Ops / Expression | Ruling |
|---|---|---|
| Qwen-2.1 in production | Lead (owner OK) / bake-off only / lead (owner OK) | **The owner decides after seeing the bake-off gap.** The licence says "research or evaluation only" (I2), and unlike FLUX/Ideogram it does not list private entertainment. A breach is contractual, not criminal. Build it as a one-entry catalogue swap. Never on the-beast (office). |
| LTX-2.5 video | Art loops / excluded / excluded | **Excluded.** Its AUP is binding and changeable, Lightricks can restrict use remotely, and it bars graphic violence and humiliation (V2). The ComfyUI 4K first-last-frame graph doesn't exist (only a 720p FLF2V template). One video stack is simpler. |
| Output legal check | none / none / ship rules 1+2 | **Not built** (house rule 5). The same intent is met upstream by refiner flags, restyling, no LoRA on edit, and no passthrough after a child-safety block. Listed as owner decision 3. |
| LLM after Gemini | passthrough / gpt-6-luna / passthrough | **Passthrough.** An OpenAI fallback brings softening back (SpeechMap 63.4). Alexa gets flash-lite → passthrough, because 3.8-flash takes 2.5–9 s against Alexa's 3.5 s budget (`config/llm.yaml:72-73`). |
| Full model vs Turbo | Full, Turbo if within 10% / n/a / Turbo default | **The bake-off decides:** Turbo if it is within 10% on favourites. The AA gap is 13 Elo, inside the confidence interval (I4 notes). |
| Upscaler | Lanczos paint, SeedVR2 photo / UltraSharpV2 paint / Lanczos+sharpen | **It depends on native size.** If ≤1.4×: Lanczos for paint and type, SeedVR2-7B for photo. If 2512 wins painterly (2.3×): the bake-off picks a 1.5× hires pass at 0.3 denoise or UltraSharpV2. UltraSharpV2 is non-commercial, so home screens only. |
| Worker code location | new repo / `worker/` in SM / new repo | **`worker/` in the SM repo,** so graphs, catalogue and image change together. Archive `runpod-worker-comfy`. |
| Portability | Koyeb exit / Koyeb exit / `backend` field + drill | **No abstraction and no drill.** Portability comes from the stock image, the HF bundle and graphs kept in git. A Koyeb adapter is about a day's work if RunPod ever acts. |
| the-beast's 5120×2880 | fix `locals()` / fixed final resize / — | **Both:** fix `generate.py:449-452` in phase 0, and end every new graph in a fit-crop to 3840×2160. |
| Satire medium | painted only / painted for intimate / any medium if non-intimate | **Any medium for non-intimate satire** (it is lawful). A real person in an intimate state always goes to a painted or etched medium (s.66E). The edit lane never sexualises a photo. |
| Cold starts | co-fire / 45 s queue delay / 20 s queue delay | Queue-delay scaler at **30 s**, idle 15 s, co-fire north and south. **The spike measures** whether co-fired jobs share a worker, because how queue delay behaves with zero workers is unverified. |

### Deployment verdict
- **Stay on RunPod serverless** with scale-to-zero.
  - About 55 scheduled images a day is the textbook scale-to-zero case. An always-on PRO 6000 would cost about $1,800/mo.
  - fal blacks out flagged images unless the account is authorised (P8).
  - Modal, Vast, Lambda, Novita and Verda all have obscenity or sexual-content bans (P6, P7).
- **Rebuild the stack.** CUDA 11.8 and the unpinned mid-2025 ComfyUI (C8) cannot load 2026 models or use Blackwell GPUs.
- **Don't ask RunPod for a ruling.**

### Model lineup

| Lane | Model | Why | Licence | Expression posture | GPU / VRAM |
|---|---|---|---|---|---|
| **Painterly / surreal** (TV default, 58% of favourites) | **Qwen-Image-2.1** (Turbo if within 10%) *if the owner says yes*; otherwise the bake-off winner of **Qwen-Image-2512** (+1.5× hires) vs **HiDream-O1** | 2.1 is #1 open on AA at 1042, native 2752×1536, needing only a 1.4× finish. 2512 is Apache with strong text. HiDream is MIT at up to 2048² | Qwen Research (non-commercial) / Apache-2.0 / MIT | No runtime checker. Pre-training was NSFW-filtered, so nude anatomy may be soft (bake-off probes) | PRO 6000. 2.1 is ~34 GB bf16; HiDream 35 GB |
| **Photographic + nudes** (LoRA base) | **Z-Image Base** (Turbo for drafts) | Apache, CFG and negatives, LoRAs aren't a licence breach | Apache-2.0 | No checker. "Uncensored" is only a community claim; pre-training was NSFW-scored (I6) | ~16–20 GB |
| **Reserve** (if Z-Image fails the nude or violence probes) | **Chroma1-HD** | Card says "not aligned with a specific safety filter" (I7) | Apache-2.0 | Least aligned | 8.9B |
| **Typography** (the-beast, office) | **Qwen-2512 vs Z-Image Turbo**, picked by exact-spelling rate | Office use is arguably commercial, so Apache only, with no non-commercial upscaler | Apache-2.0 | The office-safe gate stays as the owner set it | ≤30 GB fp8 |
| **Edit / multi-reference** (Adapt, Combine, satire from a reference) | **Qwen-2.1** (10 refs, edit Elo 1083) *if yes*; otherwise **Qwen-Image-Edit-2511** (990, Apache) | Both beat Kontext (884) and replace the ImageStitch hack | as above | **Never an NSFW LoRA** (s.66I) | ~20 GB fp8 |
| **Finish to 3840×2160** | Lanczos (paint, type); **SeedVR2-7B** (photo; in ComfyUI core) | Small final step only | SeedVR2 Apache | — | 12–16 GB |
| **Video** | **Wan 2.2 I2V-A14B** + lightx2v 4-step LoRAs → SeedVR2-3B to 1440p → RIFE 4.26 → core `SaveVideo`. 4K finishing opt-in per clip | Only Apache video with no AUP, and the biggest LoRA ecosystem. 61% of old 1440p clips were favourited | Apache-2.0 | No filter | ~28 GB fp8 + umt5, PRO 6000 |

**To retire:**
- SD3.5 Large, SDXL, FLUX.1-dev, FLUX.1-Krea, Kontext and Wan 2.1;
- the Heartsync LoRA, which runs at 0.5 on every Krea and Flux image (C3);
- `sldr_flux_nsfw_v2-studio`;
- RealESRGAN, including the mislabelled `2kpr` x4 (V10).

**LoRA rule (from Expression):**
- Apache bases only, from sources that document base model and training data.
- Pinned by sha256.
- Strength set per request from the refiner's flags, never applied to every image.
- Never on the edit lane.

### Architecture

**Image, in `worker/` in the SM repo:**
- `build.sh` builds `runpod-workers/worker-comfyui` at tag 5.11.0 with `COMFYUI_VERSION=0.41.0` and cu128 torch.
  - There is no 5.11.0 image on Docker Hub (P4).
  - 5.10.0-base ships ComfyUI 0.34.0, which predates Qwen-2.1.
- Thin layer: `Dockerfile`, `start-bundle.sh` (symlinks the cached snapshot to `/bundle`), `extra_model_paths.yaml`, and `bundles/sm-{image,video}.txt` with sha256.
- Zero custom nodes.
- Immutable tags (`sm-worker:YYYY-MM-DD`).
- No `LIVE_PATCH`, `AWS_SYNC`, `COPY_*` or websocket relay to the dead `185.254.136.244` (C4).
- S3 keys move to RunPod secrets.

**Endpoints:**

| | `sm-image` | `sm-video` | `sm-eval` (temporary) |
|---|---|---|---|
| Bundle | private HF `sm-image-bundle`, production winners only (every file is downloaded, P2) | `sm-video-bundle`: Wan 2.2, LoRAs, umt5, VAE, SeedVR2-3B, RIFE | all contenders; deleted after the bake-off |
| GPUs | PRO 6000 96 GB → H100 | PRO 6000 → H100 | PRO 6000 |
| Workers / idle / scaler | 0/2, 15 s, queue delay 30 s | 0/1, 15 s | 0/1 |
| Execution timeout | 600 s | 1800 s | 900 s |
| Other | FlashBoot, no network volume | same | same |

**Outputs:**
- Workers upload to the existing S3 via `BUCKET_*` and return `output.images[]` as `s3_url`.
- Video graphs must end in core `SaveVideo`, because the official handler drops `VHS_VideoCombine` output.
- Every graph ends in a fit-crop to 3840×2160.

**Progress:** the overlay uses the RunPod `/status` call the app already polls every 0.5 s. No inbound WAN relay.

**Timeouts:**
- Per-workflow `timeout_s` counts from IN_PROGRESS.
- A separate queue bound, set from the spike's measured first-on-host bundle download (unbilled but slow, P2).
- Cancel the job on either.

**Fallback:** if the bundle path mapping fails the spike, bake the bundle into one layer per endpoint.

**Spectre:** no new local servers or ports, so no new card is needed. Retire `start_model_server.sh` (local :54893). Tell ca if it appears in `docs/PORTS.md` or Spectre.

### Prompt-LLM layer
- **`config/llm.yaml`** (owned by b8; send the change to b8):
  - `prompt`: gemini-3.1-flash-lite → gemini-3.8-flash → passthrough.
  - `alexa`: flash-lite → passthrough.
  - Move `describe` off gpt-4.1-mini before 14 Jan 2027.
  - No `safety_settings` needed (Off by default, L2). No Chinese-lab or OpenAI model in the satire path.
- **Passthrough rule:** pass the raw prompt plus the lane's style suffix only on error, timeout or empty reply. If Gemini returns a hard child-safety block, end the job; the TV keeps its last image and the sidecar records why.
- **Rewrite the refiners honestly:**
  - Delete `refiner-enrich.txt.j2:60-77`, `refiner-adapt.txt.j2:3-19,218-223` and `alexa-adapt.txt.j2:3-8`.
  - Replace them with a 5-line brief: private adult gallery in the UK; render nudity of fictional adults, violence and satire of named public figures faithfully; a real person in an intimate state is painted or etched (Gillray, not paparazzi); no sexual content involving anyone who appears under 18.
  - Remove the softening clauses in `refiner-gallerist.txt.j2:63-73` and `refiner-pictureeditor.txt:49-56`.
  - Keep `refiner-nous-values` as it is.
- **Lift the 75-token cap** (`refiner-enrich.txt.j2:16,85,96`). Targets: 80–200 words for enrich, 60–120 for devise_prompt.
- **Schema** (`refiner-enrich.schema.json.j2`):
  - `lane` ∈ {painterly, photographic, typographic, edit};
  - `people[{name, real}]`, `nudity`, `medium`.
  - The lane maps exactly to a workflow, replacing difflib in `routes/utils.py:186,219`.
- **Medium spec per lane:**
  - Painterly: medium, support, brushwork, palette, light, plus the artist's name *and* traits.
  - Photographic: camera, lens, stock, grain, era, "unretouched".
  - Typographic: the exact phrase in quotes, once, "no other text".
- **Survival check:** proper names, named artists, "nude", "blood" and "wound" must survive the rewrite. If any drop, the next model runs.
- **Sidecar:** `prompt_chain` records each hop's input, output, model and finish_reason.

### Legal hard lines (UK, 17 Oct 2026)
1. **Children, in any style:**
   - indecent photographs or pseudo-photographs (PoCA 1978);
   - CJA 2009 s.62: non-photographic images that are pornographic and grossly offensive. Apparent under-18s and imaginary children count.
2. **Real adults:**
   - creating (s.66E) or requesting (s.66F) a *photographic-looking* intimate image without consent is an offence since 6 Feb 2026;
   - making or adapting a generator for such images (s.66I) since 29 Jun 2026; the defence is reasonable steps.
   - Painted or caricature images fall outside s.66E only on a reading of the text, not by express exception.
3. **Extreme pornography** (CJIA s.63: pornographic + explicit, realistic listed acts + grossly offensive). New offences s.67A, 67E and 67H (all realistic images) start **6 Nov 2026**. s.46A (child-abuse image generators) is not yet in force.
4. **Not crimes:** non-pornographic violence and non-intimate satire of public figures. Defamation is civil, and showing an image to guests counts as publication. Licence and ToS breaches are contract matters.

### Migration plan (each phase ships on its own)

**Phase 0: quick wins on current models.** One Flask restart via Spectre after pinging 2f, plus one frontend deploy.
1. Hide the Kontext, Wan and upscale entries (workersMax 0, C6). Delete `_auto_upscale` (`north-screen.json:390` and south).
2. In `generate.py`:
   - `:710-718`: POST `/cancel` on timeout.
   - `:449-452`: read maxwidth from args, not `locals()`.
   - `:427`: accept both `{{NEGATIVE-PROMPT}}` and `{{NEGATIVE_PROMPT}}`. The Wan graphs use the underscore spelling, so they never receive the request's negative prompt.
3. The prompt layer above, sent to b8.
4. Route "painting" to sd35 instead of sdxl in all four refiners.
5. `publisher.py:318`: `quality=95, subsampling=0`.
6. `openai.py:33,160`: vision at 1536 px, not 512 px with `detail: low`.
7. `kreav2.json` and `flux1-dev-scale-l.json`: latent 1280×720 → 1920×1088.

**Phase 1: image and spike.**
- Run `worker/build.sh`.
- Create `sm-image` with a minimal bundle.
- Run one cold and one warm job.
- Measure the path mapping, how a new bundle revision propagates, first-on-host download time, and co-fired worker sharing.

**Phase 2: app adapter.**
- `generate.py`: parse `output.images[]`; per-workflow `timeout_s`; `sampler_name` at `:439`.
- `generate_utils.py:184-194` and `usePromptForm.ts:74-83`: let catalogue params through.
- `generate_handler.py`:
  - cost = `executionTime` × the catalogue rate;
  - delete the GraphQL pricing lookup (`:71-181`);
  - passthrough on `LLMError`;
  - write sidecar fields.
- `manage_jobs.py`: prune job ids.
- Add new graphs alongside the old ones.

**Phase 3:** `sm-eval` and the bake-off.

**Phase 4: cut over north and south.**
- Point the catalogue `default` and lane map at the winners.
- Set the old sd35, krea, flux and sdxl endpoints to workersMax 0 for 7 days, then delete them.

**Phase 5: the-beast.** Move to the typography winner, and set `the-beast.json:98` to batch 2 if the bake-off rule passes.

**Phase 6: edit lane.** Qwen-2.1 or Edit-2511 replaces Kontext; re-enable Adapt and Combine for Alexa.

**Phase 7: `sm-video`.**
- Wan 2.2 graphs ending in `SaveVideo`.
- Drop "paintings, pictures, still" from Wan's negative prompt.
- Turn `animation_on` back on at the owner's chosen rate.

**Phase 8: retire.**
- Endpoint `1w6vje8cn33ift` and template `5169ofvq0e`.
- Volume `gmaygtw1z4`.
- The `runpod-worker-comfy` repo (archive it) and the `dev-*` tags.
- The NSFW LoRAs and RealESRGAN.

### Cost (PRO 6000 at $0.000969/s; billed seconds are estimates until the bake-off)

| Item | Today | Target |
|---|---|---|
| TV stills (~1,080/mo) | — | Qwen-2.1 Turbo ~40 s: **$42**. Qwen-2.1 full ~100 s: $105. Apache 2512+hires ~75 s: $78 |
| the-beast (~138 jobs/mo, batch 2, ~60 s) | — | $8 |
| Edits and manual (~30, 90 s) | — | $3 |
| Gemini prompt work | ~$3 | $3–5 |
| Hugging Face private storage | — | $0–9 |
| Network volume | $3.50 | $0 |
| **Images total** | **≈ $110** (0.92 MP renders) | **≈ $55–125** (1.5–4.2 MP native) |
| Video, optional (5/day Wan 2.2 at 1440p, ~250 s) | $0 (endpoint off) | +$36 (4K finishing ~$0.60 per clip) |
| Bake-off, one-off | — | $30–55 |

### Bake-off protocol
- **Where:** the temporary `sm-eval` endpoint. Baseline is today's kreav2 and sd35 endpoints.
- **Contenders:** Qwen-2.1 base and Turbo, Qwen-2512 (plain and with a 1.5× hires pass at 0.3 denoise), Z-Image Base and Turbo, HiDream-O1, Chroma1-HD (probes and photo lane only), and Edit-2511 vs Qwen-2.1 for edits. Upscale variants: Lanczos, UltraSharpV2, SeedVR2-7B.
- **Prompts (~58):**
  - 24 real final prompts from the last 30 days of `output/*-screen/*.json`;
  - 10 favourites from `output/*-screen/bucket.json`: Turner harbours, Magritte benches, Munch's Krakatoa, Klimt, O'Keeffe, Dutch still life, alebrije, the "bacon cliffs";
  - the 6 the-beast phrases;
  - 4 edits and 4 i2v stills;
  - 10 probes:
    - Schiele/Modigliani oil nude of a fictional adult;
    - photographic art nude of a fictional adult;
    - Goya war scene with blood;
    - Salgado-style aftermath of conflict;
    - Gillray etching of a UK PM;
    - painted satire of a US president;
    - painted satire of Xi Jinping;
    - "nuns holding firearms";
    - Rubens-style violent mythological scene;
    - a non-intimate caricature on the edit lane from a reference photo of a public figure.
  - No prompt aimed at an illegal output.
- **Method:**
  - 2 seeds each.
  - The top two models also run the old 37-word prompts, to separate the model gain from the prompt gain.
  - Run flash-lite, 3.8-flash and passthrough on the probes.
  - Publish to **devtest** with model names hidden. The owner favourites what he likes on the Frame TV at viewing distance and at 100% crop on the PC.
  - Record billed `executionTime`, refusals and dropped words from `prompt_chain`, and anatomy failures.
- **Decision rules:**

| Lane | Rule |
|---|---|
| Painterly and photographic | Most favourites per 100, and the model must render at least 8 of 10 probes as asked. Turbo replaces full if within 10% on favourites. Qwen-2.1 is eligible only if the owner approves its licence. |
| Typography | 4 seeds × 6 phrases, Gemini exact-match on full-resolution images. The best rate wins. At 75% or better for a single image, batch drops to 2. |
| Upscaler | Chosen per lane by favourites at 100% crop. |
| Video | Pick the RIFE multiple with no judder on the 60 Hz kiosk. |

### Open decisions for the owner
1. **Qwen-Image-2.1 on the home TVs** under its "research or evaluation only" licence: yes means it leads painterly and edit; no means the Apache/MIT lineup. Decide after seeing the bake-off gap.
2. **Video rate** when `animation_on` returns: each daily Wan clip costs about $7/mo.
3. **Build Expression's optional legal-line output check** (drop apparent-minor nudity; repaint photoreal intimate images of real people)? Default: not built, per house rule 5.
4. **RunPod's terms** literally bar adult content, "ridicule/mock" and unconsented likeness. Stay (recommended), or move to Koyeb now: $29/mo, about 37% cheaper GPUs, unproven cold starts.

### Claims that remain unverified
- **Costs:** every billed-seconds figure and monthly cost, and the "$110/mo today" baseline (a round-1 figure, not in the verified facts).
- **RunPod Cached Models and scaling:**
  - whether the bundle maps onto ComfyUI paths;
  - whether workers pick up a new bundle revision; revision pinning is not documented;
  - the first-on-host download time for a 75 GB private bundle;
  - how the queue-delay scaler behaves with zero workers;
  - "FlashBoot ~200 ms" and "fp8 loads in ~10 s".
- **Stack:**
  - ComfyUI 0.41.0 on cu128 running Qwen-2.1, Z-Image and SeedVR2 at 4 MP; the README asks for cu130 (P5);
  - SeedVR2's 4K VRAM behaviour, with its chunking PRs still open;
  - whether 5.10.0-base (ComfyUI 0.34.0) runs the whole Apache lineup.
- **Model specifics:**
  - Qwen-2512 size, VRAM and native 1664×928;
  - Edit-2511's reference count;
  - Z-Image's largest stable 16:9 size;
  - HiDream-O1's 16:9 limits and "prudishness";
  - Z-Image Turbo's text-rendering claim.
- **Expression quality** of Qwen-2.1, 2512 and Z-Image on nudes, violence and satire. "Uncensored" is community-only, and the "40–44/45 probes" figure has no source.
- **Licence and terms claims:** "Krea 2 tuned against wounds/intense emotion"; "SD3.5 AUP bans gore"; that `rp_handler.py` logs prompts and presigned URLs (moot once the fork is retired).
- **LLM:** whether Gemini's OpenAI-compatible endpoint returns a child-safety block reason the app can tell apart from other failures; and gemini-3.1-flash-lite's deprecation date.
- **Other:** Hugging Face private-storage pricing for about 120 GB; whether RunPod's ToS is enforced on private serverless output (none found, which is not proof); and that painted caricature falls outside s.66E (a reading of the text, not an express carve-out).