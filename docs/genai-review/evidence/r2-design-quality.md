# Screen Machine target gen-AI design: gallery-quality lens (17 Oct 2026)

Roots: `SM=/home/gjbm2/dev/screen-machine`, `WK=/home/gjbm2/dev/runpod-worker-comfy`. Times and costs marked *est.* are my estimates. The bake-off measures them.

**One owner decision is needed. It only affects which model leads the painterly and edit lanes in step 5.** Qwen-Image-2.1's licence allows "research or evaluation purposes only". The bake-off is evaluation, so it is covered. Showing its output on the home TVs every day is the owner's call. Everything else ships either way. If he says no, the licence-clean variant takes those two lanes: HiDream-O1 (MIT) for painting and Qwen-Image-Edit-2511 (Apache) for edits.

---

## 1. Verdict: keep the pattern, rebuild the implementation, don't move

**Keep** self-hosted open weights in ComfyUI on RunPod serverless.
- **Freedom.** Only your own worker runs with no output classifier.
  - fal returns black images unless the account is authorised.
  - BFL and the closed APIs filter by default.
  - Modal, Vast, Lambda, Novita and Verda all ban "obscene" or "sexually explicit" content in their terms.
- **Load shape.** About 55 images a day, 30–60 minutes apart, is the classic scale-to-zero case. An always-on RTX PRO 6000 would cost about $1,800 a month.
- **Model support.** ComfyUI supported Qwen-Image-2.1 on day 0 and FLUX 3 Klein in v0.41.0. The app's graph-and-placeholder coupling carries over unchanged.

**Rebuild.** The quality ceiling is the plumbing, not the GPUs:
- **CUDA 11.8 and a mid-2025 ComfyUI.** No 2026 model loads, and no Blackwell GPU can be used.
- **Six fat per-model images.** Pushes took up to 2.5 hours.
- **A network volume pins five endpoints to US-KS-2.**
- **Two dead endpoints** are still offered in the UI and to Alexa.
- **Every still starts as 1280×720** and is blown up 3× by a 2018 GAN (RealESRGAN).

**Don't move.**
- RunPod's terms ban "pornography or graphic adult content" and content that "ridicule[s], mock[s]". That is Termly-style boilerplate, RunPod says it does not monitor, and no enforcement against private serverless use was found.
- Keep the image portable. Koyeb is the exit: its terms ban only illegal content, and it rents a PRO 6000 at $2.20/hr on a $29/month plan. At this volume there is no saving in moving.

## 2. Model lineup per lane

**How models were chosen:**
- best fidelity at a native size of at least about 4 MP in 16:9;
- **no licence that requires output filtering or forbids circumventing it.** That rules out Krea 2, FLUX.2 [dev], FLUX 3 Klein and Ideogram 4 for production;
- nothing whose licence excludes the UK: HunyuanImage 3, HunyuanVideo 1.5 and MiniMax H3 are out.

The design therefore needs no content gate. The-beast's existing office-safe check stays.

