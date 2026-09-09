# Local validation — 9 September 2026

This record separates deterministic tests, real assistant inference and real H3 rendering. It accompanies [the implementation findings](RELIABILITY.md) and [the installed-model comparison](ASSISTANT_EVALUATION.md). No model download or cloud inference was needed.

## Automated checks

- Backend: **1,413 tests passed**. Two existing dependency deprecation warnings remain in the FastAPI/Starlette test client.
- Frontend: **357 tests passed** across 28 files; production build passed.
- ComfyUI bridge: **40 tests passed**.
- Production-bundle Chromium smoke: four unique game actions, zero page errors. It covers immediate inventory without a video request, saved quick-action opt-out, handoff recipients, stale targets, a lost acknowledgement without duplicate submission, and availability of intended/visible acceptance after inspection failure. Its API is mocked.
- A separate browser check used the real saved QA city: empty player inventory, saved Elara, correct controls and disabled take/drop/give for her coin. Inventory opened by button and exact text with zero POST/model/video requests during the measured interaction; world/turn hashes stayed unchanged and no page errors occurred. QA port 8768 needed a read-only GET Origin-header shim for the application's production-port allowlist. Initial unrelated Studio compile/autosave requests were blocked before measurement. This is distinct from the mocked smoke above.
- `git diff --check` passed. Tests do not establish universal model correctness.

The added regressions exercise malformed envelopes and JSON, nonfinite values, unsupported grammars, bounded repairs, exact quotes, NPC roles and secrets, inventory/ownership, conditional world rules, door controls, combat state, branch acceptance, invalid editor input, cancellation after delayed child receipts, edited presentation retaining effects, and language metadata through the actual H3 compiler.

## Real Game observations

All game renders used **0.2 MP, 608×320, three requested seconds and four steps**, against a separate local QA data folder.

- A text-only city premise produced a street, blue shop door and brass coin without uploaded references or a separate image render. Initial planning used two model calls totalling **8.27 seconds**; request creation through the completed plan took **15.97 seconds**, including preparation.
- `I take the Brass Coin` used the fast mechanics route. One directing call took **3.12 seconds**; request creation through the plan took **3.58 seconds**. The rendered character visibly held a coin, and accepted world state recorded the player as holder across subsequent reads and a server restart. These are single observations, not latency guarantees.
- A weakened clockwork enemy, with an explicitly authored final-blow rule and a sword already held by the player, received a real actor/writer/director plan. The render showed a sword strike and a collapsed sentry. Acceptance saved health `0`, defeated/dead `true`, and retained the player's sword. The subsequent actions API disabled both talking to and attacking the defeated enemy while leaving examination available.
- The combat actor initially represented mechanical clicks as a dialogue line. This was retained in the baseline evidence and motivated clearer nonverbal-actor instructions. Cached local Whisper did not detect spoken words in that render; transcription is not proof of exact sound or lip sync.

