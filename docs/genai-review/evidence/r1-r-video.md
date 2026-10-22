# Open-weight video generation, October 2026: options for Screen Machine's i2v and first-to-last-frame art loops

## Bottom line

- **Upgrade now. Wan 2.1 is a generation behind.** The open-weights leaders today are MiniMax H3 (released Jul/Aug 2026), MAGI-2 Preview (Aug 2026) and LTX-2.5 (Aug 2026). None of them existed when the app was built.
- **Alibaba stopped publishing weights after Wan 2.2.** Wan 2.5, 2.6, 2.7 and 3.0 are API-only.
- **The best open model, MiniMax H3, isn't licensed for the UK.** The owner's domains (.co.uk) suggest he is UK-based. HunyuanVideo 1.5 has the same UK exclusion.
- **Recommended pair:**
  - **LTX-2.5** as the primary 4K engine. It renders 3840×2176 directly, supports keyframe and first/last-frame (FLF) conditioning, and adds a temporal upscaler to 48 fps.
  - **Wan 2.2 I2V-A14B** with lightx2v 4-step LoRAs and SeedVR2 or FlashVSR upscaling, as the engine with the most freedom of expression.

## What runs today (checked locally)

The workflows `routes/workflows/wan_image_to_video_upscale_slow.json` and `wan_ftl_image_to_video_upscale_slow.json` run:
- Wan 2.1 i2v / flf2v 720p 14B fp16, 30 steps of uni_pc, cfg 5, 1280×720.
- 40 frames for i2v and 31 for first-to-last.
- FILM frame interpolation ×8, then RealESRGAN ×2 frame by frame.
- Output at 24 fps.

The latest clip, `output/south-screen.mp4`, is **2560×1440, 24 fps, 12.04 s, 289 frames**. It is 1440p, not 4K: the TV upscales the rest. FILM ×8 on about 16 fps source slows motion roughly 3.3×, and per-frame GAN upscaling tends to shimmer.

The worker image is `FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04`. That cannot run the newer quantised builds (FP8/NVFP4 kernels on Blackwell need CUDA 12.8+), and LTX-2.5 needs CUDA ≥12.7 and PyTorch 2.7.

Video is rarely used: only 4 of the 21,374 lines in `runpod_jobs.log` are on the Wan endpoint `wxd8n6sak3urv5`. So cost per clip hardly matters; quality and licence do.

## Summary table

"Rank" is the Artificial Analysis (AA) image-to-video board, AA-Video-I2V v1.1, which scores video with audio. I read it on 17 Oct 2026. Kling 3.0 is fixed at Elo 1000 as the anchor.

