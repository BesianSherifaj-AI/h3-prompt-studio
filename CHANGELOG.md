# Changelog

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
