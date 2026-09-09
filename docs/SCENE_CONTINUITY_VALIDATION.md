# Scene continuity validation — 9 September 2026

This release was checked at three separate levels: deterministic application tests, real local language-model planning, and actual H3 video. Passing one does not establish success at the others. The [research review](H3_SCENE_CONTROL_RESEARCH.md) documents the primary sources and which ideas were implemented; the [guide](SCENE_CONTINUITY.md) explains the controls.

## Automated checks

- **1,569 backend tests passed**, including all five H3 input modes, bounded contract validation, game-state projection, authored-control precedence, rejected/cancelled take preservation, assistant recovery and release packaging. The final full run took 25.65 seconds. Two upstream FastAPI/Starlette test-client deprecation warnings remain.
- **369 frontend tests passed** across 29 files; TypeScript and the production Vite build passed.
- **40 ComfyUI bridge tests passed.**
- Both built-UI Chromium smoke tests passed with zero page errors. Game checks four distinct queued actions and lost-acknowledgement recovery. Scene continuity checks that opening controls causes no POST, while an actual edit becomes authored/locked, survives save/reload, and handles off-screen/deleted actors and duplication. Their APIs use isolated synthetic fixtures; they do not measure actual inference or rendering.
- `git diff --check` passed. Linux Python 3.12 and Node 22 dependency resolution was checked separately from the Windows test runs; GitHub Actions runs the actual Linux build and test workflow.

These are **1,978 automated tests**, plus the browser scenarios. Repeated runs during development are not added to that total.

## What changed after the live counterexamples

The first new scene-control evaluation ran six cases twice. All twelve compiled, but only ten passed its strict automatic checks. A camera-only scene described already-authored furniture as prop rows, and one treated the entire room as an object. Manual review also caught an actor instruction repeating across a multi-shot turn. Generated actor actions are now scoped to their current approved beat, and the evaluator distinguishes known furniture from invented props.

The second twelve-case run passed nine strict cases; eleven compiled. Its most consequential failure was a director response cut off at 1,400 output tokens during a three-shot handoff. The input used 1,576 tokens in an 8,192-token context, so this was an output allowance problem. The director now scales its ceiling with requested shot and actor counts; this request receives 2,900 tokens. It does not request another model pass or require the model to fill that allowance.

That run also found the writer explicitly assigning `hold` while generated director rows assigned `act`. Canonical staging now preserves explicit writer holds in existing generated rows as well as fallbacks. Authored controls retain precedence. An evaluator refinement accepts the fixture's explicitly described empty doorway as an existing architectural feature; it still rejects unknown props, rooms presented as objects, duplicate fixture aliases and duplicate tokens. Earlier reports keep their original scores and check versions.

These counterexamples motivated targeted regressions, including exact production request/repair budgets, cached-stage reuse, off-screen voice staging, first-person body handling, locked actor rosters, duplicate identities and an absent optional continuity block with an explicit lock. No failure was converted into a success merely because it rendered or returned valid JSON.

## Final planning follow-ups

All runs used the installed **Qwen 3.8 27B Q4_K_S**, an 8,192-token context, temperature 0.55 and exclusive ownership on one RTX 4090 with 24 GB VRAM. The [public machine-readable record](scene-control-results.json) preserves six runs and hashes for all 53 case records, including failures, check versions, stage budgets and manual notes. No user stories or machine-specific paths are included.

