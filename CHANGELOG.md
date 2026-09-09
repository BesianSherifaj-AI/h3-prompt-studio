# Changelog

## 1.3.0 — Scene continuity

- Added per-shot actor, prop and environment controls to Studio and Game. Physical actions stay assigned to their characters; passive bystanders keep their established posture. Counts, appearance and start/end placement accompany important object identities.
- Scene direction uses the existing assistant call. The deterministic compiler validates bounded scene contracts in all five H3 modes, preserves reference aliases and exact dialogue, handles first-person bodies correctly, and removes exact repeated action prose.
- Director output limits scale with shot and cast counts so multi-shot scene controls have room to finish. Explicit writer holds remain authoritative in generated direction; an absent but locked optional continuity block stays valid.
- Canonical game state supplies item counts, holders, known posture, location and visual condition. Completed staging is refreshed on the next turn, while newly authored controls survive replanning. Unknown IDs, duplicate entries and conflicting authored counts are rejected before rendering.
- The director receives the existing object identity registry in creative, quick-action and recovery paths. Wrong IDs reusing exact known names trigger bounded repair; separately registered same-name objects remain distinct.
- Added final-frame continuity checks. Definite reported mismatches pause before acceptance even in automatic play; uncertain visibility does not create or delete items. A rejected take retains the previous accepted world.
- Fresh ending inspections require coverage of every listed character and prop, allowing explicit uncertainty. Missing checks use the existing recovery step; saved legacy requests retain their original protocol and observations remain readable.
- Added a cited review of official H3 guidance and 2025–2026 research, reproducible scene-control planning evaluation, an original coin continuity demo, browser regression checks, clean-package validation and GitHub Actions CI. See [scene continuity](docs/SCENE_CONTINUITY.md) and [validation](docs/SCENE_CONTINUITY_VALIDATION.md).

## Reliability and speed — local update, 2026-09-09

- Known item and movement actions use validated game mechanics and one directing call. Inventory opens immediately. Creative dialogue, combat and unknown situations retain the actor/writer path; quick actions can be disabled.
- Text-only openings retain the premise and can establish persistent locations, props and new characters without separate reference renders. New doors receive usable controls, and defeated character state survives accepted turns.
- Bound generated performances to approved actions, preserved guides and camera locks, and repaired optional Studio choices independently of the scene. Ending observations no longer replace intended next moves with contradictory visual guesses.
- Hardened structured-response parsing, bounded recovery, lost-response receipts, request deadlines, atomic editor/acceptance updates and cancellation of late-arriving child jobs. Empty Studio observation schemas are compatible with LM Studio.
- Exclusive assistant loads explicitly verify GPU KV caching. Added installed-model evaluation and resumable authored 30/60-second Studio render examples. See [reliability details](docs/RELIABILITY.md) and [measured assistant evaluation](docs/ASSISTANT_EVALUATION.md) for evidence and limits.

## Separate scene queue — local update

- Each Game click or message now queues a separate scene with a three-second editing window. Waiting moves remain editable while rendering, execute against the preceding accepted ending, and can be removed or paused. Queue entries retain request IDs; refresh restores them paused and uncertain submissions are reconciled rather than blindly repeated.
- Removed the invalid assumption that every player suggestion starts with “I”. Optional unsafe/duplicate suggestions are filtered separately from scene validation. Old failed attempts are distinguished from a current failure after the story has progressed.
- Roleplay context separates completed history from the new action, retains prior dialogue and world effects, supplies explicit current-holder facts, and checks for verbatim replay. Ending images and design references are labelled differently; still references are not instructions to insert portrait shots.

## Local Game recovery update — 2026-09-08

- Fixed the reported key-pickup failure: natural player-choice wording no longer fails the coordinator schema. The coordinator writes one beat; the application attaches established identities and exact dialogue. Typed consequence fields bind to existing world IDs.
- Failed assistant requests retain their validation reason, timing and terminal status. Retrying keeps the saved parent, valid earlier responses and review preferences. Model selection is frozen per turn; cancelled output is discarded.
- Fixed saved-motion continuation from an image-start clip: the original opening becomes context instead of being sent again as a conflicting first frame.
- Moved movement arrows beside the video and the composer directly beneath it. Added Play/Edit/History navigation, visible editor scrolling and direct AI-response recovery. External UPSCALE remains optional under video Details.

## 1.2.0 — local review build

- Added the persistent Game side editor: characters, identity/wardrobe/object assignments, reference removal and undo, scene controls, aspect ratio, seeds and ordered LoRAs. New games begin blank; Play and Guide separate character actions from directing instructions.
- Added branch-local world state, character knowledge, grounded target actions, movement/camera controls and revisioned next-turn guidance. Frozen attempts retain their original inputs while future settings are edited.
- Routed character responses through a shared H3 director/compiler, with durable assistant requests, supervised testing and an inspectable What ran receipt. Failed ending inspection retains the playable video and has separate recovery.
- Added experimental H3 still assets using five video frames and a middle-frame extract; installed native Z-Image-Turbo BF16/FP8 remain selectable. Generated lasting identities require appearance review.
- Added explicit pixel 0.2 MP / three-second preview controls and a Motion Lab for saved paired experiments. No automatic benchmark queue is started by opening it.
- Added optional CPU microphone transcription into an editable composer, retained voice recordings, supported native audio references, and separate soundtrack mixes that preserve original clips.
- Original application source is now MIT licensed. Published examples remain unchanged; this build is prepared for local review, not published.

## 1.1.0

- Added Game: choose a player character, describe an action or speech, and let the local vision assistant write and render the other characters' response. Three choices suggest the player's next move.
- Added optional review and editing before rendering, exact quoted-player-speech validation, and durable turn recovery with recorded request IDs.
- Added persistent story branches and an explicit active ending. Browsing history does not change the continuation source; rerolls replace a take within its turn and preserve alternatives.
- Moved Studio's Photos, Story & Dialogue and Settings beside the video. Continue now identifies its source ending; the separate-scene planner remains in Advanced.
- Added sequential whole-story playback and branch-specific film export. New-footage previews remove recorded motion context while preserving original outputs.
- Added explicit new-action duration budgets for Studio continuations and Game, with room for saved motion on H3's frame grid. Legacy total-duration clips retain their original settings.
- Added optional native ComfyUI Z-Image-Turbo reference generation, image inspection, stable reference tags and character/wardrobe/prop bindings. BF16 is the configured baseline; FP8 is optional when installed and compatible.
- Added mocked story, route, asset, recovery and timing regression coverage, plus a deterministic portable-release packager. Current validation and measured render results are recorded separately.

## 1.0.0

Initial published local prompt and video workspace: image roles and character assignments, exact dialogue, per-shot direction, LM Studio model selection and GPU hand-off, direct ComfyUI H3 rendering, seeded alternatives, three continuation suggestions, combined clips, saved setups and portable project exports.

The [published v1.0 showreel and example projects](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/tag/v1.0.0) remain unchanged. [The Message demo](demo/README.md) preserves its original prompts, references, footage and verification notes.
