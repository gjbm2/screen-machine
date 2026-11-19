# Generator back end: project plan (living)

**Owner:** this session (gen-AI review, started 17 Oct 2026). **Background:** [GENAI_REVIEW_2026-10.md](GENAI_REVIEW_2026-10.md).
**Rule:** this file is updated at every step, so the work can resume from here after an interruption. The status line at each step is the truth; the log at the bottom says what happened.

## Goal

Bring the RunPod generator back end to current models and a lighter deployment:
- keep versatility by workflow: each workflow sets its own render size and finish step. Higher native resolution is an option per workflow where its cost and time are reasonable, not a goal in itself (owner, 17 Nov);
- accurate per-image cost, and reporting on it, reconciled to what RunPod actually bills;
- current open models per lane, chosen by a blind bake-off;
- one pinned ComfyUI worker image in this repo, weights cached on RunPod's hosts, `sm-image` for stills first (then `sm-video` later) instead of seven endpoints.

## Owner rulings that shape the plan

- 17 Oct: hi-fi art; no self-inhibiting models or prompt layers (tasteful nudity, satire, justified violence).
- 18 Oct: legal and licences are handled offline, so they never filter choices.
- 24 Oct: no background services, jobs or soaks without the owner's one-line OK.
- 17 Nov: **pre-spike first**: test one deployment and its performance before building on cached models. Keep it small: no 24 h soak, no over-engineering.
- 17 Nov: native resolution is a per-workflow cost/quality trade-off, not a goal; keep versatility by workflow.
- 17 Nov: accurate per-image costing and reporting is a goal, "even if we have to do something a bit ingenious to infer that".
- 17 Nov: stills first. Video is expensive and slow to test, and stills are most of what we generate, so video waits until the stills are live (step 6, last before retiring).
- 17 Nov: ultracode on for this project. Use subagents only to run workstreams in parallel; no judge panels or appraisal loops.

## Access (checked 17 Nov)

| What | Where | Status |
|---|---|---|
| RunPod | `.env` `RUNPOD_API_KEY`; GraphQL (`modelReferences` on endpoints) + REST v2 `api.runpod.io/v2` | OK; balance $140 |
| Docker Hub | `docker login` as gjbm2 (credential store `desktop.exe`) | OK |
| Hugging Face | `~/.config/screen-machine/hf.token` (fine-grained, gjbm2, repo write) | OK; gated-repo read not ticked |
| Output bucket | `BUCKET_*` env on the current RunPod templates | OK, reuse |
| Docker on WSL | Docker 28.0.4; 770 GB free on / (C: has 166 GB) | OK |

Never print or commit any of these. The HF token goes to RunPod as a RunPod secret only.

## Attendance

Owner, 17 Nov: "continue through this project plan assuming I am not in attendance. If you need me to attend, then send me an email." Email via the app's alert channel: `utils.alerts.channels.email_gmail.GmailChannel().send(subject, body)` with `.env` loaded (goes to ALERT_EMAIL_TO). Email only for a real need: a decision only the owner can make, a spend beyond the plan, or the bake-off favouriting evening.

## Steps

