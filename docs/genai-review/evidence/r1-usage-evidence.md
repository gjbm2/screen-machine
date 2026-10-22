# Screen Machine gen-AI usage audit: evidence report (read-only, 17 Oct 2026)

**Sources:**
- `runpod_jobs.log`
- 2,037 `output/**.json` sidecars
- bucket `favorites`
- `logs/screen_machine.log*`, which covers only 16 Oct 04:55 to 17 Oct 11:31
- `logs/alerts_state.json`
- the schedules, workflows and refiner prompts
- RunPod's own read-only API: GET endpoints, templates, billing, health and job status. Nothing was changed, and no secrets were printed.

Scratch scripts are in `/tmp/claude-1000/genaudit/`. The pulled billing data is in `billing.json` and the parsed sidecars are in `rows.json`.

## 0. Headline numbers
| Metric | Value |
|---|---|
| Lifetime RunPod spend (Mar 2025 to 17 Oct 2026, real billing) | **$1,705 serverless + $69 network volume + $9 pods ≈ $1,783 (≈ £1,370)** |
| Lifetime jobs | **≈ 28,200.** RunPod health counters: sd35 12,534, krea 10,684, flux1 4,040, sdxl 939. This matches a ~28.4k estimate derived from billing. |
| Current rate | ~**54 images/day**, ~1,600/month (north 18/day, south 18/day, the-beast 4.6 batches × 4/day) |
| Typical monthly spend | $50–110. Peak was **$260 in May 2025**, of which $198 was Wan video. |
| Real cost per job | Since Aug 2025: **$0.048**. Last 30 days: **$0.068 per image** ($110 / ~1,620). |
| App's own cost figure | Wall-clock × a hard-coded $0.000584/s ÷ 1.3, so £0.0297 per image. It **under-states the real cost about 1.75×**. Its live price lookup fails on every job (RunPod GraphQL HTTP 400: `Field "id" is not defined by type "EndpointFilterInput"`). |
| Native generation resolution | **1280×720 (0.92 MP) in every text-to-image workflow**. It is then put through RealESRGAN_x4plus to 5120×2880 and resized to 3840×2160. |
| Interactive (UI) use | `_recent`: 193 in Jul 2025, 63 in Aug, 17 in Sep, then 22 / 2 / 3 / 7 / 1 per month up to Mar 2026, and nothing since. **Effectively all volume is now scheduled.** |

## 1. runpod_jobs.log: jobs per endpoint
The log has no timestamps. The file was created on 2025-08-09 08:14 (filesystem birth time) and is append-only, so it covers 2025-08-09 to 2026-10-17 (434 days, ~49 jobs/day). It does not cover the earlier period: it holds only 4 Wan jobs, while 44 Wan videos survive in output/.

| Endpoint | Workflow | Jobs | Share | Lifetime failed / completed (RunPod health) |
|---|---|---|---|---|
| 72q39lkzxcmfca | krea (FLUX.1-Krea-dev + NSFW LoRA 0.5) | 10,738 | 50.2% | 204 / 10,684 (1.9%) |
| ks1jy6452r59ox | sd35 (SD3.5 Large), the default | 8,419 | 39.4% | 138 / 12,534 (1.1%) |
| k98wg36ip597n0 | flux1-dev (fp8 + NSFW LoRA 0.5) | 1,309 | 6.1% | 141 / 4,040 (3.4%) |
| 14n0ueqye259so | sdxl | 820 | 3.8% | 12 / 939 (1.3%) |
| oh422aycpybd5u | kontext 1pic/2pic + upscale | 84 | 0.4% | counters 0 |
| wxd8n6sak3urv5 | wan i2v / first-to-last | 4 | 0.02% | counters 0 |

Position in the file shows a stable krea/sd35 mix throughout. flux1 bulges at 45–60% of the file, Kontext disappears after about 84%, and Wan appears only at about 7% and 90%.

## 2. Volume and spend over time (RunPod billing, the only reliably dated source)
Jobs per month are estimated as billed seconds ÷ average billed seconds per job for each endpoint. Those averages were measured Aug 2025 to Oct 2026: krea 56.8 s, sd35 47.2 s, flux1 85.2 s, sdxl 45.8 s, kontext 124 s, wan 867 s.

