# Screen Machine gen-AI target design: ops-simplicity lens (17 Oct 2026)

## 1. Verdict: keep the pattern, rebuild the packaging

**Keep:** self-hosted ComfyUI on RunPod serverless, paid per second, scaling to zero.
- The workload is about 54 images a day on a schedule, with no interactive use, so it is bursty and not latency-sensitive. Serverless suits it.
- Self-hosting is still the only setup with no provider-side classifier. fal blacks out flagged images unless the account is authorised. BFL, Ideogram and Krea require filters by licence.
- Moving to another host would add work without making anything simpler:
  - Koyeb's terms are cleaner and its PRO 6000 is about 37% cheaper, but it needs a $29/mo plan and has no cached models.
  - Beam has no PRO 6000 serverless and bills CPU and RAM on top of the GPU.

**Rebuild:** the setup is about 1.5 generations behind and costs more than it should.
- **Endpoints: 6 → 2** (`sm-image` and `sm-video`). Both run the same image.
- **Worker:** drop the fork for the official `runpod-workers/worker-comfyui`, built unmodified with build args. Our layer is 3 small files with **zero custom nodes**: every graph below uses core ComfyUI nodes only.
- **Weights leave the image.** Each endpoint gets one private Hugging Face "bundle" repo through RunPod Cached Models. Downloads are not billed, the files sit on the host's NVMe, and the endpoints are no longer pinned to one datacentre.
- **Precision:** fp8 everywhere on a cu128 stack. NVFP4 and cu130 are not worth the driver-gating trouble at this volume.
- **"Add a model" goes from about 10 steps to 3:**
  1. Upload the files to the bundle with `hf upload`.
  2. Drop a ComfyUI API graph into `routes/workflows/`.
  3. Add one catalogue entry in `src/data/workflows.json`.

  A worker rebuild is needed only when a new architecture needs a newer ComfyUI. That push is about 10 GB with no weights, so minutes rather than 2.5 hours.

**One rule that keeps things simple: only Apache-2.0 or MIT models go into production.** FLUX.2, FLUX 3 Klein, Krea 2 and Ideogram 4 all require content filters or human review, and forbid getting around them. Using them means building and maintaining a filter, which is an extra component and goes against the brief. Apache and MIT models require nothing.

## 2. Model lineup per lane

| Lane | Model | Licence | Expression posture | Weights / VRAM | GPU |
|---|---|---|---|---|---|
| **Default: painterly, surreal, typography** | **Qwen-Image-2512** (20B MMDiT) | Apache-2.0 | No runtime checker. Pretraining was NSFW-filtered, so nudity is possible but anatomy may be soft. Strong at rendering text. | fp8 about 20 GB, plus Qwen2.5-VL-7B fp8 at 9.4 GB. Peak 46 GB at bf16 (RunPod benchmark); about 32 GB at fp8 (my estimate). Native 16:9 is 1664×928. | PRO 6000 |
| **Photographic, and the least-inhibited lane** | **Z-Image Base** (CFG and negatives), with Turbo for drafts | Apache-2.0 | No checker; the community calls it uncensored. The first-party report says pretraining was NSFW-scored, so test it. | 6B, about 12 GB, plus Qwen3-4B; fits in 16 GB. About 2 MP native. | PRO 6000 / L40S |
| **Edit and multi-reference** (adapt, combine, satire from a reference photo) | **Qwen-Image-Edit-2511** + Lightning 8-step LoRA | Apache-2.0 | No filter. **No NSFW LoRA on this lane, ever** (SOA s.66I). | fp8 20.5 GB plus the **same** encoder and VAE as 2512, so one family covers both lanes. AA edit Elo 990 vs Kontext's 884. Takes 1–3 references natively, so the ImageStitch hack goes. | PRO 6000 |
| **Upscale to 3840×2160** | Painterly: **4x-UltraSharpV2** (DAT2), then a Lanczos downscale. Photographic: **SeedVR2-7B fp8** (in ComfyUI core). | CC-BY-NC-SA (fine for private use); Apache-2.0 | n/a | 140 MB; 12–16 GB | same worker |
| **Video** (i2v and first-to-last frame) | **Wan 2.2 I2V-A14B** + lightx2v 4-step LoRAs → SeedVR2-3B to 2560×1440 → RIFE 4.26 ×2 → **core `SaveVideo`** | Apache-2.0 | No filter, and the largest LoRA ecosystem | fp8 about 2×14 GB plus umt5 fp8 | PRO 6000, then H100 |
| **Bake-off only** | Qwen-Image-2.1 (+Turbo); HiDream-O1 | Qwen Research ("research or evaluation" only; a bake-off is evaluation); MIT | 2.1 has no filter clause and no acceptable-use policy | 2.1: 7B, about 17 GB int8, native 2752×1536 | eval endpoint |