The NPC conversation test caught two additional integration problems: every newcomer was required to request an identity image, and blank inferred language labels passed planning but failed the H3 compiler. Newcomers now support stable text identities without portrait generation. Missing labels are completed in one metadata-only request, and saved failed plans can resume without rewriting their dialogue. The compiler retains the language-tag syntax required by the official guide. [MiniMax H3 dialogue guidance](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md#44-speakers-dialogue-and-singing)

The first completed introduction said the requested question and newcomer reply, but visually put the collected coin on the pavement. Intended acceptance retained its logical holder. This exposed contradictory old placement captions in the render prompt. The correction projects canonical starting/ending assignments without committing them, removes old placement clauses from render-only captions, and places bounded continuity instructions beside the shot action. An actual second take kept the coin visibly in Alex's hand while Elara introduced herself. Her stable character ID and the coin's holder survived acceptance and restart. The baseline video is retained. Cached CPU Whisper transcribed the original dialogue as “Who are you? Hello, I'm Alara”; this supports presence of the two lines, with an approximate name transcription, rather than exact pronunciation or lip-sync accuracy.

The next actual `give` turn used one directing call (**3.62 seconds**, **4.11 seconds** from turn creation to plan). Its approved effect transferred the same coin to Elara. The rendered ending visibly showed her holding it, and acceptance saved her original character ID as holder with the player's held inventory empty. The continuity retake and handoff reported **43.808** and **67.559 seconds** of H3 server execution respectively. These are individual observed timings. A twelve-second Game export combines the four accepted scenes, with **288 video frames at 24 fps**; its last sampled frame still shows Elara holding the coin. FFmpeg decode passed, and independent cached CPU transcription found the expected question and introduction during the conversation segment.

The final Qwen 27B configuration was then exercised in the actual image-backed city pipeline. Asked “Who has the coin?”, Elara answered “I have it.” and lifted her already-held coin, with no proposed state change or asset job. Actor, writer and director calls took **2.64, 4.55 and 4.61 seconds**. Cached CPU transcription found both exact lines in the generated scene. H3 visually duplicated the coin while raising the character's hands; the Qwen ending inspection flagged both the duplicate and an unregistered background figure. That take was rejected through the normal cancellation action. The previous good handoff remained active, the complete accepted world stayed unchanged, and exactly one coin remained recorded with Elara. This retained failure demonstrates recovery and the remaining visual-model limitation; it is not included in the successful twelve-second export.

## Repeated assistant evaluation

The first full evaluation v2 run used fourteen cases twice: **26 creative model cases and two deterministic locked-door guards**. All 76 stage calls passed their schemas and all 26 model cases compiled into H3 prompts. **22 of 26** passed the explicit behavioral checks; both guards rejected before inference. Median model-case time was **8.89 seconds**, excluding initial model preparation, with **64.26 effective output tokens/second** across measured stages.

Both repeats of two scenarios failed: examining a key became picking it up, and a cooperative NPC with a known door code expressed an intention to answer without actually speaking. These failures motivated additional semantic checks. This run invokes the creative planner directly, without the application's fast mechanics or StoryManager's one semantic repair. Its evidence and score remain unchanged.

A follow-up Gemma run of those two cases three times, explicitly allowing one semantic repair, passed **1 of 6**. It exposed overly permissive speech/exception checks and one false rejection of a safe inspection due only to unnecessary metadata. Those checks were corrected, and the failed evidence remains in `model-eval-semantic-recovery`. A separate installed Qwen 3.8 27B Q4_K_S diagnostic then passed both difficult cases on the first attempt, with no semantic repairs, in **9.95** and **9.20 seconds**. That diagnostic loaded the preceding guard version; its better behavior must not be attributed to the latest guard edits. Broader model selection requires the separate full-suite follow-up.

That final Qwen 27B follow-up passed **26/26 automatic model cases**, plus both locked-door guards, using one allowed semantic correction per case. All **78 stage calls** passed their schemas; **two corrections** were used. Median case time was **9.26 seconds** with **11.12 seconds** of initial preparation. It is the selected runtime candidate. Manual review still found one creative handoff with contradictory narrative/effects and smaller scene-description inconsistencies; the endpoint instruction was clarified afterward. The full evidence, limits and correction policy are described in [ASSISTANT_EVALUATION.md](ASSISTANT_EVALUATION.md). These are finite automatic criteria, not a claim that every response or generated image is correct.

The subsequent give/drop follow-up passed **4/4** without correction. Manual review confirmed that all four final holders matched the narrated outcome, with one minor surface-description ambiguity retained in the notes. The final source passed the full backend suite at the count above, and the real Qwen city pipeline exercised both image-backed planning and ending inspection.

Manual review also recorded softer remaining problems: door-opening prose sometimes implied arriving from outside the established room; a locked-door attempt invented fatigue; a text-only synthetic opening described the workshop without registering its location; and Albanian responses preserved the quotation and language label but answered imprecisely. Structural success is therefore not presented as a universal writing or gameplay quality score. The separate real city test did register its street, door and coin.

## Real Studio films

Nine actual H3 scenes were rendered and joined using saved-motion continuations. These are original **authored Studio plans**, not a score for autonomous writing. Both were made without uploaded identity pictures.

- **A Very Polite Pursuit:** three ten-second scenes, four steps, 0.2 MP. The finished file has **720 video frames at 24 fps, exactly 30.0 seconds**, plus audio. H3 server execution times were **93.827, 101.595 and 67.736 seconds**.
- **The Extremely Important Parcel:** six ten-second scenes, eight steps, 0.2 MP. The finished file has **1,440 video frames at 24 fps, exactly 60.0 seconds**, plus audio. H3 server execution times were **95.904, 110.723, 114.798, 82.697, 81.984 and 79.983 seconds**.

Those times include the work reported by each H3 server job and differ in cache/loading state. They do not establish a controlled four-step versus eight-step comparison. Original generated clips and their saved motion sources remain available; the exports trim each new scene to exactly ten seconds. FFmpeg decoding and frame-count checks passed.

Sampled-frame review found consistent main characters and props, a readable ball handoff, the parody's exaggerated salutes and movement, and its final yellow-duck reveal. The chase uses shaded cartoon forms rather than strictly flat pixel sprites. The cone maneuver is approximate, and the parody's crouch does not convincingly show passing under the rail. These limitations are retained in the review, not described as perfect acting.

Both films have audio. The native chase track is quiet (mean **−60.5 dB**, peak **−30.0 dB**); the parody measured mean **−44.5 dB**, peak **−6.0 dB**. Cached Whisper small on CPU with voice-activity detection found no spoken dialogue in either silent-comedy film. No sound was replaced, and these measurements do not constitute a full listening assessment.

The first chase video succeeded but its ending inspection hit LM Studio's unsupported boolean-item schema. After the schema fix, only inspection was retried; the original video run was retained. The remaining film scenes completed without that error.

## Evidence and preservation

The separate local research workspace holds `films/run.json`, original/source/exact scene files, final films, contact sheets, `live-review.json`, `audio-review.json`, the city snapshots, and `game-action-qa` evidence. Evaluation v1 records remain unchanged. Evaluation v2 adds the real compiler gate and corrects the synthetic fixture's old `t2v` mode to H3's `t2va`.

Before installation, all **18 existing live stories** were readable and their worlds passed the new validator. Their **34 turns**, including one awaiting review, one awaiting acceptance and one uncertain turn, were recorded for preservation. The deployment helper takes a fresh source snapshot and a stopped data backup, then verifies saved branch, inventory, turn and settings fingerprints after restart. It does not resolve the user's pending decisions.
