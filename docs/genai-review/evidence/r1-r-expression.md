# Expression, guardrails and licensing audit: Screen Machine gen-AI stack (17 Oct 2026)

## Bottom line

1. **There are four legal lines to keep, and the stack needs nothing beyond them** (detail in §6):
   - no sexual images of children, whatever the style;
   - no photoreal-looking intimate images of real people;
   - no image-edit tool that can be shown to work as a "nudifier";
   - no realistic extreme pornography.

   Painterly or cartoon satire of politicians, artistic nudes of fictional adults and non-pornographic violence are all outside UK criminal law.
2. **Today the stack has no filter at all.** There is no safety checker in `runpod-worker-comfy/src/rp_handler.py`, none in the ComfyUI graphs, and no moderation call in `routes/`.
   - The FLUX [dev] licences require "content filtering … for unlawful content" **or** a human review before display.
   - Scheduled auto-display does neither, so the stack is out of step with the FLUX licence it runs under.
   - A narrow filter that blocks only illegal content (see §7) satisfies that clause and the UK law together, and it filters nothing artistic.
3. **Black Forest Labs' FLUX models are becoming *more* restricted.** Pre-training data is filtered for nudity, and fine-tuning after training suppresses concepts. This holds for FLUX.1 dev, Krea and Kontext, for FLUX.2, and for FLUX 3 Image Klein, released two days ago.
   - FLUX is still the most polished family for non-nude art.
   - It is not the right base for the nudes.
4. **The current "uncensoring" LoRAs are weak and one is risky.**
   - `Heartsync/Flux-NSFW-uncensored` has no stated provenance, no training data and the wrong licence tag.
   - The active "Adapt an image" Kontext workflow loads `sldr_flux_nsfw_v2-studio.safetensors` at strength 1.0. An edit model with a nudity LoRA, fed arbitrary photos, is the exact pattern the new UK offence s.66I covers (in force 29 Jun 2026).
5. **Open models that are permissive about nudity now exist:**
   - Z-Image (Apache-2.0);
   - Chroma1-HD (Apache-2.0);
   - Qwen-Image 2512 and Edit-2511 (Apache-2.0);
   - Qwen-Image-2.1, the best-ranked open model on the arena, has no filter but carries a research-only licence;
   - Wan 2.2 (Apache-2.0) for video.
6. **HunyuanImage 3.0 and HunyuanVideo 1.5 cannot be used in the UK.** Their licence excludes UK territory outright.
7. **The prompt-writing model has to move anyway.** The code calls `gpt-4o` (`routes/openai.py:82`), which shuts down on 14 Jan 2027. Google's Gemini is the least sanitising frontier option:
   - its filters default to off;
   - only child-safety blocks are fixed;
   - it scores highest on SpeechMap's free-speech index.

   Avoid Chinese-lab LLMs as the prompt writer for political satire.
8. **Hosted APIs are not a way round guardrails.**
   - fal.ai needs account approval to switch its checker off.
   - BFL's API needs organisation approval for higher `safety_tolerance` levels.
   - The closed models (GPT Image, Nano Banana, Grok Imagine, Midjourney) block nudity and/or real people heavily.
9. **RunPod's own terms (updated 24 Mar 2026) prohibit "pornography or graphic adult content"** and content that "ridicule[s], mock[s]". The self-hosting platform is itself a contractual risk.

## 1. Summary table

Ratings show how much each model or service self-inhibits: **None / Light / Heavy**, with **?** where unknown. V = VERIFIED from a primary source, R = REPORTED by a secondary source.

