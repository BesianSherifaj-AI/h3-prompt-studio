# Game and Studio reliability work

The central problem was broader than model choice. Valid JSON could still describe the wrong action, a second AI stage could change the action again, and a failed acceptance or editor update could leave inconsistent state. A slower GPU configuration also made a more capable installed model unnecessarily expensive to use.

## What now owns an action

Known item and movement operations are resolved by `backend/gameplay.py` and `backend/world.py`. Their accepted effects determine possession, clothing, opening state and location. The director chooses framing, timing and sound, while generated performance text is bound to the approved action. Inspecting an object therefore cannot become picking it up merely because the director wrote a different movement.

The fast route requires an unambiguous command or a selected structured action against visible, known targets. Compound, quoted, unknown and conversational requests stay with the creative planner. The player can disable quick actions when they want NPC reactions on every interaction. Active guides, camera locks, current state and the saved ending also reach the fast director.

Authored world rules, active guides/custom instructions and unsupported relevant character or item conditions conservatively require creative resolution. In that route, a typed action's ordinary effects are conditional proposals rather than compulsory success: a rule can prevent lifting a cursed item. Existing lock, possession and reachability guards still apply. Inventory display remains immediate.

Conversation and fictional combat remain creative tasks. Each NPC sees its own context, public responses already heard and relevant visible facts. Private motivations are withheld from other actors. Explicitly addressed NPCs are selected ahead of bystanders. Defeated, dead or incapacitated characters cannot continue ordinary interaction. Character-state effects allow supported combat consequences to persist.

Named read-only inspections now reach both the actor and coordinator as an explicit placement contract. Unsupported holder/location changes are rejected; concrete NPC transfers and narrowly grounded authored exceptions retain their evidence. An unused optional exception cannot invalidate an otherwise safe inspection. Addressed questions also check the specific contradiction where an actor intends to answer but supplies no spoken or performed answer. These English-language checks are deliberately limited: they preserve refusal, deliberation and nonverbal behavior, and do not prove arbitrary rule interpretation or general conversational quality.

The writer now receives the actual story premise, including on a text-only opening. It can propose bounded new locations, props and characters without generating a separate image for every object. New IDs are checked against the entire world, assignments use canonical effect fields, and discoveries become facts only when the turn is accepted. New doors get usable controls when the model omitted them, while explicit restrictions and locks remain authoritative.

## Recovery and acceptance

Structured responses are checked locally even when the model server claims schema compliance. LM Studio's documentation explicitly distinguishes constrained output from the model's ability to handle complex schemas, particularly for small models. The evaluated Qwen variant often returned syntactically valid responses which failed game invariants; see the separate [measured model comparison](ASSISTANT_EVALUATION.md). [LM Studio structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output)

The transport classifies failures, records per-attempt diagnostics, remembers temporary schema incompatibility and bounds corrective work. An uncertain network submission is not automatically repeated. Eligible completed but invalid semantic responses can be corrected once with the rejected response retained as data. A timeout is not proof that remote inference or rendering stopped.

Frontend requests have bounded deadlines including body reads. Durable request IDs survive lost acknowledgements and reloads, so recovery finds the original operation. Stale action targets and cross-story submissions are rejected. Inventory reads are immediate and do not generate video.

Edited plans are validated before GPU submission. Accepted state is first computed on a copy, then the branch endpoint, effects and history are advanced together. Editor updates are validated and persisted before changing the live object, preserving any active worker's turn identity. Cancellation also handles child jobs whose IDs arrive after the stop request.

Ending inspection is separate from rendering: a valid saved video survives a failed observation. The user can retry only the inspection or accept the intended result. Visual effects use known IDs and a restricted schema; a single frame cannot establish death, hidden locks, ownership, speech or private knowledge. Intended acceptance retains the approved plan's next choices instead of replacing them with potentially contradictory visual guesses. Explicit visual acceptance can use the observed outcome.

The real Studio test caught an additional compatibility issue: the empty visual-effect array used a boolean `items: false` schema. LM Studio rejected it before inference. An object item schema with `maxItems: 0` preserves the same empty-array constraint and works with its grammar converter.

## Performance and limits

The installed Gemma 4 12B QAT was the stronger local candidate in this machine's initial eleven-case creative planning comparison. Exclusive assistant loads now explicitly request and verify GPU KV-cache placement instead of inheriting CPU placement. The optional resident CPU profile retains its explicit CPU placement. Model configuration, measured timings and the limits of that small comparison are documented in [ASSISTANT_EVALUATION.md](ASSISTANT_EVALUATION.md). [LM Studio load configuration](https://lmstudio.ai/docs/developer/rest/load)

Expanded testing subsequently selected the installed Qwen 3.8 27B Q4_K_S: it supplied the missing factual answers, preserved inspections and passed the repeated automatic suite at a median 9.26 seconds per model case, with two bounded corrections. Manual continuity limits remain documented. The application still supports selecting another installed compatible model; this is a measured choice for the tested 24 GB machine.

Actor, writer and director planning share one owned model lease. Simple mechanics skip actor and writer inference entirely. Discovery does not automatically add an image-generation job. These changes reduce work rather than merely shortening timeouts.

Current held/worn assignments also reach the compiled video prompt beside the shot action and ending. The renderer receives distinct initial/final assignments for a handoff or drop; unchanged possessions remain with their carrier. Old placement clauses are removed from render-only prop captions while original world descriptions remain saved. Bounded generated context is rebuilt for each turn so a later transfer cannot inherit a stale hold instruction.

H3 remains a generative video model. Its documented reference and continuation modes help condition an image or saved motion; they do not enforce a game's object database or guarantee exact acting. Pixel style, tiny props, handoffs, lip sync and identity can still drift in the rendered footage. The application can preserve correct logical state and expose a discrepancy, but cannot guarantee that every generated frame matches it. [MiniMax H3 project](https://github.com/MiniMax-AI/MiniMax-H3)

## Reproducible checks

- Run the backend suite with `.venv/Scripts/python -m pytest -q`.
- Run `npm test -- --run` and `npm run build` in `frontend`.
- Run `node tools/browser_game_smoke.mjs` for the isolated production-bundle browser scenario. It uses a mocked local API and does not prove live model or render quality.
- Run `node --test comfy_extension/tests/*.test.mjs` for the transfer and queue guard.
- `scripts/evaluate_game_assistant.py` records real inference requests, schemas, semantics and timings against explicitly selected installed models. Read its help before execution; it owns and releases its evaluation instance.
- `scripts/render_story_examples.py` prepares two original authored Studio stories without network work. `--execute` renders them only against the isolated QA app on port 8768; `--resume` recovers saved receipts without blind resubmission. It exports a 30-second chase and a 60-second parody and preserves the original scene videos. These are authored render/continuation tests, not autonomous writing scores.

Live evidence and final measured results are recorded separately from synthetic unit tests. Failed baseline outputs are retained; they are not silently rewritten after the implementation improves.