| Month | sd35 | krea | flux1 | old flux | sdxl | kontext | wan | ≈ jobs | $ |
|---|---|---|---|---|---|---|---|---|---|
| 2025-03 | 298 | – | – | 647 | – | – | – | 945 | 94* |
| 2025-04 | 632 | – | 159 | 475 | 59 | – | 184 | 1,508 | 184 |
| 2025-05 | 1,227 | – | 217 | – | 3 | – | 302 | 1,748 | 260 |
| 2025-06 | 596 | – | 613 | – | 36 | – | 3 | 1,248 | 59 |
| 2025-07 | 586 | – | 720 | – | 23 | 252 | 7 | 1,588 | 89 |
| 2025-08 | 512 | 598 | 248 | – | 24 | 44 | – | 1,427 | 66 |
| 2025-09 | 1,179 | 1,197 | 60 | – | 36 | 14 | 2 | 2,488 | 102 |
| 2025-10 | 483 | 906 | 346 | – | 26 | 1 | – | 1,761 | 80 |
| 2025-11 | 522 | 1,454 | 50 | – | 55 | 9 | – | 2,091 | 88 |
| 2025-12 | 325 | 771 | 41 | – | 47 | 4 | – | 1,187 | 50 |
| 2026-01 | 443 | 908 | 109 | – | 74 | 3 | – | 1,537 | 64 |
| 2026-02 | 581 | 1,199 | 109 | – | 27 | 6 | – | 1,922 | 92 |
| 2026-03 | 938 | 1,010 | 126 | – | 168 | 1 | – | 2,243 | 105 |
| 2026-04 | 758 | 617 | 47 | – | 60 | – | – | 1,483 | 72 |
| 2026-05 | 0 | 3 | – | – | – | – | – | 3 | 0 |
| 2026-06 | 679 | 292 | 20 | – | 56 | 1 | – | 1,048 | 46 |
| 2026-07 | 74 | 70 | 11 | – | 21 | – | – | 176 | 9 |
| 2026-08 | 824 | 625 | 56 | – | 87 | 2 | – | 1,594 | 81 |
| 2026-09 | 793 | 662 | 79 | – | 106 | – | 2 | 1,641 | 111 |
| 2026-10 (to 17th) | 315 | 429 | 8 | – | 31 | – | – | 784 | 54 |

\*Mar 2025 also includes $45 on other, older test endpoints. The system was essentially off in May–Jul 2026.

**GPU price drift:**
- The endpoints allow only 80 GB A100/H100 GPUs. sd35 and flux1 also allow L40/L40S/6000 Ada, but at lower priority.
- Effective $/billed-hour was flat at about **$2.73/h** (A100 rate) from Jun 2025 to Jan 2026.
- It has risen to **$4.45–4.81/h in Sep–Oct 2026** (H100-class), so cost per second is up about 70%.

## 3. Sidecars: mix, video share, timings, cost, resolution
**Caveat: survivor bias.** The schedules run `purge days:30 include_favorites:false` daily. So anything older than 30 days in output/ is either a favourite or an orphan. The last 30 days (17 Sep to 17 Oct) are a complete sample: north 540, south 544, the-beast 536.

Sidecars by month and workflow (2,037):

| Month | unrecorded† | krea | sd35 | flux1 | sdxl | kontext | wan-i2v | other | total |
|---|---|---|---|---|---|---|---|---|---|
| 2025-05 | 121 | – | 51 | 3 | – | – | 25 | – | 200 |
| 2025-06 | 2 | – | 22 | 31 | – | – | 1 | – | 56 |
| 2025-07 | 2 | – | 20 | 14 | 2 | 45 | 1 | 5 | 89 |
| 2025-08 … 2026-01 | 0 | 8 | 17 | 3 | 4 | – | – | – | 30 |
| 2026-09 | 239 | 224 | 213 | 30 | 32 | – | 2 | – | 740 |
| 2026-10 | 298 | 344 | 208 | 3 | 28 | – | – | – | 881 |
| undated (tmp*) | 3 | – | 22 | 1 | – | – | 15 | – | 41 |

