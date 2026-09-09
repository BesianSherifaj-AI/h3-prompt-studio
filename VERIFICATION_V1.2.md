# v1.2.0 local review record

Validated locally on Windows, RTX 4090, ComfyUI Desktop H3 runtime and LM Studio. Publication remains pending the owner's review. Private games, prompt/model diagnostics and newly generated test footage are outside the public release package.

## Current defaults and controls

The latest requested Game default is **0.2 MP, three seconds, eight steps, 2D pixel art**. This replaces the earlier proposed 0.3 MP / five-second default for new games only. Existing stories keep their settings. Style, aspect ratio, quality, player viewpoint and NPC initiative remain editable; pixel art is a presentation choice, not an independent compute optimization.

Studio and Game share the H3 compiler and native renderer. Game's actor, world coordinator and director receive a stable engine instruction layer. Actor requests include scoped reference metadata, actual relevant images and the current ending; only that actor's private memories are exposed. Exact player speech and approved NPC speech are bound by application code before compilation.

The Game editor covers people, photos/audio, explicit assignments, world guidance, timed scenes, first-person/third-person/top-down views, native render settings and ordered LoRAs. Play and Guide are separate. A saved URL can reopen a Game without generating anything.

## Live results

- **H3 still extraction:** native five frames, four-step FL2VA LoRA, 608×320, middle-frame extraction. First courtyard: 50.096s job total / 43.234s Comfy. A later stricter 2D adventure-art prompt produced a flat raster scene with two adult characters in **25.039s** under different warm-cache conditions. Intermediate results looked too voxel-like and were not used for the final example. These timings are not a controlled H3 versus Z-Image comparison.
- **Supervised pixel Game:** actor → world → director → video → ending observation completed. 608×320,73 frames,eight steps. Video job83.779s / Comfy82.194s. CPU speech transcription matched the requested question/reply. This isolated application behavior from model reliability.
- **Actual LM Studio Game:** installed Qwen3.5 9B Q4_K_M, exclusive unloading,8192 context. A fresh 2D Game planned in **45.9s**, including load, with no semantic repair. Actor15.62s,world23.34s,director6.94s. The live compiled H3 prompt was valid, with the exact new reference, matching clothes, camera, visual action and speaker-bound English lines.
- **Actual 2D Game video:** first-frame input,608×320,73 frames,3.0417s,eight steps. **58.496s video job /56.159s Comfy.** First/middle/last frames retained the flat pixel look, both characters, outfits, table, key and closed door. The Guide raised her palm. CPU ASR returned exactly “The door? Yes. It's right there.” ASR does not independently prove speaker attribution or lip sync.
- **Automatic ending inspection:** the real9B model described the scene and gesture, flagged uncertainty about a hand, and supplied three next choices. The reviewed take was accepted and remains playable. Rendering and inspection used separate GPU stages.
- **Continuation planning:** a separate real9B test converted “Choose whatever happens next” into a specific new Guide gesture. The actual ending reached the model, old dialogue was absent, and explicit Continue compiled with the exact accepted `.mmh3` source and39context frames. Planning took46.84s with one semantic repair. No second video was queued. Automatic transition chose a cut; this is recorded separately from explicit Continue.
- **UPSCALE:** detected the existing separately installed desktop GUI, and opened it with the completed test clip. The GUI's own scale/FPS/quality options and settings remain in place. No processing was started. This GUI accepts videos, not still images.
- Windows confirmed the launched UPSCALE child window was responding with its normal application title. Studio does not click Start pending or change its settings.
- **Live progress:** the deployed same-origin event stream returned the actual completed run status. Native backend WebSocket connection was verified; event filtering is tested. The final render finished before the live step observer connected, so live sampling-step events were not witnessed in this run. Intermediate H3 images are not shown by the current runtime.

## Automated and interface checks

- Backend: **977 tests passed**, including compiler/mode/reference/media transfer, branch and request recovery, world effects/private memory, exact dialogue, revised drafts, Stop-and-apply, stale-direction replacement, inherited-keyframe repair, audio tools, progress transport and UPSCALE handoff. Two upstream deprecation warnings remain.
- Frontend: **322 tests passed** across 26 files. TypeScript and production build pass. Vite reports a nonblocking bundle-size advisory.
- Live UI: desktop and narrow layouts, character creation, reference upload/removal/undo, owner versus holder, persistent Guide, portrait settings, seed and ordered LoRAs, movement intents, cancellation and full What ran prompt display were exercised in separate QA games.
- Original v1.0 demo files and legacy project export/import APIs remain unchanged. Tests cover portable project packaging; full portable world-session/video relocation is not claimed.
- Microphone record/stop/editable transcript behavior is covered by UI tests; no physical microphone was recorded during this run. CPU transcription was run on actual generated audio. A real durationless WebM fixture was accepted, measured and normalized while retaining original bytes.
- A final inherited-keyframe defect was repaired: same-place cuts now condition on the actual ending instead of the original opening; new location/viewpoint cuts release inherited exact keyframes appropriately. Explicit new user keyframes remain authoritative. Six new regression cases compile correctly. This last fix was not followed by another GPU render.

## Known limits and deferred validation

The initial0.8B model failed semantic role ownership; the first9B attempt also exposed schema/prompt weaknesses. The repaired9B pipeline passed the live example. This is not evidence that every installed model can roleplay reliably. A bounded repair may run once, then validation keeps an invalid turn from rendering.

Visual contradictions are not a complete automated world-state verifier. Tiny props, hands, identity and viewpoint can still drift. “Use visible outcome” retains uncertainty and does not magically infer every object consequence. First-person and top-down controls are compiler-tested; additional generated examples of those views remain for user trials.

