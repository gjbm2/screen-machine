# Topics A and B: image editing and 4K upscaling, research report (17 Oct 2026)

## What the repo does today (read locally, verified)

- **Adapt an image / Combine two images** both run FLUX.1 Kontext dev on endpoint `oh422aycpybd5u`.
  - The working canvas is 1280×720 (`EmptySD3LatentImage`), and the reference passes through `FluxKontextImageScale` (about 1 MP).
  - "Combine two images" uses `ImageStitch` to put both inputs side by side as one reference. Each input therefore gets roughly half of a 1 MP budget.
  - Settings are 20–25 steps, guidance 2.5. The output goes through `RealESRGAN_x4plus` and then `ImageResize+` with a 6400 cap.
  - `flux_1_kontext_dev_1pic-l.json` hard-codes the LoRA `sldr_flux_nsfw_v2-studio.safetensors`. The image actually contains Heartsync `lora.safetensors`, which the catalogue's `lora` param overrides.
- **All text-to-image graphs** (sd35, krea, flux1, sdxl) also render at 1280×720 and then apply RealESRGAN ×4.
- **Upscale Image** is RealESRGAN ×4 followed by a Lanczos resize to 3840×2160.
- **Wan i2v** renders 1280×720 at 40 frames, then FILM ×8 interpolation, then RealESRGAN ×2. The result is 2560×1440, not 4K.
- **Consequence:** every 4K pixel on the TVs comes from about 0.92 MP of real model output, stretched 3× in each dimension by a 2021 photo-trained GAN. That GAN adds no painterly detail; it imposes its own texture. **Fixing this is a bigger quality gain than any single model swap.**

---

## Summary table

### Topic A: editing and multi-reference (replacing FLUX.1 Kontext dev)

