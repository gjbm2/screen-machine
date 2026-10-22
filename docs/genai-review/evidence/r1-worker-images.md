# RunPod worker and deployment audit (read-only, 17 Oct 2026)

The images are stuck at mid-2025 and hard to upgrade. Each one also bundles its own copy of the model weights. Two of the six model endpoints are switched off but still listed in the app. Several real bugs are hurting image quality and wasting GPU time. Cold-start speed is acceptable, so the case for changing the deployment pattern rests on how hard upgrades are, not on latency.

Paths below are relative to the worker repo `/home/gjbm2/dev/runpod-worker-comfy` (marked **W:**) or the app repo `/home/gjbm2/dev/screen-machine` (marked **A:**). RunPod was read through GraphQL, REST `GET /v1/endpoints` and `GET /v2/<id>/health`. No secret values were printed, and nothing was changed.

## Inventory

GPU codes, as confirmed by the REST `gpuTypeIds`: AMPERE_80 = A100 80GB, ADA_80_PRO = H100 (PCIe, HBM3 or NVL), ADA_48_PRO = L40, L40S or RTX 6000 Ada, AMPERE_48 = A40 or RTX A6000. FlashBoot is on for every endpoint. All images are pulled from a **private** Docker Hub repo using RunPod registry auth `cm88p5oli…`. The Hub API returns 404, so I could not measure image sizes. The `dev-*` images are also no longer in the local docker store.

Sizes in the table are **estimates**. I added up the weight files: Hugging Face API sizes for public files, and published sizes for the gated BFL and Stability files. On top of that I assumed a base of about 12–14 GB. A local SDXL image from March 2025 measured 21.6 GB, which fits that assumption.

| Endpoint id (name) | Image tag (last build) | Models in the image (+ from the network volume) | GPU pool | min/max (standby) | Idle | Exec timeout | Net vol | Est. size | Usage |
|---|---|---|---|---|---|---|---|---|---|
| `72q39lkzxcmfca` (render-krea) | `gjbm2/runpod-worker-comfy:dev-krea` (6 Aug 2025) | FLUX.1-Krea-dev bf16 23.8 GB, T5-XXL fp16 9.8 + fp8 4.9, CLIP-L, ae, Heartsync NSFW LoRA 0.69, ESRGAN x2 + "x4" (see F3) | A100-80, H100 | 0/2 (2) | 5 s | 600 s | none (any data centre) | ~52–54 GB | 10,684 ok / 204 failed; about 50% of recent jobs |
| `ks1jy6452r59ox` (render-sd35, **DEFAULT**) | `:dev-sd35` (4 Aug 2025) | SD3.5 Large 16.5, T5 fp16 9.8, CLIP-G 1.39, CLIP-L (+ ESRGAN from volume) | A100-80, H100, L40/L40S/6000 Ada | 0/2 (2) | 5 s | 600 s | gmaygtw1z4 (US-KS-2) | ~40–42 GB | 12,534 / 138; about 40% |
| `k98wg36ip597n0` (render-flux) | `:dev-flux1` (5 Aug 2025) | FLUX.1-dev bf16 23.8 (loaded as fp8), T5 fp16 + fp8, CLIP-L, ae, Heartsync LoRA (+ ESRGAN from volume) | A100-80, H100, A40, A6000, L40/L40S, 6000 Ada | 0/2 (2) | 5 s | 1200 s | US-KS-2 | ~52–54 GB | 4,040 / 141; about 5% |
| `14n0ueqye259so` (render-sdxl) | `:dev-sdxl` (4 Aug 2025) | SDXL base 1.0 6.9, two SDXL VAEs (+ ESRGAN from volume) | A100-80, H100 | 0/1 (1) | 5 s | 600 s | US-KS-2 | ~20–22 GB | 939 / 12; about 4% |
| `oh422aycpybd5u` (render-flux1-kontext) | `:dev-flux1-kontext` (6 Aug 2025) | Kontext-dev 23.8, T5 fp16 + fp8, CLIP-L, ae, Heartsync LoRA, ESRGAN (+ sldr_flux_nsfw_v2 LoRA from volume) | A100-80, H100 | **0/0 (disabled)** | 20 s | 600 s | US-KS-2 | ~52–54 GB | 84 jobs ever; serves kontext 1pic, 2pic **and upscale.json** |
| `wxd8n6sak3urv5` (render-wan2) | `:dev-wan2` (built 15 Jul, re-pushed 4 Aug 2025) | Wan2.1 i2v 720p 14B fp16 32.8, umt5-xxl fp16 11.4, clip_vision_h 1.26, wan VAE (+ FLF2V 14B 32.8 from volume) | A100-80, H100 | **0/0 (disabled)** | 5 s | 5000 s | US-KS-2 | ~58–60 GB | last success 27 Sep 2026 |
| `1w6vje8cn33ift` (gjbm_flux1dev -fb) | `timpietruskyblibla/runpod-worker-comfy:3.4.0-flux1-dev` (upstream, 2024) | – | A100, H100 | 0/0 | 5 s | 600 s | US-KS-2 | n/a | dead and unreferenced; template `5169ofvq0e` is also orphaned |

