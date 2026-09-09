# Directing consistent H3 scenes

Scene continuity gives the video model a concrete account of the current shot. It identifies the visible cast, assigns each character a performance or a hold, describes important objects and their counts, and records starting and ending placement. These are generation instructions. The rendered video can still disagree with them.

## In Studio

Open **Scene continuity** in the scene editor. For each visible character, describe the starting pose and position, the permitted action, and the ending. Choose **Stay in place** for someone who remains seated, listens or watches. A character can remain seated while speaking, blinking or extending a hand; holding a position does not require a frozen face.

For an important prop, specify its appearance, quantity, starting placement and ending placement. Reuse the same object identity across shots. A handoff of one coin should say that the recipient receives the existing coin and that the former holder's hand becomes empty of that coin. Two distinct coins need separate identities or an explicitly authored group quantity.

Describe scene layout and intended background activity separately. An empty room needs no invented occupants; an authored busy street can keep its pedestrians. Describe color and location when established by the brief or reference. Leave genuinely unclear image details unspecified rather than guessing. A face reference, a wardrobe reference and an object reference retain their separate roles.

**Make my prompt** requests this staging in the existing planning pass. **Build without AI** compiles explicit controls directly. Editing a generated continuity block makes it an authored control that replanning preserves. Off-screen speakers belong in the off-screen roster; they do not receive an on-screen physical performance.

## In Game

The game supplies current entity IDs, item holders, worn items, location and known state. The selected player action and the actual NPC responses determine who participates. The director stages that approved action; other visible characters hold their established places instead of copying the active character.

Object placement is projected on a copy of the world before rendering. The starting assignment applies before the approved action, and the final assignment applies after it. The application commits accepted changes to persistent world state; compiling a prompt cannot advance the game. An inspected key does not become a pickup merely because H3 would find that movement interesting.

Generated scene controls are refreshed for each turn. Completed actions are not permanent directing instructions. The prior accepted ending and known appearance provide continuity context; a cancelled candidate does not become the next starting point.

The ending inspector compares the actual final frame with the final shot's intended cast and props. A definite reported continuity mismatch pauses for review before advancing the story, including when routine automatic rendering is enabled. Missing or obscured objects are uncertain evidence, not permission to create or delete an item. A single frame cannot verify an entire movement, speech, or continuous stillness.

Fresh inspections require one assessment for every character and prop in a nonempty final contract. **Uncertain** is a valid assessment; omitting identities is not. Incomplete responses leave the video available at the inspection-recovery step and preserve the previous accepted ending. Saved older inspection requests keep their original protocol for recovery, and existing observations remain readable. Explicitly requesting another inspection or another take uses the current protocol.

## Portable project data

Projects remain schema version 1. Each shot may contain `scene_contract` with these optional groups:

- `actors`: rows with `subject_id`, `activity` (`act` or `hold`), `start`, `action` and `end`.
- `objects`: rows with `entity_id`, `name`, `description`, `count`, `start` and `end`.
- `environment`: established layout, lighting and appearance.
- `background_activity`: intended background motion or stillness.

Actor IDs must identify visible subjects in that shot. Each actor and object identity appears at most once in its group. Counts are positive whole numbers from 1 through 100. A contract allows up to 32 actor rows and 24 important object rows, with bounded prose and a total size limit. This keeps large inventories from turning into unbounded prompt text. Game prioritizes changed and relevant props; an inventory is not a request to display everything in it.

The `scene_contract_source` marker distinguishes generated controls from authored controls. Applications should preserve generated data during ordinary saving, and mark it authored when the author edits its meaning. IDs are application identifiers; they are not captions or text that H3 should draw on screen. The compiler owns H3 binding and dialogue syntax and rejects forged section headers or unresolved references in continuity fields.

Older projects and saved assistant responses remain readable. Without an explicit block, the compiler emits restrained identity and idle-motion guidance from the existing shot roster; it does not invent a precise sitting pose, clothing color or hand assignment.

## Performance and verification

Scene control adds no separate model stage. Existing mechanical game actions still use deterministic rules and a single director request; opening inventory uses no model. Exact duplicate action/ending prose is removed where safe. More useful detail can still increase output tokens, so compare recorded stage latency rather than assuming a longer prompt is faster.

The director output allowance scales with the requested shot and cast counts, from a normal 1,400-token baseline up to the transport's 4,096-token limit. A three-shot, three-actor plan receives 2,900 tokens: the previous flat allowance cut off a real response. This raises the available ceiling without requiring longer output or another model call. Very large plans and model mistakes can still need review.

The [research review](H3_SCENE_CONTROL_RESEARCH.md) explains which 2025–2026 methods apply to this local H3 workflow. It distinguishes prompting and state-management ideas from trained models, temporal-attention changes and approximate accelerators. No third-party model weights, hosted credentials or private project history are included in the repository.

The repeatable [scene-control demo](../scripts/render_scene_control_demo.py) prepares three original ten-second scenes without connecting to a server. `--execute` runs them against an isolated local QA app; `--resume` reconciles the saved requests instead of blindly submitting them again. The coin demonstration is authored direction, not a measure of autonomous writing ability. Review its actual frames and video before using it as evidence of model compliance.
