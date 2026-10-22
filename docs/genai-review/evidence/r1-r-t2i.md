# Open-weight text-to-image models as of 17 Oct 2026: gallery-grade fine art at 4K, 16:9

## Bottom line
- **The current stack is about 1.5 generations behind.** The app runs FLUX.1-dev, FLUX.1-Krea, SD3.5 Large (the default) and SDXL. On Artificial Analysis (AA), the best open model today is Qwen-Image-2.1 at Elo 1042. FLUX.1-dev scores 841 and FLUX.1 Krea 878. A 164–201 Elo gap means voters would prefer the new model roughly 72–76% of the time (my calculation from the Elo gaps). SD3.5 Large and SDXL no longer appear on the current AA v2.1 board.
- **Open models still trail closed ones by about 180 Elo at the top.** GPT Image 2.5 Sunburst scores 1220 on AA, so it would win about 74% of head-to-heads against the best open model. The gap to Seedream 5.0 Pro (1100) is only about 58 Elo.
- **Openness on expression has split by lab.** Black Forest Labs (BFL: FLUX.2, FLUX 3 Klein), Krea and Ideogram now filter training data and add safety post-training. Their licences also require output filtering or review. Alibaba (Qwen-Image, Z-Image) and Lodestones (Chroma) are the permissive end.
- **Only one open model claims native 4K: FLUX 3 Image Klein, released two days ago.** Everything else tops out around 4 MP (about 2K), so 3840×2160 still needs an upscale step.
- **A second censor sits in front of the image models.** The app rewrites prompts with hosted `gpt-4o` (`/home/gjbm2/dev/screen-machine/routes/openai.py:82`). Several new models also ship their own LLM prompt rewriter: FLUX 3 Klein's Qwen3.5-9B upsampler (BFL says it is required), Qwen-Image-2.1's prompt enhancer, and HiDream's Gemma-4-31B refiner. Each rewriter can soften a prompt before the image model sees it.

## Summary table

AA = Artificial Analysis Text-to-Image v2.1, fetched 17 Oct 2026. LMArena scores are from the 24 Sep 2026 update, reported by flami.pro.