**What every image shares:**
- Base image is `nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04` with Python 3.10.
- torch is **2.7.1 from the default PyPI wheel, built for CUDA 12.6**, even though the install asks for CUDA 11.8 (`--cuda-version 11.8`). Evidence: W:logs/base_build.log:10321.
- ComfyUI is whatever `master` was around 30 Jun 2025 (frontend 1.23.4, so roughly v0.3.43). That build step has been reused from the docker cache ever since.
- Custom nodes come from a snapshot taken 2025-07-09: VideoHelperSuite, Frame-Interpolation, fitsize, kjnodes 1.0.7 and essentials 1.1.0 are active; DepthAnything and IPAdapter are disabled.
- Outputs go from GPUs in Kansas to S3 in eu-west-2 (`stable-diffusion-bucket-gjbm2`). Flask receives a presigned URL and downloads from it.

**Usage and speed:**
- Total model weight in the registry is about 200 GB.
- Average cost per image, from the output metadata: krea £0.031, sd35 £0.024, flux £0.032, sdxl £0.025, wan £0.43.
- Time from submitting a job to getting the result (A:logs/screen_machine.log, 16–17 Oct): sd35 fastest 19 s, median 44 s, p90 52 s; krea fastest 34 s, median 51 s, p90 82 s.
- That works out to about 20–40 s of cold-start overhead. With a 5 s idle timeout and schedules every 30 minutes, nearly every job starts cold.

## Findings, ranked by impact

**F1 – The platform is frozen at mid-2025, and the image design blocks upgrades (High).**
- Evidence:
  - W:Dockerfile:5 sets the CUDA 11.8 base.
  - W:Dockerfile:46 installs ComfyUI without a version. Line 45 shows the earlier pin to 0.3.26.
  - W:2025-07-09_21-38-53_snapshot.json:2 has `"comfyui": null`, and the snapshot pins nothing for ComfyUI either.
  - The `[base 8/14] … CACHED` lines in every *_pipeline.log show the ComfyUI step was never re-run.
- Where things are now:
  - ComfyUI is at v0.41.0 (15 Oct 2026) and torch at 2.14.1 (30 Sep 2026).
  - The upstream worker (`runpod-workers/worker-comfyui` 5.11.0, 21 Sep 2026) uses `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`, torch 2.11 (CUDA 12.8 build), a pinned ComfyUI version, a uv virtualenv, and a quick self-test (`--quick-test-for-ci`) during the build.
  - RunPod's own templates in this account already offer `runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda12.8/13.0` and `runpod/pytorch:…cu1300-torch291`.
- What it costs:
  - The torch build has no kernels for Blackwell cards (B200, RTX PRO 6000, 5090), so those GPU pools can't be used.
  - No SageAttention and no fast fp8 matrix maths are used. W:src/start.sh:93 starts ComfyUI with no performance flags.
  - Any model architecture released after July 2025 needs a newer ComfyUI. That means rebuilding the base image, and because of F2 that forces a full re-download and re-push of every image.
  - Python 3.10 reaches end of life this month.