†"Unrecorded" means the batch path (`publish:false`, the-beast, plus 76 test "a naked woman" images on 27–28 May 2025) writes `workflow: null`. In the last 30 hours of logs, the-beast batches went 16× to sd35 and 8× to krea.

**Last 30 days, north + south (1,084 images):** krea 568 (52%), sd35 421 (39%), sdxl 60 (6%), flux1 33 (3%). The router is gpt-4o using the `style_guidance` text: photographic prompts go to krea, painterly or surreal ones to sd35.

**Video vs image:**
- Retained sidecars: 46 of 2,037 are video (2.3%), almost all from Apr–Jul 2025.
- Jobs since Aug 2025: 4 of 21,374.
- Lifetime spend: Wan accounts for **$333 / $1,705 (19.5%)** while being about 1.7% of jobs.
- Animation is off now: `animation_on` is unset, so the Animate trigger terminates.

**Generation time (app wall clock, which includes queue, cold start, ESRGAN, PNG and S3 upload) and cost:**

| Workflow | n | Median s | p90 s | Max s | Last-30d median / p90 s | App £ median | **Real $/job (billing ÷ jobs)** | Real billed s/job |
|---|---|---|---|---|---|---|---|---|
| krea | 576 | 46.7 | 106 | 949 | 46.8 / 108 | 0.021 | **0.052** | 56.8 |
| sd35 | 527 | 44.9 | 83 | 640 | 47.3 / 87 | 0.020 | **0.038** | 47.2 |
| flux1-dev | 84 | 48.3 | 131 | 492 | 59.2 / 201 | 0.022 | **0.070** | 85.2 |
| sdxl | 64 | 39.5 | 91 | 254 | 40.3 / 87 | 0.018 | **0.051** | 45.8 |
| kontext-1pic | 44 | 36.3 | 68 | 510 | – | 0.016 | 0.096 | 124 |
| wan-i2v | 29 | **915** | 1,097 | 1,840 | – | 0.41 | ~0.70–0.88 | 867 |
| wan-ftl | 1 | 2,062 | – | – | – | 0.93 | – | – |

Other timing points:
- Last 30 days: 27.7% of images took more than 60 s, 7.2% more than 120 s, 2.1% more than 300 s, and 0.7% more than 600 s.
- The sd35 median doubled from about 22 s (Jun–Jul 2025, when output was 2160 wide) to about 47 s (Sep–Oct 2026).
- One RunPod status sample (sd35, 17 Oct 11:30): **delayTime 9.8 s, executionTime 40.3 s**.

**Resolution of final files** (PIL headers on all retained files; ffprobe on the videos):
- **sd35:** 3840×2160 496, 2160×1215 51, 5120×2880 5
- **krea:** 3840×2160 576
- **sdxl:** 3840×2160 62
- **flux1:** 2160×1215 44, 3840×2160 40
- **the-beast batch:** 5120×2880 658. This is the raw 4× ESRGAN output, never downscaled.
- **kontext:** mostly 2160×1215
- **Over time:** all of 2025 May–Jul was mostly 2160×1215. Everything since Aug 2025 is 3840×2160 or 5120×2880.
- **Videos:** all 2560×1440, 24 fps, H.264, 12 s. The source is 1280×720 Wan 2.1, upscaled with ESRGAN x2.
- **Every 4K still is a 3× GAN upscale of a sub-1 MP render.** The LATENT-IMAGE width and height are never overridden: logs show only `batch_size` replaced in that node, and the DOWNSCALER set to 3840×2160 or 6400.

