# H3 Studio + Game 1.2 build contract

Status: core v1.2 local review build; broader experiments remain deferred. Original code: MIT. See VERIFICATION_V1.2.md for measured results and remaining limits.

Latest user override: prioritize a working local build; live generation tests use only pixel-style approximately 0.2 MP previews. Test H3 as a still generator and retain Z-Image-Turbo. Broader 0.3–1.0 MP and concurrency benchmarks are deferred. Do not publish before user review.

## Three delivery gates

1. Reliable player intent → roleplay → directed H3 → render → observation → accepted branch. Blank setup, exact speech, frozen attempts, revisioned world/configuration, recoverable jobs, supervised provider, real queue receipt. First live check: 0.3 MP / 8 steps / about five new seconds.
2. Shared editable project throughout play: cast, references/ownership, scenes, guidance, render settings/LoRAs, audio. Separate Play/Guide, grounded contextual actions, persistent character contexts and measured assistant concurrency. Accessible side editor/player and narrow layouts.
3. Motion Lab, experimental 0.2 MP / 3 seconds, paired recipe/motion tests, measured timings, scene-plan tools, regression/live/portable validation. User reviews local v1.2 before any publication. H3-World, Gaussian environments and external TTS remain isolated optional adapters.

## Stable integration interfaces

Existing project schema stays version 1, with extension fields. Existing story and video routes remain compatible.

Public story gains `project`, `world`, `guides`, `configuration_revision`, `player_character_id`. Project is the editable branch draft, not a historical render snapshot. Settings extend current keys with `aspect_ratio`, `seed`, `loras`, `initiative`, `assistant_provider` (`lmstudio` or `supervised`), `concurrency` (default 1), `transition` (`auto`, `continue`, `cut`). Preserve full `project.comfy_render` entries when adjusting ordinary presets.

PATCH /api/stories/{id}: accepts existing title/premise/player_name/settings plus project, world, guides, player_character_id, expected_configuration_revision. A successful patch increments only configuration_revision. Edits while busy apply next turn. Guides: {id, revision, text, scope: next|persistent, enabled}.

POST /api/stories/{id}/turns: existing message/request_id plus optional `intent` {kind,target_id,recipient_id,extent,speed,camera,presentation}, expected_parent, configuration_revision. Snapshot the branch draft/world/guides/settings before work. Each attempt has its own request ID; a transport retry uses its existing ID.

GET /api/stories/{id}/actions?target_id=: returns {targets:[{id,name,kind}],actions:[{kind,label,enabled,reason,target_id?}]}. POST /api/stories/{id}/preview accepts a plan and compiles without queueing.

Turn actions retain approve/retry/cancel/reroll/edit/resume and add retry-inspection, accept-intended, accept-visible, stop-and-apply. Every action is revision/idempotency checked. Stop-and-apply cancels/drains before replacing from the same parent. Inspection failure preserves the rendered take.

GET /api/assistant/requests exposes durable pending supervised requests with stage/actor/context/image IDs/schema/hash. POST /api/assistant/requests/{id}/complete takes context_hash and result, validates the same schema, rejects obsolete or conflicting results, and resumes only the owning stage. No permanent background agent or credential integration.

## Shared state rules

World: schema_version, current_location_id, locations[], entities[], characters[], objectives[], rules, events[]. Stable IDs everywhere. Location: id/name/description/exits[]/asset_ids. Entity: id/name/kind/location_id/holder_id/worn_by_id/asset_ids/affordances/state. Character: id/name/control/description/personality/goals/speaking_style/private_knowledge/relationships/asset_ids/location_id/witnessed_events. Empty initial worlds are valid. Branch snapshots keep their own world and guidance. NPC requests only receive that actor's private knowledge.

Narrative output extends legacy action/setting/final_state/dialogue/characters/asset_requests/choices with structured effects and full direction. Shared director produces project-compatible shots/camera/performance/sound/language/delivery. Unknown/missing reference bindings cannot silently become images. One intended turn = one H3 render; rerolls do not replay effects.

Attempts freeze project, world, configuration revision, guide revisions, resolved plan, assets, seed and render settings. World changes commit once on acceptance. Late cancelled responses cannot commit. A guide revision edited during rendering is not consumed by acceptance of an older revision.

## Acceptance fixtures

- Blank setup and player/NPC creation; add/remove/reassign photos during play; wardrobe and holder preservation.
- Exact quoted question and NPC response; concrete continuation instead of bare 'continue'; private knowledge isolation; branch excludes future events.
- Take/give/open/move/return with validated state; Guide next/persistent and stop/apply behavior.
- Queue receipt, changed sampler seed, multiple LoRAs, aspect/mode-aware bindings; continuation overlap and branch selection.
- Refresh, cancellation, uncertain queue response, failed asset and failed observation without duplicate render.
- Real 0.3 MP Studio continuation, Game dialogue and generated-location cut; low-resolution experiments and large/small assistant timings.
- Desktop/narrow keyboard usability, existing suites and portable export/import. Preserve all published examples.

## Ownership and handoff

Integrator owns backend/stories.py, backend/app.py, persistence, routes, release/checkpoints and integration fixes. Editor agent owns Game UI/types/hooks and new editor components. Narrative agent owns new world/director modules and their tests. Rendering agent owns transfer/timing/audio/Motion Lab modules and their tests. Avoid concurrent edits to other owners' files; communicate interface changes first.

Record every checkpoint below with actual test results, remaining failures and next task. Do not mark unmeasured performance or experimental backends validated.

## Checkpoints

- Baseline d2584ac: clean v1.1 checkout. Blueprint and interfaces saved before implementation. ComfyUI/LM Studio availability will be rechecked before live tests.
- 2026-09-08 integration: durable branch drafts/attempt snapshots, world/character contexts, shared roleplay/director/compiler, supported media wiring, GUI editor and receipts implemented. Legacy API and published examples retained.
- Live pixel asset: native H3 five-frame / four-step 608×320 still extraction succeeded, 43.234s Comfy and 50.096s job total. Reviewed courtyard geometry and pixel appearance; tiny prop identity remains uncertain.
- Live pixel Game: supervised actor → coordinator → director → H3 → ending inspection completed. 608×320, 73 frames, eight steps, 82.194s Comfy / 83.779s video job. Two outfits and scene layout retained. Separate CPU ASR returned “The key on the table.”; speaker attribution and lip sync were not automatically established.
- UI acceptance: live add/remove/undo, owner versus holder, persistent guide, aspect/seed/two-LoRA settings, movement payloads, cancellation, desktop/narrow layouts. Final UI report and private footage stay outside the release.
- Assistant acceptance: initial0.8B and9B trials exposed semantic failures despite fitting the context budget; validation stopped rendering. Hardened9B pipeline passed actual planning/compilation in45.9s with zero repairs. The final flat2D Game rendered in58.496s and actual ASR matched both requested lines. Automatic ending inspection and explicit acceptance succeeded.
- Latest additions: new Game defaults 0.2 MP / 3s / 2D pixel, freely editable style; explicit POV/third-person/top-down; scoped real reference images; stable system engine; owner/holder compilation; same-origin live progress relay; existing UPSCALE GUI launch with selected video. Final suites: 977 backend / 322 frontend tests pass. Publication remains pending user review.
- Final continuity repair: same-place cuts use the accepted ending; changed places/viewpoints release inherited exact keyframes while preserving new user keyframes. Six new regression cases pass. A separate real-assistant continuation preview matched the exact accepted motion source and omitted previous speech; no additional video was queued.