**Retired:** SD3.5 Large, SDXL, FLUX.1-dev, FLUX.1-Krea, FLUX.1-Kontext, Wan 2.1, RealESRGAN (including the mislabelled "x4"), the Heartsync LoRA and `sldr_flux_nsfw_v2`.

**Excluded:**
- FLUX.2 [dev] and FLUX 3 Klein, Krea 2, Ideogram 4: their licences require filtering or review and forbid circumvention.
- HunyuanImage 3 and HunyuanVideo 1.5, MiniMax H3: their licences exclude the UK, including *display* of output.
- LTX-2.5: its binding AUP bars graphic violence, shaming or humiliating individuals and impersonation, and Lightricks keeps the right to restrict use remotely. Its 4K first-to-last-frame mode is also not a stock ComfyUI graph.

**Qwen-Image-2.1 is the owner's decision, not mine.** It is #1 among open models (AA 1042; edit 1083). On its own it would replace both 2512 and Edit-2511 at 2752×1536, which needs only a 1.4× upscale. That would be the simplest lineup of all. Its licence says "research or evaluation purposes only", which does not obviously cover a home gallery. The design makes it a one-entry swap if the owner accepts that reading or Alibaba relicenses it, as it did for 2512.

**The-beast (typography):** Qwen's text rendering should let the batch drop from 4 to 2. Keep the existing vision selector as the check.

## 3. Hosting and deployment architecture

**Image build (`worker/build.sh`, new, in the screen-machine repo so the app and worker change together):**
1. `docker build https://github.com/runpod-workers/worker-comfyui.git#5.11.0 --target base --build-arg COMFYUI_VERSION=0.41.0 -t gjbm2/sm-worker-base:5.11.0-c0.41.0`
   - The 5.11.0 tag is not on Docker Hub, but building the tag ourselves gets the workflow model pre-flight.
   - ComfyUI 0.41.0 also runs Qwen-2.1 and FLUX 3 if they are ever wanted.
   - torch stays cu128 as upstream pins it.
2. `worker/Dockerfile`: `FROM` the base above, then `COPY extra_model_paths.yaml start-bundle.sh`. `start-bundle.sh` symlinks `/runpod-volume/huggingface-cache/hub/models--gjbm2--sm-*-bundle/snapshots/<newest>` to `/bundle`, then execs upstream `/start.sh`.
3. Push with an immutable tag, e.g. `gjbm2/sm-worker:2026-10-18`. No `LIVE_PATCH`, `AWS_SYNC`, `COPY_*` or progress-relay IP.

**Endpoints:**

| | `sm-image` | `sm-video` |
|---|---|---|
| Cached model | `gjbm2/sm-image-bundle` (private HF; ComfyUI folder layout; only the files the graphs use; about 75 GB) | `gjbm2/sm-video-bundle` (Wan 2.2 experts, LoRAs, umt5, VAE, SeedVR2-3B, RIFE; about 45 GB) |
| GPU priority | RTX PRO 6000 96 GB → L40S/6000 Ada 48 GB → H100 | PRO 6000 → H100 → H200 |
| Workers | 0/2, idle 5 s, scaler QUEUE_DELAY 45 s | 0/1, idle 5 s |
| Exec timeout | 600 s | 1800 s |
| Other | FlashBoot on, no network volume, S3 keys as RunPod secrets | same |