## 4. Failures, timeouts, cold starts, refusals
- **Alert ledger** (alerting has been live since 28 Jul 2026):

  | Alert | Count | Dates |
  |---|---|---|
  | `generation.job_failed` | 82 | 6 Aug to 13 Oct 2026 |
  | `runpod.timeout:sd35.json` at 1,200 s ("if it never left IN_QUEUE, check RunPod credit") | **35** | clustered 13–29 Aug 2026 |
  | `runpod.job_failed:kreav2.json` | 9 | 14 Aug to 13 Oct |
  | sdxl timeout | 1 | 17 Aug |
  | flux timeout | 1 | 25 Sep |
  | `sched-wedged:the-beast` | 81 | – |

  Against roughly 4,000 jobs in that period, failures are about 2–3%. `sched-wedged:the-beast` fires because a `wait=True` generation blocks the scheduler loop for up to 20 minutes.
- **Last 30 hours of logs:** 73 generations completed, 0 failures, 0 timeouts. Every job logs the pricing GraphQL 400 described above.
- **Cold starts:**
  - Each endpoint has workersMin 0 and **idleTimeout 5 s**, with FlashBoot on. Jobs arrive every 30–60 minutes per endpoint, so **almost every job starts cold**.
  - Billed time is 47–57 s per sub-1 MP image on A100/H100 hardware. *Inference, not measured per job:* plausibly about half of that is loading the model into VRAM, plus a 4× ESRGAN pass, a roughly 20 MB PNG, and an S3 upload to eu-west-2 from a US datacenter.
  - Network volume `gmaygtw1z4` (50 GB, US-KS-2) is attached to sd35, flux1, sdxl, kontext and wan, which pins them to one datacenter. Krea has no volume and **had no timeouts**. *Hypothesis:* the August sd35 IN_QUEUE timeouts were caused by GPU scarcity under that pin.
- **Dead endpoints:** **Kontext (`oh422…`) and Wan (`wxd8…`) have workersMax = 0**, so they cannot start any worker. This affects:
  - the "Adapt an image", "Combine two images", "Upscale Image", "Image to Video" and "First-to-last" catalogue entries, all still `alexavisible: true`;
  - the north and south `_auto_upscale` trigger, which uses `upscale.json` on the Kontext endpoint.

  Any request to these will sit IN_QUEUE until the 1,200 s timeout. No Kontext time has been billed since Jun 2026.
- **LLM refusals:**
  - **0** refusal strings ("I'm sorry", "can't assist", "content policy" and similar) in the 2,037 stored prompts.
  - **0** refusal lines in the 30 hours of logs.
  - **No finish_reason or SAFETY events are logged.** The only "REJECT" decisions come from the owner's own vision checks: no visible text, and "not dynamic enough to animate".
  - Even so, the refiner prompts carry heavy instructions against gpt-4o's self-censorship:
    - `refiner-enrich.txt.j2`: "ABSOLUTE ZERO CENSORSHIP … FORBIDDEN from refusing";
    - `refiner-adapt.txt.j2`: "FORBIDDEN from applying moral judgment";
    - `refiner-gallerist` / `nous-values`: "never reject or sanitize … names of public figures".

    These were added on 2025-04-22 and 2025-07-13, which suggests real friction earlier.
  - `openai_prompt` does not handle `message.refusal`. A structured-output refusal would end in `json.loads(None)` raising before RunPod is called, with no specific alert. **So refusals are invisible in the data rather than shown to be absent.**
- **The only content gate in the pipeline is the owner's own:** the-beast ("Nous – The Beast", an office screen) runs a gpt-4o vision check: "Approve only if suitable for display in an office context (no nudity …)". Every logged result was APPROVE.

## 5. Owner preference signal (favourites)
`config/live/ratings.json` contains **only live shader-scene ratings** (98 `scene/*` keys) and **no ratings for generated images**. Favourites in `bucket.json` are the only signal: 138 in total, set mostly in May 2025.

| Workflow | Favourites (all) | Since 2025-08-09 | Jobs since 2025-08-09 | Favourites per 1,000 jobs |
|---|---|---|---|---|
| sd35 | **80 (58%)** | 22 | 8,419 | 2.6 |
| wan-i2v (video) | **27 (20%)** | 0 | 4 | – |
| flux1-dev | 10 | 6 | 1,309 | 4.6 |
| krea | 8 | 8 | 10,738 | **0.75** |
| kontext | 3 | – | 84 | – |
| sdxl | 3 | 3 | 820 | 3.7 |
| unrecorded or no sidecar | 7 | – | – | – |

