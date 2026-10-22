# Screen Machine gen-AI target: the expression-and-risk lens (17 Oct 2026)

**Rule for this lens:** no layer of the stack may hold back tasteful nudity, satire of public figures or artistically justified violence. The only limits kept are UK criminal law. Where round 1 and the fact-check differ, the fact-check wins.

## 1. Deployment pattern: rebuild in place, make it portable

- **Keep the pattern:** self-hosted open weights, ComfyUI, and serverless that scales to zero. At about 55 images a day, nothing else avoids a check by the provider:
  - fal blacks out flagged images unless the account is authorised;
  - BFL, Replicate and Runware switch on checkers or limits by default;
  - closed models block nudity or real people;
  - dedicated pods cost $860–1,800 a month.
- **Rebuild the implementation.** CUDA 11.8 and a mid-2025 ComfyUI can't run 2026 models. Upgrades stall for two reasons: each model has its own baked image, and the endpoints are pinned to the US-KS-2 volume.
- **Stay on RunPod for now.** Its terms conflict with the brief on paper (§3). The fix is to be able to leave in an afternoon.

## 2. Model lineup

**How models were chosen:** prefer Apache or MIT weights with no acceptable-use policy (AUP). Exclude any licence that:
- requires a content filter;
- bans getting round safety features, which makes LoRAs a breach;
- binds you to an AUP the vendor can change;
- excludes the UK.