### Step 0: quick fixes on today's models — STATUS: DONE 17 Nov (dd0959ce; frontend deployed, Flask restarted via Spectre). Open: check Krea/Flux 1920×1088 + lanczos ×2 images and cost per image after a day; `_auto_upscale` trigger still targets Kontext (runtime scheduler state, left alone); Alexa adapt/animate intents still default to the hidden workflows
Ships on its own; one Flask restart (via Spectre, peers told first).
1. Move off RunPod REST v1 (retired 15 Nov; `rest.runpod.io` is gone): `routes/jobs/runpod_api.py` endpoint name, `routes/generate_handler.py:73-81` cost lookup. Cost = RunPod `executionTime` (+ `delayTime`) × the serverless per-second rate.
2. Hide Wan (endpoint off) and Kontext (both jobs 27 Oct–17 Nov timed out at 20 min) in the catalogue until they work.
3. `maxwidth` via `locals()` (`generate.py`): the-beast publishes 5120×2880.
4. Accept both `{{NEGATIVE-PROMPT}}` and `{{NEGATIVE_PROMPT}}`.
5. Route "painting" to SD3.5, not SDXL (`refiner-enrich.txt.j2`).
6. JPEG quality 95, no chroma subsampling (`publisher.py`).
7. Vision checks at 1536 px, not 512 "low" (`openai.py`).
8. Krea and Flux latents 1920×1088 instead of 1280×720 (per-workflow choice; check gen time and cost per image after the restart, revert a workflow that doesn't pay its way).
9. Prompt layer: DONE in the generator's own refiner templates (e4369e17: jailbreak text → plain adult-gallery brief, 75-token cap → 80–200 words, keep 'nude'/'blood' in plain words). llm.yaml (gpt-4o fallback → passthrough; describe off gpt-4.1-mini before 14 Jan 2027) sent to cf for whoever owns llm.yaml.

### Step 0b: true per-image cost + reporting — STATUS: DONE 17 Nov (c4bf3b0e; deployed, Flask restarted). Proportional share of each endpoint-hour by billed overlap (idle tail shared proportionally, not to the last job). Check after the first pass: Jobs page → Cost
Finding (17 Nov): RunPod billed **$123.15** for 18 Oct–17 Nov (`GET /v2/billing/serverless`, day buckets; yesterday $4.39), while the app's recorded cost is about half that. Serverless bills the worker's whole life (cold start + model load + run + idle tail), not just the job.
1. **Provisional cost at job end:** (billed cold start + `executionTime` + idle timeout) × the pool's serverless per-second rate, from the job's `/status`.
2. **Reconciled cost, hourly:** a small job in the Flask job tracker (not a new service) reads `/v2/billing/serverless?bucketSize=hour` per endpoint and spreads each hour's `totalAmount` over that hour's jobs by their billed seconds (overlap-weighted for jobs that cross the hour). Idle time with no job goes to the last job before it. The job's `cost` becomes "reconciled" and replaces the provisional figure; the sum per endpoint per hour equals RunPod's bill exactly.
3. **Reporting:** the Jobs page shows each job's cost (provisional or reconciled), and a cost view: per workflow (cost per image, median, count), per day and per month, per source (scheduler, generator, art director, Alexa), with RunPod's own total beside ours so any gap shows.
4. Backfill: reconcile the jobs.db history from 27 Oct (billing buckets go back further).

### Step 1a: pre-spike (does cached-on-demand work, and how fast?) — STATUS: DONE 17 Nov, PASS (results below)
Budget about $10. Nothing heavy on PC22 except the one image build (nice'd, no screen live).
1. Research the mechanics (parallel; workflow wf_e00d66e6-da1, running): Cached Models mount path, revision, private-repo token, GraphQL `modelReferences` shape; the exact ComfyUI-ready files for Qwen-Image-2.1 (+ text encoder, VAE) and the graph.
2. Build one weightless image in `worker/`: upstream worker-comfyui 5.11.0 source (base stage only), ComfyUI **0.45.0** (10 Nov), torch 2.11 cu128. `worker/src/sm_models.sh` (called from start.sh) registers every cached HF snapshot laid out like ComfyUI's models/ folder (or Comfy-Org `split_files/`) in extra_model_paths.yaml. Tag `gjbm2/sm-worker:2026-11-17`. STATUS: building (background, nice 19; log in the session scratchpad logs/build.log).
3. Model repo: private `gjbm2/sm-image-bundle` — DONE @ 4883bb9e: Qwen-Image-2.1 bf16 DiT + Turbo LoRA + Qwen3-VL-8B bf16 encoder + VAE, 33.35 GB, laid out like ComfyUI's models/ folder. Built by `worker/bundle/build_bundle.py worker/bundle/sm-image.json`: a server-side HF copy (CommitOperationCopy), nothing through PC22. Graphs to test: scratchpad g_full.json / g_turbo.json (from the research), to become `routes/workflows/qwen21*.json`.
4. Throwaway endpoint `sm-prespike`: workersMax 1, no volume, all datacentres, PRO 6000 then H100, FlashBoot, cached model = the bundle.
5. Run a handful of jobs: first-ever cold, cold after idle (×3), warm (×3), a co-fired pair, one 4 MP render. Record delayTime, executionTime, worker id, whether the model was already on the host.
6. Verdict against the pass bar, then delete or keep the endpoint.

**Measure per workflow:** time and cost at the workflow's chosen size (e.g. Qwen at 1664×928 vs ~4 MP), so each workflow's size is a measured choice.

**Pass:** typical submit→generation no worse than today (Krea waits ~33 s); worst cold under ~2–3 min; model download not billed.
**Fail → fallback:** same pinned image, weights baked into a per-endpoint layer, no network volume.

**Pre-spike results (17 Nov, RTX PRO 6000 Blackwell, $3.69/h, Qwen-Image-2.1, bundle 33 GB):**

| Case | queue+start (delay) | run (exec) | GPU cost |
|---|---|---|---|
| Very first start (image pull 25 GB + endpoint re-save) | 528 s | 31 s | — (not billed) |
| Cold after 2 / 10 / 30 min idle (FlashBoot worker, model on host) | 1.5 / 12 / 11 s | 31 / 40 / 37 s (≈20–25 s model load) | ≈ $0.04 |
| Warm, Turbo LoRA 8 steps, 2752×1536 (4.2 MP) ×3 | 0–2 s | 12–14 s | ≈ $0.013 |
| Warm, Turbo, 1664×928 | 0 s | 4.6 s | ≈ $0.005 |
| Warm, full 40 steps, 2752×1536 | 0 s | 50 s | ≈ $0.051 |
| Warm, full 40 steps, 1664×928 | 0 s | 15 s | ≈ $0.016 |
| Pair sent together (max 2) | 2nd waited 14 s on the same worker | 12–13 s | |

Verdict: PASS. Cold submit→done 33–52 s at 4.2 MP vs today's Krea ~62 s at 0.9 MP + upscale; download never billed; workers stay on hosts that hold the model. Today's Krea all-in is £0.049/image (reconciled). Quality of the first Turbo image (Turner harbour) is excellent. Lessons: Docker Hub image needs the RunPod registry cred; HF_TOKEN must be on the endpoint env; model path is /runpod/model-store/huggingface/<org>/<repo>/<sha>; RunPod pre-starts extra workers (not billed while initialising).

### Step 1: spike (full `sm-image`) — STATUS: DONE 17 Nov (9c5463f9; deployed; first app job end-to-end OK: 3840×2160 JPEG on devtest, provisional cost £0.034 = 43.7 s billed incl. 6.6 s cold start)
Contenders kept small: painterly = Qwen-2.1 Turbo (+ full) vs Krea (today); photo = Z-Image Turbo vs Flux-dev (today); lettering = Qwen-2.1 vs today. Krea 2, Chroma, SeedVR2 left out of the first pass (add only if a lane needs it).
1. Bundle + Z-Image Turbo (diffusion bf16 12.3 GB, qwen_3_4b TE 8.0 GB, ae VAE 0.3 GB) → ~54 GB.
2. S3 output: RunPod secrets sm_bucket_url / sm_bucket_key_id / sm_bucket_secret (copied from the krea template) in the endpoint env, so the worker returns s3_url like the old one. `worker/endpoints.py` + `endpoints.json` save template `sm-worker` (ybl9x2slrk) and endpoints declaratively. Old template 0n2zw54mp7 to delete.
3. App adapter: worker output `images[]` → result URL; `{{POSITIVE-PROMPT}}` also fills `prompt`; `{{SAMPLER}}` fills `sampler_name`; new graphs in routes/workflows with a Lanczos finish to the screen size; catalogue entries with the new endpoint.

Grow the bundle to the full image set (Qwen-2.1 + Turbo, Z-Image, SeedVR2, finish graphs). App adapter: `output.images[]`, per-workflow timeout, `sampler_name`, catalogue params reach the backend, `prompt_chain` in sidecars.

### Step 2: short bake-off — STATUS: DONE 17 Nov. Owner's blind picks (round1, 20 prompts, no "none good"):
- Photographic: zimage-turbo 4/8, kreav2 2/8, qwen21-turbo 2/8 → Z-Image Turbo (≈£0.012/image vs Krea £0.067 in the round).
- Painterly: qwen21 (full) 7/8, sd35 1/8, qwen21-turbo 0/8 → Qwen-Image-2.1 full.
- Lettering: qwen21-turbo 4/4, flux 0/4 → Qwen-Image-2.1 Turbo. (worker/bakeoff/round1.json → page https://screen.finsburybankforsavings.com/api/bakeoff/round1); email the owner when it's ready. Runs on the live endpoints (no separate sm-eval).
Kept small (owner, 17 Nov: "we don't need to do things dozens of times in order to know if they work").
- Per lane, 2 contenders at most, against today's model as the baseline.
- About 8 prompts per lane, 1 seed: 5 real recent prompts or favourites + 3 expression probes (a nude, a violent scene, political satire). the-beast: its 6 phrases once. Edits: 3. No video here (step 6 has its own small test).
- Published to devtest with the model names hidden; the owner picks per prompt in one short sitting (emailed when ready). Cost per image comes from step 0b.
- A contender that refuses or softens a probe, or clearly loses, is out after that one pass; rerun only a close call.
- Budget about $10–20.

### Step 3: cut-over — STATUS: DONE 17 Nov (918eabda; deployed; sm-image workersMax 3). Refiners route to zimage-turbo / qwen21 (default) / qwen21-turbo (stylised + lettering); verified live: a documentary prompt went to zimage-turbo, a Turner painting to qwen21. Old workflows not offered to refiners, still selectable by hand; Kontext ids in schedules run their replacement (`replaced_by`). the-beast's batch=4 lives in untracked runtime scheduler state (routes/scheduler/the-beast.json) — left as is.
North/south to the winners; old endpoints workersMax 0 for 7 days, then deleted.

### Step 3b: progress + the interactive workflow list — STATUS: DONE 17 Nov (f8d20fb6)
Owner, 17 Nov: "'Getting ready' lasts for ages and I see no updates during rendering"; "did you update all the workflow choices available interactively ... that must be part of the workplan".
- Progress: worker/handler.py sends ComfyUI steps via RunPod progress_update; generate.py feeds them to the tracker. Verified live: "Rendering 3/40 … 40/40", percent 7→95 every 5 s. "Getting ready" tonight was mostly cold starts after my endpoint changes (each template/bundle change rolls the workers: image pull ~3 min), then ~20 s of model loading (now shown as "Starting the workflow").
- Workflow list: only the new ones on the generator page/Alexa/refiners: Photographic (Z-Image Turbo), Painterly (Qwen Image 2.1), Stylised and lettering (Qwen Image 2.1 Turbo), Adapt, Combine. Old ones hidden + replaced_by (SDXL was still offered to refiners with 0 workers). Old endpoints at 0 workers (RunPod 10-worker quota).

### Step 3c: Krea 2 (+ bypass) as a second photographic contender — STATUS: DONE 18 Nov: owner picked Krea 2 4, Z-Image 3, none 1 → Photographic = Krea 2 Turbo (3d92b996); Z-Image kept as 'Photographic, alternative' (owner, 17 Nov: "add krea with a suitable patch")
- Patch: Fedor's bypass (HF mirror Rand000mGuy/filterbypass, `fedor_bypass.safetensors`, 1 kB raw diff on txtfusion.projector columns 9-10; Civitai is region-blocked from the UK). MyAIForce (12 Jul 2026) rates it best: fewer artifacts, keeps prompt adherence; weaker on explicit content (fine for a gallery). Strength 3 (author: 3–5 full, 1–2 subtle).
- Files: Comfy-Org/Krea-2 krea2_turbo_fp8_scaled (13.1 GB), qwen3vl_4b_fp8_scaled TE, qwen_image_vae; bundle 72.7 GB @ 3bfda65d, on a separate endpoint sm-eval (8qsre6gme7s90l) so sm-image (TVs) stays pinned at d624f8b4. Graph routes/workflows/krea2-turbo.json (8 steps, cfg 1, 2048×1152, lanczos finish); catalogue entry hidden.
- Round2: 8 photographic prompts, Z-Image vs Krea 2 (worker/bakeoff/round2.json).

### Step 3d: prompt layer v2 — no implicit censorship, right length, painterly vocabulary — STATUS: DONE 18 Nov (9ee7e556, 4ecb99a6; deployed)
Owner, 17 Nov: refiners/LLM must not de facto remove political content or nudity (only Nous, the-beast's corporate refiner, is censorious on purpose); are the refined prompts too long for modern models?; SDXL/SD3.5 knew painting better, so refiners may need to be more explicit about medium, technique and tonality ("best of both worlds": a strong LLM refiner + a strong renderer in tandem).
- Baseline (worker/bakeoff/refiner_probe.py, 9 probes, 17 Nov): enrich 0/9 lost (126 words); curator 4/9 (nude, naked, Xi, Putin); gallerist 4/9 (naked, Xi, Putin, blood); picture_editor 6/9 (nudity, all politicians, crucifixion); tabloid 3/9 (nudity, wound). Causes: "treat with journalistic gravity", "reframe with respect", safe-only examples.
- Next: research (prompt length per model, anti-softening practice, Gemini safety settings, painterly prompting) → rewrite curator, gallerist, picture editors, theme_and_style/devise_prompt, enrich → probe again (target 0 lost) → a short painterly render check (refiner output on qwen21) → owner email.

### Step 3e: waits for a GPU — STATUS: DONE 18 Nov (14a11830)
Owner, 18 Nov: "'getting ready' still takes a very long time ... unacceptable". Evidence: last 6 h on the new endpoint median 37 s request→picture, p90 67 s; the long waits all coincided with my endpoint changes (each change retires workers; new hosts pull image + 73 GB bundle) and, at 00:3x, a re-pin that left model-less unhealthy workers holding the 3-worker cap while the PRO 6000 pool was throttled (22 min).
- sm-live (v0lfqu2owx3lc4, idle 120 s) for generator + Alexa (workflows.json runpod_id_live); sm-image (idle 5 s) for schedules.
- Warm-up on generator open / workflow change: POST /api/jobs/warm (routes/jobs/warm.py), the workflow's graph at 64×64, ≤1 per workflow per 2 min, ~2 US cents.
- GPU pools: BLACKWELL_96, AMPERE_80, ADA_80_PRO, HOPPER_141.
- Measured after: generator job right after a warm-up 12 s end to end (delay 0.1–0.2 s, run 10–11 s).
- RULE: don't change sm-image/sm-live (template, bundle, GPUs, env) while anyone uses them; changes are disruptive for 5–20 min. Do them late at night, then warm both endpoints and check health. (A blue/green pair would make it seamless; not built.)

### Step 3f: fast first picture without warm dwell — STATUS: DONE 19 Nov (f824a590)
Owner, 18 Nov: "dwell/idle time after action is not the right approach ... architect the system and/or make vendor choices to avoid having to keep instances warm"; the baked-image design was chosen because FlashBoot made start-up a couple of seconds; any replacement must be "comparably performant ... in respect of both total cost per image and cool start time"; understand RunPod's true performance and boot policy. Hosted per-image APIs ruled out (no unfettered control).
- History (job store, 27 Oct → 18 Nov): old baked kreav2 (897 jobs) queue p50 12 s / p90 99 s, start→first frame p50 16 s, request→picture p50 60 s / p90 150 s, £0.049 median; flux p50 87 s / p90 292 s. New: zimage p50 11–15 s / p90 31–48 s, krea2 p50 14 s, £0.009–0.023 median. New is not slower on the record, but the new data is 1–2 days and warm-heavy (bake-offs).
- Root causes seen: workers are pinned to hosts holding the image + models; when other customers rent those GPUs the workers go THROTTLED and jobs queue (8 min, 18 Nov 07:00/07:30); one 73 GB bundle for every model means a resumed worker often holds the wrong model (+10–16 s load) and fresh hosts fetch 73 GB.
- Research (workflow wf_fbd15288-b9b): FlashBoot pauses the container with GPU memory kept and resumes in ~1–2 s if the host is free (independent test: 7.4 s restore; new host 110–456 s incl. a 27 GB pull); idle after the timeout isn't billed. Modal (CPU snapshots) estimated 18–27 s cold, GPU snapshots alpha; Baseten/Beam slower or no PRO 6000. Load speed today ~1.7–2.5 GB/s. Official int8/NVFP4 Comfy-Org weights would halve bytes (needs cu130 + quality A/B). RunPod's ToS text bars adult content (owner handles legal; account-ban risk noted).
- Plan: (1) measure boot policy: worker/probe_boot.py, idle gaps 30 s → 60 min, on a one-model endpoint sm-zimage (bm00rlfpej1cyj, bundle gjbm2/sm-zimage-bundle 20.7 GB, idle 5 s, own template) and on the old baked krea endpoint (max 1 for the test); (2) if resume keeps the model: one endpoint per model (krea2, zimage, qwen = t2i/turbo/edit/combine), shared by schedules and the page, idle 5 s, sm-live and warm-ups' 2-min dwell retired; (3) start-on-intent (first keystroke, not page open); (4) handler prefetch of the graph's files during ComfyUI boot; (5) int8/NVFP4 weights after an A/B.
- 18 Nov: A100 (AMPERE_80) dropped from sm-image and sm-live pools (no fp8, 1.5–2× slower).
- 18 Nov boot probe (idle 5 s on both): the baked image resumed the same worker in ~2 s with the model still loaded after 30 s–5 min idle (exec = warm 32 s), reloaded at 10–20 min (+17 s), and at 40–60 min waited 3–8 min for a host. The cached-model worker resumed but reloaded the model every time (exec 21–28 s vs 7 s warm; total 32–63 s), and was better at 40–60 min (~35 s). Cause (worker logs): RunPod keeps a paused container only while nobody else wants the GPU (PRO 6000 tonight 10–60 s, then THROTTLED; 5090 1–5 min; mornings ~10 min), then removes it; and on a kept container ComfyUI 0.45's DynamicVRAM had dropped the weights (the baked image's old ComfyUI kept them).
- Fix (c154525e, c97209f3, a615fec4): per-endpoint COMFY_ARGS (`--disable-dynamic-vram`) and SM_PREFETCH (weights read into RAM during boot); sm-zimage on image 2026-11-18-3, pools 5090 → PRO 6000 → H100 → H200, max 3. Measured: resumed 13.4–13.9 s at warm speed (5090), cold free host 28–32 s (PRO 6000) vs 32–63 s before and 59–74 s baked; ~$0.008–0.016 per image on the 5090, ~$0.013–0.029 on PRO 6000, vs baked $0.06–0.10. No dwell (idle 5 s).
- Next: one endpoint per model (krea2, qwen bundles like sm-zimage), same flags, several pools, max ≥3; point workflows.json at them; retire sm-live + warm-ups. Do it while the owner is off the app (new hosts re-download). Check --disable-dynamic-vram on Qwen's GPUs first (≥48 GB). Priority FlashBoot (account team) would cut throttling.
- 19 Nov: int8 weights (step 3f item 5) not viable on RunPod now: they need a cu130 worker image (driver >= 580); a probe endpoint limited to CUDA 13 hosts (allowedCudaVersions 13.0) across our four GPU pools got no worker in 25 min. fp8 stays. Recheck later (worker/endpoints.py takes "cuda").
- 19 Nov: built (c90733d2): bundles gjbm2/sm-krea2-bundle @ 07fc453e (18.6 GB) and gjbm2/sm-qwen-bundle @ afeedec4 (33.4 GB); endpoints sm-krea2 jgd96xqhx0r9np (5090 first) and sm-qwen rwl6rp3wgg890o (96/80/141 GB), max 1 for tests, sm-zimage max 1 (unused) for quota. Cut-over DONE 19 Nov on the owner's go (f824a590, worker/cutover_3f.py): catalogue runpod_id per workflow (krea2 → sm-krea2; qwen21/turbo/edit/combine → sm-qwen; zimage-turbo + upscale-finish → sm-zimage), runpod_id_live removed; sm-image and sm-live at 0 (rollback until 26 Nov, then delete), sm-krea2 3, sm-qwen 3, sm-zimage 2. Probe: Qwen resumes after 1-10 min idle in 24-37 s with the model loaded; Krea 2 15-17 s, ~70 s when RunPod moves the worker to another host. App jobs on devtest right after: krea2 33 s, zimage 32 s, qwen21 59 s, qwen21-turbo 77 s (cold, queued behind the other Qwen job), £0.02-0.05.

### Step 3g: what is commissioned is what is drawn — STATUS: IN PROGRESS 19 Nov
Owner, 18 Nov: the system must not discourage, censor or inhibit what is commissioned; art must sometimes shock. Owner, 19 Nov: the adult-content side (its workflows, routing text, test cases, bake-off results and notes) is the deployment's private config, not in this public repo. It lives in `deployments/<id>/private/` (gitignored; the step's log is `docs/PLAN_3G.md` there). In git: the overlay that merges it in (catalogue: `routes/utils.merge_catalogue` and `vite.config.ts`; graphs and template includes: `routes/utils.SEARCH_PATHS`; endpoints: `worker/endpoints.py`) and the eval runner `worker/eval/run.py`, whose shared cases are the everyday ones.

### Steps 4–6 — STATUS: 4 DONE 19 Nov, 5 DONE, 6 DONE 19 Nov
4. the-beast to the typography winner — DONE 19 Nov (8ed01ff8): the Nous refiner now uses qwen21-turbo.json (qwen21.json only for a plainly painted scene); its 6 phrases: 12/12 picks, 5/6 spelled right first try ("TECH POWER" dropped "TO"; the schedule picks the best of 4 by spelling). 5. Edit lane — DONE 17 Nov (d2f63804): qwen21-edit / qwen21-combine on sm-image replace Kontext; adapt refiner defaults to qwen21-edit; upscale.json hidden (Kontext endpoint). App test: snowy-dusk edit of a devtest image, 3840×2160, £0.027. 6. Video (`sm-video`), only after stills are live and settled: its own tiny test (3 stills, 2 contenders), then the owner's chosen rate. Nothing video-related (endpoints, bundles, H3 fit checks) before then.
   - 19 Nov trial (owner: 'enable video for north- and south-screen images for a couple of days'): clips slowed for the TVs' eased loop (c400cb40: RIFE x3 to 15 s, silent; the animator asks for slow continuous motion). A trial copy of each screen's schedule is on top of its stack (base untouched, prevent_unload false): each Generate now throws Animate right after the picture lands when drawing-room animation_on is true (the old timed 08:05/every-120 Animate is left out of the copy). animation_on switched on by the cloud schedule's toggle_animation event. Revert: throw toggle_animation to cloud (off) and DELETE /api/schedulers/<screen>/schedule (pops the copy). First trial clip (north, 13:31) zoomed in on its own; fixed (25d53e2b) by anchoring the picture at the first, middle and last frame and pinning the camera in the animator; north re-animated 13:53. Owner's first view (19 Nov evening): still lifes shouldn't move; no camera moves; gentle life; the ease must really ease; rests between plays should be aperiodic (30 s to 3 min by the size of the movement); anchor first and last frame only. Done: the mid-clip anchor is gone (it made the clip swing away and back twice); the animator never moves the camera or relights; the trial schedules ask the `animate_triage` reasoner (picture + theme + prompt) MOVE or STILL and pass its brief of what moves to Animate; the clip's ease is rendered into a 30 s, 30 fps file after RunPod (routes/clip_ease.py, catalogue `loop`; measured: the browser's 0.065x start showed two 0.6 s ticks and stepped for ~3 s), and the TV plays it at 1x then rests on the picture for a random time; a clip that changes too much of the picture (camera move, `max_motion`) is not published and the still stays. Later the same evening (owner): the clip's play info travels in the MP4's comment tag; clips at 1344x768 looked blurry on the 4K TVs, so the clip's motion is laid onto the full-size still it was made from (snapshotted when Animate starts): still pixels where nothing moves, the clip where it does, frame 0 = the still (~4 min of CPU per clip, in its own process); the TV fades from the still into the clip and back. A clip is part of its still (`still` in its sidecar, routes/clips.py): bucket listings show the still once (Film badge; Moving/Still in the inspection view), publishing the still publishes its newest clip after it, and delete/purge/copy/move take its clips along. Today's trial clips were linked to their stills by time.
   - DONE 19 Nov (62631723): test on sm-video-lab (bundle 82 GB, now deleted), 3 stills (Turner storm, rainy street, misty loch) x MiniMax H3 FL2VA (pruned fp8, Turbo 8-step, Qwen3-VL-32B nvfp4; fp8 not int8 because the worker is cu128) vs Wan 2.2 I2V A14B (fp8, lightx2v 4-step). Both kept the stills' look and moved coherently; H3 followed the asked motion more (the car passes, the dawn brightens, the wave breaks over the bow), runs at 24 fps (Wan 16) and adds a soundtrack; 98-113 s a clip vs Wan 87-98 s on a PRO 6000. H3 live: h3-i2v.json / h3-fl2v.json on sm-video srxuhd77ow2yco (gjbm2/sm-h3-bundle @ 55ed5c3b, 44.4 GB, max 1, idle 5); Wan workflows replaced_by them; animator refiners name them and add an 'Audio:' sentence. Fix found on the way: the refiner's workflow enum only held refiner-visible workflows, so an animate request was switched to a still; a workflow the caller names is now always a choice. App test (devtest, lobster still life): 5 s 1344x768 clip with sound, 316 s cold incl. the host's first bundle download, £0.10. Rate: the owner's call (each clip ~£0.05-0.10). Left: delete Wan wxd8n6sak3urv5 + volume gmaygtw1z4 with sm-image/sm-live after 26 Nov; template sm-worker-video-lab.

### Step 7: retire — STATUS: image endpoints DONE 18 Nov (owner: retire the old image endpoints, keep the old video ones until the new video endpoints are proven). Deleted: krea 72q39lkzxcmfca, flux k98wg36ip597n0, sd35 ks1jy6452r59ox, kontext oh422aycpybd5u, sdxl 14n0ueqye259so, flux1dev 1w6vje8cn33ift, and their templates (RunPod frees a deleted endpoint's template within ~90 min). Old workflow ids keep working: workflows.json `replaced_by` sends them to the new ones. Kept: wan wxd8n6sak3urv5 (template y0cwmm64ym) and volume gmaygtw1z4 it runs on, until step 6.
Old endpoints/templates, volume `gmaygtw1z4`, `runpod-worker-comfy` repo + `dev-*` tags, the old LoRAs, RealESRGAN, local model server `:54893`.

## Facts found along the way

- Usage 27 Oct–17 Nov (jobs.db): 1,147 jobs (~55/day), 98% scheduler; kreav2 908, flux1-dev 211, sd35 26, kontext 2 (both timed out). ~£41 total. Median generating 20 s; median queue wait 33 s (krea), 44 s (sd35), 65 s (flux).
- Live endpoints (GraphQL, 17 Nov): krea `72q39lkzxcmfca` (max 2, no volume), flux `k98wg36ip597n0` (max 2), sd35 `ks1jy6452r59ox` (max 2), kontext `oh422aycpybd5u` (max 1, idle 20), sdxl/wan/flux1dev max 0. All but krea on volume `gmaygtw1z4` (US-KS-2). Idle timeout 5 s.
- Upstream: worker-comfyui GitHub 5.11.0 (21 Sep); Docker Hub newest 5.10.0 (1 Sep), base 14.7 GB.
- REST v2 has `/v2/serverless/{id}/metrics/cold-starts` and `/v2/billing/serverless`; no cached-model field (GraphQL `modelReferences` has it; GraphQL retires early 2027).

## Log

- 17 Nov: plan written. HF token verified (gjbm2, fine-grained). Owner: go on the pre-spike, kept small.
- 17 Nov: workflow wf_e00d66e6-da1 launched: research (RunPod cached models; Qwen-2.1 files + graph) and the step-0 builder, in parallel. Resume: Workflow({scriptPath: ~/.claude/projects/-home-gjbm2-dev-screen-machine/e4fdeccc-da6c-4b8b-b2e6-32d24afca616/workflows/scripts/genai-prespike-and-step0-wf_e00d66e6-da1.js, resumeFromRunId: 'wf_e00d66e6-da1'}).
- 17 Nov: owner: native resolution is per-workflow, not a goal; plan goal and step 0 item 8 reworded.
- 17 Nov: owner: accurate per-image costing + reporting is a goal. RunPod billing v2 hourly buckets per endpoint work with our key ($123.15 billed 18 Oct–17 Nov vs about half that recorded). Step 0b added.
- 17 Nov: owner away; email if needed (Attendance). worker/ created from upstream 5.11.0 + sm_models.sh; image build started.
- 17 Nov: owner: bake-offs kept short. Step 2 cut to ~8 prompts per lane, 1 seed, 2 contenders max.
- 17 Nov: owner: stills first; video deferred to step 6 with its own tiny test.
- 17 Nov: owner: restrained use of parallel agents (one stream by default). Private HF repo gjbm2/sm-image-bundle created.
- 17 Nov: research back (scratchpad res_rp.md, res_models.md). Key facts: cached model path /runpod-volume/huggingface-cache/hub/models--org--repo/snapshots/<sha>/ (SDK alt /runpod/model-store/huggingface/...); private repo via endpoint env HF_TOKEN (a RunPod secret `{{ RUNPOD_SECRET_hf_token }}`; create via POST /v2/account/secrets); ref resolved to a SHA at save, so a repo push needs a re-save of modelReferences; download not billed (worker Initializing); one model per endpoint; saveEndpoint is a FULL replace. Qwen-2.1 in ComfyUI since 0.37; using 0.45.0. Full bf16 sm-image set ~83–97 GB.
- 17 Nov: step 0 shipped (dd0959ce): REST v2 + billed-seconds cost (executionTime + cold start × the GPU's serverless rate; idle tail not yet), Wan/Kontext hidden, maxwidth, NEGATIVE_PROMPT, painting→sd35, JPEG q95/4:4:4, vision 1536 high, Krea/Flux 1920×1088 → crop → lanczos ×2 (RealESRGAN out). Frontend deployed, Flask restarted (peers told).
- 17 Nov: bundle gjbm2/sm-image-bundle built server-side (33.35 GB). sm_models.sh handles both cache layouts, dedupes, logs the mount tree.
- 17 Nov: image gjbm2/sm-worker:2026-11-17 pushed (25 GB unpacked, push 8 min, digest f8c42f7e). RunPod secret hf_token created. Template 0n2zw54mp7; endpoint **sm-prespike 1p4605rkgfo392** (BLACKWELL_96 then AMPERE_80, max 1, idle 5, FlashBoot, model gjbm2/sm-image-bundle resolved to 4883bb9e). Delete both when done.
- 17 Nov: step 0b shipped (c4bf3b0e). Dry run on a jobs.db copy: 1,145 jobs reconciled; 21 days £68.43 billed, £0.41 unattributed; krea median £0.049/image (app had recorded ~60% of the bill).
- 17 Nov: pre-spike first job: worker UNHEALTHY for 30 min — Docker Hub repo gjbm2/sm-worker is private; fixed by putting the existing RunPod registry cred `dockhub-gjbm2` (cm88p5oli0001ky083ca8cu1f) on the template. Re-saving the endpoint then failed model validation until HF_TOKEN (`{{ RUNPOD_SECRET_hf_token }}`) was put in the ENDPOINT env too (template env isn't used for validation). Cached model was already DEPLOYED on the first host at /runpod/model-store/huggingface/gjbm2/sm-image-bundle/<sha> (the SDK layout, not the docs' huggingface-cache one). workersMax 2 for now; RunPod pre-starts several workers (INITIALIZING/THROTTLED aren't billed).
- 17 Nov: endpoint renamed sm-image (same id 1p4605rkgfo392). Bundle grown to 54 GB @ d624f8b4 (+ Z-Image Turbo). worker/endpoints.py pins the bundle branch to its commit (RunPod keeps the first resolution). App graphs routes/workflows/qwen21.json, qwen21-turbo.json, zimage-turbo.json (core-node lanczos finish {{FINISH}} with centre crop to the screen) + catalogue entries (alexavisible false). generate.py: worker_output() adapter (images[] → URL; base64 → output/ file), prompt key for Qwen's encoder, sampler_name, {{FINISH}}, {{SHIFT}}. Not yet restarted.
- 17 Nov: found: after a version change a stale worker with NO model mounted took jobs (validation failed: files not found). Fix: image 2026-11-18 — sm_models.sh exits 2 when no cached models and SM_REQUIRE_MODELS=1 (set on the endpoint), start.sh then exits, so such a worker never goes ready.
- 17 Nov: 9c5463f9 deployed (Flask restart via Spectre, peers told). Workers reset (max 0 → 2) to drop stale model-less workers. Round1 started: 20 prompts × 2-3 contenders = 52 pictures, refiner none, published to devtest. Known gap: the new worker sends no step progress (no websocket relay), so generating_at stays empty; the ring runs on the estimate.
- 17 Nov: round1 made: 56 pictures, no failures. Wall time per picture (incl. queue): krea 33–111 s (£0.04–0.14), qwen21-turbo 15–50 s (£0.010–0.030), zimage-turbo 8–46 s (£0.005–0.034), qwen21 full 55–73 s (£0.042–0.052), sd35 19–71 s (£0.019–0.086), flux 28–97 s (£0.033–0.077). Probes checked by eye: every model rendered the nude, the violence and the satire (no refusals or blanks). Owner emailed with the link.
- Next after picks: step 3 cut-over: point north/south (and the refiners' routing) at the winners via the catalogue (`alexavisible`, `style_guidance`, `default-workflow`), keep old endpoints at workersMax 0 for 7 days.
- 17 Nov: d2f63804 deployed (Adapt/Combine on Qwen edit). e4369e17: refiner brief + length (live, templates read at use). 58 says no need to check with it before deploys today.
- 17 Nov: cf (llm.yaml owner) moved prompt/alexa/describe off gpt-4o/gpt-4.1-mini (1d073229: Gemini Flash-Lite then gpt-6-luna). Refiner passthrough on LLMError committed (not on safety blocks); loads at the next Flask restart (batch with the cut-over). gemini-3.1-flash-lite earliest shutdown 7 May 2027 (cf will move it).
- 17 Nov: owner picked (round1). Cut-over 918eabda deployed + Flask restart (also loaded refiner passthrough 8517bbff). sm-image workersMax 3.
- 17 Nov 23:3x: FOUND + FIXED: the endpoint had stayed on its first template (0n2zw54mp7: image 2026-11-17, no bucket env, no SM_REQUIRE_MODELS); RunPod ignores a new templateId on saveEndpoint, and GraphQL's endpoint env isn't what workers get. So workers returned base64: an Alexa still on north was saved as PNG (not shown on the TV; 67 made worker_output convert to JPEG) and one 4K PNG exceeded RunPod's output size ("worker sent no result"). endpoints.py now updates the endpoint's own template; workers reset; test job returns s3_url. Unused template ybl9x2slrk deleted.
- 17 Nov 23:35: f8d20fb6 deployed (progress + workflow list). My Flask restart killed the owner's Alexa job 'puppy in the style of turner' (I didn't wait for the 1 active job); re-ran it to devtest. Lesson: wait for active jobs before restarting.
- 18 Nov: research (prompt length/form per model, anti-softening, Gemini, painterly) → shared _gallery_brief + _prompt_form in the gallery refiners, must_keep + routes/prompt_guard.py, nudes routed to zimage-turbo. Probe: 17/45 lost → 0/45; all 120–220 words. Painterly renders on qwen21 (Goya, Bacon, naked Putin) keep content, real brushwork. Owner's explicit-nude tests failed on Qwen (default Painterly + refiner none; Qwen's training avoided nudity) and on paraphrase ("legs open" → "wide-legged"); fixed by routing + verbatim first sentence (4ecb99a6), checked on 12 pictures. Krea 2 followed instructions best. cf declined explicit Gemini safety_settings (defaults already OFF; 0 blocks in 6,829 calls). Nous left censorious (owner).
- Open: generator default workflow is Painterly (qwen21) — owner's call whether Photographic should be default.