| Model (release) | Open weights / licence | Usable in the UK? | Resolution / length | i2v / FLF | AA I2V Elo | Weight / compute | Content behaviour |
|---|---|---|---|---|---|---|---|
| **MiniMax H3** (weights 3 Aug 2026) | Yes. MiniMax H3 Community Licence | **No: UK, EU, US and Korea are excluded** | 768p short side locally (the 2K stage is API-only); 4–15 s at 24 fps; stereo audio | Yes / **Yes (FL2VA)** | **1140**, #1 open, level with Seedance 2.5 (1138) | 33B dense (≈20B needed at inference); about 8×H200 at 50 steps; 8-step Turbo LoRA exists | No filter in the weights. The hosted Context-IR and 2K stages are moderated |
| **LTX-2.5** (11 Aug 2026) | Yes. LTX-2.x Community Licence (free under $10M revenue) | **Yes** | **Native 3840×2176 through the DFR pipeline**; 24 fps, plus a temporal upscaler to 48 or 96 fps; up to about 20 s | Yes / **Yes (keyframe pipeline)** | Fast 984, Pro 958 (about Kling 3.0; below Veo 3.1 at 1028) | 22B with a Gemma-4 12B text encoder; bf16 pack about 66 GiB; NVFP4 and int8 builds | Licence bans circumventing any filters, though none are documented in the weights. Community says base anatomy and nudity are weak; uncensored finetunes exist |
| **Wan 2.2 I2V-A14B** (28 Jul 2025) | Yes. Apache-2.0 | **Yes** | 480p or 720p; ~5 s (81 frames) at 16 fps for A14B (720p at 24 fps applies to TI2V-5B) | Yes / **Yes** (ComfyUI `WanFirstLastFrameToVideo`) | Not on the 2026 boards | 27B mixture-of-experts, 14B active; 4-step distilled LoRAs | No filter; largest LoRA ecosystem, including NSFW |
| Wan 2.2 TI2V-5B (Jul 2025) | Apache-2.0 | Yes | 720p at 24 fps | Yes / no official FLF | – | 5B; fits 8 GB with offload | As Wan |
| Wan 2.5 / 2.6 / 2.7 / 3.0 | **No weights** | – | Wan 3.0 is API-only | – | Wan 3.0 1127 (closed) | – | – |
| **MAGI-2 Preview** (3 Aug 2026) | Yes. Apache-2.0 | Yes | 10 s only; base clip then a refiner to 1080p; audio | Yes / no | 1046 (#2 open) | 114B mixture-of-experts, 6B active; **needs 8 Hopper GPUs; 307 GB** | Not documented |
| Kandinsky 6.0 Pro / Lite (HF 9 Sep 2026) | Yes. MIT | Yes | 5 s at 864×480 base; separate VSR ×2.25 or ×4; 44 kHz audio | Yes (first frame only) / no | Not ranked | Pro 29B, Lite 3B; 10-step distilled versions | Not documented |
| HunyuanVideo 1.5 (20 Nov 2025) | Tencent Hunyuan Community Licence | **No: EU, UK and Korea are excluded** | 480p or 720p, plus super-resolution to 1080p | Yes / no | – | 8.3B; 14 GB with offload | – |
| NVIDIA Cosmos3-Super-Image2Video (May/Jul 2026) | OpenMDW-1.1 (permissive) | Yes | 720p, up to 400 frames | Yes / no | Silent board 1253 (4-step build 1275) | 64B; 8×H200 ≈ 55 s, 2×H200 ≈ 3 min | **Training data scrubbed of sexual content and graphic violence**, so it fails the expression brief |
| LongCat-Video (Oct 2025) | MIT | Yes | 720p at 30 fps; long continuation | Yes | – | 13.6B | – |
| SkyReels V3 (29 Jan 2026) | Skywork licence | – | Reference-to-video, extension, talking avatars | – | – | 19B | – |
| Mochi, CogVideoX | Apache | – | 2024-era | – | – | – | Not worth considering |

**Closed models for comparison** (same board, 17 Oct 2026):

| Model | Elo |
|---|---|
| Dreamina Seedance 2.5 | 1138 |
| Gemini Omni Flash 1.1 | 1132 |
| Wan 3.0 | 1127 |
| MiniMax H3 Max (API) | 1125 |
| Seedance 2.0 | 1121 |
| FLUX 3 | 1086 |
| Grok Imagine Video 1.5 | 1067 |
| Veo 3.1 | 1028 |
| PixVerse V6 | 1024 |
| Kling 3.0 Omni Pro | 1004 |
| Kling 3.0 Pro (anchor) | 1000 |

Sora 2, Runway Gen-4.x and Hailuo under its old name are not on the current board; H3 is Hailuo's successor.

On the silent i2v board, AA's FAQ gives only the top entries: Gemini Omni Flash 1368, Bach 1.0 Pro 1362, Wan 3.0 1362, H3 1356, Vimoo 1348; then Cosmos3 4-step 1275. **LTX-2.5's silent score is not published.** That matters because the gallery's loops are mostly silent.

## Detail

### MiniMax H3: the quality leader, but blocked for the UK
- **VERIFIED** from the HF card (repo created 28 Jul 2026, modified 13 Aug):
  - "H3-Omni-Transformer is a 33B-parameter dense, single-stream Transformer, with approximately 13B parameters residing in AdaLN-related branches."
  - The text encoder is Qwen3-VL-32B. It produces 768p by default, 4–15 s at 24 fps, with 32 kHz stereo audio.
  - **H3-Base-FL2VA takes zero, one or two images**, so text-to-video, i2v and first-to-last are one checkpoint.
  - The 2K stage (H3-Regenerate-2K) and the prompt-expansion stage (H3-Context-IR) are not open; you call MiniMax's API for them. Those hosted inputs "are subject to automated moderation… pornographic… may be blocked."
  - https://huggingface.co/MiniMaxAI/MiniMax-H3
- **VERIFIED, licence:**
  - "'Excluded Territories' means the European Union, the United Kingdom, the Republic of Korea and the United States."
  - "You may not use, reproduce, modify, distribute, or display the MiniMax H3 Works or any of their Outputs… outside the Applicable Territory."
  - A licence for excluded regions is "granted based on robust controls and guardrails."
  - The acceptable-use policy also bars use that "violates… the social, ethical, or moral standards of other countries."
  - Displaying output on TVs in a UK home is outside the licence, wherever the GPU runs.
  - https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE and docs/QA-about-License.md
- **Speed:**
  - VERIFIED (LMSYS, 27 Aug 2026): on 8×H200 at 1344×768 and 50 steps, a 5 s FL2VA clip takes **41.3 s** with SGLang (lossless), down to 13.7 s with lossy caching. LMSYS calls a single GPU "far past the point… practical" at that step count. https://www.lmsys.org/blog/2026-08-27-minimax-h3-h200/
  - REPORTED: one RTX 5090 does a 5 s clip at 14 steps in **81 s** end to end (ai-muninn, 11 Sep 2026).
  - REPORTED: a 14.4 s clip at 8 steps takes about 90 s on one H200 and 51 s on one B200, denoising only (MiniMax-AI/awesome-minimax-h3-integration).
  - lightx2v publishes an Apache-2.0 **8-step FL2V Turbo LoRA** (`lightx2v/Minimax-h3-Turbo`, Aug 2026).

### LTX-2.5: the best fit for a UK 4K gallery
- **VERIFIED** from the HF card (public 11 Aug 2026, modified 11 Oct) and the GitHub repo:
  - 22B DiT with a custom Gemma-4 12B encoder, a new diffusion video decoder, native multishot, and a "substantially improved distilled model."
  - Ships distilled bf16, int8-convrot (ComfyUI) and **NVFP4** transformers, plus ×2 spatial and ×2 temporal latent upscalers.
  - The repo's **DFR pipeline** renders final size directly: "UHD 4K is `3840×2176`." `--temporal-upscalings 1/2` gives 48 or 96 fps.
  - A **KeyframeInterpolationPipeline** ("Interpolate between keyframe images") and `--image PATH FRAME_IDX` conditioning cover FLF and seamless loops (first frame equals last frame).
  - Sources: https://huggingface.co/Lightricks/LTX-2.5 ; https://github.com/Lightricks/LTX-2 (README, packages/ltx-pipelines/docs/pipelines.md)
- **Speed:**
  - VERIFIED (NVIDIA Labs Sol page, 13 Aug 2026): a **4K 5 s clip** with distilled NVFP4 takes **92 s baseline / 60 s optimised on one B200**, and 323 s / 170 s on an RTX 5090. On 4×B200 multi-step, 4K 5 s takes 75.6 s → 38.5 s. https://nvlabs.github.io/Sana/Sol-Engine/LTX25/
  - REPORTED: 1280×704, 121 frames in 28.7 s on one RTX 5090 at 31.7 GB peak (ai-muninn).
  - REPORTED: RunPod sizes the bf16 distilled pipeline at 80–96 GB (H100 or RTX PRO 6000), and the full 4K HDR workflow at 141 GB+ (H200 or B200). It also says ComfyUI ships T2V, I2V and **FLF2V** templates (RunPod blog, 12 Aug, updated 15 Oct 2026).
- **Licence (VERIFIED, LICENSE-2_x):**
  - No territory exclusion; free under $10M revenue.
  - The acceptable-use policy has **no nudity, violence or politics clause**. It bars deepfakes "without their consent", defamation and harming minors.
  - It also bars circumventing "content filters, watermarking… latent disclosure" and requires disclosure that content is machine-made "in any context". A gallery caption should suffice.
- **Content behaviour (REPORTED):**
  - Base LTX produces weak anatomy and a "plastic" look on nude prompts (WanGP issue #1759).
  - Uncensored community finetunes exist for LTX-2.3: SulphurAI/Sulphur-2-base (May 2026, about 2.2k likes) and TenStrip/LTX2.3-10Eros. Lightricks says "the large majority of LoRAs… trained on LTX-2.3 run on LTX-2.5."

### Wan 2.2: the safe, unrestricted workhorse
- **VERIFIED** (HF API, Wan-AI org, 17 Oct 2026): the newest Wan weights are Wan2.2-Animate-2-14B (Aug 2026) and Wan-Dancer-14B (Jul 2026), both character-animation models. **There are no 2.5, 2.6, 2.7 or 3.0 repos.** Pages claiming open Wan 2.7 or 3.0 weights (wan27.org, OpenArt) are contradicted by the official org.
- Wan2.2-I2V-A14B is a 2-expert mixture-of-experts model at 480p or 720p (A14B frame rate not stated in the README), Apache-2.0. https://github.com/Wan-Video/Wan2.2
- ComfyUI has a "Wan 2.2 14B First-Last Frame to Video" workflow using the same nodes the app already uses (https://docs.comfy.org/tutorials/video/wan/wan2_2). That makes it a near drop-in upgrade of the current graphs.
- **Speed-ups:**
  - VERIFIED: `lightx2v/Wan2.2-Distill-Loras` are rank-64 high- and low-noise 4-step LoRAs, Apache-2.0, updated 16 Oct 2026.
  - VERIFIED: `lightx2v/LightWan2.2-A14B` (May 2026) combines NVFP4, sparse attention and 4 steps. On one RTX 5090, 720p I2V takes **26.7 s versus 2,685 s** for 40-step base. It is Blackwell-only.
  - REPORTED: H100 at 720p with the 4-step LoRA is roughly 1 minute. No clean benchmark was found.
- FusionX and CausVid were the Wan 2.1-era shortcuts and are superseded by the lightx2v 2.2 distills.
- TeaCache and MagCache are training-free caches. They help 20–50-step runs; my inference is they add little on top of 4-step distillation.
- **Content (REPORTED):** no filter in the weights, and the largest community LoRA and finetune ecosystem, including NSFW. For gallery-grade output it needs a 720p→4K upscale step, below.

### Others, briefly
- **MAGI-2 Preview** (VERIFIED card, 3 Aug 2026): Apache-2.0, 114B mixture-of-experts (6B active), T2V and I2V only, fixed 10 s, base clip then a 1080p refiner. Requires "NVIDIA Hopper GPUs, 8 of them" and about 307 GB of weights. Not viable on serverless; worth watching for a distilled release.
- **Kandinsky 6.0** (VERIFIED card, HF 9 Sep 2026, MIT): Pro 29B and Lite 3B; 5 s at 864×480 base; first-frame only, so no FLF; separate MIT VSR model at ×2, ×2.25 or ×4. Unranked, so an experiment only.
- **HunyuanVideo 1.5** (VERIFIED licence): "does not apply in the European Union, United Kingdom and South Korea." Excluded.
- **Cosmos3** (VERIFIED card): training data filtered for sexual content, NCII, graphic violence and more. Also 64B. Wrong fit for the brief.

### Upscaling to 4K and frame interpolation
- **SeedVR2 3B/7B** (ByteDance, Apache-2.0). Now repackaged natively for ComfyUI (Comfy-Org/SeedVR2, 6 Jun 2026) with fp8, mxfp8, NVFP4 and "sharp" builds. REPORTED as the best restoration quality but heavy at 4K, sometimes reinterpreting fine detail (upsampler.com, 2026).
- **FlashVSR v1.1** (Apache-2.0, updated 15 Aug 2026). REPORTED as faster and more temporally stable, about 17 fps at 768×1408 on an A100.
- **Kandinsky 6 VSR** (MIT, Sep 2026). 2- or 4-step tiled diffusion.
- **LTX-2.5 IC-LoRAs:** Pixel-Spatial-Upscaler, Refine-Details and Restore (Sep 2026) can act as video-to-video refiners. NVIDIA published a LTX-2.5 refiner for H3 output (`Efficient-Large-Model/SoL-Refiner-LTX-2.5-for-MiniMax-H3`), which shows the pattern works.
- **Interpolation:** Comfy-Org/frame_interpolation (Apr 2026) packages **RIFE 4.25 and 4.26** (lite, standard, heavy) and FILM natively.
  - RIFE 4.26 is the pragmatic swap for FILM ×8. GIMM-VFI is higher quality but slow.
  - For LTX, the in-latent temporal upscaler to 48 fps beats any after-the-fact interpolation.
  - Either way, avoid an ×8 slow-motion stretch unless that dreamy look is wanted.
- **Attention and quantisation:** SageAttention3 (FP4, Blackwell; arXiv 2505.11594) is available only via a third-party ComfyUI node. NVFP4 checkpoints load in stock ComfyUI on Blackwell; on cu128 they run emulated, about 2.3× slower (REPORTED).

### Deployment implications for video
- The new packs are large: LTX-2.5 bf16 is about 66 GiB, H3 is 42.5–124 GB, Wan 2.2 A14B fp16 two experts is about 57 GB.
- Runpod now offers **cached models**. Hugging Face models (public, gated or private) are pre-placed on hosts, and "you aren't billed for worker time while your model is being downloaded" (VERIFIED, https://docs.runpod.io/serverless/endpoints/model-caching). Baking video weights into Docker images is no longer necessary.
- The CUDA 11.8 base has to go to CUDA 12.8+ (13 for the lightx2v NVFP4 runtime).
- Prices (REPORTED, usagepricing.com, Jul 2026 reprice): serverless flex H100 ≈ $4.55/h, H200 ≈ $5.93/h; B200 serverless is unverified (≈ $8.6/h in an older snapshot).
- At those rates a 4K LTX-2.5 clip on a B200 would be roughly $0.15–0.25. That is my estimate.

## Ranked shortlist for this gallery

1. **LTX-2.5 distilled + DFR, NVFP4 or bf16, on B200/H200 (or H100 with fp8-cast):**
   - True 3840×2176 output, FLF and keyframe loops, 48 fps temporal upscale, a licence usable in the UK, ComfyUI-native.
   - About Kling 3.0 level with audio. Its silent rank is unknown.
   - Test its nudity and violence behaviour on the owner's prompt set; fall back to Sulphur or 10Eros-style finetunes or LoRAs if the base is prudish.
2. **Wan 2.2 I2V-A14B + lightx2v 4-step LoRAs → SeedVR2-7B or FlashVSR to 4K → RIFE 4.26:**
   - Apache-2.0, no filter, the richest LoRA ecosystem, minimal change to the app's ComfyUI graphs.
   - Lower base resolution means 4K relies on the upscaler.
   - Keep it as the "expressive" route and the fallback.
3. **MiniMax H3 FL2VA + Turbo LoRA:** the best open quality. **Usable only if MiniMax grants a UK licence**, and their stated condition ("robust controls and guardrails") conflicts with the brief. Watch for a territory change.
4. **Watch list:** MAGI-2 (needs a distilled or smaller release) and Kandinsky 6 Pro (no FLF; quality unproven).
5. **Reject:** HunyuanVideo 1.5 (UK licence), Cosmos3 (filtered training data), Mochi and CogVideoX (obsolete), Wan 2.5–3.0 (no weights).

**Legal hard lines, stated factually:** sexualised images of minors are illegal everywhere and barred by every licence here. Every licence also bars non-consensual deepfakes of real people. UK law also criminalises creating sexually explicit deepfakes of real adults. Satire of public figures is not barred by the LTX or Wan licences, apart from the defamation clause in LTX's acceptable-use policy.

## What I could not verify
- No like-for-like **single-H100** time for Wan 2.2 A14B 720p with 4 steps, or for LTX-2.5 1080p distilled. The figures above are RTX 5090, B200 and GB200 numbers, or estimates.
- LTX-2.5's and Wan 2.2's scores on AA's **silent** i2v board. Wan 2.2 is absent from the 2026 boards altogether.
- Whether LTX-2.5's base weights refuse or degrade nudity, violence or political content. Only community anecdotes on LTX-2.3 exist; there is no systematic test.
- H3's local (non-API) content behaviour. No source reports refusals, but none tested sensitive content either.
- Kandinsky 6's public release date: HF created 9 Sep 2026, and one roundup says weights were published 6 Oct. Its content policy and quality ranking are also unconfirmed.
- Whether the ComfyUI LTX-2.5 FLF2V template supports exact identical first and last frames for seamless loops. The repo's keyframe pipeline suggests yes, but I did not test it.
- Current Runpod serverless B200 per-second pricing (the official table did not render).
- The MAGI-2 refiner's VRAM, and any plan for a smaller MAGI-2.

Working files (model cards and licences) are in `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/e4fdeccc-da6c-4b8b-b2e6-32d24afca616/scratchpad/cards/`. Nothing in the repo or on Runpod was changed.