- The exact three-shot handoff was replayed with two explicitly cached earlier stages and one fresh director request. It passed the checks and compilation. The new response happened to use 1,291 tokens, below even the old ceiling: sampling changed its length, so this is not a controlled causal comparison.
- The subsequent twelve-case run completed **12/12 compilations and 34/34 schemas**, with **10/12 strict cases**, no repairs and median **16.36 seconds**. Both remaining failures were camera-only fidelity cases. One added a second descriptive ID for the known token; the other progressively framed the cast despite a request to keep everyone visible and left a later passive actor's starting pose unspecified.
- Inspection of the actual director request showed the known token's ID was absent from its input. The director now receives a bounded structured object registry in creative, mechanical and recovery paths. Known same-name objects keep separate IDs; unknown generated IDs reusing an exact known name are rejected for repair rather than merged by guessing. Later held actors also regain known posture where no approved state change occurred.
- The targeted camera follow-up passed **2/2**, with no repair or duplicate token. One result's prose still described revealing a character who was listed as visible, so the score does not certify every natural-language camera instruction.
- The final broader Game/Studio regression passed **13/13 model cases**, **38/38 stage schemas**, and **13/13 compilations**; its separate locked-door guard rejected before inference. There were no repairs. Median model-case time was **13.69 seconds**, excluding the **9.69-second** initial preparation; the complete run took **196.47 seconds**. Observed output throughput was **60.49 tokens per measured stage second**, including prompt processing, transport and validation.

These final checks include spoken factual answers, exact player quotations, grounded inspections and combat consequences. Manual review still found pocketing added after pickup, a Studio hold label accompanying a small step, and an incorrect raw director description of a door lock. The broader harness tests narrative/direction/compilation, whereas the scene-specific harness additionally applies canonical scene assembly. Its raw-stage errors must not be confused with verified final Game state. A descriptive alias that disregards the supplied registry can still require review; the application does not use broad fuzzy-name merging.

Runs were sequential but not randomized controlled benchmarks. Prompts, schemas, checks, response lengths and cache warmth changed during development. More detailed staging takes output tokens; these measurements do not establish a universal speedup or a zero-failure rate. Per-shot identity/state instructions remain deliberate, and some aggregate story prose still overlaps later shot descriptions.

## Actual new H3 demo

**The Coin Safety Inspector** rendered three new ten-second scenes at 0.2 MP, eight steps and 608×320. The joined result is exactly **30 seconds / 720 video frames at 24 fps**, with native audio; full FFmpeg decoding passed. Server execution times were **84.400, 81.064 and 81.015 seconds**. All three jobs and ending inspections completed; the isolated QA settings remained unchanged.

Sampled frames show a single coin moving from standing Mira to seated Ivo, then onto the bench. Ivo remains seated in the samples, and both end empty-handed. The exact prescribed hands are not reliably distinguished; the static courtyard gained door/window details and facial acting is understated. The [demo page](../demo/scene-continuity/README.md) publishes actual footage, images, authored contracts, hashes, timings and the sampling limits. No visual mistakes were corrected in post-production.

The original optional ending checklists had four, zero and three entries for four intended identities. Fresh inspections now require exactly one assessment per listed identity, accepting explicit uncertainty. Missing or partial checks preserve the previous branch and use inspection recovery. Saved legacy requests retain their protocol/schema/cache across restart; existing observations remain readable.

Three new strict inspections of the same actual final frames returned **4/4 required assessments each**, with all twelve labelled matches by the model. They passed production schema and coverage validation in **8.34, 8.61 and 8.25 seconds**, without another render or any story-state change. The owned model was released afterward. Original optional reports remain unchanged. This establishes checklist completeness, not perfect visual judgement: the manual hand-staging limitation remains relevant.

## Scope and remaining limits

The scene cases cover a seated bystander, merely mentioning an existing prop, a one-object handoff, two distinct tokens, an off-screen factual answer and camera-only Studio direction. The broader Game suite covers combat, item operations, locked doors, directly addressed NPCs, exact player quotations, text-only openings, history, grounded answers and Studio authoring.

The final-frame inspector can report a visible mismatch before acceptance, but cannot verify an entire motion, quiet sitting throughout a clip, exact spoken words or a hidden item. Occlusion is uncertainty. Correct logical state and detailed prompts do not guarantee correct pixels. No model weights or approximate H3 acceleration patches were installed as part of scene control.

The [published demos](../demo/scene-continuity/README.md) distinguish authored directing tests from autonomous planning. Earlier real Game renders, item persistence, enemy defeat, image-free city creation, cancelled duplicate-coin footage, and the 30/60-second Studio films are documented in [the preceding validation record](VALIDATION_2026-09-09.md). Those historical measurements are retained rather than presented as new runs of this release.