| Model | Released | Params (generator + text encoder) | Licence (private home gallery?) | Native max res | Arena | Expression / guardrails | ComfyUI | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Qwen-Image-2.1** (+Turbo) | 20 Sep 2026; Turbo 9 Oct | 7B DiT + Qwen3-VL-8B | Qwen Research, non-commercial: "research or evaluation purposes only" | 2752×1536 at 16:9; 2048² | AA 1042 (#21 overall, **#1 open**); LMArena 1228 (#17, preliminary) | No safety section on the card. Vendor tests report it permissive *(REPORTED)* | Native from day 0 | **#1** |
| **Krea 2** Raw / Turbo | 18–23 Jun 2026 | 12B DiT + Qwen3-VL-4B | Krea 2 Community; free under about $1M revenue *(REPORTED)*; filtering or review required | about 2K | Open checkpoints not on AA. The hosted API model "Krea 2 Large" scores 1034 | Safety-tuned against nudity, wounds and intense emotion *(REPORTED)*. Bypass LoRAs exist, plus the Kroma fine-tune | Native | **#2** |
| **Z-Image** Base / Turbo | 25 Nov 2025; Base 23 Jan 2026 | 6B + Qwen3-4B | **Apache 2.0** | about 2K (tested at 2048²) | AA 934; LMArena 1084 | Widely reported as unfiltered *(REPORTED)* | Native | **#3** |
| **Chroma1-HD** (+ Kroma, Zeta-Chroma) | 8 Aug 2025 | 8.9B (FLUX.1-schnell base) | **Apache 2.0** (Kroma uses Krea's licence) | about 1 MP | Not ranked | Card: "not been aligned with a specific safety filter" | Native | **#4** (uninhibited lane) |
| **HiDream-O1-Image** / Dev-2604 | 8 May / 14 May 2026 | 8B, pixel-space (no VAE) | **MIT** | 2048² | AA 974; LMArena 1116; Dev-2604 was the #1 open model when it debuted in May | Anecdote: avoids nudity | Comfy-Org repackage | **#5** (A/B test it) |
| FLUX.2 [dev] | 25 Nov 2025 | 32B + Mistral 24B | FLUX non-commercial; **filters or review mandatory** | 4 MP | AA 1000 | NSFW-filtered pre-training, safety post-training, filters in the repo | Native | Not for the expressive lane |
| FLUX.2 [klein] 4B / 9B | 15 Jan 2026 | 4B / 9B | Apache 2.0 (4B) / non-commercial (9B) | 4 MP | AA 838 / 931 | Filtered pre-training. Community "ablated" text encoders and LoRAs exist *(REPORTED)* | Native | Fast utility model |
| FLUX 3 Image Klein | 15 Oct 2026 | 5.6B + Qwen3-VL-4B, plus a 9B prompt upsampler | FLUX Kommunity; free under $5M revenue; filter or review required | **4K (claimed)** | Not yet ranked | Dataset filtering and safety post-training. Early users report broken anatomy *(REPORTED)* | Day 0 | Watch for now |
| Ideogram 4 | 3 Jun 2026 | 9.3B + Qwen3-VL-8B | Ideogram non-commercial; **you may not circumvent its filters** | 2048 per side | AA 1026 (**#2 open**); LMArena 1205 | Hive moderation in the reference code. Baked-in refusals *(REPORTED)* | Comfy-Org repackage | **Excluded** |
| HunyuanImage 3.0 (+Instruct) | 28 Sep 2025; Instruct Jan 2026 | 80B MoE (13B active), about 168 GB at bf16 | Tencent Community: **does not apply in the UK, EU or South Korea** | Not stated on the card | AA 932 / 988 | Not checked | Partial | **Excluded** |
| ERNIE-Image / Turbo | 2–7 Apr 2026 | 8B DiT | Apache 2.0 | Not checked | AA 907 / 914 | Not checked | Comfy-Org | Poster and text niche |
| Ming-Image-0.1-Design; MOSS-Image | 17 Sep 2026; 11–16 Oct 2026 | 6B; 5B | MIT; Apache 2.0 | 2048 | AA 999; MOSS not ranked | Not checked | vLLM-Omni; MOSS has a plug-in | Design and text niche |
| Qwen-Image-2512 | 30 Dec 2025 | 20B MMDiT + about 7B text encoder | **Apache 2.0** | 1664×928 at 16:9 | LMArena 1125 | Not checked | Native | Apache fallback if the 2.1 licence is a problem |
| Cosmos3-Super-T2I (NVIDIA) | 28 May 2026 | 64.6B | OpenMDW 1.1 (permissive) | Not checked | AA 984–992 | Not checked | Not checked | Too large, unclear value |
| Legacy (in use now) | 2023–2025 | — | — | about 1 MP | FLUX.1-dev 841, FLUX.1 Krea 878; SD3.5 L and SDXL off the board | FLUX.1 filtered | — | Replace |

**Closed models for comparison** (AA v2.1, 17 Oct):

| Model | AA Elo |
|---|---|
| GPT Image 2.5 Sunburst | 1220 |
| GPT Image 2.5 Flare | 1207 |
| Nano Banana 2.1 | 1174 |
| Grok Imagine 2.0 | 1167 |
| MAI-Image-2.6 | 1166 |
| FLUX 3 Image (API only) | 1116 |
| Seedream 5.0 Pro | 1100 |
| Qwen-Image-3.0-Pro (API only) | 1094 |
| Ideogram 4.5 | 1016 |

Other boards:
- On LMArena, Reve 2.1 is #5 at 1302.
- Midjourney is absent from both boards because it has no API. BudgetPixel's arena puts Midjourney V8.2 at #3 (15 Oct) *(REPORTED)*. Midjourney's docs confirm V8.1 shipped on 30 Apr 2026.
- Imagen is not listed; Google's current line is Nano Banana.

## Detail by model

### Qwen-Image-2.1 (Alibaba): best open model by arena
- **VERIFIED from the model card and GitHub ([HF card](https://huggingface.co/Qwen/Qwen-Image-2.1), [repo](https://github.com/QwenLM/Qwen-Image-2.1), fetched 17 Oct 2026):**
  - Released 20 Sep 2026; Turbo (8 steps, CFG 1) on 9 Oct.
  - 7B single-stream DiT (32 layers), Qwen3-VL-8B encoder, and a 64-channel RGBA VAE.
  - Native 2K; recommended 16:9 size is 2752×1536.
  - One model does both text-to-image and editing, with up to 10 reference images.
  - Day-0 support in ComfyUI, Diffusers, vLLM-Omni, SGLang and LightX2V.
- **Licence, VERIFIED:** the LICENSE file says "Non-Commercial shall mean for research or evaluation purposes only." A private, unpaid gallery is clearly non-commercial. Whether it counts as "research or evaluation" is a judgement for the owner. Earlier Qwen-Image (Aug 2025) and Qwen-Image-2512 are Apache 2.0.
- **Rankings:**
  - AA, VERIFIED (live board, 17 Oct): Elo 1042, #21 overall, #1 open.
  - Editing board: Elo 1074, #1 open. HunyuanImage 3.0 Instruct is at 1066 *(REPORTED, [remio.ai, 2 Oct 2026](https://www.remio.ai/post/qwen-image-2-1-benchmark-puts-open-weights-first-yet-overall-rank-stays-18th))*.
  - The Open Weights board (16 Oct) has it #2, behind FLUX 3 Image, which it lists as #1 *(REPORTED, [theopenweights.com](https://theopenweights.com/leaderboards/text-to-image))*.
- **Speed and memory *(REPORTED, vendor or blog figures)*:**
  - About 34 GB peak at bf16 and 3.3–4.5 s per 1 MP image on a GB300. FP8 saves memory but not time.
  - About 5.9 s on an RTX 5090. INT8 (about 16 GB) fits a 24 GB card ([ai.rs](https://ai.rs/ai-for-business/qwen-image-2-1-open-weights-licence-vram)).
- **Expression *(REPORTED, low confidence: vendor blogs only)*:** NSFW-API vendors report that the base model renders adult prompts without refusing (44 of 45 in one test). NSFW LoRAs mainly sharpen anatomy ([spicyapi.ai](https://spicyapi.ai/blog/qwen-image-2-1)). The official card has no safety section (VERIFIED).
- **Weakness *(REPORTED)*:** in hands-on photographic tests, Krea 2 beat it on anatomy, skin and prompt detail ([MyAIForce](https://myaiforce.com/qwen-image-2-1/); [CriticaTV, 21 Sep 2026](https://www.criticatv.com/z-image-vs-flux-klein-vs-qwen-image-krea-2-comfyui/)). Neither review tested painterly styles.

### Krea 2 (Krea): the most "aesthetic" open model, but safety-tuned
- **VERIFIED from the HF card and Krea's page ([HF](https://huggingface.co/krea/Krea-2-Turbo); [krea.ai/krea-2-open-source](https://www.krea.ai/krea-2-open-source)):**
  - v1.0 on 22 Jun 2026.
  - 12B dense DiT, Qwen Image VAE, Qwen3-VL text encoder.
  - Raw is the undistilled base for LoRAs; Turbo is 8 steps at CFG 0.
  - Krea markets it as "the most aesthetic open-source image model".
  - The card's sample prompts are mostly painterly, ink, impressionist, collage and airbrush work. That is a good fit for a gallery.
  - Krea publishes its own style LoRAs, e.g. softwatercolor, darkbrush and vintagetarot (HF, 19–23 Jun 2026).
  - The card says the data was filtered, targeted safety fine-tuning was applied, and **"deployers must implement content filtering or equivalent review."**
- **Expression *(REPORTED, [MyAIForce, 12 Jul 2026](https://myaiforce.com/uncensoring-krea-2/))*:**
  - The safety tuning sanitises bruises, scars and wounds, flattens intense facial expressions, and suppresses nudity.
  - The tuning sits mainly in layers 9–10, and bypass LoRAs target those layers.
  - Lodestones' **Kroma** is an uncensoring fine-tune on the Krea 2 licence (v0.3.1, HF updated 11 Oct 2026). It is tagged not-for-all-audiences and its teacher model was trained on booru and e621 data. Expect an anime and explicit bias.
- **Licence:** a custom Community licence. Reports say it is free below about $1M revenue (one guide also says 50 seats) *(REPORTED, [localaimaster](https://localaimaster.com/blog/krea-2-local-guide))*. I could not parse the PDF.
- **Resources *(REPORTED)*:** about 13 s per 1 MP and about 12.5 GB VRAM at fp8 on an RTX 3090 ([comfylab.dev](https://comfylab.dev/blog/guides-pro/krea-2-comfyui-guide-turbo-model/)). The bf16 transformer is 26.3 GB (VERIFIED from HF file sizes).

### Z-Image (Alibaba Tongyi-MAI): the permissive Apache workhorse
- **VERIFIED ([HF Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo), [HF Base](https://huggingface.co/Tongyi-MAI/Z-Image)):**
  - 6B single-stream DiT, Apache 2.0.
  - Turbo needs 8 function evaluations, runs in under a second on an H800 and fits in 16 GB.
  - The Base model is undistilled, supports CFG and negative prompts, and is pitched for "highest level of creative freedom" and broad styles.
- **Rankings:** AA 934 (VERIFIED); LMArena 1084 *(REPORTED)*.
- **Expression *(REPORTED)*:** widely described as unfiltered, with no runtime filter shipped ([localaimaster](https://localaimaster.com/blog/uncensored-local-image-generation); HF discussion #50, 1 Dec 2025). Reported to know many celebrities ([z-image.me test](https://z-image.me/en/blog/Humans_of_ZImage_Celebrity_Recognition_Test)).
- **Weakness *(REPORTED)*:** a glossy "retouched beauty" look by default ([CriticaTV](https://www.criticatv.com/z-image-vs-flux-klein-vs-qwen-image-krea-2-comfyui/)).
- Lodestones' **Zeta-Chroma** (pixel-space, on Z-Image, Apache) is still work in progress (HF, updated 14 Jul 2026).

### Chroma1-HD and its successors (Lodestones)
- **Chroma1-HD, VERIFIED ([card](https://huggingface.co/lodestones/Chroma1-HD)):** 8.9B, based on FLUX.1-schnell, Apache 2.0. The card says it is "not been aligned with a specific safety filter".
- **Chroma1-Radiance:** pixel-space, native in ComfyUI since v0.3.60 ([Comfy blog](https://blog.comfy.org/p/humo-and-chroma1-radiance-support)); still work in progress (HF updated 27 Jul 2026).
- **Chroma2-Kaleidoscope:** built on FLUX.2-klein-base-4B, Apache. Its card literally says "WIP".
- Quality is below the 2026 frontier, and no arena lists it. It is the most dependable uninhibited base, with a large LoRA ecosystem.

### HiDream-O1-Image (HiDream.ai)
- **VERIFIED ([HF](https://huggingface.co/HiDream-ai/HiDream-O1-Image)):**
  - Released 8 May 2026; Dev-2604 on 14 May. MIT licence.
  - 8B pixel-level unified transformer with no VAE and no separate text encoder, native up to 2048².
  - Optional prompt agents: Gemma-4-31B, then a fine-tune called "Prompt-Refine".
  - Self-reported HPSv3 scores: 10.37 overall and 10.44 on Arts, ahead of FLUX.2-dev (9.28) and Qwen-Image (9.94).
  - The checkpoint is about 35 GB; community FP8 versions exist.
- **Rankings:** AA's X post (May 2026) called Dev-2604 the "leading open weights" model at debut *(REPORTED)*. It now sits at AA 974 and LMArena 1116.
- **Expression *(REPORTED, single anecdote)*:** one user says O1 "has never seen a nipple" ([Comfy-Org discussion](https://huggingface.co/Comfy-Org/HiDream-O1-Image/discussions/5)).

### FLUX family (Black Forest Labs)
- **FLUX.2 [dev], VERIFIED ([HF card](https://huggingface.co/black-forest-labs/FLUX.2-dev); [BFL overview](https://docs.bfl.ml/flux_2/flux2_overview)):**
  - 32B, non-commercial, released 25 Nov 2025 ([MarkTechPost](https://www.marktechpost.com/2025/11/25/black-forest-labs-releases-flux-2-a-32b-flow-matching-transformer-for-production-image-pipelines/)).
  - Pre-training data was "filtered for multiple NSFW categories". Several rounds of safety fine-tuning followed. The repo ships NSFW and IP filters, and "filters or manual review must be used under the license".
  - AA 1000.
  - About 32 GB transformer at FP8. The full FP8 pipeline is about 45 GB on an H100 *(REPORTED, [Spheron](https://www.spheron.network/blog/deploy-flux2-gpu-cloud-production-guide/))*.
- **FLUX.2 [klein] 4B/9B** (15 Jan 2026): Apache 2.0 (4B) and non-commercial (9B). AA 838 and 931.
- **FLUX 3 Image Klein, VERIFIED ([BFL blog, 15 Oct 2026](https://bfl.ai/blog/flux-3-image-klein); [docs](https://docs.bfl.ml/flux_3/flux3_image_klein); [HF](https://huggingface.co/black-forest-labs/flux-3-image-klein)):**
  - 5.6B. Weights are 11.2 GB at bf16 and 5.6 GB at FP8; the INT8 build runs in 8 GB. Native 4K.
  - Distilled version: 16 steps at CFG 1. Base version: 50 steps at CFG 3.5.
  - BFL says not to use it without its 9B Qwen3.5 prompt upsampler (about 28 GB extra).
  - Pre-training dataset filters, safety post-training, and an external red-team by Cinder.
  - Kommunity licence: free commercial use under $5M revenue; requires "content filtering measures" or review "for unlawful or infringing content". The licence has no general nudity ban; it prohibits CSAM, non-consensual intimate images (NCII) and breaches of publicity rights ([LICENSE](https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md)).
  - BFL's numbers: 4.1 s for 1 MP and 15 s for 2048² on an RTX 5090 at INT8 ([VentureBeat](https://venturebeat.com/technology/black-forest-labs-opens-flux-3-image-klein-weights-with-free-commercial-use-limited-to-smaller-businesses)).
- **FLUX 3 Klein reception *(REPORTED, [HN, about 16 Oct 2026](https://news.ycombinator.com/item?id=50123580); [byteiota](https://byteiota.com/flux-3-image-klein-ships-open-weights-8gb-5m-catch/))*:**
  - Many complaints of "half-melted faces" and broken hands.
  - Users speculate that "safetymaxxing" damaged anatomy. That is unconfirmed.
  - Editing gets praise.
- The full FLUX 3 Image (1 Oct 2026, up to 5456×3072) is **API-only**.

### Ideogram 4: excluded under the expression criterion
- **VERIFIED ([HF](https://huggingface.co/ideogram-ai/ideogram-4-fp8); licence "Last Updated: June 3, 2026"):**
  - 9.3B, Qwen3-VL-8B encoder, up to 2048 per side.
  - The reference pipeline screens prompts and outputs through Hive moderation.
  - The licence says "you will not circumvent, remove, alter, deactivate, degrade or thwart" its safety measures.
- **REPORTED:** Civitai says the censorship is baked into the model ([Civitai](https://civitai.com/models/2676710/ideogram-4)).
- It is the strongest open model for typography and design (AA 1026, #2 open), but legally you cannot remove its filters.

### Other models, in brief
- **HunyuanImage 3.0** (28 Sep 2025, VERIFIED from the LICENSE): "does not apply in the European Union, United Kingdom and South Korea", and use outside that territory is unlicensed. That excludes it if the gallery is in the UK. It is also about 168 GB at bf16, and the default config uses 4 GPUs.
- **HY-Image 3.5** (Sep–Oct 2026) is API-only *(REPORTED)*.
- **ERNIE-Image** (Baidu, Apr 2026, Apache, 8B DiT, runs on 24 GB) is aimed at posters and text rendering ([card](https://huggingface.co/baidu/ERNIE-Image)).
- **Ming-Image-0.1-Design** (Ant, 17 Sep 2026, MIT, 6B) targets UI and infographics, and is validated on a single 80 GB GPU.
- **MOSS-Image** (OpenMOSS, Apache, 5B, 11–16 Oct 2026) focuses on layout and JSON prompts and is too new to judge.
- **GLM-Image** (Jan 2026, MIT; AA 888) and **LongCat-Image** (Dec 2025, Apache; AA 858) are not competitive for art.
- **Bria FIBO / FIBO 1.5** (Jul 2026; AA 847) are trained on licensed data for commercial safety, with a gated licence. Not suitable.
- **Kandinsky 5.0 Image Lite** (Nov 2025, MIT, 6B) has no 2026 image successor; Kandinsky 6.0 is a video model.
- **NVIDIA Sana:** no new text-to-image release since 2025. **Cosmos3-Super-T2I** is 64.6B.
- **Stability AI:** I found no new open image model after SD3.5 (Oct 2024).
- **Wan 2.5, 2.6 and 2.7:** no open weights. Wan 2.2 (Jul 2025, Apache) remains the last open Wan model, usable for single frames.
- **ByteDance:** no open Seedream. Bagel (AA 586) is weak, but **SeedVR2** is a useful open upscaler.

### Getting to 4K
- Every shortlisted model except FLUX 3 Klein generates natively at about 4 MP or less. 3840×2160 is 8.3 MP.
- So the plan is native generation at about 2752×1536 (Qwen) or about 2K (Krea/Z-Image), then a 1.4–2× diffusion upscale.
- SeedVR2 is the common open choice. Evidence on upscaler quality is anecdotal ([r/comfyui snapshot, Mar 2026](https://reddit.sentinel-team.org/posts/1rrk71v/snapshots/2026-03-13T21%3A28%3A18.36706Z)).

### Public figures and legal hard lines
- **For political satire, don't rely on the model knowing the name.** Use reference-image conditioning instead: Qwen-Image-2.1 and FLUX 3 Klein take up to 10 references, and HiDream-O1 does subject-driven generation.
- **Licences:** BFL, Ideogram and Krea licences bar NCII and CSAM and point to rights of publicity. None bans nudity or violence as such (VERIFIED for BFL and Ideogram).
- **UK law:** section 66E of the Sexual Offences Act 2003, inserted by section 138 of the Data (Use and Access) Act 2025, has been in force since **6 Feb 2026**. It makes it an offence to *create* a "purported sexual image" (nude or sexual) of a real adult without consent *(REPORTED via the [Parliament commencement letter](https://api.parliament.uk/committees/publications/51220) and [Olliers](https://www.olliers.com/news/new-law-criminalises-deepfake-creation/))*. So nude caricatures of real politicians are off-limits in the UK whatever the model. Non-sexual satire and violence are not affected by this offence.

## Ranked shortlist for this gallery

1. **Qwen-Image-2.1 (+Turbo)** as the main "sophistication" model.
   - Why: top open arena score, the 16:9 2K size fits the TVs, unified editing with 10 references (for satire and series work), reportedly low refusal, and native ComfyUI support.
   - Caveats: the "research or evaluation" wording in the licence; weaker photographic anatomy than Krea 2. Qwen-Image-2512 (Apache) is the fallback.
2. **Krea 2 Turbo, with Raw for LoRAs** as the painterly and editorial lane.
   - Why: best artistic look in hands-on reports, plus Krea's own style LoRAs.
   - Caveat: it must be "de-safetied" with layer-9/10 bypass LoRAs or Kroma before it will show wounds, nudity or intense emotion. Test that before committing.
3. **Z-Image Base/Turbo** as the uninhibited, cheap, fast lane. Apache licence; the Base model takes LoRAs and CFG.
4. **Chroma1-HD** as an explicitly unaligned fallback with deep LoRA support. Keep an eye on Kroma and Zeta-Chroma.
5. **HiDream-O1-Image (Dev-2604)**: MIT, strong scores on art benchmarks. Run an A/B on nudity before adopting.

Watch only: FLUX 3 Image Klein (native 4K, but filtered and poorly received so far). Not suitable for the expressive brief: FLUX.2 [dev] and [klein] (good for editing and utility). Excluded: Ideogram 4 and HunyuanImage 3.x. Retire: SD3.5 Large (the current default), SDXL and FLUX.1-dev.

## What I could not verify
- **Art-specific arena rankings for open models.** LMArena's Art category exists, but only "GPT Image 2 #1 in all seven" was reported (alphasignal, 30 Jul 2026). AA's category filters render client-side and I couldn't read them.
- **Which AA "Krea 2" entry matches the open checkpoints.** AA lists Krea 2 Large and Medium without Hugging Face links.
- **Krea 2 licence terms** ($1M revenue vs 50 seats; exact filtering clause). The PDF wouldn't parse.
- **Nudity, violence and satire behaviour of Qwen-Image-2.1, HiDream-O1, ERNIE, MOSS and FLUX 3 Klein.** Only vendor or anecdotal claims exist; no neutral test.
- **Whether Chinese models refuse Chinese political figures.** No test found.
- **Independent per-image timings on H100, 4090 and 5090** for Qwen-2.1, Krea 2 and HiDream-O1. All figures above are vendor or blog numbers.
- **HiDream-O1 VRAM at bf16, and Chroma1-HD native resolution.** Not stated in sources I read.
- **Whether FLUX 3 Klein really holds 4K quality.** No independent 4K tests yet; HN users report structure breaking down at high resolution.
- **Midjourney V8.2's official release and its arena position.** Third-party sources only.
- **Third-party Elo and claims from small vendor blogs** (spicyapi, orcarouter, localaimaster). Treat as low confidence.

## Other primary sources
- [AA T2I leaderboard](https://artificialanalysis.ai/image/leaderboard/text-to-image) (live, 17 Oct 2026)
- [flami.pro LMArena summary](https://flami.pro/en/blog/lmarena-text-to-image-leaderboard) (30 Sep 2026)
- [Qwen-Image-2512](https://huggingface.co/Qwen/Qwen-Image-2512)
- [Kroma](https://huggingface.co/lodestones/Kroma)
- [MOSS-Image-SFT](https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT)
- [Ming-Image](https://huggingface.co/inclusionAI/Ming-Image-0.1-Design)
- [Midjourney models docs](https://docs.midjourney.com/docs/models)

Model-card notes used for this report are in `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/e4fdeccc-da6c-4b8b-b2e6-32d24afca616/scratchpad/cards/`.