**F2 – Putting weights inside each image makes building and pushing painful (High).**
- Why every change is expensive:
  - W:Dockerfile:74: the model-download stage is built `FROM base`, so any change to the base re-runs every model download and re-pushes every model layer.
  - The build also depends on a local HTTP model server on this machine: W:start_model_server.sh:5 serves `/mnt/d/dev/models`, and W:fetch_model_2.sh:12 fetches from `host.docker.internal:54893`.
- Evidence of the push pain:
  - Single pushes of 2.5 hours: wan2 on 13 Jul 18:08→20:36, sd35 on 13 Jul 17:41→20:13.
  - The git history is a string of push fixes: "Fixed upload?" (32d0c05), "Fixing push" (483c804), "Parallel push" (22e8485), "Parallel push maybe finally ok?" (fc07df3), then the same message plus "...still slow" (8a1e4b6, f619dc1).
  - W:push_race.sh pushes three tags of the same image at once and keeps whichever finishes first.
- Wasted space:
  - About 42 GB is duplicated across images: T5 fp16 ×4, T5 fp8 ×3, CLIP-L ×4, ae ×3, the LoRA ×3.
  - About 25 GB is never used by that endpoint's workflow:
    - flux1 ships T5 fp16 but its workflow uses fp8 (A:routes/workflows/flux1-dev-scale-l.json:49);
    - krea ships fp8 but kreav2 uses fp16 (A:routes/workflows/kreav2.json:40);
    - kontext ships fp16 but its workflows use fp8;
    - sdxl's two VAEs are never loaded.
  - FLUX.1-dev ships as the 23.8 GB bf16 file but is cast to fp8 on load (A:…/flux1-dev-scale-l.json:62).
- What would work better: RunPod now has **Cached Models** (docs.runpod.io/serverless/endpoints/model-caching).
  - A Hugging Face repo, which can be public, gated or private, is pre-cached on the host at `/runpod-volume/huggingface-cache/hub/`.
  - You are not billed while it downloads, and workers on the same host share it.
  - The limit is one cached model (one repo) per endpoint.
  - That fits one slim, pinned runtime image shared by all endpoints, plus one weights bundle per model family (Comfy-Org-style `split_files`, which can include LoRAs and upscalers).

**F3 – The images, the Dockerfile and the workflows have drifted apart, and the upscaler is mislabelled (High, affects image quality).**
- The working tree has uncommitted changes adding ESRGAN to sd35 and flux1, and T5 fp16 to flux1, krea and kontext (`git diff`).
  - The rebuild checker only looks at *committed* changes to the lines that mention the model type (W:rebuildall_parallel.sh:128). Changes to the download lines never trigger a rebuild.
  - The build logs confirm it: dev-sd35 (W:logs/sd35_pipeline.log:19457–19835) and dev-flux1 (W:logs/flux1_pipeline.log:12137–12674) were built without ESRGAN.
  - So sd35, flux and sdxl get their upscaler from the network volume, and krea and kontext from the image.
- The file named `RealESRGAN_x4plus.pth` from `2kpr/Real-ESRGAN` is **4,885,111 bytes**.
  - That is exactly the size of `realesr-general-x4v3.pth` (a compact general model).
  - The real x4plus is 67,040,989 bytes (xinntao release). The x2plus file (67,061,725 bytes) is genuine.
  - It is baked into krea and kontext at W:Dockerfile:158 and :170.
  - So the most-used endpoint, krea, does its ×4 upscales with a different, lighter model than sd35 and flux1. Recent outputs include 5120×2880 images, which are ×4 results. Confirm with a sha256 check.
- The biggest limit on 4K fidelity is the workflows themselves, whichever model is used:
  - All text-to-image workflows render at **1280×720 (0.92 MP)** and then upscale with a GAN (A:routes/workflows/kreav2.json:4; sd35.json:136).
  - The default upscaler is x2plus (A:src/data/workflows.json:60, 122, 182, 266, 328, 400), so the result is 2560×1440 before it is fitted to 3840×2160.