- **Elo column:** the Artificial Analysis open-weights editing board, fetched 17 Oct 2026; the page itself is undated ([board](https://artificialanalysis.ai/image/leaderboard/editing/open-weights)). FLUX.2 [dev] is the anchor at 1000.
- **Size column:** file sizes from Comfy-Org's Hugging Face repos.

| Model (release) | Licence | Elo | Weights (ComfyUI files) | Expression posture | Verdict |
|---|---|---|---|---|---|
| FLUX.1 Kontext dev (Jun 2025), **current** | FLUX.1 dev non-commercial | **884** (rank 12–15) | 12B | NSFW-filtered base; the repo patches it with a LoRA | Replace |
| **Qwen-Image-2.1** (20 Sep 2026) + Turbo (9 Oct 2026) | Qwen Research Licence: "Non-Commercial" = "research or evaluation purposes only" | **1083 (#1 open)** | 7B DiT: 14.2 GB bf16 / 7.3 GB int8. Text encoder Qwen3-VL-8B: 17.5 GB / 9.4 GB | No filter in the weights. Base pretraining NSFW-filtered (reported). Community tests: 40/45 sensitive prompts drawn | Best quality; licence is a grey area for home display. Pilot it |
| **Qwen-Image-Edit-2511** (17–23 Dec 2025) | **Apache-2.0** | 990 | 20B: 40.9 GB bf16 / 20.5 GB fp8. Qwen2.5-VL-7B: 16.6 GB / 9.4 GB. Lightning 4/8-step LoRAs | No filter. Lineage pretraining NSFW-filtered (verified). NSFW LoRAs exist | **Safest production drop-in** |
| FireRed-Image-Edit-1.1 (Mar 2026) | Apache-2.0 | not rated | Qwen-Edit-class 20B (Comfy-Org hosts 1.0 at 40.9 GB) | Unknown | Optional A/B against 2511 |
| **FLUX 3 Image Klein** (15 Oct 2026, 2 days old) | FLUX Kommunity (free commercial use under US$5M revenue) | not yet rated. BFL's internal evals claim it beats Qwen-Image 2.1 | 5.6B (11.2 GB bf16); Qwen3-VL-4B encoder | "Dataset filters during pre-training… fine-tuning to suppress harmful behaviors"; reference CLI loads an NSFW classifier | Test it, but expect inhibition |
| FLUX.2 [dev] (Nov 2025) | FLUX non-commercial; **"Filters or manual review must be used"** | 1000 | 32B fp8 35.5 GB + Mistral-3 24B fp8 18 GB | NSFW-filtered pretraining and concept suppression | Reject |
| FLUX.2 [klein] 9B / 4B (Jan 2026) | Non-commercial with filters required / Apache | 1015 / 940 | about 13 GB for 4B | Same BFL mitigations | Reject 9B; 4B too weak |
| HunyuanImage 3.0 Instruct (Jan 2026) | Tencent licence: **"does not apply in the EU, UK and South Korea"** | 1064 | 80B MoE, "≥ 8 × 80 GB" | — | Reject (territory and hardware) |
| HiDream-O1-Image (8 May 2026) | MIT | 956 | 8B, pixel-space, up to 2048² | Undocumented | Second tier |
| Bagel / OmniGen2 / Step1X-Edit (2025) | various | 793 / 787 / 600 | — | — | Obsolete |
| Z-Image-Edit | unreleased ("To be released") | — | — | — | n/a |

What the Elo gaps mean (expected head-to-head win rate):
- Qwen-Image-2.1 vs Kontext dev: +199 Elo, about 76%.
- Qwen-Image-Edit-2511 vs Kontext dev: +106 Elo, about 65%.

### Topic B: getting to 3840×2160

| Option | Licence | Painterly fidelity | Cost / VRAM | Use |
|---|---|---|---|---|
| RealESRGAN x4plus (current) | BSD | Photo-trained prior; flattens and "photographises" brushwork | Trivial | Retire as the default |
| **Native ~4 MP generation + 1.4× resize** (Lanczos, optional mild sharpen) | — | **Perfect**: adds nothing | ms | **Default for art** |
| 4x-UltraSharpV2 (DAT2, May 2025), then downsample | CC-BY-NC-SA-4.0 | Author: "works amazingly on illustrations and artwork" | Seconds | Fast GAN alternative |
| **SeedVR2 3B/7B** (+ 7B "sharp") (Jun 2025) | Apache-2.0; **in ComfyUI core since 10 Jul 2026** | Strong on photos and AI renders. Reported plastic skin and halos; synthesises organic texture on flat art | 7B fp16 at 24 GB+; 8 GB with GGUF + BlockSwap | Photographic stills; video |
| Same-model tiled re-diffusion (Qwen-Image-2.1 Tiled Upscale, Ultimate SD Upscale) | per model | Best style match, but invents detail; faces drift | about 20 s at 4 MP, 43 s at 8 MP on RTX 5090 (reported) | Occasional "hero" stills |
| SUPIR (2024) | Non-commercial; repo last pushed May 2025 | Photo restorer; hallucinates | 12 GB diffusion + 16 GB LLaVA | Superseded |
| VOSR 2.0 (22 Aug 2026) | Code Apache-2.0; weight terms unclear | One-step, 1.4B, fidelity knobs | Unknown | Watch |
| FlashVSR v1.1 (Nov 2025) / FlashVSR+ (Oct 2026) | Apache-2.0 | Built for real footage at 4× | about 17 FPS at 768×1408 on an A100 | Fast video |
| SwiftVR (8 Jun 2026) | Code Apache; HF weights licence "other" | — | Claims 14 FPS at 4K on an H100 | Watch (no ComfyUI) |

---

## Topic A in detail

### 1. Qwen-Image-2.1: top quality, licence caveat
- **Verified** from the [model card](https://huggingface.co/Qwen/Qwen-Image-2.1) (HF created 14 Sep 2026; licence dated 20 Sep 2026):
  - One model for text-to-image and editing; 7B visual DiT.
  - "Up to 10 reference images"; mask, circle and annotation-guided local edits; identity preservation.
  - Native 16:9 output is **2752×1536**. That is 4.2 MP and only 1.40× short of 3840×2160 (Topic B).
- **Verified, [Turbo card](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo) (9 Oct 2026):** 8 denoising steps, CFG 1, prefix KV caching.
- **Verified, ComfyUI:** native from day 0 ([Comfy blog, 20 Sep 2026](https://blog.comfy.org/p/qwen-image-21-in-comfyui-open-weight)), with 10 reference inputs. Comfy-Org's repo (modified 13 Oct 2026) has int8 diffusion (7.3 GB) plus int8 encoder (9.4 GB), about 17 GB in total, so it fits a 24 GB serverless GPU. Today's Kontext/Flux stack does not.
- **Verified, licence** ([LICENSE](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)):
  - Use is granted "FOR NON-COMMERCIAL PURPOSES ONLY", and Non-Commercial is defined as "research or evaluation purposes only".
  - A private home gallery is not commercial, but it is also not literally "research or evaluation". That is a grey area for the owner to judge. Compliance is not technically enforced.
- **Expression:**
  - **Verified:** the Qwen-Image tech report (Aug 2025, the 2511 lineage) states "the NSFW Filter is applied to exclude content containing sexual, violent, or other offensive material" in Stage-1 pretraining curation ([arXiv 2508.02324](https://arxiv.org/html/2508.02324)).
  - **Reported** for 2.1:
    - AtomicChat says Qwen-Image "ships none" (no safety checker), and the stock pipeline drew 40/45 sensitive adult prompts. Ablating refusals in the encoder changed 0/45 ([HF card, Oct 2026](https://huggingface.co/AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF)).
    - Vendor SpicyAPI reports 44/45 passed, including gore 9/9. One intimate two-person prompt was softened to a kiss, and there were extra limbs in two-person scenes at 2K ([3–5 Oct 2026](https://spicyapi.ai/blog/qwen-image-2-1)).
  - **Reading:** no refusal mechanism, but under-trained anatomy for explicit material. Tasteful nudity is generally achievable. Expect to add LoRAs, and note that 2509/2511 LoRAs do not carry over to the new architecture.
- **Drift (reported):** a community "Consistency LoRA" exists because style edits shift framing by a few percent, per seed ([ausboss](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA)).

### 2. Qwen-Image-Edit-2511: low-risk production replacement
- **Verified** ([card](https://huggingface.co/Qwen/Qwen-Image-Edit-2511), HF 17 Dec 2025):
  - Apache-2.0.
  - "Mitigate image drift, improved character consistency, integrated LoRA capabilities", and better multi-person fusion of separate images.
  - Multi-image input natively via `QwenImageEditPlusPipeline`.
- **Verified, ComfyUI:** native support; fp8 20.5 GB plus encoder 9.4 GB, about 30 GB, which fits a 48 GB card (L40S/A6000) without offload ([Comfy-Org](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI)).
- **Verified, speed:** [Lightning 4/8-step LoRAs](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) (updated 16 Oct 2026).
- **Reported:** the core `TextEncodeQwenImageEditPlus` node exposes image1–3. Each reference is encoded separately, so it **removes the ImageStitch hack** the 2-picture workflow uses today.
- **Expression (reported):** community NSFW LoRAs exist ([example](https://huggingface.co/ScottzillaSystems/qwen-image-edit-plus-nsfw-lora)), the same pattern the repo already uses with Kontext.
- FireRed-Image-Edit-1.1 ([HF](https://huggingface.co/FireRedTeam/FireRed-Image-Edit-1.1), Apache, 2 Mar 2026) is in the same 20B class. Its self-reported GEdit score is 7.94 vs 7.88 for 2511 ([benchmark doc](https://github.com/FireRedTeam/FireRed-Image-Edit/blob/main/docs/benchmark.md)), a marginal difference.

### 3. BFL line (FLUX.2, FLUX 3 Klein): capable, but self-inhibiting by design
- **FLUX.2 cards, verified:**
  - [klein 4B card](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (14 Jan 2026): "filtered pre-training data for multiple categories of 'not safe for work' (NSFW)", plus multiple rounds of "suppressing certain concepts".
  - [FLUX.2 dev page](https://huggingface.co/black-forest-labs/FLUX.2-dev): "Filters or manual review must be used with the model under the terms of the FLUX.2 [dev] Non-Commercial License."
  - That licence requirement directly conflicts with the owner's no-guardrails requirement. FLUX.2 dev is also heavy: 35.5 GB plus an 18 GB encoder, needing an 80 GB card.
- **FLUX 3 Image Klein, verified** ([BFL blog, 15 Oct 2026](https://bfl.ai/blog/flux-3-image-klein); [HF, 13 Oct 2026](https://huggingface.co/black-forest-labs/flux-3-image-klein)):
  - 5.6B, 10 references, "native 4K generation".
  - Internal evals claim it beats Qwen-Image 2.1, Krea 2 and FLUX.2 [pro]. Those are self-reported and unaudited ([VentureBeat](https://venturebeat.com/technology/black-forest-labs-opens-flux-3-image-klein-weights-with-free-commercial-use-limited-to-smaller-businesses)).
  - Safety: "dataset filters during pre-training… targeted fine-tuning during post-training to suppress harmful behaviors… progressively harder to elicit harmful content". The reference CLI loads `Falconsai/nsfw_image_detection`.
  - No licence-mandated filter was found.
  - It is cheap to host and the most interesting new arrival, but on the evidence it is the most likely of the strong options to be prudish.

### 4. Ruled out
- **HunyuanImage 3.0 Instruct:** the [LICENSE](https://huggingface.co/tencent/HunyuanImage-3.0-Instruct) "DOES NOT APPLY IN THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA". It also needs "≥ 8 × 80 GB" even for the 8-step Distil.
- **Bagel, OmniGen2, Step1X-Edit, HiDream-E1.1:** bottom of the AA board.
- **Z-Image-Edit:** still "To be released" ([repo](https://github.com/Tongyi-MAI/Z-Image)).
- **Qwen-Image 3.0:** API-only since 21 Jul 2026 (reported, [Decrypt](https://decrypt.co/374084)).
- **Krea 2** (Jun 2026): BFL's blog states it "does not support editing".

### Recommendation, Topic A
1. Replace both Kontext workflows with **Qwen-Image-Edit-2511** (Apache, fp8, Lightning 8-step). Feed each reference separately rather than stitched, and render at the model's native ~1–2 MP+ instead of a 1280×720 canvas.
2. Run **Qwen-Image-2.1 Turbo** side by side. If it wins on the gallery's own prompts and the owner accepts the licence wording, it can **merge editing and text-to-image into one endpoint** that fits on 24 GB.
3. Put FLUX 3 Klein through an expression test battery before considering it:
   - a tasteful nude oil study;
   - a Goya-style war scene with blood;
   - satire of a Western head of state and of Xi Jinping;
   - all of these as edits as well as generations.
4. Legal hard lines, stated as facts: sexualised imagery of minors is illegal in all forms (in the UK this includes non-photographic and AI images). Sexually explicit images of real identifiable adults made without consent carry criminal exposure in the UK. Political satire of public figures is not in that category. The editing workflows make "real person in, image out" easy, so the hard line applies at that point.

---

## Topic B in detail

### Biggest lever: stop upscaling 3×
Generating at about 4 MP natively means the final step is only ~1.4×, and that step is low-risk whatever tool does it.
- **Qwen-Image-2.1:** 2752×1536 (verified, card).
- **FLUX.2:** "up to 4 MP" (verified, [BFL, 25 Nov 2025](https://bfl.ai/blog/flux-2)).
- **HiDream-O1:** up to 2048² (verified, [card](https://huggingface.co/HiDream-ai/HiDream-O1-Image)).
- **FLUX 3 Klein:** claims native 4K (verified as a claim; untested).
- Pushing flow-matching models past their trained resolution produces repeating-pattern artefacts (reported, [FlowMatching-Upscaler](https://github.com/ttulttul/ComfyUI-FlowMatching-Upscaler)). Use the native buckets and do not force 3840×2160 directly.

### The final ~1.4× step, by content type
- **Painterly or illustrative (most of the gallery):**
  - Lanczos plus a mild sharpen keeps brushwork exactly as rendered.
  - [4x-UltraSharpV2](https://openmodeldb.info/models/4x-UltraSharpV2) (DAT2, 23 May 2025, CC-BY-NC-SA, which is fine for personal use) then a downsample adds crispness in seconds.
- **Photographic:** SeedVR2 7B.
  - Apache-2.0 ([HF, Jun 2025](https://huggingface.co/ByteDance-Seed/SeedVR2-7B)).
  - **Native in ComfyUI core** ([PR #14424](https://github.com/Comfy-Org/ComfyUI/pull/14424), merged 10 Jul 2026; [docs](https://docs.comfy.org/tutorials/utility/seedvr2): FP16/FP8/INT8, a 7B "sharp" variant, described as "conservative").
  - VRAM tiers (verified, [numz README](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler) v2.5.24, 24 Dec 2025): 24 GB+ for FP16, 12–16 GB for FP8, 8 GB for GGUF + BlockSwap.
  - **Reported risks** ([Apatero](https://apatero.com/blog/seedvr2-removing-artifacts-complete-guide-2025)): "skin looking plastic", halos, and a tendency to "synthesize organic textures onto crisp vector edges". This is why it is not the default for paintings.
- **Occasional hero stills:** a same-model tiled redraw.
  - The [Qwen-Image-2.1 Tiled Upscale](https://comfy.icu/models/2988629/Qwen-Image-2-1-Tiled-Upscale) workflow (7 Oct 2026, reported): about 2 MP tiles, per-tile colour matching. About 20 s for 4 MP and 43 s for 8 MP on an RTX 5090 in Fast mode.
  - The author notes faces drift and detail varies with seed.
  - This keeps the generator's own aesthetic, unlike GAN or SeedVR2 priors, but it is a re-interpretation, not an enlargement.
- **Retire SUPIR:** non-commercial "Declaration", repo last pushed 12 May 2025, needs 12 GB plus 16 GB for LLaVA ([repo](https://github.com/Fanghua-Yu/SUPIR)).
- **Watch VOSR 2.0:** 1.4B, one-step, with fidelity and generativity knobs ([repo](https://github.com/cswry/VOSR), 22 Aug 2026; weight licence unclear).

### Video
Today's output is 1440p. Getting to 2160p from Wan's 720p is a 3× step.

- **SeedVR2 (best quality, slow):**
  - Reported numbers ([RedbeardNZ/numz benchmarks](https://huggingface.co/RedbeardNZ/SeedVR2_comfyUI), undated, mid-2025 code): the 7B model on an H100 processes 149 frames from 480p to 1080p in about 361–450 s (0.41 FPS). The 3B FP8 on an RTX 4090 runs about 0.19 FPS on the same task.
  - My estimate: 4K output is about 4× the pixels, so roughly 0.1 FPS on an H100, or ~20 min for 5 s at 24 fps.
- **FlashVSR v1.1 / FlashVSR+ (Oct 2026), fast** ([repo](https://github.com/OpenImagingLab/FlashVSR), Apache):
  - About 17 FPS at 768×1408 on an A100, and designed for 4×.
  - Block-sparse attention is tuned for A100/H100; on RTX 40/50 it is "unknown".
  - The repo warns that some ComfyUI ports drop LCSA (locality-constrained sparse attention), and "quality loss" follows.
- **SwiftVR** ([arXiv 2606.09516](https://arxiv.org/abs/2606.09516), 8 Jun 2026): claims 14 FPS at 4K on an H100. Weights are on [HF](https://huggingface.co/H-oliday/SwiftVR) under an "other" licence; no ComfyUI integration. Watch it.
- **Cost lever:** the current graph interpolates ×8 with FILM before upscaling, which multiplies the frames the upscaler must process. Run video upscaling at the native frame count first, then interpolate, and only for clips worth keeping (an async "finish" job).

---

## What I could not verify
- How Qwen-Image-2.1, Edit-2511 or FLUX 3 Klein handle **named public figures and political satire**, including any China-specific suppression (e.g. Xi Jinping). I found no direct image-model tests; only text-LLM censorship reports exist.
- FLUX 3 Klein's actual nudity behaviour, whether its licence requires filters (BFL's FLUX 3 Action licence reportedly does), and any independent Elo (not yet on the AA board).
- HiDream-O1 and FireRed-1.1 content posture: the cards are silent.
- The assumed real-world 4K timings for SeedVR2 stills and video on current ComfyUI core. The only benchmarks found are mid-2025 custom-node runs; the ~0.1 FPS 4K figure is my extrapolation.
- Any head-to-head of SeedVR2 vs UltraSharpV2 vs tiled redraw **on paintings specifically**. The painterly guidance above follows from how each method works plus anecdotal reports, not a benchmark.
- VOSR 2.0 weight licence; SwiftVR weight licence text; whether FlashVSR+ has a ComfyUI port that keeps LCSA.
- Whether the core ComfyUI Qwen Edit Plus node still caps at 3 images for 2511 (a custom node allows 5).
- The exact "Non-Commercial" reading of the Qwen Research Licence for private non-research display. That is a legal judgement, not a technical one.
- The current status of UK statutes on AI-generated intimate images and AI CSAM tools; I did not re-check them this session.

Working files (model cards and licences) are in `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/e4fdeccc-da6c-4b8b-b2e6-32d24afca616/scratchpad/cards/`. The repo graphs I read are in `/home/gjbm2/dev/screen-machine/routes/workflows/` (the `flux_1_kontext_dev_*`, `upscale.json` and `wan_*` files) and `/home/gjbm2/dev/runpod-worker-comfy/Dockerfile`.