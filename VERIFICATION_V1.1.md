# v1.1 validation — 8 September 2026

This is a local RTX 4090 (24 GB) validation, using ComfyUI Desktop on Windows, the installed H3 INT8 reference model, the eight-step reference Turbo LoRA, and the 32B Heretic H3 text encoder. The assistant was the selected Qwen3.8 27B Q4_K_S model in LM Studio, with automatic GPU unloading. These samples are functional checks, not a controlled hardware benchmark.

## Live video checks

- Studio: “Choose whatever happens next” became a specific new action and a structured spoken line. The saved parent motion state was correct. A five-second new-action request produced 175 raw frames: 39 context frames and 136 new frames (5.667 seconds). ComfyUI execution took 92.387 seconds. Independent CPU transcription matched “Where are you going?” exactly.
- Game: the player asked “Is someone waiting for us?” and the assistant assigned the reply to the other character. The five-second continuation produced 136 new frames. ComfyUI execution took 99.141 seconds; the complete planning/render/ending-inspection turn took about 148 seconds. CPU transcription matched all 11 intended words across the question and reply. Browser refresh reconnected to the same run.
- Game scene cut: the assistant requested and generated exactly two missing references, a moonlit greenhouse and a blue music box, then inspected them before H3. Established face and outfit images were reused. The cut produced 124 complete frames (5.167 seconds), with no overlap removed. Its first take took 78.054 seconds but duplicated one character.
- Corrected cut: explicit two-person staging and cast-count instructions produced one man and one woman in all nine inspected frames. The same two generated assets were reused. The retake took 77.383 seconds and transcription matched the five intended words exactly. The original take remains an alternative; the selected branch contains the corrected take once.
- A subsequent review-only move requested no new assets. Cancelling it queued no video and left the active ending unchanged. Automatic rendering was restored afterward.
- Studio alternate: a new seed produced different motion and the same four intended spoken words in 92.513 seconds of app video-job time. Refresh kept the selected alternate. Preview left the active ending untouched; explicitly branching replaced the fourth clip at the same story position, with the original four-clip branch preserved.

Rendering times above are ComfyUI execution times. They exclude assistant planning, image preparation, ending inspection and playback/export processing.

## Assets and model handoff

The installed native Z-Image-Turbo recipe used eight steps, CFG 1, `res_multistep`, `simple`, AuraFlow shift 3, the Qwen 3 4B encoder and the `ae` VAE. Both compatible variants rendered the same 512×512 prompt with seed 31415926: BF16 in 13.598 seconds and FP8 in 6.559 seconds of ComfyUI execution. Cache/loading conditions differed, so BF16 remains the baseline default and FP8 is an optional selection.

The BF16 test exposed Windows backslashes in ComfyUI's output folder. After fixing the exact owned-folder check, the completed image was recovered under the same request ID without generating a duplicate. Traversal rejection remains tested. The later live sequence completed assistant → image generation → assistant inspection → H3 → ending inspection with verified model release and idle-queue checks.

## Playback, UI and recovery

The selected six-scene branch exported to exactly 809 video frames: 33.708415 seconds versus 33.708335 seconds summed from individual scene streams. Full audio/video decoding passed. The new-scene cut's scene file is byte-identical to its raw clip. The updated film contains the corrected take and excludes its rejected alternative.

Studio was inspected at desktop width and at 430 pixels. Game was also inspected at 430 pixels, with no horizontal page overflow. Editor tabs worked using arrow keys; the settings drawer worked with Escape. The selected Studio take survived reload, previewing history did not rewind the active ending, and a single main Continue action remained next to the player. Temporary viewport overrides were reset.

Automated coverage includes branch lineage, Studio alternate registration, selected endpoints, exact quoted speech, ownership and reference tags, first-turn reference changes, legacy frame budgets, failed/cancelled turns, retry receipts, uncertain queue responses, asset reuse and safe model handoff. Published v1.0 examples and originals were preserved.

Final suites passed: **863 backend tests, 301 frontend tests, and 40 ComfyUI bridge tests**. The production TypeScript/Vite build passed. Backend tests reported two upstream Starlette deprecation warnings.

A live app restart originally lost ownership of its loaded assistant and blocked the next video. The fix persists the exact endpoint, model and instance, then rechecks them before unloading. The final live sequence loaded the selected 27B assistant, restarted Studio, and successfully prepared H3 with no manual unload and no video queued. GPU use fell to approximately 1.3 GiB. Saved Game suggestions also receive a player-agency check: an old suggestion commanding the other character was replaced with a player action, without changing the stored original.

## Remaining visual limits

The first greenhouse take demonstrates that H3 can duplicate a character despite a correct project and clean reference images. Another take or clearer staging can help; an application cannot guarantee identity, exact hand motion or reliable object manipulation. The corrected wide/medium samples do not establish pixel-identical faces or fine engraving fidelity. CPU speech recognition checks words, not speaker identity, precise lip sync or natural voice quality. Ending inspection uses a still image and explicitly records uncertain details; it does not claim to hear speech. Basic Z-Image text-to-image generation is not identity-preserving image editing.

Live test media and private run IDs are kept outside this source checkout. The separately published v1.0 showreel remains the original demonstration, not evidence for the v1.1 Game tests.
