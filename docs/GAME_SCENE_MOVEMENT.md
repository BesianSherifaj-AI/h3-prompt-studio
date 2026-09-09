# Scene selection and movement in Game

The game separates the people and objects described in an inspected image, the identities and possessions accepted into the story, and the instructions for the next generated movement. A visible passerby is not automatically a named NPC, and a visible coin is not automatically in your inventory.

## Your player starts with an identity

From version 1.4.1, a new game's missing player appearance is established during its existing opening planning call, before H3 renders the first scene. The approved description belongs to the same player ID and is saved when that opening is accepted. Later moves reuse it. There is no extra identity-generation call. Your authored description or explicitly assigned character photos remain the basis for that appearance.

For older games that never saved an appearance, pressing an arrow opens **Who are you playing?** with the current picture. The game finds people automatically; selecting a person saves your choice and continues the waiting move. **Describe my character instead** accepts a short description such as “the person in the orange jacket” even if recognition fails. Cancelling leaves the move unsent. **Change character** remains available as an explicit correction.

An older movement that failed for missing identity is restarted against the repaired configuration with a fresh request. The old failed record stays in history; later queued moves keep their order. If the identity has already been repaired, **Continue my move** resumes directly. An uncertain submitted render is never treated as a missing-identity retry.

## Select something in the image

The **In this frame** panel lists inspected people, physical doors and useful objects with descriptions and image positions. Select a row to inspect it, approach it or talk to a person. A newly selected target receives a stable identity for that proposed turn; it enters the persistent world only when the turn is accepted. This does not unlock a door, grant an object or establish an unknown holder.

For an older ending with only a prose description, choose **Inspect this ending**. This makes one vision request using the saved image. It does not render a video, accept a pending take or change inventory. People without identifying evidence keep descriptive labels such as “Person in green jacket.” The expected cast and a character's name alone are not enough to recognize a figure.

You can also select a person on the current accepted ending and choose **This is me**. The game saves that appearance with the selected frame. It will not bind your player to another established NPC. Stale selections and selections from an unaccepted ending must be refreshed or reviewed first. First-person camera movement does not require identifying an on-screen player body.

## Use the arrows

With quick actions enabled, eligible basic direction buttons prepare the shot directly and skip language-model loading, writing and ending inspection. ComfyUI still runs H3 to generate the movement. Player movement uses the saved appearance, visible footfalls and a stationary camera by default; camera movement keeps the characters in place. In the current view, forward means away from the viewer, backward means toward the viewer, and left/right follow the screen.

The actual accepted ending image becomes H3's first frame (**I2VA**). This anchors the start instead of asking a writer to describe the scene again. A missing player identity, custom rules, active guides, unusual conditions or incompatible authored controls can require identification or the creative planning path. Inspect **What ran** to see which path and conditioning were used.

## Return to a saved view

Accepted movement records local step coordinates and saved endpoints on the current branch. An inverse move can reuse a compatible earlier endpoint as H3's last frame (**FL2VA**, first-and-last-frame conditioning). Named-location returns also require an established destination.

A saved image is eligible only when the current world, appearance, references and presentation remain compatible. Returning does not restore an old inventory, undo a handoff or revive an enemy. If an earlier view is incompatible or unavailable, it is not imposed as the destination image. The recorded coordinates are logical navigation bookkeeping, not a measured map or GPS position.

## Review what actually happened

A basic movement ends with **not inspected** status. Its earlier scene list is labelled as coming from a previous inspected frame, and target actions wait for a fresh scan because the new positions have not been checked. You can keep using eligible arrows without repeatedly loading the assistant, then request **Inspect this ending** when you need current visible targets.

For turns that use ending inspection, a reported continuity mismatch pauses progression for review. Pending-review candidates are visible in the panel but cannot become the next interaction until the ending is accepted. A still image cannot verify the complete movement, spoken words or continuous object conservation. H3 can still produce weak motion, identity drift or inconsistent scenery; review the actual clip before treating its appearance as established evidence.

## Version 1.4.1 player recovery validation on 2026-09-09

Focused checks cover opening appearance before rendering and persistence on acceptance, assigned portraits alongside background references, cancelled/failed openings, description fallback, stale selections, switching from person A to B, and recovery of a failed queued move without losing later actions. The built browser scenario exercises the real game components with isolated test responses; it does not claim live model accuracy.

The reported saved game was separately repaired using its accepted opening description and visual comparison with the current ending. The user's assigned ending photo remained bound to the player. Resuming its one waiting Right move exposed a stopped ComfyUI service; after starting the installed runtime, retrying that saved move completed without a language-model call. Visual review showed the original player moving right while nearby figures stayed in place. The 608 × 320, eight-step, 73-frame clip took 29.986 seconds in ComfyUI and 31.30 seconds in the video job, excluding service startup and the browser queue. This is one recovery example, not a performance benchmark or a guarantee of every generated movement. Private game media is not published.

## Version 1.4.0 movement validation on 2026-09-09

The reported multi-person city ending was copied into private QA. An explicit scene scan and player selection preceded three actual 0.2-resolution, eight-step H3 renders, each about three seconds long. The live saved game and its pending take were not changed.

The first Forward used the accepted ending as its I2VA input and visibly moved the selected player away from the viewer, but also brought a bystander forward. Back used the original saved ending as its FL2VA destination and moved the player toward that view. The bystander defect prompted individual hold instructions for observed background figures. The corrected Forward moved the player down the street while the previously wandering figure stayed in place. These are inspected examples, not a statistical quality guarantee; the correction used a new generation seed.

H3 execution took 30.049 seconds for the first Forward, 17.012 seconds for Back and 15.799 seconds for the corrected Forward. End-to-end turn times were 33.43, 18.90 and 18.31 seconds respectively on the local setup. These turns recorded deterministic preparation and no ending inference. The initial explicit scene scan did use the assistant. Timings exclude the browser queue's three-second editing window and are not portable hardware benchmarks.

Focused regression checks cover current/return frame assembly with assistant calls forbidden, saved navigation after reopening, design-reference compatibility, player binding, stale selections and visible review controls. A production frontend build and the dedicated scene-recovery browser scenario also passed. The full backend/frontend suites were not rerun for this release. Private copied game media is not included in the public package; earlier published demos retain their original version labels.