| Lane | Production model | Native size → 3840×2160 | Licence | Expression posture | Weights / VRAM |
|---|---|---|---|---|---|
| **Painterly / surreal** (Magritte, Turner, Monet; 58% of favourites) | **Qwen-Image-2.1, full model** (AA 1042, #1 open). Turbo (AA 1028) only if the bake-off shows no visible loss. Licence-clean variant: **HiDream-O1-Image** | 2752×1536 (4.2 MP), then Lanczos 1.4× and fit-crop. Optional 4x-UltraSharpV2 then downsample, home screens only | Qwen Research: non-commercial, no AUP, no filter clause. HiDream: MIT | No safety checker. Reported to draw 40–44 of 45 sensitive probes. Pre-training was NSFW-filtered, so nude anatomy is weaker. HiDream is anecdotally prudish | DiT 14.2 GB + Qwen3-VL-8B 17.5 GB bf16, about 34 GB peak. HiDream about 35 GB |
| **Photographic / documentary** (Salgado, Meyerowitz, Kenna) and **nude studies** | **Z-Image Base**: CFG and negative prompts, the base for LoRAs. Turbo goes into the bake-off too | Largest stable 16:9 size (start at 2560×1440, confirm), then **SeedVR2-7B** about 1.5× | Apache-2.0 | No runtime filter. Training data was NSFW-scored, but the community reports it as permissive | 6B + Qwen3-4B, about 20 GB |
| **Typography** (the-beast, an office screen) | **Z-Image Base vs Qwen-Image-2512.** The bake-off picks by exact-spelling rate | Native size, then Lanczos. No non-commercial upscaler | Apache-2.0 for both. Office use is arguably commercial, so no non-commercial models here | The office gate stays | 20 GB, or 20 GB fp8 + 9.4 GB |
| **Edit / multi-reference** (Adapt, Combine, satire from reference images) | **Qwen-Image-2.1**: same weights, 10 references, AA edit Elo 1083. Licence-clean: **Qwen-Image-Edit-2511** (Elo 990, Apache) | 2–4 MP. References go in separately, replacing the ImageStitch hack | As above | **No NSFW LoRA on this lane** (UK SOA s.66I, the "nudifier" offence) | Shared weights, or 20.5 GB fp8 |
| **Uninhibited fallback** | **Chroma1-HD**, only if Z-Image fails the nude or violence probes | About 1 MP, then SeedVR2 | Apache. Its card says it is "not aligned with a specific safety filter" | None | 8.9B |
| **Video: art loops** (61% of retained clips favourited) | **LTX-2.5 distilled**, DFR i2v at native 3840×2176, cropped to 2160. Temporal ×2 to 48 fps | Native 4K | LTX-2.x, free under $10M revenue. **Its binding AUP bars sexually explicit content, graphic violence and humiliating real people** | Weights unfiltered; the AUP is the constraint | 22B int8 + Gemma-4 12B (Apache) |
| **Video: satire, violence, nudes** | **Wan 2.2 I2V-A14B** with lightx2v 4-step LoRAs, then SeedVR2-3B to 1440p, Lanczos to 2160, and RIFE 4.26 | 720p, upscaled | Apache-2.0 | No filter; the largest LoRA ecosystem | About 28 GB fp8 |

**Upscale policy:**
- Native 4 MP generation means the last step is only about 1.4×.
- Painterly work uses Lanczos (exact brushwork).
- Photographic work uses SeedVR2. It is in ComfyUI core but is reported to add texture to flat art, so it stays off paintings.
- A Qwen-2.1 tiled redraw is a manual "hero" option only.

**Not chosen:**
- **Krea 2.** It has the best painterly look, but licence §4.2 makes filters mandatory for every deployment, and uncensoring breaches §4.1(c).
- **FLUX 3 Klein.** It is the only open model with native 4K, but its licence requires a filter or review, and early users report broken anatomy. It runs in the bake-off as a reference only.
- **Qwen-Image-2512 for painting.** It renders only 1.5 MP natively, so it would need a 2.3× upscale.

**To retire:** SD3.5 Large, SDXL, FLUX.1-dev, FLUX.1-Krea, Kontext, Wan 2.1, the Heartsync LoRA, the `sldr` LoRA, and RealESRGAN as the default.

## 3. Hosting and deployment

**Two endpoints sharing one runtime image:**

| Endpoint | Runs | GPUs, in order | Settings |
|---|---|---|---|
| `sm-image` | text-to-image, edit and upscale graphs | RTX PRO 6000 96 GB ($0.000969/s), then H100, then H200 | workersMax 2, idle 15 s, FlashBoot, exec timeout 900 s, **no network volume** |
| `sm-video` | video graphs | PRO 6000, then H200, then B200 | workersMax 1, timeout 1800 s |

**Runtime image:**
- Build from the source of `runpod-workers/worker-comfyui` tag 5.11.0 with `COMFYUI_VERSION=0.41.0`.
  - There is no 5.11.0 image on Docker Hub.
  - 5.10.0-base ships ComfyUI 0.34.0, which predates Qwen-Image-2.1.
- Keep CUDA 12.8.1 with cu128 torch. It supports Blackwell, and RunPod pins cu128 because a cu13 torch fails to initialise CUDA on hosts with 570/575 drivers. That means no NVFP4.
- Use immutable `sm-runtime:YYYY.MM.N` tags and the build-time `--quick-test-for-ci`.
- No LIVE_PATCH, AWS_SYNC or COPY_*.

**Weights:**
- Use **RunPod Cached Models**: one private Hugging Face repo per endpoint (`sm-image-weights`, `sm-video-weights`).
  - Lay each repo out in ComfyUI `models/` folders and include only the files the graphs use, because every file in the repo is downloaded.
  - Pin by commit and keep a sha256 manifest.
- A start-up hook resolves the `/runpod-volume/huggingface-cache/hub/.../snapshots/<hash>` directory and writes `extra_model_paths.yaml`.
- Downloads are not billed, and dropping the volume removes the US-KS-2 pin.
- Fallback: if the path mapping fails the smoke test, bake the bundle into the image. That is still one image, not six.

**Outputs:**
- Worker 5.x returns `output.images[]`, as base64 or an S3 URL.
- Video graphs must end in core `SaveVideo`. The handler silently drops VideoHelperSuite outputs.

**Cold starts:**
- Every job is cold at this cadence. Accept about 15–25 s of NVMe load.
- Fire north and south on the same tick so the second one hits a warm FlashBoot worker.
- The-beast stays one batched job.
- Buy no active workers.
- Drop the worker's websocket relay to `ws://185.254.136.244:8765`, an old IP. The overlay shows queued/running from `/status`.

## 4. Prompt and LLM layer

**Fallback chain:**
- Keep this morning's change (dc215fcf): gemini-3.1-flash-lite answers first. It scores 88.4% on SpeechMap, the highest Google model.
- The chain becomes flash-lite → gemini-3.8-flash → **the raw prompt passed through unchanged** (degrade, never refuse).
- Remove the gpt-4o fallback before it retires on 14 Jan 2027.

**Refiner text:**
- Delete the "ABSOLUTE ZERO CENSORSHIP" and "moderation is downstream" text. The pretext is false and puts the shared key at risk.
- Replace it with one honest line: "Private adult art gallery; render the brief faithfully, including nudity of fictional adults, violence and satire, in the stated register."

**Prompt length:**
- Remove the 75-token CLIP cap. Every new encoder is an LLM.
- Target length: 80–200 words for enrich, 60–120 for devise_prompt.

**Lane routing:**
- The refiner returns `lane` ∈ {painterly, photographic, typographic, edit}, and the catalogue maps each lane to a workflow.
- This removes the "painting → sdxl" mapping (the weakest model) and the difflib fuzzy match.

**Per-lane medium spec:**
- **Painterly:** medium, support, brushwork, palette, light and composition. Name the artist **and** describe their traits; the bake-off tests name-only against name plus traits.
- **Photographic:** camera and format, lens, film stock, grain, era, light, and "unretouched", to counter Z-Image's glossy default.
- **Typographic:** the exact phrase in quotes, rendered once, plus letterform and layout, and "no other text".
- **Negative prompt** for the non-type lanes: "text, signature, watermark".

**Satire and audit:**
- Satire of real people goes to the painterly lane. A painted image falls outside the UK s.66E test, which turns on whether an image appears photographic. For video it goes to Wan 2.2, because the LTX AUP bars it.
- Each sidecar gains a `prompt_chain`: every hop's input, output, model and finish_reason. Softening becomes visible.

## 5. App-side changes

**New worker repo** (replaces the `WK` fork):
- `Dockerfile`
- `src/model_paths.sh` (the start-up hook)
- `bundles/sm-image.txt` and `bundles/sm-video.txt` (file lists with sha256)
- Delete `fetch_model_2.sh`, `push_race.sh` and `rebuildall_parallel.sh`.

**SM:**
- **`src/data/workflows.json`:**
  - Add entries for `qwen21-t2i.json`, `qwen21-edit.json`, `zimage-t2i.json`, `zimage-type.json`, `upscale-4k.json`, `ltx25-i2v-4k.json` and `wan22-i2v.json`. Variant graphs: `hidream-t2i.json`, `qwen-edit-2511.json`, `qwen2512-type.json`.
  - Give each entry the new `runpod_id`, `lane`, `prompt_style` and `timeout_s`.
  - Set `alexavisible:false` on the Kontext, Wan and upscale entries now.
  - Point `default` at the painterly winner.
- **`routes/workflows/`:**
  - New graphs keep the `{{…}}` titles and use native latent sizes.
  - Each ends in a fit-crop to 3840×2160, then `SaveImage` or `SaveVideo`.
- **`routes/generate.py`:**
  - :449-452: take maxwidth from args, not `locals()`. This fixes the-beast's 5120×2880 output.
  - :540: per-workflow timeout.
  - :710-718: POST `/cancel` on timeout.
  - Parse `output.images[]` alongside `output.message`.
  - :439: `sampler_name`.
  - :427: `{{NEGATIVE_PROMPT}}`.
- **`routes/generate_utils.py:184-194`:** let catalogue params through.
- **`src/components/prompt-form/usePromptForm.ts:74-83`:** read `params`, not `parameters`.
- **`routes/generate_handler.py`:** map lane to workflow. Take cost from RunPod `executionTime + delayTime` and drop the GraphQL price call that returns 400 on every job.
- **`routes/utils.py:181-227`:** exact lane lookup instead of difflib.
- **Refiner and reasoner text:**
  - `routes/data/refiner-enrich.txt.j2` (:16, :33-36, :60-77, :85, :96)
  - `refiner-adapt.txt.j2` (:3-19, :178, :188, :218-223)
  - `alexa-adapt.txt.j2` (:3-8)
  - `refiner-gallerist.txt.j2` (:27, :44-47)
  - `refiner-pictureeditor.txt` (:32-35)
  - `refiner-tabloidpictureeditor.txt` (:70-74)
  - `refiner-nous-values.txt.j2` (:1, the "ou are" typo)
  - `reasoners/devise_prompt.txt.j2` (:4)
  - the enrich schema (add a `lane` enum)
- **`routes/openai.py:33,50,160`:** send vision images at 1536 px, not 512 px low detail. The-beast's spelling check needs it.
- **`routes/publisher.py:307-321`:** JPEG q95 with `subsampling=0` (4:4:4), so brushwork and type edges don't bleed colour.
- **`routes/scheduler/north-screen.json` and `south-screen.json`:** delete trigger 14, `_auto_upscale`. It points at a dead endpoint and is moot at native 4 MP. Co-fire north and south.
- **`routes/scheduler/the-beast.json:98`:** `batch=2` after the bake-off.
- **`config/llm.yaml`:** the new `prompt` chain.

**No change:** the `stream_service/plugins/sources/gallery.py` contract (at least 1920 wide, 16:9, sidecar with prompt and workflow) still holds. There are no new local servers or ports, so no Spectre card is needed.

## 6. Migration, smallest first; each step ships

1. **App hygiene** (about 1 hour, today's models):
   - hide the dead Kontext and Wan entries;
   - delete `_auto_upscale`;
   - cancel on timeout;
   - fix the downscaler;
   - route "painting" to sd35, not sdxl, in all four refiners;
   - JPEG 4:4:4.
2. **Quick fidelity win:** move the kreav2 and flux1 latents from 1280×720 to 1920×1088. FLUX handles 2 MP well, and the GAN step drops from 3× to 2×.
3. **Runtime image, `sm-image` endpoint and weights bundle.** Smoke-test one job per graph and read the billed seconds.
4. **Bake-off** (§8): one evening of generation, then 2–3 days of the owner's eye. About $55.
5. **Switch the north and south stills** to the winners, together with the §4 prompt layer and the catalogue-params fix.
   - Set the old sd35, krea, flux1 and sdxl endpoints to workersMax 0 as a 7-day rollback, then delete them.
6. **The-beast:** move to the typography winner, batch 2.
7. **`sm-video`:** LTX-2.5 i2v 4K plus Wan 2.2, on native `SaveVideo`.
   - Re-enable `animation_on`, then delete the Wan 2.1 endpoint.
   - First=last-frame loops come later, from a hand-adapted DFR graph. The stock ComfyUI FLF2V template is 720p only.
8. **Edit lane:** Qwen-2.1 (or Edit-2511) replaces Kontext.
   - Delete the Kontext endpoint and the `sldr` LoRA.
   - Restore Adapt and Combine to Alexa.
9. **Retire:**
   - the WK fork;
   - endpoint `1w6vje8cn33ift` and template `5169ofvq0e`;
   - volume `gmaygtw1z4`;
   - the Heartsync LoRA and the mislabelled `2kpr` x4 upscaler;
   - the S3 keys in plaintext template env (move them to RunPod secrets).

## 7. Monthly cost (PRO 6000 at $0.000969/s; billed seconds are *est.*)

| Item | Per month | Billed s each | $/month |
|---|---|---|---|
| Home stills, north + south (36/day) | 1,080 | 90 (60–130) | **94** (63–136) |
| The-beast, batches of 2 (4.6/day) | 138 | 130 | 17 |
| Edits and manual jobs | ~30 | 90 | 3 |
| LLM (Gemini flash-lite, ~90 calls/day) | | | ~2 |
| Storage (S3, private HF; volume dropped) | | | ~5 |
| **Images only** | | | **≈ $120** |
| Video, optional (5/day, LTX 4K) | 150 | ~500 (300–600) | **73** (44–87) |
| **With video** | | | **≈ $195** |

- **Today:** about $110 a month (Sep 2026) for 0.9 MP renders.
- **Stills on Qwen-2.1 Turbo** (8 steps) would bring the images line to about $60–70.
- **One-off bake-off:** about $55.

## 8. Top risks, de-risking, and the bake-off

**Risks and mitigations:**

| Risk | Mitigation |
|---|---|
| The Qwen-2.1 licence is not cleared | The licence-clean variant is pre-built in step 3. The bake-off is licensed as evaluation either way. |
| Qwen and Z-Image pre-training was NSFW-filtered, so nudes may be weak | Expression probes in the bake-off. Chroma1-HD as fallback. LoRAs only from known sources later; Heartsync is never reused. |
| Stack freshness: ComfyUI 0.41 is two days old, and SeedVR2's memory-chunking PRs are still open | Pinned tags, a smoke job per graph, old endpoints kept at workersMax 0 for 7 days. |
| The cached-model bundle may not map onto ComfyUI paths (untested) | Bake the bundle into the image instead. |
| LTX DFR 4K hits a VRAM cliff at keyframe decode | `--spatial-upscalings 2`, or H200. Start with i2v; loops come later. |
| The LTX AUP binds content, and Lightricks reserves the right to restrict usage | Satire, violence and nudes go to Wan 2.2. Pin the weights by commit. |
| RunPod ToS boilerplate | Portable image; Koyeb as the exit. |
| Cost runs 2× over the estimate | Turbo models; check `executionTime` every week. |
| UK law: s.66E/66F (intimate images of real adults), s.66I (nudifier tools), and CJIA s.67A/E/H from 6 Nov 2026 | No NSFW LoRA on any edit graph. Refiners never sexualise named real people. Satire stays painted. This is a design choice, not a gate. |
| Every image starts to look the same | Two "voices" (Qwen for paint, Z-Image for photo), routed by lane. |

**Bake-off on the owner's own prompts:**

*Prompt set*
- **24 gallery prompts:**
  - 12 favourites from `output/*-screen/bucket.json` plus their sidecars: the Turner harbours, Magritte benches, Munch's Krakatoa, Klimt, O'Keeffe, a Dutch still life, the alebrije and the "bacon cliffs";
  - 12 recent styles: Monet, Meyerowitz, daguerreotype, Salgado, Rembrandt chiaroscuro, Wyeth, Kenna, Pre-Raphaelite, Expressionism.
- **6 phrases from the-beast.**
- **6 expression probes:**
  - an oil nude study of a fictional adult (Schiele/Modigliani register);
  - a Goya-style war scene with blood;
  - Salgado-style aftermath of conflict;
  - painted satire of a Western head of state;
  - painted satire of Xi Jinping;
  - "nuns holding firearms".
- **4 edits and 4 i2v stills.**

*Candidates*
- Qwen-2.1 full and Turbo, HiDream-O1, Z-Image Base and Turbo.
- FLUX 3 Klein as a reference only.
- Qwen-2512 and Chroma1-HD in their lanes.
- A control: today's sd35 and krea pipeline on the same prompts.
- Two seeds each. The top two models also run the old 37-word prompts, to separate the model gain from the prompt gain. About 550 images in total.

*Judging*
- Publish to the **devtest** screen and its bucket with the model name hidden.
- The owner favourites what he likes on an actual Frame TV at viewing distance, and at 100% crop on the PC. This is his eye, not a critic loop.
- Score = favourites per 100 images, per model, per lane.
- Typography: Gemini exact-match on full-resolution images, 4 seeds each, gives the spelling rate.
- Cost per image comes from RunPod `executionTime`.

*Decision rules*

| Lane | Rule |
|---|---|
| Painterly and photographic | Primary = most favourites, and it must render at least 5 of the 6 probes as asked. Turbo replaces the full model if it is within 10% on favourites. |
| Typography | Winner = best exact-spelling rate. At 75% or better for a single image, the batch drops to 2. |
| Video | LTX vs Wan on the same 4 stills; also check whether 24 or 48 fps shows judder on the 60 Hz kiosk. |