**Cold starts, without paying for idle time:**
- One image endpoint takes all the traffic, about 2.3 jobs an hour instead of a trickle per model. That raises the chance of a FlashBoot resume, which takes about 200 ms with weights still in VRAM.
- The 45 s queue delay means jobs that land together (north and south from the same cloud event, the beast's parallel batch) **queue onto one warm worker** instead of starting 2–4 cold ones.
- fp8 weights read from host NVMe load in roughly 10 s, compared with fp16 on H100 today.

**Outputs:**
- Outputs go to the existing S3 bucket through the worker's `BUCKET_*` settings and come back as `output.images[]` with `type: s3_url`.
- Video must end in core `SaveVideo`. The official handler drops VideoHelperSuite (`VHS_VideoCombine`) output.
- Every graph's last node is a fixed 3840×2160 resize. That removes the `maxwidth`/`locals()` bug, so the-beast no longer publishes 5120×2880.

**Progress:** drop the worker → house :8765 relay. The overlay shows Queued, Working and Finalising from the RunPod `/status` call the app already makes every 0.5 s. That leaves no inbound WAN dependency.

**Fallback if the cached-model bundle path fails the spike:** bake each bundle into a `FROM sm-worker` layer. That is still one image per endpoint and two endpoints, just with slower pushes.

**Platform terms:** RunPod's ToS bans "pornography or graphic adult content" and content that "ridicule[s], mock[s]". That wording is boilerplate copied across several hosts, and there is no evidence it is enforced on private serverless outputs. Koyeb is the documented way out if that changes: the same container, run as plain ComfyUI behind HTTP. Do not build it now.

## 4. Prompt and LLM layer

- **Keep `utils.llm` and the `config/llm.yaml` `prompt` and `alexa` tasks** (live since dc215fcf today).
  - Chain: `gemini-3.1-flash-lite` → `gemini-3.8-flash` → `gpt-6-luna`. Check it is in `models.list` before adding it; it replaces `gpt-4o`, which retires 14 Jan 2027.
  - Gemini's adjustable safety filters default to Off on 2.5 and later, so no `safety_settings` are needed. Child-safety blocking is fixed, which is fine.
  - No Chinese-lab LLM in the chain, because of satire.
- **Delete the jailbreak and pretext text.** Replace it with a 6-line honest brief:
  - this is a private adult art gallery;
  - tasteful nudity, political satire and artistically motivated violence are in scope;
  - nudity is for fictional adults only;
  - real people appear only in non-sexual and non-photoreal-intimate contexts. This is the law under SOA s.66E/F, stated as an instruction, not a gate.
- **Drop the 75-token cap.** Qwen and Z-Image use LLM text encoders, so target 60–150 words of natural language.
- **Degrade, never refuse:** if every model in the chain fails, generate from the unrefined prompt rather than raising.
- **Audit trail:** write `prompt_in`, `prompt_out` and `llm_model` to the sidecar so silent softening shows up.
- Flag to show_service's owner: the `describe` task uses `gpt-4.1-mini`, which retires on the same day.

## 5. App-side changes

| File | Change |
|---|---|
| `routes/generate.py` | Output adapter: `url = out.get("message") or out["images"][0]["data"]`, with an mp4 extension when the result is animated. Read `timeout_s` from the catalogue instead of the hard-coded 1200 at :540. **Call `job.cancel()` on timeout** at :710-718. Change `sampler` → `sampler_name` at :439 and `NEGATIVE-PROMPT` → `NEGATIVE_PROMPT` at :427. |
| `routes/generate_handler.py` | Cost = RunPod `executionTime` × the endpoint's rate from the catalogue. Delete the GraphQL pricing lookup that returns 400 on every job (:24-213). Fall back to the raw prompt when the LLM fails (around :518). Write the sidecar fields from §4. |
| `src/data/workflows.json` | Replace the 9 entries with about 7: `qwen2512`, `zimage`, `qwen-edit`, `upscale`, `wan22-i2v`, `wan22-flf`, and an `eval-*` entry while testing. Two `runpod_id`s, `timeout_s`, and `style_guidance` for the router. |
| `routes/workflows/` | Add new graphs built from ComfyUI templates with the existing `{{…}}` placeholder titles. After retirement, delete `kreav2`, `krea`, `sd35`, `sdxl-scale`, `flux1-dev-scale-l`, `flux_1_kontext_*` and `wan_*`. |
| `routes/data/refiner-enrich.txt.j2` | Remove the token cap (:16, :85, :96), the routing lines (:33-36) and the censorship block (:60-77). |
| `refiner-adapt.txt.j2` (:3-19, :188, :218-223), `alexa-adapt.txt.j2` (:3-8), `refiner-gallerist.txt.j2` (:27, :44-47), `refiner-pictureeditor.txt` (:32-35), `refiner-tabloidpictureeditor.txt` (:70-74), `refiner-image-to-video.txt` (:30), the FTL refiner (:40), `refiners.json` (:63, :88) | Update model ids and routing; remove the pretext text. |
| `routes/scheduler/north-screen.json`, `south-screen.json` | Delete the `_auto_upscale` trigger (14), since the graphs now output 4K. |
| `routes/scheduler/the-beast.json:98` | Batch 4 → 2. |
| `config/llm.yaml` | The chain from §4. |
| `src/components/prompt-form/usePromptForm.ts:74-83` | `parameters` → `params`. Ships with the frontend deploy. |
| `routes/manage_jobs.py` | Prune job ids on completion. |

This adds no new local servers, ports or Spectre cards. `start_model_server.sh` (local :54893) is retired.

## 6. Migration plan (each step ships on its own)

1. **App hygiene, no new infrastructure:**
   - Hide the Kontext, Wan and upscale catalogue entries, which point at endpoints with workersMax 0.
   - Delete `_auto_upscale`.
   - Add cancel-on-timeout.
   - Remove the pretext text and the token cap; add the LLM fallback to the raw prompt.
   - Batch the Python changes into one Flask restart through Spectre, and ping 2f first because room_service runs inside Flask.
2. **Worker image:** `worker/build.sh` and push. About 10 GB, minutes.
3. **Spike `sm-image` with a minimal bundle** (2512 + encoder + VAE + UltraSharpV2):
   - one job each, cold and warm;
   - confirm the bundle path mapping, and whether an updated bundle revision reaches the workers.
   - If either fails, switch to the bake-in fallback.
4. **App output adapter,** then add `qwen2512` alongside the old entries. Nothing is switched yet.
5. **Bake-off** (§8): a temporary `sm-eval` endpoint and bundle for Qwen-2.1 and HiDream-O1, deleted afterwards.
6. **Cut over text-to-image:**
   - point the catalogue `default` and the refiner routing at the winners;
   - fill the rest of the `sm-image` bundle (Z-Image, SeedVR2-7B);
   - watch for 2 days.
7. **Edit lane:** `qwen-edit` replaces Kontext 1pic/2pic and is re-enabled for Alexa.
8. **Video:** set up the `sm-video` endpoint and bundle with the Wan 2.2 graphs, then turn on `animation_on` at whatever rate the owner chooses.
9. **Retire** after a week with the old endpoints at workersMax 0 as rollback (rollback = repoint `workflows.json`):
   - the 6 old endpoints, `1w6vje8cn33ift` and its orphan template;
   - network volume `gmaygtw1z4`;
   - the `dev-*` image tags;
   - the `runpod-worker-comfy` repo (archive it);
   - the NSFW LoRAs, RealESRGAN, and the local model server.

## 7. Monthly cost estimate

These are estimates, not measurements. The bake-off reads real billed seconds.

| Item | Basis | $/month |
|---|---|---|
| Stills, current mix (54 jobs/day ≈ 1,650/mo) | about 45 s billed per job (cold load ~10 s, generate 15–20 s, upscale 4–20 s, upload) × PRO 6000 at $0.00097/s | **$72** (range $48–112) |
| Stills with beast batch at 2 (≈ 1,350 jobs/mo) | as above | **$59** (range $39–92) |
| Gemini prompt work | about 90 calls/day on flash-lite | about $4 |
| Hugging Face private storage, ~120 GB | not verified; may need HF PRO | $0–9 |
| Network volume retired | | −$3.50 |
| **Subtotal, images** | | **about $60–100**, against $110 today ($0.068/image) |
| Optional video, 5/day (150/mo) | Wan 2.2 4-step 720p + SeedVR2-3B to 1440p + RIFE, about 200–300 s × $0.00097 | **+$30–45** |
| Optional 4K finishing for chosen clips only | SeedVR2 at 4K adds roughly 10 min per clip | about $0.60 per clip |

At 5 videos a day the total is about $90–145/month. About 3 videos a day keeps it under $110. The bake-off itself costs about 500 jobs, roughly $25.

## 8. Top risks and how to reduce them

1. **The cached-model bundle is untested.** Path mapping, refresh on a bundle update, and the lack of revision pinning are all unknown. *Reduce:* step 3 spike before anything depends on it. If a revision does not propagate, version the repo name (`sm-image-bundle-v2`), which is a one-field endpoint change. If the path mapping fails, bake the bundle into the image.
2. **Apache models could look worse than Krea 2 or Qwen-2.1 on the painterly work the owner favourites.** 2512's native 1664×928 needs about a 2.3× upscale. *Reduce:* the bake-off, including a variant with a 1.5× hires pass at 0.3 denoise; HiDream-O1 (MIT) as the second contender; Qwen-2.1 left to the owner's licence call.
3. **Nudity quality.** The 2512 lineage and Z-Image were NSFW-filtered in pretraining. *Reduce:* expression probes in the bake-off. If anatomy is doll-like, add one artistic-nude LoRA from a source with known provenance, **on the text-to-image lanes only**.
4. **ComfyUI 0.41 on cu128 torch.** The ComfyUI README recommends cu130, but that is guidance, not enforced. *Reduce:* the 5.11 pre-flight plus the spike. If it breaks, fall back to the stock `5.10.0-base` with ComfyUI 0.34.0, which runs the whole Apache lineup.
5. **SeedVR2 VRAM at 4K.** The chunking PRs are still open. *Reduce:* the PRO 6000's 96 GB, tiling, fp8, and 3B for video.
6. **RunPod's terms.** *Reduce:* outputs are private and the design is portable; Koyeb is the documented exit. Accept the risk for now.
7. **Real people.** The UK offences are s.66E/F (creating or requesting a photoreal intimate image) and s.66I (making a nudifier). *Reduce:* no NSFW LoRA on the edit path, and the refiner brief states the lines. No classifier is added, and the-beast's office gate stays as the owner set it.
8. **The 1440p video ceiling.** *Accept it:* 61% of the old 1440p clips were favourited. 4K finishing is opt-in per clip.

**Bake-off on the owner's own prompts:**
- **Prompts:**
  - 24 real final prompts from the last 30 days of `output/*-screen/*.json`;
  - 6 favourites: Turner harbour, Magritte bench, Munch's Krakatoa, Klimt, O'Keeffe, Dutch still life;
  - 4 the-beast phrases;
  - 8 expression probes: a Schiele-style nude oil study, a Goya-style war scene with blood, painted caricatures of a named Western head of state and of Xi Jinping, a violent mythological scene, and an edit-lane caricature from a reference photo of a public figure.
- **Contenders:** kreav2 and sd35 as today's baseline, Qwen-2512, Z-Image Base, Qwen-2.1, HiDream-O1. Upscaler variants: UltraSharpV2, SeedVR2 and the hires pass. 2 seeds each.
- **Method:** show them blind, full-screen on the actual TVs. The owner scores 1–5 and answers "would hang it". Log softening (`prompt_in` vs `prompt_out`) and the billed seconds per job.
- **Decision rule:**
  - the default is the best mean on painterly prompts plus favourites;
  - the photo lane is the best on photographic prompts;
  - any model that fails 2 or more of the 8 expression probes cannot be the default;
  - the beast batch drops to 2 only if at least 3 of the 4 phrases spell correctly at 1 image each.