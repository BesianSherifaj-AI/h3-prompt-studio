# Assistant recovery validation — 30 September 2026

This patch fixes load recovery and readiness reporting without changing saved workspace choices or accepting invalid world effects.

## Changed behavior

- A definite model-inventory or validation failure before load submission releases the pending-load blocker. An uncertain submitted load still requires reconciliation.
- Readiness requires the exact app-owned model instance and effective context. Offline, foreign, malformed, wrong-context and contradictory KV-cache configuration cannot appear ready.
- Failed connection checks clear stale readiness and loading state, preserve selections and expose Reconnect. Successful checks clear their connection error.
- Connections uses an explicit Cancel action. Game editor controls remain above sticky status notices and below the Connections dialog.
- Unknown-object Game errors identify the rejected IDs. Narrative/schema guidance requires existing IDs or matching `discoveries.entities` declarations in the same response. Invalid effects remain rejected.

## Automated verification

- Full backend suite: **1,900 passed**, two dependency deprecation warnings.
- Frontend unit suite: **462 passed**.
- Production build: passed; existing bundle-size warning remains.
- `node scripts/assistant-settings-smoke.mjs` from `frontend`: desktop and 390px profile independence, persistence, model/context preservation, Cancel, failed/successful Save, visible Game errors, readiness, reconnect and reload passed with zero runtime errors.
- Existing backend coverage verifies profile migration, frozen queued profiles, context reloads, CPU/KV placement, vision eligibility and protection of unrelated model instances.

## Local live verification

Services: Studio 8766, LM Studio 1234, ComfyUI 8010, RTX 4090. The secondary configured ComfyUI endpoint 8000 was offline.

- Studio: `qwen3.8-27b@q4_k_s`, **32,768 context**, GPU/H3 handoff. The saved user context was preserved. Effective loaded context matched. A neutral four-second scene plan completed in **5.3 seconds**.
- Game: `qwen3.5-4b-uncensored-hauhaucs-aggressive`, **8,192 context**, CPU. Returned load configuration verified GPU ratio 0, disabled GPU 0, CPU KV cache and the requested context.
- First Game handoff attempt failed safely because the model invented an undeclared object; one automatic repair also failed. After improved ID/discovery feedback, one bounded repeat produced the correct `key` → `mira` holder effect in **114.17 seconds**, without private-code leakage into the shared plan. A distant-character transfer was rejected before inference. The test review was cancelled without rendering or committing its effects.
- This is a limited quality sample. CPU Game is slow; one suggested follow-up contained a minor story inconsistency. Loading and a valid handoff do not establish production-quality reasoning for arbitrary stories.
- One synthetic Studio render: **0.2 MP, 608×320, four steps, seed 30092026**, FL2V Turbo four-step LoRA, text-only pixel robot. Completed in **72.5 seconds**; 107 native frames, **4.458 seconds**, H.264/AAC stereo 32 kHz. Full audio/video decode passed.
- Browser playback reached the ending at readyState 4. Dense frame progression showed the robot wave, bow and return upright on the same stage. No speech was requested; audio content quality was not independently reviewed.
- The completed test result was attached to a Studio story and reloaded with the same active run. The selected Studio Qwen profile restored after the H3 handoff. Existing projects/media and saved settings were preserved; runtime code/build and settings were backed up before promotion.

The previously published 50-short package and feature film were not regenerated or fully re-reviewed in this recovery patch.