| Model / service | Nudity | Satire of public figures | Violence | Licence or terms that matter | Evidence |
|---|---|---|---|---|---|
| FLUX.1 dev / Krea / Kontext (in use) | **Heavy**: NSFW filtered from pre-training, concepts suppressed after training (V) | Light: no refusals; likeness limited to what the model knows (R) | Light (R) | Non-commercial; covers "private entertainment, hobby". Requires filters or manual review (V) | [Kontext card](https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev), [Krea card](https://huggingface.co/black-forest-labs/FLUX.1-Krea-dev), [BFL 26 Jun 2025](https://bfl.ai/blog/flux-1-kontext-dev) |
| FLUX.2 dev (32B) / klein 9B / klein 4B | **Heavy**: "nude and pornographic material" filtered; vulnerabilities cut "77–98%" (V) | Light? | Light (gore filters are API-only) | dev and 9B non-commercial with filter-or-review; 4B Apache-2.0 (V) | [FLUX.2-dev card](https://github.com/black-forest-labs/flux2/blob/main/model_cards/FLUX.2-dev.md), [BFL blog 24 Feb 2026](https://bfl.ai/blog/capable-open-and-safe-combating-ai-misuse), [klein-4B card](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) |
| **FLUX 3 Image Klein** (5.6B, 15 Oct 2026, native 4K) | **Heavy** (expected): dataset filtering plus post-training (R). Early testers complain of anatomy glitches (R) | Weights: Light?. Optional reference filter refuses "living public figures" and NSFW; off by default (V) | Light? | FLUX Kommunity License: free under $5M revenue; filter-or-review; bans "digital replica" and defamatory use (V) | [README §Safety](https://github.com/black-forest-labs/flux-3-image-klein), [safety.py](https://github.com/black-forest-labs/flux-3-image-klein/blob/main/src/flux3/safety.py), [LICENSE](https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md), [VentureBeat 15 Oct 2026](https://venturebeat.com/technology/black-forest-labs-opens-flux-3-image-klein-weights-with-free-commercial-use-limited-to-smaller-businesses) |
| Qwen-Image-2.1 (7B, Sep 2026) | Light: no safety checker; drew 4/4 nudity and 4/4 suggestive probes; NSFW reportedly filtered from pre-training (R) | Light? | None: 5/5 violence (R) | **Qwen Research licence: "research or evaluation purposes only"**. A home gallery is arguably neither (V) | [card](https://huggingface.co/Qwen/Qwen-Image-2.1), [LICENSE](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE), [AtomicChat test](https://huggingface.co/AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF) |
| Qwen-Image 2512 / Edit-2511 | Light (R) | Light? | None? | Apache-2.0 (V) | HF API |
| Z-Image Turbo / Base | **None–Light**: "fully uncensored from scratch… explicit content… on the table" (R, Decrypt 2 Dec 2025) | None: knows celebrities (R) | None? | Apache-2.0 (V) | [HF](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo), [Decrypt](https://decrypt.co/350572/chinas-z-image-dethrones-flux-king-of-ai-art) |
| Chroma1-HD (8.9B, FLUX-schnell base) | **None**: "has not been aligned with a specific safety filter" (V); retrained on NSFW and anime data (R) | None? | None? | Apache-2.0 (V) | [card](https://huggingface.co/lodestones/Chroma1-HD), [comfyui-wiki](https://comfyui-wiki.com/en/models/chroma) |
| HiDream-O1-Image (May 2026) | ? (no safety statement in card) | ? | ? | MIT (V) | [card](https://huggingface.co/HiDream-ai/HiDream-O1-Image) |
| Ideogram 4 open weights (Jun 2026) | ? (`safety.md` is gated) | ? | ? | Non-commercial; Hive moderation available in the CLI (V) | [card](https://huggingface.co/ideogram-ai/ideogram-4-fp8) |
| SD3.5 Large (current default) | Heavy-ish: weak anatomy, filtered data (R) | Light | AUP bans "extreme gore… mutilation, torture" (V) | Stability AUP (effective 30 Sep 2026) bans "sexual acts" content and "bypassing… safeguards" (V) | [use policy](https://stability.ai/use-policy) |
| HunyuanImage 3.0 / HunyuanVideo 1.5 | n/a | n/a | n/a | **"Does not apply in… United Kingdom"**: use and *display of output* outside the licensed territory is unlicensed (V) | [LICENSE](https://huggingface.co/tencent/HunyuanImage-3.0/blob/main/LICENSE), [HV1.5 LICENSE](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5/blob/master/LICENSE) |
| Wan 2.2 (still the newest open Wan; 2.5 and later are API-only) | Light: widely used for adult content with LoRAs (R) | Light? | None? | Apache-2.0 (V) | [Wan-AI on HF](https://huggingface.co/Wan-AI) |
| LTX-2.5 (22B, Jul 2026) | ? (no filter found in the repo) | AUP bans deepfakes "without their consent" and defamation (V) | ? | LTX-2.x community licence: disclose machine-generated content you disseminate; don't circumvent the licensor's filters (V) | [LICENSE-2_x](https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x) |
| **LLMs** | | | | | |
| OpenAI GPT-5.x/6 (gpt-4o retires 14 Jan 2027) | Light: sensitive content allowed in "artistic… contexts"; no explicit erotica (V). Adult mode delayed (R), reportedly paused (R) | Light: satire framing explicitly fine (V) | Light: no "extreme gore" except in artistic contexts (V) | Model Spec 2026-08-18; SpeechMap lab index 57.1 | [Model Spec](https://model-spec.openai.com/2026-08-18.html), [deprecations](https://developers.openai.com/api/docs/deprecations), [TechCrunch 7 Mar 2026](https://techcrunch.com/2026/03/07/openai-delays-chatgpts-adult-mode-again/) |
| Google Gemini 3.8 Flash / 4 | Light: filters default **Off** for 2.5 and later; child safety "always blocked" (V) | **None–Light**: SpeechMap index 86.8, #1 (V) | Light | Prohibited Use Policy bans pornography but allows "artistic considerations" (V) | [safety settings, updated 2026-10-15](https://ai.google.dev/gemini-api/docs/safety-settings), [PUP](https://policies.google.com/terms/generative-ai/use-policy), [SpeechMap](https://speechmap.ai/labs/) |
| Anthropic Claude | Light: AUP bans sex acts and erotica; nudity itself is not named (V) | None: "clearly disclosed parody, satire… not prohibited" (V) | Light: no "glorify[ing] graphic violence, gore" (V) | AUP effective 12 Nov 2026; SpeechMap 71.9 | [AUP](https://www.anthropic.com/legal/aup) |
| xAI Grok (text) | Light (R) | None: SpeechMap 81.7, peak 93.1 (V) | None? | AUP bans pornographic depictions of real people and nudifying (R) | [SpeechMap](https://speechmap.ai/labs/) |
| DeepSeek / Alibaba Qwen3 / Moonshot Kimi | Light? | **Heavy on China-political topics** (R); indices 58.3 / 50.8 / 57.6 (V) | Light? | — | [SpeechMap](https://speechmap.ai/labs/) |
| Mistral | Light | Mixed: index 56.2, peak 87.9; Large 4 scores 48.5% (V) | Light | — | [SpeechMap](https://speechmap.ai/) |
| Self-hosted "abliterated" LLMs (refusal behaviour removed, e.g. huihui-ai Qwen3) | None | None | None | Quality cost not measured (R) | [featherless listing](https://featherless.ai/models/huihui-ai/Qwen3-8B-abliterated) |
| **Hosted APIs** | | | | | |
| BFL API | **Heavy by default**: `safety_tolerance` 0–7, default 2; "organization determine[s] the approved maximum" (V) | Heavy (R) | Gore filtered (V) | — | [API reference](https://docs.bfl.ml/api-reference/utility/generate-an-image-with-flux-3.md) |
| fal.ai | **Heavy by default**: "Disabling it requires account authorization" (V) | — | — | — | [FLUX.2 schema](https://fal.ai/models/fal-ai/flux-2/api), [arguments](https://fal.ai/docs/documentation/model-apis/model-arguments) |
| Replicate | Light: checker on for web; "you can disable… with the API" (V) | — | — | — | [docs](https://replicate.com/docs/reference/how-does-replicate-work) |
| Runware | Light: `safety.checkContent` appears to be opt-in (R) | — | — | — | [docs](https://runware.ai/docs/image-inference/introduction) |
| OpenAI GPT Image 2.x | Heavy: `moderation` = `auto` or `low` only (V) | Light–Heavy (R) | Heavy (R) | AA text-to-image #1, Elo 1220 | [image docs](https://developers.openai.com/api/docs/guides/image-generation) |
| Nano Banana (Gemini image) | Heavy (R) | Heavy: public figures rejected (R) | Heavy (R) | — | [laozhang](https://blog.laozhang.ai/en/posts/gemini-image-generation-people-restriction) |
| Grok Imagine | Heavy on real people since Jan 2026; "spicy" mode is consumer-only (R) | Heavy (R) | ? | Ofcom investigation ongoing (R) | [NPR 12 Jan 2026](https://www.npr.org/2026/01/12/nx-s1-5672579/grok-women-children-bikini-elon-musk), [atlascloud](https://www.atlascloud.ai/blog/guides/grok-xai-nsfw-image-generation-policy) |
| Midjourney / Seedream | Heavy: PG-13 (R) / unverified | Heavy (R) | Heavy (R) | — | — |
| **Compute platforms** | | | | | |
| RunPod (current) | ToS bars "pornography or graphic adult content" (V) | ToS bars content that "ridicule[s], mock[s]" (V) | ToS bars "violent" content (V) | Updated 24 Mar 2026; enforcement on serverless unknown | [ToS](https://www.runpod.io/legal/terms-of-service) |
| Modal | Bars "indecent or obscene" content (V) | Bars defamatory content (V) | — | Agreement effective May 2026 | [terms](https://modal.com/legal/terms) |
| Vast.ai | No adult-content clause found (V, page grep) | — | — | — | [terms](https://vast.ai/terms) |

## 2. What the app does today

- **Prompt LLM:** `routes/openai.py:82` defaults to `model_name="gpt-4o"`.
  - The refiner prompts already say "Do not censor or dismiss prompts" (`routes/data/refiner-pictureeditor.txt:65`).
  - I found no refusal strings in `logs/screen_machine.log*`.
  - OpenAI shuts down `gpt-4o-2024-08-06` and `-11-20` on **14 Jan 2027**, with `gpt-6-luna` as the replacement; `gpt-4o-2024-05-13` goes on 23 Oct 2026 (V, [deprecations](https://developers.openai.com/api/docs/deprecations)).
- **LoRAs:**
  - `Heartsync/Flux-NSFW-uncensored` is downloaded into the flux1, krea and kontext images (`runpod-worker-comfy/Dockerfile:144,156,168`). It is used at 0.5 strength in `flux1-dev-scale-l.json`, `krea.json` and `kreav2.json`.
  - The active "Adapt an image" workflow (`routes/workflows/flux_1_kontext_dev_1pic-l.json`, node 194) loads `sldr_flux_nsfw_v2-studio.safetensors` at 1.0/1.0. It is not in the Dockerfile's download list, so where it comes from is unclear. The only HF copy is [xey/sldr_flux_nsfw_v2-studio](https://huggingface.co/xey/sldr_flux_nsfw_v2-studio), which has no model card (R).
- **No filters anywhere.** I grepped `rp_handler.py`, `routes/generate*.py` and `openai.py` for nsfw, safety, filter and moderation, and found nothing.

**Evaluation of the Heartsync LoRA (V, [HF API and card](https://huggingface.co/Heartsync/Flux-NSFW-uncensored)):**

| Point | Finding |
|---|---|
| Dates | Created and last modified the same day, 2025-05-05 |
| Disclosure | No training data, method, trigger words, rank or sample grid |
| Licence | Tagged `creativeml-openrail-m`, which is impossible for a FLUX.1-dev derivative; the FLUX dev licence governs |
| Example code | Uses `guidance_scale=7.0` and a negative prompt. Both are wrong for guidance-distilled FLUX-dev, a sign of low effort |
| Description | Calls itself "a playground… to test censorship boundaries" |
| Publisher | The Heartsync org is the "KAISAR" team (humangen.ai), whose 133 Spaces are mostly free demo apps ([org page](https://huggingface.co/Heartsync)) |

Generic NSFW LoRAs on FLUX are commonly reported to pull faces and skin towards a stock "glamour" look and to dilute styles (R). It runs at 0.5 on every flux1 and krea image, nude or not. **Verdict:** unknown provenance and an unmeasured style tax. Replace it with a base model that does not need it.

## 3. Open-weight image and video models in detail

**Black Forest Labs (FLUX).**
- Every card since Kontext says: "We filtered pre-training data for multiple categories of 'not safe for work' (NSFW)…". Fine-tuning after training is meant to suppress concepts, "inhibiting certain behaviors and concepts" (V, Kontext and Krea cards).
- FLUX.2 extends this: "multiple categories of nude and pornographic material", with third-party red-teaming by Cinder and "77–98%" fewer vulnerabilities (V, [BFL blog, 24 Feb 2026](https://bfl.ai/blog/capable-open-and-safe-combating-ai-misuse)).
- BFL says these mitigations target synthetic CSAM and NCII (non-consensual intimate imagery), but the data filter removes all nude and pornographic training data. The practical effect is weak nude anatomy, not refusals.
- No ComfyUI path runs BFL's "provided content filters", so the open weights never refuse. The licence still requires *some* filter for **unlawful** content, or a human review (FLUX dev licence §2(e), V, [text](https://raw.githubusercontent.com/black-forest-labs/flux2/main/model_licenses/LICENSE-FLUX-DEV)). Nudity is not unlawful, so a filter scoped to illegal content satisfies the clause.
- **FLUX 3 Image Klein** (VERIFIED from the repo):
  - The reference safety pass is "Off by default".
  - When switched on, it refuses prompts that name "a specific living public figure (celebrities, politicians…)" and outputs with NSFW score above 0.85 (Falconsai classifier).
  - That tells you BFL's intent on satire, even though the weights are not claimed to forget faces.
  - Specs: up to 4K, 5.6B parameters, runs in 8 GB at INT8.
  - Not yet on the Artificial Analysis (AA) leaderboard. BFL's internal tests claim it beats Qwen-Image-2.1 and FLUX.2 [pro] (R).
- **Licence wording to watch:** the FLUX dev licence excludes uses "in direct interactions with or that has impact on end users". A private home gallery for guests is a mild ambiguity, but "private entertainment, hobby projects" is explicitly allowed.

**Qwen-Image.**
- The best primary-ish evidence is AtomicChat's same-seed test on 2.1 ([card](https://huggingface.co/AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF)):
  - "Qwen-Image ships none [no safety checker]"; the stock pipeline "already draws all 9 sensitive categories".
  - Removing the refusal direction from the Qwen3-VL text encoder ("abliteration") changed **0 of 45** outcomes.
  - Conclusion: "What the denoiser never learned to draw, no encoder can add."
- **Abliterated text encoders do not uncensor diffusion models.** Only denoiser fine-tunes or LoRAs do. A FLUX 3 "uncensored encoder" repo admits "We have not measured the effect" (R, [Sakura](https://huggingface.co/webmp3/Sakura-FLUX.3-Image-Klein-TextEncoder-Uncensored-GGUF)).
- Quality: Qwen-Image-2.1 has AA Elo 1042, the top open model. FLUX.2-dev is 1000, HiDream-O1 974, Z-Image Turbo 934, FLUX.2-klein-9B 931, FLUX.1-Krea 878, FLUX.1-dev 841 (V, [AA leaderboard, 17 Oct 2026](https://artificialanalysis.ai/image/leaderboard/text-to-image)).
- The catch is the licence. 2.1 is "research or evaluation purposes only" under Chinese law. The older 2512 and Edit-2511 releases are Apache-2.0.

**Z-Image and Chroma.** These are the permissive-licence, nudity-capable options.
- Z-Image is Apache-2.0 and widely described as uncensored (R). Its quality is about 100 Elo below Qwen-2.1.
- Chroma's card disclaims any safety alignment (V). It is not on AA and is less polished; it is best treated as a fine-tuning base (R).
- A Z-Image HF thread claims the model can produce sexualised minors (R, [discussion 50](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo/discussions/50)). A model with no filter does not hold the child-image line by itself, which is why §7 adds an output guard.

**Video.**
- Wan 2.2 (Apache-2.0) is still the newest open Wan release. HF shows only 2.2-family uploads up to Aug 2026 (V).
- LTX-2.5 is open (Jul 2026) and renders UHD at 3840×2176. Its AUP is a responsible-AI-licence style list: consent for deepfakes, disclosure if disseminated, no circumventing filters. No filter ships in the repo.
- HunyuanVideo 1.5 is excluded in the UK.

## 4. The prompt-writing LLM

- **Gemini:**
  - Adjustable categories (`sexually_explicit`, harassment and others) "default… Off for Gemini 2.5 and later". Core child-safety harms "are always blocked and cannot be adjusted" (V).
  - Google's Prohibited Use Policy bans porn but "may make exceptions… artistic" (V, last modified 17 Dec 2024).
  - SpeechMap ranks Google DeepMind #1 at 86.8; gemini-4-argon scores 87.0% (V, updated 2026-10-14).
  - The app already has a Gemini client (`config/llm.yaml`), so this is the lowest-friction way to stop sanitising.
- **OpenAI:**
  - The Model Spec (2026-08-18) forbids erotica and "extreme gore… except in… artistic or other contexts where sensitive content is appropriate". It names "sexual deepfakes" as the hard line and says satire needs no disclaimer (V).
  - Adult mode slipped from Dec 2025 to Q1 2026 (R, TechCrunch). A single source says it was paused indefinitely on 26 Mar 2026 (R, weak).
  - Fine for tasteful-nude *descriptions*; it softens explicit or gory ones.
- **Claude:** satire is explicitly allowed. Sexually explicit content and glorified gore are not. Nudity is not named (V, AUP effective 12 Nov 2026).
- **Grok:** the most permissive frontier model on political speech (xAI index 81.7, peak 93.1, V). A good fallback for satire.
- **DeepSeek, Qwen and Kimi:** censorship of China-political topics is well documented (R). Avoid them as the prompt writer for political satire.
- **Self-hosted abliterated LLMs:** no refusals at all, but quality is unmeasured and there is no GPU on PC22 to spare for them.

## 5. Hosted and closed services

None of these offers *less* restriction than self-hosting, and most need approval before they get anywhere near it:

- **BFL:** `safety_tolerance` runs 0–7 with a default of 2; levels above the approved one return HTTP 422.
- **fal:** turning the checker off "requires account authorization"; flagged images come back black.
- **Replicate:** the checker can be turned off through the API.
- **Closed models:** GPT Image 2.5 is the top arena model at 1220, but OpenAI's `moderation` setting only goes down to `low`, and nudity is blocked (R). Nano Banana, Grok Imagine since the January 2026 deepfake wave, and Midjourney (PG-13) all restrict nudity and/or real people heavily (R).

## 6. UK legal hard lines (England and Wales), stated factually

1. **Children, in any style.**
   - Indecent photographs and pseudo-photographs: Protection of Children Act 1978 (statute; not re-fetched).
   - Non-photographic "prohibited images", including drawings and CGI, that are pornographic and focus on a child's genitals or depict sexual acts: s.62 of the Coroners and Justice Act 2009 (V, [s.62](https://www.legislation.gov.uk/ukpga/2009/25/section/62)).
   - Making, adapting or possessing a tool "made or adapted for use for creating… CSA images" carries up to 5 years under SOA 2003 s.46A, inserted by the Crime and Policing Act (CPA) 2026 s.72. **Not yet in force** (V, [s.72](https://www.legislation.gov.uk/ukpga/2026/20/section/72)).
2. **Intimate images of real adults.**
   - **Creating** a "purported intimate image" without consent is an offence: SOA s.66E, in force 6 Feb 2026 (SI 2026/31), V, [s.66E](https://www.legislation.gov.uk/ukpga/2003/42/section/66E).
   - It covers only an image that "appears to be, or to include, a photograph or film of the person".
   - "Intimate" includes exposed genitals, buttocks or breasts, cover only by underwear, see-through clothing and sexual acts (V, [s.66D](https://www.legislation.gov.uk/ukpga/2003/42/section/66D)).
   - **Sharing** such an image is an offence under s.66B (since 31 Jan 2024, V, [s.66B](https://www.legislation.gov.uk/ukpga/2003/42/section/66B)). Showing it to guests can count as sharing.
   - **Painted or cartoon caricature that does not look like a photograph is outside both offences.**
3. **Nudifier tools.** SOA s.66I, inserted by CPA 2026 s.99 and **in force 29 Jun 2026** (SI 2026/689), carries up to 3 years.
   - It covers anyone who "makes or adapts a thing… for use as a generator of purported intimate images", judged by what "a reasonable person… would consider".
   - The defence is "all reasonable steps to prevent" use without consent (V, [s.99](https://www.legislation.gov.uk/ukpga/2026/20/section/99)).
   - This is the risk with the Kontext workflow plus NSFW LoRA plus uploaded photos.
4. **Extreme pornography (possession).** CJIA 2008 s.63 (V, [s.63](https://www.legislation.gov.uk/ukpga/2008/4/section/63)) needs *both* of these:
   - the image is "pornographic", meaning produced "solely or principally for the purpose of sexual arousal";
   - it portrays "in an explicit and realistic way" life-threatening acts, serious injury to anus, breasts or genitals, necrophilia, bestiality, or non-consensual penetration.

   Strangulation and suffocation porn is being added (CPA s.104, not yet in force). Artistically justified, non-pornographic violence is outside s.63.
5. **Civil and contractual only.**
   - Defamation applies, and the gallery's guests count as "publication".
   - The UK has no statutory publicity right.
   - The FLUX, Kommunity, LTX and Stability licences add contractual bans on "digital replica" and defamatory use.

## 7. Implications for the redesign

- **Keep a guard for the legal lines only, and nothing else:**
  - If an output has nudity, run a minor/age classifier on it and block anything flagged.
  - If the input is a photograph of a real person, or the prompt names a real person and the style is photoreal, and the output has nudity, block it.

  This satisfies the FLUX/Kommunity licence §2(e), gives the s.66I "reasonable steps" defence, and filters nothing artistic.
- **Split the routing:**
  - FLUX 3 Klein or FLUX.2 for polished non-nude art at 4K.
  - A nudity-capable Apache base (Z-Image, Chroma, Qwen-Image-2512) for the nudes and the bodies, with LoRAs from known sources.
  - Use Qwen-2.1 only if its research-only licence is accepted.
  - Drop SD3.5 as the default.
- **Retire Heartsync,** and remove or guard the `sldr` LoRA on the image-edit path.
- **Move the prompt writer from gpt-4o to Gemini** with filters off, Grok as fallback, and no Chinese-lab LLM for satire.
- **Check the GPU host:** confirm RunPod's position on adult content, or move to a provider whose terms permit it.

## What I could not verify

- How FLUX 3 Image Klein actually behaves on nudity and celebrities (two days old). The Kommunity licence was read through a fetch summary, not in full.
- Ideogram 4's `safety.md` (gated). HiDream-O1 and LTX-2.5 pre-training data filtering.
- SD3.5's training-data NSFW filtering (from memory of the SD3 paper, arXiv 2403.03206).
- Whether Chinese open image models (Qwen, Z-Image) block Chinese political figures; only 2022 ERNIE-ViLG evidence exists.
- xAI's acceptable use policy and Midjourney's guidelines (both 403); Seedream and Imagen policies.
- OpenAI adult mode: the "paused indefinitely" claim rests on one weak source.
- Whether RunPod enforces its adult-content clause on serverless workloads.
- Where `sldr_flux_nsfw_v2-studio.safetensors` comes from in the deployed Kontext image.
- Whether seawolf2357 (Heartsync) is linked to the ginipick/openfree/VIDraft Spaces orgs.
- Quality cost of abliterated LLMs as prompt writers. Measured style drift from the Heartsync LoRA, which needs an A/B test on the owner's prompts.

Relevant paths:
- /home/gjbm2/dev/screen-machine/routes/openai.py
- /home/gjbm2/dev/screen-machine/routes/workflows/flux_1_kontext_dev_1pic-l.json
- /home/gjbm2/dev/screen-machine/routes/workflows/flux1-dev-scale-l.json
- /home/gjbm2/dev/screen-machine/routes/workflows/krea.json
- /home/gjbm2/dev/screen-machine/routes/workflows/kreav2.json
- /home/gjbm2/dev/runpod-worker-comfy/Dockerfile
- /home/gjbm2/dev/runpod-worker-comfy/src/rp_handler.py