- **27 of the 44 retained Wan videos were favourited (61%).**
- Krea produces the most images but has the lowest favourite rate. The numbers are small and favouriting has mostly lapsed, so treat this as directional only.

## 6. What gets asked for
- **Automated flow:**
  - The cloud schedule picks a daily "inspiration". The `theme_and_style` reasoner turns it into A/B themes and a style. `devise_prompt` then writes a prompt, and the `enrich` refiner rewrites it.
  - All of these LLM steps run on gpt-4o.
  - Last 12 days of styles: Impressionist landscape; Meyerowitz colour street photography; Monet; Victorian daguerreotype; Salgado black-and-white documentary; HDR landscape; Pre-Raphaelite (Millais); chiaroscuro Rembrandt/Caravaggio; Wyeth realism; Michael Kenna long exposure; Monet again; Expressionism (the Great Storm of 1987).
- **Last 30 days of prompts:**
  - By medium: 21% photographic, 9% painting, 5% print or graphic, 14% surreal or abstract. Krea prompts are 36% photographic; sd35 prompts lean surreal (22%) and painting (16%).
  - Most-cited artists: Magritte 14, Grant Wood 8, Monet ~24 (all forms), Dalí 10, Turner 6, Ansel Adams ~12, Constable ~13, Hopper 5, Bruegel 9, Kenna 7, Salgado, Cartier-Bresson, Maier, Kertész, Wyeth.
- **Favourited prompts:**
  - Turner-light derelict harbours
  - Magritte-style surreal scenes (chat bubbles over park benches, an avatar clock tower)
  - Munch's take on Krakatoa
  - Klimt gold-leaf velvet
  - O'Keeffe desert gardens
  - Dutch genre still lifes
  - Alebrije phoenix
  - hyperreal "bacon cliffs" surrealism
- **Manual and test prompts** (devtest and cloud, 2025):
  - "nuns holding firearms" (a provocative painted tableau)
  - Kontext edits (turn the city into cheese, add a bonnet to a kitten, convert to oil painting)
  - Wan motion prompts
  - 76 runs of "a naked woman" on 27–28 May 2025, apparently a capability test
- **Mature or satirical content is almost absent from automated output.** Among 2,037 prompts: "violent" appears 3 times (seas), "battle" 7 times (pirates, castles), and public figures or satire 0 times. Expression limits are therefore exercised mainly by manual and Alexa requests, which leave no searchable record of refusals.
- **The-beast is a typography job:** six fixed phrases ("be good company", "solve for people", "action this day" …) rendered 4 images per batch. A gpt-4o selector then picks one "with correct spelling and letterforms", so **3 of every 4 beast images are thrown away**. That is about 25% of current image volume.

## 7. Deployment facts (RunPod config, read-only)
- **Seven endpoints**, one Docker image per model (`gjbm2/runpod-worker-comfy:dev-<model>`, 50–100 GB container disk). Models are baked into the images; there is no live patch and no S3 model sync.
- **Settings:** workersMax 2 for krea, sd35 and flux1; 1 for sdxl; **0 for kontext, wan and the old flux**. Scaler is QUEUE_DELAY 2–4 s; idleTimeout 5 s (Kontext 20 s).
- **NSFW LoRA:** `Heartsync/Flux-NSFW-uncensored` is **always on at 0.5 for every krea and flux1-dev image**, landscapes included. Kontext references a different LoRA at 1.0 (`sldr_flux_nsfw_v2-studio`), presumably loaded from the network volume. SD3.5 Large and SDXL run without any LoRA.
- **Samplers and steps:** sd35 dpmpp_2m / sgm_uniform, 30 steps, cfg 3; krea euler, 30 steps, cfg 1.5; sdxl 40 steps, cfg 8; Kontext 20 steps; Wan 30 steps, 40 frames, 720p.
- **Output path:** the worker writes a PNG to S3 eu-west-2, and Flask downloads it and converts to JPEG.
- **The LLM layer is all gpt-4o:** router, refiners, reasoners and vision judges. OpenAI spend is not measured. *Estimate:* about 90 calls/day, which is plausibly $15–25/month.

