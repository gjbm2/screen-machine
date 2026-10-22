# Gen-AI deployment pattern: audit and best practice (17 Oct 2026)

**Scope:** how and where the models run (deployment), not which models to pick. Model names below only matter for VRAM sizing and cost.
**Method:** I only read things. I read the local repos, read the RunPod REST API (GET only; no keys printed), and checked the web on 2026-10-17.
**Labels:** **VERIFIED** means a primary source (vendor docs or pricing page, GitHub, the Hugging Face API, the RunPod billing API). **REPORTED** means a secondary source.

## Summary table: the patterns for 150 images/day (2–4 MP, upscaled to 4K) plus 5 videos/day (5 s, 720p)

| Pattern | Est. $/month | Typical cold start | Can you turn off output filters? | Lock-in | Effort |
|---|---|---|---|---|---|
| **0. Today, unchanged** (RunPod serverless, one endpoint per model, fp16 weights, H100) | **~$440** (~$110 at today's ~50 images/day) | 30–90 s when no worker is warm | Yes. Nothing filters your own worker. | Low | None |
| **1. RunPod serverless rebuilt (recommended):** 2 endpoints, current ComfyUI, fp8 or NVFP4 weights, RTX PRO 6000 or 5090, FlashBoot, cached-model bundle | **~$120–200** | FlashBoot resume ~1–5 s; full start ~20–60 s | Yes | Low (same ComfyUI graphs) | Medium: one Dockerfile, endpoint settings, small app adapter |
| 2. Modal plus GPU memory snapshots | ~$95–170 after $30 free credit | ~1 min without snapshots; a few seconds with them (alpha) | Yes, technically. The terms ban "indecent or obscene" content. | Medium (Python-first) | High: re-platform |
| 3. Comfy API (Comfy Org managed, launched Sep 2026) | ~$285+ (plus plan fee) | Not published | Yes. Its terms are the narrowest of any provider here. | Medium–high (Comfy Cloud) | Low–medium |
| 4. Per-call hosted APIs for open models (fal, Runware, Replicate, DeepInfra) | ~$170–470 | ~0 (the provider keeps it warm) | **Depends on the provider.** fal needs account authorisation and blacks out flagged images. | Medium | Low, but limited to their graphs, max 2048 px, limited LoRAs |
| 5. Always-on dedicated pod | $857 (5090) to $1,793 (PRO 6000) | 0 | Yes | Low | Low |

**Recommendation:** keep the self-hosted, serverless, ComfyUI pattern. In Oct 2026 it is still the right shape for a low-volume, bursty, private and unfiltered art generator, and none of the alternatives is cheaper while giving the same freedom over content. The current *implementation* is badly out of date, though:

- It uses CUDA 11.8.
- The ComfyUI version is effectively 2025 and unpinned.
- Model weights are fp16 and baked into each image.
- There are six model-specific endpoints, and they are tied to one datacentre's volume.

Rebuilding it should cut the cost per image by roughly 2–4x and makes current models runnable at all.

---

## 1. Audit of the current setup (VERIFIED from the repos and the RunPod API)

**Worker repo** (`/home/gjbm2/dev/runpod-worker-comfy`; last commit `3c53ad7`, "Added Krea image", 2025-08-04):
- `Dockerfile` builds `FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04` and runs `comfy install --cuda-version 11.8` with no version pin. The snapshot JSON has `"comfyui": null`.
- ComfyUI's README now says *"Using a cu130 or above version of pytorch is required on Nvidia 20 series and above"*, and *"torch 2.7 is minimally supported"* ([README, Oct 2026](https://github.com/Comfy-Org/ComfyUI), VERIFIED). CUDA 11.8 cannot use RTX 5090 or PRO 6000 (Blackwell) cards, FP8 or NVFP4 kernels, or the 2026 model architectures.
- Weights are baked per `MODEL_TYPE`, in fp16. For example, FLUX.1-dev fp16 plus T5 in both fp16 and fp8, and Wan 2.1 14B fp16. That makes the images big, the pushes slow (the commit log says "Parallel push maybe finally ok...still slow"), and every cold start has to load the large weights into the GPU.
- `start.sh` has `LIVE_PATCH`. It runs `git reset --hard; git checkout v0.3.29` and `pip install -r requirements.txt` on ComfyUI **when the container starts**, and it is turned on in the sdxl and wan templates. `AWS_SYNC` and `COPY_MODELS` do S3 and volume copies at start-up (wan template). All of this is billed cold-start time and makes the container change at runtime.

**Endpoints** (REST `GET /v1/endpoints`):
- There are 7 endpoints. All have FlashBoot on, a 5 s idle timeout and min 0 workers.
- The GPU lists are mostly A100/H100 80 GB. flux and sd35 also allow 48 GB cards.
- Six of the seven mount network volume `gmaygtw1z4` (50 GB, **US-KS-2**). That pins those endpoints to one datacentre's GPU stock.
- `render-flux1-kontext` (oh422aycpybd5u) and `render-wan2` (wxd8n6sak3urv5) have **workersMax = 0**, so they cannot run at all. The Adapt, Combine, Upscale and Image-to-Video catalogue entries are dead: lifetime jobs are 84 for Kontext and 4 for Wan.

**Real spend** (REST `GET /v1/billing/endpoints`):

| Month | Spend |
|---|---|
| Apr 2026 | $71.8 |
| May | $0.2 |
| Jun | $45.9 |
| Jul | $9.1 |
| Aug | $81.2 |
| Sep | $110.9 (Krea 43%, SD3.5 42%) |
| 1–16 Oct | $53.3 (~$3.6/day, ~2,800 billed s/day) |

- The effective rate is **$0.00133/s**, which is exactly RunPod's H100 flex rate. The workers are landing on H100s.
- About 50–55 images/day (output sidecars, approximate) works out to roughly **50 billed GPU-seconds per image**, or about **$0.07 per image**.
- Sidecar wall-clock times (Aug–Oct) give a median of about 47 s and a p90 of 87–109 s for sd35 and krea.
- **Inference (not measured):** SD3.5L or Krea at about 1.5 MP plus ESRGAN takes about 10–15 s of GPU work on an H100. So the majority of billed seconds are probably cold start and model load.
- Schedules fire every 15/30/60/120 minutes, far longer than the 5 s idle timeout, so almost every job starts cold.

**App coupling:**
- `routes/generate.py` posts a ComfyUI API-format graph through `runpod.Endpoint(...).run()` and polls.
- It reads `output["message"]`, which is the **pre-5.0** worker output format. The official worker-comfyui ≥5.0 returns `output.images[]` instead ([README](https://github.com/runpod-workers/worker-comfyui), VERIFIED). Switching to the official worker needs a small adapter.
- The graphs themselves are portable: ComfyUI's OpenAPI spec still has `/api/prompt` alongside the new `/api/jobs` ([openapi.yaml](https://github.com/Comfy-Org/ComfyUI/blob/master/openapi.yaml), VERIFIED).

## 2. RunPod serverless in Oct 2026 (section A)

**Prices.** Serverless rates are per second, from the [pricing page](https://www.runpod.io/pricing) (fetched 2026-10-17, VERIFIED). The serverless list rates are what third parties call "flex":

| GPU | $/hr | $/s | Pod $/hr (Secure) |
|---|---|---|---|
| B200 180 GB | 8.64 | 0.00240 | 7.99 |
| H200 141 GB | 5.93 | 0.00165 | 5.29 |
| H100 80 GB | 4.79 | 0.00133 | 2.89–3.99 |
| **RTX PRO 6000 96 GB** | **3.49** | **0.00097** | 2.49 |
| A100 80 GB | 2.72 | 0.00076 | 1.79 |
| L40S / L40 / 6000 Ada / MIG-48 GB | 1.75 | 0.00049 | 1.09 |
| **RTX 5090 32 GB** | **1.58** | **0.00044** | 1.19 |
| A6000 / A40 48 GB | 1.22 | 0.00034 | 0.59 |
| RTX 4090 24 GB | 1.10 | 0.00031 | 0.89 |

- Active-worker discounts are now "via sales inquiry" ([serverless pricing docs](https://docs.runpod.io/serverless/pricing), VERIFIED).
- Third parties quote 20–32% off flex (REPORTED, [aliteq](https://aliteq.com/runpod-serverless-pricing-explained-2026)).
- At this volume an always-on active worker never pays back: one PRO 6000 is about $1,800/month.

**What is billed** ([docs](https://docs.runpod.io/serverless/pricing), VERIFIED):
- Cold start is billed "from when the worker starts until your handler is ready, mostly while models load into GPU memory".
- Image pulls and cached-model downloads while the worker is scaled down are not billed.
- Idle time is billed up to the idle timeout (default 5 s).
- Container disk is $0.10/GB-month. Network volumes are $0.07/GB-month.

**FlashBoot** ([blog, 14 Jul 2026, updated 10 Oct](https://www.runpod.io/blog/serverless-gpu-cold-starts-flashboot), VERIFIED, vendor claim):
- It pauses the container instead of stopping it, so VRAM and weights stay loaded. A resume takes about 200 ms.
- If the paused container was evicted or released, you get a full start: about 30 s to 2+ min, depending on image and model size.
- "Priority FlashBoot" (last to be evicted) is arranged through the account team.
- The 2023 launch data showed 95% of starts under 2.3 s on a small model ([blog](https://www.runpod.io/blog/introducing-flashboot-serverless-cold-start), VERIFIED, old).

**Cached models ("Model Store")** ([blog, 4 Aug 2026](https://www.runpod.io/blog/building-runpods-model-store); [docs](https://docs.runpod.io/serverless/endpoints/model-caching), VERIFIED):
- The cache is tiered: host NVMe, then a datacentre volume, then the origin. The scheduler prefers hosts that already hold the model. Download time is not billed.
- Gated and private Hugging Face repos work with a token.
- Limits: **one cached model per endpoint**. **All files in the repo are downloaded**, every quantisation included. It is Hugging Face only. Revision pinning is not documented. A private non-HF "Model Repository" is in beta.
- Implication: put exactly the ComfyUI files you need into one curated private HF "bundle" repo per endpoint, and point `extra_model_paths.yaml` at the snapshot directory. This is my design and is **untested**.

**Network volumes:**
- Volumes "restrict you to specific data centers" ([optimisation docs](https://docs.runpod.io/serverless/development/optimization), VERIFIED).
- Release notes ([VERIFIED](https://docs.runpod.io/release-notes)):
  - High-Performance Network Volumes, Jun 2026.
  - Global volumes for Serverless, beta, 8 Oct 2026 (cannot be combined with a network volume).
  - MIG slices on H100 and PRO 6000, May 2026.
  - Async jobs (May), Batch Jobs (beta, 19 Aug).
  - Runpod SDK 2.0 with Flash folded in (15 Oct). Flash is "serverless functions, no Docker", GA since April.
- RunPod's own ranking for weights: cached models first, then baked into the image (for private weights), then a network volume.

**Official worker-comfyui:**
- Latest release is 5.11.0 (21 Sep 2026); it adds a workflow pre-flight check that reports missing models before queueing ([releases](https://github.com/runpod-workers/worker-comfyui/releases), VERIFIED).
- `main` Dockerfile: `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`, `COMFYUI_VERSION=0.34.0`, torch **2.11.0+cu128**. torch is deliberately pinned to cu128 because "a cu13 torch fails CUDA init" on RunPod hosts with driver 570/575 ([Dockerfile](https://github.com/runpod-workers/worker-comfyui/blob/main/Dockerfile), VERIFIED).
- ComfyUI 0.34.0 is 7 releases behind 0.41.0 (15 Oct 2026). FLUX 3 [klein] local support arrived only in **v0.41.0**, and Qwen-Image-2.1 fixes and compile landed in v0.38–0.39 ([ComfyUI releases](https://github.com/Comfy-Org/ComfyUI/releases), VERIFIED). So override `COMFYUI_VERSION` when building.
- RunPod recommends `FROM runpod/worker-comfyui:<ver>-base` plus `comfy-node-install` and `comfy model download`, rather than forking ([customization.md](https://github.com/runpod-workers/worker-comfyui/blob/main/docs/customization.md), VERIFIED). The current repo is a fork, which is the pattern RunPod now advises against.

## 3. Alternatives (section B)

| Platform | Key price | Cold start / features | Content terms | Notes |
|---|---|---|---|---|
| **Modal** | PRO 6000 $0.000842/s, H100 $0.001097/s, L40S $0.000542/s, plus CPU and RAM; $30/month free; region pinning 1.15–1.75x ([pricing](https://modal.com/pricing), VERIFIED) | GPU memory snapshots are **alpha** and "don't speed up loading weights from storage" ([docs](https://modal.com/docs/guide/memory-snapshot), VERIFIED). Modal's own SD3.5-L example: "about one minute to cold start" (VERIFIED) | Bans "indecent or obscene material", s.1.4(c), terms May 2026 ([terms](https://modal.com/legal/terms), VERIFIED) | Python-first; ComfyUI is possible but you build it |
| **Comfy API** (Comfy Org) | PRO 6000 $4.54/hr, H100 $6.23, H200 $7.71, B200 $11.23; storage $0.20/GB-month; worker concurrency 2 on Standard/Creator ([page](https://comfy.org/platform/comfy-api/), VERIFIED) | Builder packages nodes and models; `comfy build` / `comfy deploy` shipped in comfy-cli v1.20–1.22 (Sep 2026) and run on other GPU providers, RunPod named ([releases](https://github.com/Comfy-Org/comfy-cli/releases), VERIFIED). Cold start not published | Bans only CSAM, NCII, "synthetic media designed to impersonate a real individual", and incitement ([ToS](https://comfy.org/terms-of-service), VERIFIED) | ~30% above RunPod per GPU-second; new product |
| **fal Serverless** (own apps) | H100 list $4.50, from $2.49/hr (REPORTED) | `keep_alive`, `min_concurrency`; **needs approval** from fal ([docs](https://fal.ai/docs/serverless), VERIFIED) | AUP bans "sexually explicit content", "gratuitously violent content, extreme gore" ([AUP](https://fal.ai/legal/acceptable-use-policy), VERIFIED) | Poor fit for your content priorities |
| **Replicate** (Cloudflare) | L40S $0.000975/s, H100 $0.001525/s ([pricing](https://replicate.com/pricing), VERIFIED) | Private models bill setup and idle time | n/a | Expensive for private Cog models |
| **Beam** | H100 $0.000972/s, 5090 $0.000303/s, 4090 $0.000192/s; cold start not billed ([pricing](https://www.beam.cloud/pricing), VERIFIED) | 2–4 s claimed (REPORTED, competitor source) | not checked | Cheapest 5090 serverless found |
| **Cerebrium** | H100 $0.000944/s, PRO 6000 $0.000694/s, L40S $0.000542/s; Standard plan $100/month ([pricing](https://www.cerebrium.ai/pricing), VERIFIED) | Snapshotting claimed | not checked | |
| **Salad / Vast** | 5090 $0.25–0.50/hr (Salad, [pricing](https://salad.com/pricing), VERIFIED); Vast serverless at instance rates (REPORTED) | Vast ComfyUI cold start ~2 min (REPORTED) | Community hosts | Consumer hardware; use only for batch |
| RunComfy, ViewComfy, ComfyDeploy | RunComfy H100 API ~$7.49/hr pay-as-you-go (REPORTED) | n/a | n/a | Markups on top of GPU cost |

## 4. Hosted per-call APIs for open models, and their filters (section C)

| Provider | Price | Filter control |
|---|---|---|
| **fal** | FLUX.2 [dev] $0.012/MP; Qwen-Image $0.02/MP; Wan 2.2 A14B i2v $0.08 per video-second at 720p; SeedVR upscale $0.001/MP. Sizes 512–2048 px | Schema text (VERIFIED from fal's OpenAPI, 2026-10-17): *"Disabling it requires account authorization; unauthorized requests are always checked, and images flagged as unsafe are returned as black images."* The checker is on by default for FLUX.2 and Qwen. fal also runs OpenAI Omni moderation ([T&S](https://fal.ai/legal/trust-and-safety), VERIFIED) |
| **Replicate** | n/a | `disable_safety_checker` (default false) is exposed on FLUX.2-dev and Qwen-Image ([schema](https://replicate.com/black-forest-labs/flux-2-dev/api/schema), VERIFIED) |
| **Runware** | FLUX.2 [dev] $0.009 at 1 MP/20 steps, max 2048 px ([docs](https://runware.ai/docs/models/bfl-flux-2-dev), VERIFIED); Qwen-Image-2.1-Turbo $0.0141; LTX-2.5 Fast $0.09/s at 720p ([pricing](https://runware.ai/pricing), VERIFIED) | `checkContent` toggle. Terms (29 Sep 2026) ban replicating "the identity, likeness... of a living person without their consent" in training or customisation, and reserve the right to "disable... any of your Outputs" ([terms](https://runware.ai/terms), VERIFIED). BFL-hosted models add `safetyTolerance` 0–5 |
| **DeepInfra** | FLUX.2-dev $0.01 × MP × steps/28 | (REPORTED) |

These are good for occasional use. They do not fit as the main path: filters are opt-out at best, there is no custom ComfyUI graph or LoRA stack, outputs are capped at 4 MP, and the terms are tighter on likeness and explicit content.

## 5. Inference optimisations that change the economics (section D)

- **FP8** works on Ada and Hopper and needs no changes beyond loading fp8 weights. **NVFP4** (Blackwell: 5090, PRO 6000, B200) gives "up to ~2x vs fp8/bf16", **but only with PyTorch cu130**; without it, sampling can be up to 2x *slower* than fp8 ([Comfy blog, 9 Jan 2026](https://blog.comfy.org/p/new-comfyui-optimizations-for-nvidia), VERIFIED).
  - Independent check: LTX-2.3 on a 5090 went from 540 s to 278 s with NVFP4, with quality loss in text-to-video ([zenn](https://zenn.dev/toki_mwc/articles/rtx5090-nvfp4-quantization-reality?locale=en), REPORTED).
  - Conflict to resolve: RunPod pins cu128 for driver compatibility (section 2). Using NVFP4 on RunPod means building a cu130 image **and** restricting the endpoint to hosts with CUDA ≥13.0 drivers. Whether enough such hosts exist is unverified.
  - Vendors now publish NVFP4 weights directly, e.g. FLUX 3 [klein] `variants/nvfp4` at 5.3 GB vs 11.1 GB in bf16 ([HF API](https://huggingface.co/black-forest-labs/flux-3-image-klein), VERIFIED).
- **ComfyUI's native quantisation** (QUANTIZATION.md) covers fp8, nvfp4, mxfp8, int8, convrot_w4a4 and w4a8. It also has comfy-kitchen attention (v0.32), multi-threaded loading from disk (v0.23), fast-disk auto-detection (v0.37) and dynamic VRAM. Some users report reload regressions with symlinked models or HDDs ([issue #14276](https://github.com/Comfy-Org/ComfyUI/issues/14276), REPORTED). **Nunchaku** seems stalled (last tag v1.3.0dev20260306, VERIFIED), so native ComfyUI quantisation is the safer route.
- **Distilled / turbo models:** Qwen-Image-2.1-Turbo runs in 8 steps vs 40 (9 Oct 2026, [comfyui-wiki](https://comfyui-wiki.com/en/news/2026-10-09-qwen-image-2-1-turbo), REPORTED). The FLUX.2/3 klein models run in 4 steps. For video, lightx2v has 4-step turbos for MiniMax-H3 and Wan 2.2 (LightX2V 0.6.0, 12 Oct 2026, VERIFIED). These cut GPU time about 5x; whether they meet gallery-quality standards is for the model review.
- **Benchmarks** ([RunPod, 10 Sep 2026](https://www.runpod.io/articles/guides/best-gpu-for-comfyui), VERIFIED, vendor, pod prices):
  - FLUX.2 klein 9B: 1.51 s on a PRO 6000, 1.47 s on an H100.
  - Qwen-Image 2512 bf16: 18.0 s on a PRO 6000, 12.7 s on an H100, peak 46 GB VRAM.
  - The PRO 6000 is about the cheapest per image among cards that can hold flagship models.
- **One worker for all models vs one endpoint per model:** jobs here are ≥15 min apart, so each endpoint is cold for nearly every job either way. Consolidating to **2 endpoints** (image, video) gives:
  - one image to maintain;
  - more chances of a FlashBoot resume;
  - on a 96 GB card, 2–3 fp8 image models can stay resident, and ComfyUI's RAM cache handles swaps;
  - the 7 GB+ upscaler and text encoders are shared.
- **App-side lever:** fire north and south on the same tick and send both prompts in one job, so they pay one cold start.

## 6. ComfyUI itself (section E)

ComfyUI is still the right orchestration layer:
- It is the first runtime to support new open models; FLUX 3 klein was supported locally two days after release.
- It now has an OpenAPI 3.1 spec, a jobs API, `--offline` and `--disable-partner-nodes` (v0.39).
- Your 20+ API-format graphs and the LLM router keep working.

For reproducibility, pin `COMFYUI_VERSION`, pin node commits via `comfy-node-install`, and keep weights versioned in the HF bundle.

Plain diffusers on FastAPI or Flash would be leaner, but it means rewriting every workflow for no gain in quality. InvokeAI and SwarmUI are UIs, not serverless backends.

## 7. Cost model (150 images + 5 videos per day; 30 days = 4,500 images and 150 clips)

| Pattern | Image math | Video math | Total |
|---|---|---|---|
| 0. As-is | 4,500 × 50 s × $0.00133 = **$300** | 150 × ~600 s (Wan 2.1 fp16, H100; estimate) × $0.00133 = **$120** | **~$420–440** |
| 1a. Rebuilt, PRO 6000 | 4,500 × ~35 s (≈12 s load, 15 s generation at 2–4 MP fp8, 5 s upscale, overhead) × $0.00097 = **$153** | 150 × ~300 s (14B, 4-step turbo, plus interpolation and upscale) × $0.00097 = **$44** | **~$200** |
| 1b. Rebuilt, 5090, NVFP4/turbo models | 4,500 × ~40 s × $0.00044 = **$79** | on PRO 6000 **$44** | **~$125** |
| 2. Modal | as 1a at ~$0.00104/s all-in | | ~$200 − $30 credit |
| 3. Comfy API | as 1a at $0.00126/s = $198 | $57 | ~$255 + storage (150 GB ≈ $30) + plan |
| 4. Hosted per-call | $0.025–0.09 per image (turbo to 4 MP flagship, plus ~$0.008 upscale) → $110–400 | 5 s × $0.08–0.09 → $60–68 | **~$170–470** |
| 5. Always-on pod | | | $857 (5090) / $1,793 (PRO 6000) |

The 35–40 s per-image figures for patterns 1–3 are my estimates: model-load times for fp8/NVFP4 checkpoints plus RunPod's published benchmarks scaled to 2–4 MP. They are not measured. **Benchmark first:** run 20 cold and 20 warm jobs per model, then read billed seconds from `/v1/billing/endpoints`.

## 8. Target architecture (concrete)

1. **New worker repo** (replacing the fork):
   - `FROM runpod/worker-comfyui:5.11.0-base` with `COMFYUI_VERSION=0.41.x` (or your own cu128/cu130 base).
   - Pinned custom nodes: VideoHelperSuite, Frame-Interpolation, fitsize, plus whatever the new models need.
   - Remove `LIVE_PATCH`, `AWS_SYNC` and `COPY_*`.
2. **Two endpoints:**
   - `image`: 2–3 image models, an edit model, the upscalers. GPUs in order: PRO 6000, L40S/6000 Ada, H100.
   - `video`: PRO 6000, then H100.
   - FlashBoot on, idle timeout 5–15 s, workersMax 2–3, `minCudaVersion` set to match the image.
3. **Weights** as fp8, or NVFP4 on Blackwell. Ship them as one private HF bundle per endpoint through RunPod cached models. Fall back to baking them in if path mapping fails. **Detach the US-KS-2 volume** so the endpoints can use every datacentre's GPU pool.
4. **App changes:** parse worker ≥5 output (`output.images[]`, S3 URLs still supported), re-point `workflows.json`, and co-schedule screens so they share cold starts.
5. Optionally, add a per-call fallback (Runware or Replicate with the checker off) for a closed model you want to sample. Keep it off the main path.

## 9. Content terms and legal lines (factual)

- No provider-side output classifier runs on your own RunPod, Modal or Comfy worker. Hosted APIs do run one by default (section 4).
- **On paper**, RunPod's ToS (24 Mar 2026) lists "pornography or graphic adult content" as unauthorised content "submitted to the Service". It also requires content to be "not obscene, lewd, ... violent", and to carry consent from "each and every identifiable individual". It also says "Runpod will not actively monitor Content" ([ToS](https://www.runpod.io/legal/terms-of-service), VERIFIED). Tasteful nudes and satire probably sit outside "pornography", but the wording is broad. Comfy Org's terms are the narrowest.
- **Hard legal lines:**
  - CSAM, everywhere.
  - In the UK, **creating** a sexually explicit "purported intimate image" of a real adult without consent has been an offence since **6 Feb 2026** (DUA Act 2025 s.138, inserting SOA 2003 ss.66E–H; [SI 2026/31](https://www.legislation.gov.uk/uksi/2026/31/made), VERIFIED).
  - Non-sexual satire of public figures is not covered by that offence; defamation law still applies.

## What I could not verify

- Per-GPU active-worker rates on RunPod; the FlashBoot resume rate on your endpoints; how many RunPod serverless hosts run CUDA ≥13 drivers.
- Whether a cached-model HF "bundle" works with ComfyUI path mapping, including on gated repos.
- Measured 2–4 MP timings for FLUX.2 [dev], Qwen-Image-2.1 and FLUX 3 klein on a PRO 6000 or 5090; Wan 2.2, LTX-2.5 and MiniMax-H3 720p clip times. All of these are estimated above.
- Comfy API plan prices, its cold starts, and which GPU provider it runs on; fal private-app filtering; Runware's `checkContent` default; Replicate per-image prices; Vast and ComfyDeploy current terms; Modal snapshot behaviour with ComfyUI.
- Exact images/day: I used output sidecar counts, which undercount if files are overwritten.
- Exactly which board version currently has Qwen-Image-2.1 vs Ideogram 4 leading Artificial Analysis's open-weights ranking ([AA](https://artificialanalysis.ai/image/leaderboard/text-to-image?open-weights=true), REPORTED).

**Files reviewed** (all read-only):
- `/home/gjbm2/dev/runpod-worker-comfy/Dockerfile`
- `/home/gjbm2/dev/runpod-worker-comfy/src/start.sh`
- `/home/gjbm2/dev/runpod-worker-comfy/docker-bake.hcl`
- `/home/gjbm2/dev/screen-machine/routes/generate.py` (lines ~495–560, 814)
- `/home/gjbm2/dev/screen-machine/src/data/workflows.json`
- `/home/gjbm2/dev/screen-machine/runpod_jobs.log`