| Lane | Model | Licence and expression posture | Size and GPU |
|---|---|---|---|
| TVs: painterly, surreal, photographic | **Qwen-Image-2.1**: Turbo (8 steps) for schedules, base for hero images. Native 2752×1536. | Qwen Research licence: no AUP, no filter clause, no anti-circumvention clause. But it is limited to "research or evaluation purposes only" (see below). No runtime checker. Earlier models in this line were pretrained on NSFW-filtered data, so nudes are drawn but anatomy is under-trained. | 14.2 GB DiT + 17.5 GB encoder at bf16 (≈17 GB at int8). RTX PRO 6000 96 GB, then H100, then L40S |
| Nudes and figures; LoRA base | **Z-Image Base / Turbo** | Apache-2.0, no checker, no AUP. Its pretraining data was scored for NSFW content; "uncensored" is only a community claim. LoRAs are not a licence breach. | 6B, about 16 GB |
| Typography (the-beast at Nous HQ) | **Z-Image Turbo** (its card claims accurate text in English and Chinese). **Qwen-Image-2512** if spelling fails. | Apache only, because the-beast is a business display. Non-commercial weights (Qwen-2.1, and today's FLUX.1-Krea-dev) don't belong there. The office-safe gate stays. | 2512: 30 GB at fp8 |
| Edit and multi-reference | **Qwen-Image-2.1** (up to 10 reference images; #1 open model on AA's editing board at 1083). Apache fallback: **Qwen-Image-Edit-2511** (990). | Never used with an NSFW LoRA. | as above |
| Upscale to 3840×2160 | A 1.4× step. **Lanczos plus a light sharpen** for painterly work, **SeedVR2** (in ComfyUI core) for photographic work. | SeedVR2 is Apache. UltraSharpV2 is CC-BY-NC-SA, so home TVs only. | 12–16 GB |
| Video | **Wan 2.2 I2V-A14B** with lightx2v 4-step LoRAs, then RIFE 4.26, then SeedVR2 | All Apache or MIT, no AUP, and the largest LoRA ecosystem. | ≈28 GB at fp8, plus umt5. PRO 6000 or H100 |

**The owner's one decision: the Qwen-Image-2.1 licence.**
- It is the best open model and the cleanest on expression.
- A home gallery is not obviously "research or evaluation".
- A breach is a contract matter, not a crime: the licence can be terminated, under PRC law with Hangzhou courts.
- If the owner says no, Qwen-Image-2512 and Edit-2511 (both Apache) replace it with one edit to `workflows.json`. Both are in the bake-off.

**Excluded:**
- **FLUX.2 [dev] and FLUX 3 Image Klein:**
  - pretrained on filtered data, with safety post-training;
  - filtering or review is a condition of the licence;
  - the licences ban anything that would "enable functionality disabled", so NSFW LoRAs breach them;
  - Klein's optional safety module refuses any recognisable public figure.
- **Krea 2:** a filter is mandatory on every deployment (§4.2), getting round safety measures is banned (§4.1(c)), and the model is tuned against nudity, wounds and intense emotion.
- **Ideogram 4:** bans getting round its safety measures, and its reference code screens through Hive.
- **LTX-2.5:**
  - its licence binds you to Lightricks' AUP "in effect at the time of your use";
  - that AUP bans graphic violence, gore "inspired by real acts of violence", content that "shame[s], humiliate[s]" individuals, impersonation, and sexually explicit content;
  - Lightricks may also "restrict (remotely or otherwise)" use.
- **HunyuanImage 3, HunyuanVideo 1.5 and MiniMax H3:** their licences exclude the UK, including displaying the output there.
- **Retire:**
  - SD3.5 (its AUP bans extreme gore and bypassing safeguards), SDXL, and FLUX.1-dev, Krea and Kontext;
  - the Heartsync LoRA: no provenance, the wrong licence tag, and it is applied at 0.5 to every Krea and Flux image;
  - `sldr_flux_nsfw_v2-studio`: an NSFW LoRA on a photo-edit path is the pattern s.66I targets.

**LoRA rule:**
- Apache bases only.
- Sources whose model card states the base model and training data.
- Pinned by sha256.
- Strength set per request from the refiner's flags.
- Never applied to every image, and never used when editing a photo of a person.

## 3. Hosting

**Endpoints:** two serverless endpoints running one shared image, with FlashBoot on.
- `sm-image`:
  - GPUs: PRO 6000, then H100, then L40S.
  - workersMax 2, a queue-delay scaler of about 20 s and a 30 s idle timeout. North and south fire together, so they share one cold start.
- `sm-video`:
  - GPUs: PRO 6000, then H100.
  - workersMax 1, with a 1,800 s execution timeout.

**Container image:**
- Build from `runpod-workers/worker-comfyui` with `COMFYUI_VERSION=0.41.0` and cu128 torch, so Blackwell cards work.
- Worker 5.11.0 is not on Docker Hub. 5.10.0-base ships ComfyUI 0.34.0, which is older than Qwen-2.1.
- Use immutable tags.

**Weights:**
- One private Hugging Face bundle per endpoint, served through RunPod cached models. Download time is not billed.
- Put only the files the graphs use in each bundle.
- Detach volume `gmaygtw1z4`.

**Hygiene that also reduces exposure under RunPod's terms:**
- Upload outputs straight to the owner's S3 bucket.
- Keep prompts and presigned URLs out of worker logs; today `rp_handler.py` logs both.
- Report progress through `progress_update`. This also gets rid of the stale `ws://185.254.136.244:8765` default.
- Hold the S3 keys as RunPod secrets.
- Use ComfyUI's native `SaveVideo`, because the official handler drops `VHS_VideoCombine` output.

**Are RunPod's terms a real risk?**
- **On paper, yes.** The terms of 24 Mar 2026 bar:
  - "pornography or graphic adult content";
  - content that is "violent", that "ridicule[s], mock[s]… anyone", or that is "false… or misleading";
  - any identifiable person without their written consent.

  Satire breaches this literally.
- **In practice, the risk is small:**
  - the clauses are Termly-style template text, copied word for word in Prime Intellect's and Massed Compute's terms, and written for "postings";
  - RunPod says it "will not actively monitor";
  - I found no enforcement against private serverless use.
- **Worst case:** the account is closed. There is no legal exposure.
- **Response:** stay portable. Don't ask RunPod for a ruling, because a "no" would turn a dormant clause into a known one.

**Alternatives:**
1. **Koyeb, as the standby:**
   - bans only illegal content and has "no general obligation to monitor";
   - bills per second and scales to zero; a PRO 6000 costs $2.20/h, 37% below RunPod;
   - needs the Pro plan at $29/month;
   - its contract (MSA) dates from before Mistral bought Koyeb;
   - its cold starts are unproven.
2. **Beam:**
   - bans unlawful, defamatory, deceptive and publicity-violating content, and "does not generally monitor";
   - its biggest serverless GPU is an H100;
   - CPU and RAM are billed on top.
3. **Comfy API:**
   - no adult-content clause;
   - but it has catch-alls for publicity rights and content that "could reasonably be expected to cause harm";
   - costs about 30% more.

**Rejected:**
- Salad: adult jobs run on strangers' PCs.
- Modal, Vast, Lambda, Hyperbolic and Verda: obscenity clauses.
- Novita, Cerebrium, Shadeform and Together: bans on sexual content.
- fal and DigitalOcean: ban getting round safety measures.
- Replicate: bans gore.

## 4. Prompt and LLM layer

**Today** (live since 11:56, commit dc215fcf):
- The `prompt` and `alexa` tasks in `config/llm.yaml` run gemini-3.1-flash-lite, then gpt-4o.
- Gemini's adjustable filters are Off by default; only the child-safety blocks are fixed.
- flash-lite scores 88.4% on SpeechMap.

**Problems:**
- A refusal falls back to the *more* restrictive vendor. gpt-4o retires on 14 Jan 2027, and its successor gpt-6-luna scores 63.4%.
- If every model fails, no image is made.
- When the LLM waters a prompt down, nobody can see it.

**Target:**
- **Model chain:** gemini-3.1-flash-lite, then gemini-3.8-flash (86.1%), then **passthrough** (the raw prompt plus a fixed style suffix).
  - Drop gpt-4o.
  - Use xAI only if pinned to a high-scoring model: grok-4.7 scores 64.1%, and xAI's 93.1 peak came from grok-4.5.
  - No Chinese-lab LLM for satire (Alibaba scores 50.8).
- **Watering-down check:**
  - Key words in the input must survive the rewrite: proper names, "nude", "blood", "wound" and named artists.
  - If any are dropped, the next model in the chain runs.
  - Every step of the rewrite is logged to the sidecar file.
- **Replace the jailbreak text with an honest brief:**
  - Delete "ABSOLUTE ZERO CENSORSHIP" (`refiner-enrich.txt.j2:60-77`).
  - Delete the false "moderation is downstream" claim (`refiner-adapt.txt.j2:3-19` and `218-223`; `alexa-adapt.txt.j2:3-8`).
  - Say plainly instead: this is a private adult gallery in the UK; render nudity, satire of named public figures and violence faithfully; the only exclusions are the legal lines in §4b; a real person shown nude or in a sexual state gets a non-photographic medium.
  - This keeps clear of Gemini's new "jailbreak" category and protects the API key the show listener also uses.
- **Remove the clauses that soften prompts** in `refiner-gallerist.txt.j2:63-73` and `refiner-pictureeditor.txt:49-56`. Keep `refiner-nous-values` (the office) as it is.
- **Lift the 75-token cap** (`refiner-enrich.txt.j2:16`, `85` and `96`).
- **Add flags** to `refiner-enrich.schema.json.j2`: `people[{name, real}]`, `minors`, `nudity` and `medium`.

### 4b. Real people and the UK legal lines

- **Children, in any style:**
  - indecent photographs or pseudo-photographs (Protection of Children Act 1978);
  - non-photographic images that are pornographic, focus on genitals or show sexual acts, and are grossly offensive (Coroners and Justice Act 2009 s.62). "Child" means anyone who appears under 18, and imaginary children count.
  - The offence covering tools that generate child sexual abuse images (Sexual Offences Act s.46A) is not yet in force.
- **Real adults, intimate images:**
  - Since 6 Feb 2026 it is an offence to create (s.66E) or ask for (s.66F) a fake intimate image of a real person without consent. It applies where the image appears to be a photograph or film.
  - Sharing one is covered by s.66B.
  - On the wording, painted or cartoon caricature falls outside. That is a reading of the text, not an explicit exception.
- **Generator tools:** since 29 Jun 2026, s.66I covers making or adapting something for use as an intimate-image generator, judged by what a reasonable person would think. The defence is to have taken all reasonable steps to prevent use without consent.
- **Extreme pornography:**
  - Criminal Justice and Immigration Act 2008 s.63: pornographic, showing listed acts explicitly and realistically, and grossly offensive.
  - From 6 Nov 2026: s.67A (strangulation), s.67E (relatives) and s.67H (a participant under 16, or pretending to be). All three apply only to realistic pornographic images.
- **Not crimes:** violence that isn't pornographic, and non-intimate satire of public figures.
- **Civil only:** defamation, where showing images to guests counts as publication. The UK has no statutory publicity right.

**How likeness works:**
- Satire of real people is unrestricted in any medium, as long as it is not intimate.
- Get the likeness from reference images rather than relying on the model to know the face.
- If a real person would appear nude or in a sexual state, the refiner switches to an etched, painted or cartoon medium: Gillray, not paparazzi.
- Changing the style instead of refusing keeps the owner's "degrade, never refuse" rule.

### 4c. A narrow output check for legal lines only (optional; the owner dislikes gates)

**What it is:** a check that runs on the CPU in Flask, in `routes/legal_check.py`, called from `publisher.py` and the batch path. Because it runs in Flask, it works whichever GPU host is used. It uses:
- a detector for exposed body parts, such as the Falconsai classifier BFL uses (licences still to confirm);
- a face-age estimate, run only when intimate parts are detected;
- the refiner's flags.

**It applies three rules:**
1. **Apparent minor plus nudity:**
   - Drop it, either before the GPU (from the flags) or after (from the image).
   - Keep no copy and log only the reason; the TV keeps its last image.
   - The passthrough path needs this rule as a keyword screen. Otherwise a Gemini child-safety refusal would send the raw prompt straight to the image model.
2. **Real person (named, or in a reference photo) plus an intimate state plus a photographic medium:** regenerate once in a painted medium; if that fails, drop it.
3. **Everything else passes untouched,** including violence, satire and adult nudity.

**For:**
- On the edit path, rule 2 provides the "reasonable steps" defence under s.66I, which carries up to 3 years.
- Unfiltered models can turn innocent prompts ("bathers", "putti") into apparent-minor nudity. About 1,600 unattended images a month reach screens that guests see.
- It costs about 1 s of CPU per image.

**Against:**
- Age estimates on painted faces are unreliable, so some youthful-looking adults and putti will be lost.
- It is one more component to maintain.
- s.66I fits text-only generation for private use poorly.

**Recommendation:** ship rules 1 and 2, switched on, with every drop logged in the sidecar.

## 5. App-side changes

- **`config/llm.yaml`:** the model chains in §4. This file belongs to b8, so send b8 the change.
- **`routes/openai.py`:**
  - passthrough when `LLMError` is raised;
  - the watering-down check;
  - a log entry for every step.
- **`routes/generate_handler.py`** (around line 518):
  - write the flags and rewrite steps to the sidecar;
  - apply rule 1 before the GPU;
  - do the restyle for rule 2.
- **`routes/data/refiner-*` and `alexa-adapt.txt.j2`:**
  - the §4 edits;
  - update the lines that name models: enrich 33-36, gallerist 44-47, adapt 188, animator 30, and others.
- **New `routes/legal_check.py`,** called from `routes/publisher.py`.
- **`routes/workflows/`:**
  - new graphs: `qwen21-t2i`, `qwen21-edit`, `zimage-t2i`, `wan22-i2v`, `wan22-flf` and `upscale-native`;
  - each at the model's native size, with no always-on LoRA;
  - drop "paintings, pictures, still" from Wan's negative prompt.
- **`src/data/workflows.json`:**
  - the two new endpoint ids;
  - remove the dead entries;
  - add a `backend` field, so moving to Koyeb is a config change.
- **`routes/generate.py`:**
  - read the worker 5.x output format (`output.images[]`);
  - cancel the RunPod job on timeout (lines 710-718);
  - support switching backends.
- **`routes/scheduler/the-beast.json`:** pin it to Z-Image Turbo.
- **`north-screen.json` and `south-screen.json`:** move `_auto_upscale` off Kontext.
- **A new worker repo** replaces the `runpod-worker-comfy` fork.

## 6. Migration (each step ships on its own)

1. **Prompt layer:**
   - new model chain and passthrough;
   - remove the jailbreak text and the false claim;
   - lift the token cap;
   - log each step.

   This works with today's models. Restart Flask through Spectre, after telling the other Flask sessions.
2. **Remove dead entries:** the Kontext, Wan and upscale catalogue items, and `_auto_upscale`. Today each one sits in the queue for 1,200 s and then raises a false credit alert. This also removes the `sldr` path.
3. **Build `sm-image`** with its Hugging Face bundle (Qwen-2.1, Qwen-2512, Z-Image, SeedVR2). Then run the bake-off.
4. **Switch north and south** to the winning model, at native size plus the 1.4× upscale. Retire the krea, sd35, flux and sdxl endpoints and the Heartsync LoRA.
5. **Move the-beast** to Z-Image Turbo.
6. **Add rules 1 and 2 of the legal check.** Then re-enable Adapt and Combine on Qwen editing.
7. **Build `sm-video`** (Wan 2.2), and re-enable Animate when wanted.
8. **Koyeb drill:** run the same image there for one month ($29), keep the config, then downgrade the plan.
9. **Delete the leftovers:** endpoint `1w6vje8cn33ift`, template `5169ofvq0e`, volume `gmaygtw1z4` and the old image tags.

## 7. Monthly cost

About 1,670 images a month. All figures are estimates until the bake-off measures billed seconds.

| Item | Basis | $/month |
|---|---|---|
| TV images, Qwen-2.1 Turbo or base | 45 s (Turbo) or 110 s (base) billed per image, 36 a day, on a PRO 6000 at $0.000969/s | 47 or 115 |
| the-beast, Z-Image Turbo | 8 s × 18 a day | 4 |
| SeedVR2 on photographic stills | 15 s × 14 a day | 6 |
| Gemini flash-lite | about 90 calls a day | 3–5 |
| Hugging Face private storage | about 120 GB | 0–9 |
| **Images total** | today about $110, at 0.92 MP | **about 60–140** |
| Optional video, 5 a day | Wan 2.2, RIFE, then SeedVR2: 3B to 1080p (about 290 s) or 7B to 4K (about 900 s) | +45 or +130 |

## 8. Top risks and how to reduce them

1. **A model that doesn't refuse may still draw badly.** Qwen and Z-Image were pretrained on NSFW-filtered data. The bake-off measures this. Add Apache LoRAs from documented sources, and keep Chroma1-HD (Apache, no safety alignment) in reserve.
2. **The LLM quietly waters prompts down:** caught by the watering-down check, the passthrough, and the per-step sidecar log.
3. **Qwen-2.1's licence:** benchmark the Apache alternative alongside it.
4. **RunPod closes the account:** the image is portable, the weights live on Hugging Face, and the Koyeb drill proves the switch.
5. **Terms change:** hosting and LLM policies can change; Apache and MIT weights can't.
6. **Satire of Chinese political figures on Chinese models:** untested. It is in the bake-off; the fallback is Z-Image plus reference images.
7. **False positives from the check:** narrow rules, restyle before dropping, and every drop logged.

**Bake-off: one evening, judged on the Frame TVs.**
- **About 60 prompts:**
  - 30 from the last 30 days' sidecars;
  - 10 favourites (Turner harbours, Magritte, Munch's Krakatoa, Klimt);
  - the six the-beast phrases;
  - about 14 probes:
    - an adult nude oil study and a photographic art nude;
    - "nuns holding firearms";
    - a Goya-style war scene with blood;
    - a UK prime minister, a US president, Xi Jinping and a monarch, each as a Gillray etching and as a photoreal non-intimate scene;
    - a politician edited into a classical painting.
- **Testing the check:**
  - Test rule 2 with the owner's own photo; his consent takes it outside s.66E.
  - Test rule 1 for false positives only (putti, beach scenes). Never use prompts aimed at producing the illegal case.
- **Contenders:**
  - images: Qwen-2.1 base and Turbo, Qwen-2512 and Edit-2511, Z-Image Base and Turbo, and HiDream-O1 (MIT), with today's Krea and SD3.5 as the baseline;
  - LLM: flash-lite, 3.8 Flash and passthrough.
- **Record for each:**
  - the owner's score from 1 to 5;
  - refusals and dropped words;
  - anatomy failures and spelling;
  - billed seconds.