Per-character contexts and safe model-pool support exist; independent prediction concurrency1/2/4 has not been promoted or benchmarked as faster gameplay. Broad4/8/16-step seed studies,0.3–1.0 MP comparisons, new Z-Image BF16/FP8 benchmarks, H3-World and Gaussian-world backends are deferred under the latest fast pixel-only testing request. The Motion Lab stores deliberate experiments; opening it does not start a benchmark.

## User-reported Game recovery — 2026-09-08

The reported pickup failure was reproduced in the coordinator: natural player-choice wording failed an unnecessarily narrow schema, and the generic retry hid the exact error. The repair shortens the coordinator output, attaches established identities/exact speech in application code, validates typed effects against world IDs, and retains failure diagnostics. A separate defect retained an opening keyframe in saved-motion continuations; inherited openings now become context. Retry retains review preferences and rejects attempts belonging to an ending the active story has already passed.

The user's actual failed turn was resumed with its existing ID and original parent. It succeeded with the selected 9B assistant and the user's 0.2 MP, three-second, four-step settings. One video and zero new assets were queued. The accepted output is608×320,73frames,3.042seconds. It starts from the actual parent ending image; the assistant chose an automatic cut, so this live run does **not** claim a saved-latent continuation test. Saved-motion routing and frame-grid behavior have separate regression coverage.

Measured live stages: approximately32.2seconds planning including model preparation,54.18seconds video job (52.185seconds Comfy execution),20.3seconds ending inspection; approximately107.4seconds total. A separate warm assistant fixture took22.87seconds across its three stages. These are single-run measurements, not a controlled speedup benchmark. The four-step FL Turbo adapter was connected correctly. SLA deliberately uses the existing Comfy Kitchen dense path for this short sequence below its sparse threshold; no unmeasured attention change was promoted.

Nine extracted frames and complete audio/video decoding were reviewed. The player visibly reaches for and lifts the key-like prop while the characters retain their pixel outfits. The tiny prop appears dark green, rather than reliably gold; the endpoint inspection marks its identity uncertain. No dialogue was planned, and cached CPU transcription found no speech. The accepted world holder matches the intended pickup; history still includes the earlier failed duplicate without advancing it.

The live desktop/narrow checks found and fixed Game inheriting Studio's fixed-height, hidden-overflow shell. At390×844 the document now scrolls through1712px with no horizontal overflow; the editor scrolls independently while Save remains visible. Arrows sit beside/below the player, the composer follows immediately, Play/Edit/History are separate, and pre-plan failures have a direct Retry AI response button. UPSCALE is stopped and remains a manually opened optional tool under video Details. Browser screenshots had intermittent capture tiling, so layout verification also used actual DOM dimensions and keyboard scrolling.

Final suites:991 backend tests and324 frontend tests passed; the11 focused recovery tests also pass, including stale-state replacement across successive turns. Single-quoted generated speech now preserves internal contractions. Generated starting-state paragraphs are rebuilt once per turn, keeping author instructions while preventing old object holders from accumulating in later H3 prompts. These last safeguards were regression-tested after the live render; the successful historical clip was not rewritten. The installed local build includes the fixes. The earlier review ZIP has not been replaced, and nothing was published.

Private local evidence is kept outside the release tree under `research/v12-pixel-live/user-recovery`, including queue/turn receipts, the inspected clip, CPU audio report and `GUI-QA.md`.

## Separate queued scenes — follow-up verification

The user confirmed separate video turns, rather than batching clicks into one action. Game now gives every move a three-second editing window, accepts additional moves during rendering, and dispatches the next only after its predecessor is accepted. The queue is browser-local, holds up to12 entries, is bound to its story branch, and restores paused after refresh. The page must remain open to dispatch waiting entries; already submitted backend work continues independently. Removing a submitted queue row is explicitly separate from cancelling that render.

Browser tests intercepted every mutation in an isolated context. They verified the real composer, steering edit before first submission, two separate stable IDs, editing scene2 while scene1 was running, scene2's actual POST using scene1's accepted parent, and no duplicate submissions after refresh. Unit/state tests cover failures, cancellation, unknown receipts, branch changes and invalid persistence.995 backend tests and329 frontend tests passed; production build passed. Browser screenshots and receipts live outside the release under `research/v12-pixel-live/scene-queue`.

The exact “He unlock the door” request was tested with the selected9B assistant in a separate review-only story. The first test passed formatting but copied the old pickup action; it was cancelled before video generation. After separating past prose from current facts and distinguishing ending images from design references, the same request produced an insertion/turn attempt with no unnecessary assets. Planning took31.1seconds across actor/coordinator/director, without semantic repair. One actual0.2MP four-step saved-motion continuation was then rendered:608×320,124 total frames,39 preserved overlap frames,approximately3.54seconds of new footage. Video job69.1seconds; Comfy execution67.3seconds. This is a real continuation from the latest user clip's saved latent, not a new first-frame render.

Full decode and CPU audio review passed; no dialogue was planned or transcribed. The video remained pixel styled and continued the characters' poses, but H3 duplicated the key on the table. This is not a verified successful unlock or a physically reliable inventory simulation. The separate QA story remains available for review; the user's active branch was not advanced by this test. Queueing improves interaction availability, not the underlying video model's render speed or object consistency. No GitHub publication or release ZIP replacement was performed.

## Sources

The compiler follows MiniMax's separate [full-reference six-section guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.agents/skills/h3-prompt-writing/references/ref-en.txt) and [mode documentation](https://github.com/MiniMax-AI/MiniMax-H3). The short-sequence still-image method was also described by its [community workflow author](https://www.reddit.com/r/comfyui/comments/1vfqdbx/behold_minimaxh3_image_generation/); this implementation was independently tested using the installed native nodes. No dedicated image-editing capability is inferred from that experiment.