**F4 – Disabled endpoints are still offered in the app (Medium-High).**
- wan2 and kontext have workersMax 0. The catalogue still points six workflows at them (A:src/data/workflows.json:285, 356, 415, 438, 506), including the general-purpose `upscale.json`.
- A job sent there sits in the queue until Flask's 1200 s timeout (A:routes/generate.py:540) and then raises the misleading alert "check RunPod credit".
- Remove `1w6vje8cn33ift` and template `5169ofvq0e`.

**F5 – The worker never notices when ComfyUI fails, and keeps paying for the GPU (Medium-High).**
- W:src/rp_handler.py:426–437 polls `/history` until outputs appear. When ComfyUI hits an error, the outputs stay empty, so the job runs until RunPod's timeout: 600 s, or 1200 s on flux and 5000 s on wan.
- The server check at :392 retries 100,000 × 100 ms. A worker whose ComfyUI failed to start will swallow every job for the full timeout.
- Live failure counts: krea 204, sd35 138, flux 141 (3.4%), sdxl 12. Flask's alert log (A:logs/alerts_state.json) recorded 35 sd35 timeouts at 1200 s between 13 and 29 Aug 2026, and 9 failed krea jobs, the last on 13 Oct.
- Smaller bugs in the same file:
  - only the last image is returned (:281–284);
  - the video fallback takes the newest `output_video*` file in a shared folder, so on a warm worker it can return the previous job's video (:322–326);
  - `sys` is never imported, so the restart trigger crashes with a NameError (:379);
  - the progress task is not stopped on error paths (:439–443).
- Upstream 5.x instead watches the ComfyUI websocket for `execution_error`, with reconnects.

**F6 – Progress updates go to an old IP address, and the design needs an inbound WAN port (Medium).**
- The default relay address at W:src/rp_handler.py:36 is `ws://185.254.136.244:8765/`. That is the previous public IP (A:docs/DEPLOYMENT_IP_CONFIGURATION.md:142); the current one is 95.141.21.170. No RunPod template sets `BACKEND_WS_URL`.
- What that means in practice:
  - Progress frames (ComfyUI status and step counts plus RunPod job ids; no prompts or images) go to whoever now holds that IP.
  - The app's progress overlay (A:routes/generate.py:574, A:overlay_ws_server.py:180–182) is almost certainly getting no step progress. The app does not log incoming progress, so I could not confirm this directly.
- The whole design depends on RunPod reaching Flask's :8765 overlay socket from the internet. I only describe it here; I did not touch :8765.
- Simpler alternative: the handler calls `runpod.serverless.progress_update(job, …)`, and the app reads it from the `/status` call it already makes every 0.5 s (A:routes/generate.py:571). No inbound port is needed.

**F7 – The network volume ties five endpoints to one data centre (Medium).**
- Volume `gmaygtw1z4` ("gjbm2-2", 50 GB, US-KS-2) is attached to every endpoint except krea. Those workers can only start on A100 or H100 capacity in US-KS-2.
- Judging by the workflows, the volume is only needed for:
  - ESRGAN (sd35, flux, sdxl, wan);
  - `sldr_flux_nsfw_v2-studio` (the kontext 1pic workflow);
  - Wan2.1 FLF2V 14B, a 32.8 GB file read over network storage (the one FLF2V job took 2062 s).
- I did not inspect the volume's contents. The August sd35 queue timeouts may have been capacity in that data centre rather than credit; that is unverified.

**F8 – Security and hygiene (Medium to Low).**
- AWS S3 keys sit as plaintext environment values in eight RunPod templates, not as RunPod secrets.
- The Hugging Face token is passed as a build argument (W:rebuildall_parallel.sh:171, :297; W:Dockerfile:77).
  - Today it is only used in a stage that is thrown away, and the builds use the plain docker driver, so it is not in the pushed images.
  - Mounting it as a BuildKit secret would remove the risk.
- W:.dockerignore does not exclude `.env`, so the HF and Docker Hub tokens are sent to the docker daemon at build time. They are not copied into the image, since there is no `ADD .`.
- Code fetched at boot, both currently off, but traps if switched on:
  - The templates' `dockerArgs` contain an `UPDATE_SCRIPTS` hook that downloads start.sh from the public GitHub repo's main branch.
  - W:src/start.sh:9–41 (`LIVE_PATCH`) downloads rp_handler from main, then runs `git reset --hard` and `checkout v0.3.29`. That would *downgrade* ComfyUI from the ~0.3.43 that is baked in.
  - The templates set `COPY_SCRIPTS`, which nothing reads.