## 8. Data gaps
1. `runpod_jobs.log` has no timestamps and starts on 2025-08-09. Monthly figures are derived from billed seconds.
2. Sidecars are purged after 30 days unless favourited. Older metadata covers only favourites, so about 2,000 of roughly 28,000 generations have metadata.
3. The-beast sidecars carry no workflow.
4. App logs cover only about 30 hours (5 × 10 MB rotation).
5. Failed jobs and LLM refusals leave no sidecar, and refusals are not logged distinctly.
6. There are no image ratings. Favourites are sparse and mostly from 2025.
7. The app's cost field is not billing. Queue time vs execution time is known for only one job.
8. The alert history starts on 28 Jul 2026.
9. RunPod health counters for kontext and wan read 0 (reset or expired).
10. OpenAI usage and spend are not measured.
11. No images were viewed. Quality judgements here rest on favourites and metadata, not on visual inspection.

## 9. What this implies for model choice and deployment
1. **Resolution is the biggest fidelity limit, not just which model is used.** Every 4K wall image is a 0.92 MP render enlarged 3× by RealESRGAN_x4plus. That is a 2021 photo-restoration GAN, and it is poor on brushwork, line and texture. Any upgrade should:
   - render at 2–4 MP natively, or at native 4K where the model supports it;
   - use a diffusion or tiled refine upscaler instead of ESRGAN.

   This matters more than the cadence does.
2. **The workload is small, steady and not latency-sensitive.** It runs at about 54 images/day on an hourly schedule, with essentially no interactive use, so an image can take 2–3 minutes. That leaves room to spend compute on bigger or better models, more steps and two-pass upscaling. At current volume, even 3× the GPU time per image is about $150–300/month.
3. **The current deployment pattern wastes money and adds fragility:**
   - With per-model scale-to-zero endpoints and a 5 s idle timeout, nearly every job starts cold.
   - Endpoints are limited to A100/H100 GPUs, whose effective rate has risen from $2.73/h to about $4.6/h.
   - The network volume pins most endpoints to US-KS-2, which plausibly caused the August IN_QUEUE timeouts.
   - Two endpoints are silently dead, while the UI and Alexa still offer them.

   Options to look at:
   - consolidate into fewer endpoints (or one multi-model worker) with models on a volume, and drop the datacenter pin;
   - allow 48 GB GPUs;
   - batch the hourly jobs;
   - make the app's cost model real: billing API, not a hard-coded rate.
4. **Lanes the owner actually values:**
   - painterly, surreal and conceptual work (sd35 holds 58% of favourites, with many Magritte/Turner/Munch-style images);
   - video (61% of retained clips favourited).

   Krea's photographic lane does the most volume but gets the fewest favourites. The upgrade priority is therefore a top-tier open painterly/illustrative model, a lower-cost modern image-to-video model so animation can come back (Wan 2.1 at about 15 minutes and $0.70–0.90 per clip was 20% of all spend), and fixing or replacing the editing (Kontext) lane.
5. **Text rendering is a requirement.** Picking the-beast's typography from 4 images with gpt-4o means 75% of those images are discarded. A model with reliable text rendering would cut that to 1×.
6. **Expression: the evidence points at the LLM layer, not the image models, as the choke point:**
   - The image side is uncensored through baked open weights plus an always-on NSFW LoRA. That LoRA should become per-request rather than global, because it is applied at 0.5 to every krea and flux image.
   - Every prompt passes through gpt-4o, held in line only by "ZERO CENSORSHIP" instructions in the system prompt, with refusals unlogged. Any re-architecture should treat the prompt LLM as part of the guardrail surface. That means either a permissive or self-hosted LLM for refining and routing, or at minimum logging refusals and falling back to the raw prompt.
   - The owner's own office-screen filter (the-beast) is deliberate and should stay.
   - Legal limits stay in place whatever the choice: no sexual content involving minors, and no sexualised images of real, identifiable people.