- No download is pinned: every weight comes from `resolve/main` with no commit or checksum.
  - This includes `Heartsync/Flux-NSFW-uncensored` (public, ungated, tagged not-for-all-audiences, creativeml-openrail-m licence) and the 2kpr `.pth` files.
  - `.pth` files are Python pickles. torch 2.6+ loads weights-only by default, which mitigates this.
- Images use mutable `:dev-*` tags. A push silently changes what new workers run, and there is no rollback tag.
- `DETAILED_COMFY_LOGGING` defaults to true, so the start of each prompt and the presigned S3 URLs end up in RunPod logs (W:src/rp_handler.py:308–309, :390).
- Input images travel as base64 inside the `/run` request, which RunPod caps at 10 MB (A:routes/generate.py:516). JPEG references fit; large PNGs would not.

**F9 – The build tooling is home-made and partly broken (Medium to Low).**
- W:docker-bake.hcl does not match the Dockerfile's model names: `sd3` at :60 (the Dockerfile uses `sd35`), `flux1-dev` at :84 (the Dockerfile uses `flux1`), and `flux1-schnell` has no Dockerfile section.
  - So the bake file and the CI workflow (W:.github/workflows/dev.yml, which uses `bake-action`) would publish images with no models.
  - Real builds use W:rebuildall_parallel.sh, which has a hard-coded image list (:209–216) and a krea special case (:121).
- The snapshot restore runs after the model copy (W:Dockerfile:179–188). It reinstalls about 190 pinned packages from a snapshot taken on a Windows machine (`pywin32==310` at :148, `cupy-cuda12x` at :64, `flet` at :76) and fetches from GitHub and the Comfy registry at build time, so builds are not reproducible.
- Dead weight in the image: AWS CLI v2 (only used by `AWS_SYNC`, which is off) and the CUDA 11.8 / cuDNN 8 libraries (torch ships its own CUDA 12 libraries).
- ComfyUI-Manager is installed (W:logs/base_build.log:10562); upstream now switches it to offline mode.
- No test runs during the build.

**F10 – Adding a model today takes about ten manual steps, and none of them check each other.**
1. Copy-paste a Dockerfile download block of about 8 lines.
2. Put the files on the local model server.
3. Edit both rebuild scripts, including the special case.
4. Build (about an hour).
5. Push 40–60 GB (30–150 minutes).
6. Create a template with registry auth and the S3 keys.
7. Create an endpoint.
8. Paste the endpoint id into A:src/data/workflows.json.
9. Write a ComfyUI graph with `{{PLACEHOLDER}}` titles in A:routes/workflows/.
10. Hope every file the graph needs is in the image or on the volume. A missing file fails per F5: the job runs until RunPod's timeout.

Any new architecture also needs F1's base rebuild first, which brings back all of F2.

## What this points to (for the synthesis step)

- **Keep the control:** open weights, self-hosted on RunPod serverless. That leaves no provider-side moderation layer.
- **Change the packaging:** one pinned runtime image, built on or modelled after upstream worker-comfyui 5.11:
  - CUDA 12.8 or newer, torch for CUDA 12.8 or newer (works on Blackwell), pinned ComfyUI and custom nodes, SageAttention;
  - the self-test during the build;
  - immutable version tags.
- **Weights:** keep them out of the image, using a RunPod Cached Model per endpoint (one Hugging Face bundle per model family, pinned revision and sha256). That ends the push problem and lets you drop the network volume, so endpoints are no longer tied to one data centre.
- **Handler fixes:** detect execution errors from the websocket, report progress through `progress_update`, return all outputs, clear the output folder after each job, and drop the hard-coded IP.
- **Housekeeping:** take the dead endpoints out of the catalogue and move the S3 keys to RunPod secrets.
- **Fidelity:** render natively at 2–4 MP and upscale with a tiled diffusion method rather than 0.9 MP plus ESRGAN. Fix the mislabelled x4 upscaler regardless.