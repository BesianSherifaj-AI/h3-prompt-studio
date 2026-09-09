# H3 scene control: research and implementation priorities

Research checked on 9 September 2026. This review covers official MiniMax H3 guidance, current ComfyUI integration code, and relevant 2025–2026 papers and repositories. Paper dates below are first arXiv submission dates unless a conference or repository release is explicitly identified. Repository claims describe the inspected versions, not independently reproduced benchmarks.

The most useful near-term improvement is a **single, explicit description of the current shot**, assembled from accepted world state, approved actions, reference bindings and camera controls. It should identify who and what is present, where each begins and ends, which actor performs each action, and what everyone else continues doing. Add enough concrete detail to remove ambiguity, while keeping unknown details unknown and removing repeated or contradictory descriptions.

That is an engineering recommendation supported by the sources below, not a claim that longer prompts eliminate hallucinations. A model can still duplicate a prop, alter a face, move a bystander or invent an extra figure. Reliability therefore also requires visual inspection, honest uncertainty and preserving the last accepted state when a candidate is rejected. The project's [validation record](VALIDATION_2026-09-09.md) already includes a generated prop duplication despite a correct logical plan; the rejected take did not change the accepted world.

## What official H3 guidance establishes

MiniMax describes H3 as a system with distinct components. H3-Context-IR interprets instructions and multimodal relationships into a structured representation; H3-Base performs generation. Context-IR is not part of the open-source release. A local planner and compiler can fill part of that preprocessing role, but should not be described as the official Context-IR model or assumed to reproduce its behavior. The full system's capabilities also should not be conflated with the particular local checkpoint and workflow in use. [1: official H3 repository](https://github.com/MiniMax-AI/MiniMax-H3)

The official base guide asks for observable audiovisual detail: initial composition, subjects' appearance and position, environment, actions, reactions and camera behavior. Image conditioning anchors the starting composition; a continuation should describe how that state develops. Camera instructions and shot boundaries have defined syntax, and silent characters should not receive speaker identifiers. Spoken words use language-labelled dialogue markers. The base prompt separates the integrated scene description, soundscape and non-diegetic music. These requirements support a deterministic compiler that translates approved scene data into the model's expected form. [2: H3 base prompt guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt)

The Ref2VA guide further distinguishes subject identity, concrete frame anchors, video references and audio references. Multiple references to one person can define one subject; they do not imply several people. Reference roles and labels must remain consistent. The detailed shot description should explain appearance, position, actions and when referenced content applies, rather than merely summarizing the plot. A video used as camera inspiration is different from footage the output actually continues. [3: H3 reference prompt guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt)

These sources justify precise positive descriptions. They do **not** establish a universal optimal prompt length, a guarantee of exact counting, a generic negative-prompt recipe, or reliable execution of arbitrary long sequences within a short clip. This review found no controlled H3 study proving that appending more adjectives monotonically improves state consistency.

ComfyUI's native H3 nodes provide actual first/last-frame and reference conditioning, including keyframe/reference latent information that is retained during denoising. This is materially different from mentioning an image in prose. The same source also exposes model-specific packing and duration constraints. Any stronger frame anchoring must use the appropriate node and checkpoint path, rather than a text-only substitute. [4: native ComfyUI H3 conditioning](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_minimax_h3.py)

## A scene description that reduces ambiguity

The following design is an original application proposal. It combines the official prompt requirements with the existing world's identity and effect records. It is not a new model architecture and does not impose hard geometric constraints on H3.

**Identity and count.** Use stable instance IDs for each principal actor and relevant prop. Two images of the same person resolve to one actor; two separately registered coins remain two objects even if their descriptions match. Distinguish an explicit group quantity from an individual instance. Keep the number of principal characters separate from an authored crowd or an established background population. “Exactly two principal characters” should not silently erase pedestrians from a crowded street that the user requested.

**Appearance.** Preserve established clothing, colors, material, markings and body features. Separate these relatively stable properties from changing location, holder, pose and expression. A caption such as “a red cup resting on the table” can become stale after pickup: the cup remains red, while the old placement must stop acting as current truth. Do not invent a coat color or a hand assignment just to fill an optional field. If a reference establishes a detail, bind that reference to the correct identity.

**Position and viewpoint.** Describe meaningful relations in a consistent coordinate frame: beside the workbench, behind the seated actor, or screen-left in this fixed shot. Screen-left and world-left are different after a camera reversal. A new camera angle does not move the player to another room. A point-of-view player must not become an extra visible third-person body merely because that player also has a character record. Offscreen speakers can remain audible without physical actor rows.

**Active and passive performance.** Give the active actor a short, observable action. Give each visible bystander a compatible hold or reaction: remain seated, breathe, blink, watch the speaker, keep hands resting where established. Avoid “everyone stays motionless” unless that is the intended scene. Passive staging must neither mute approved speech nor copy another character's action. An idle character should not start handling the protagonist's object, approach the camera or perform an unrelated gesture because their role was left vague.

**Object transitions.** Express the same physical item at the start, during contact and at the end. A handoff is a transfer, not the creation of a second object. The former holder's hand becomes empty of that particular item. If the recipient then sets it down, the final holder is nobody; the table is its final placement. That terminal state must agree across the beat, final-state prose and logical effects. Intermediate states should not be committed as the ending.

**Environment and time.** State the intentional background activity and whether the shot continues an established scene. Describe entry, exit, opening, closing, destruction or transformation when approved. An empty corridor and a populated station require different constraints. Limit a short clip to actions it has time to show; extra explicit detail cannot make several lengthy movements or a long answer fit naturally into three seconds.

Here is a synthetic example of the resulting content. Its spatial and appearance details are intentionally authored for this example; a real continuation should use established details instead.

> A fixed medium two-shot shows two adults at a wooden workbench. Ada, in a navy jacket, stands on the left. Bo, in an ochre shirt, remains seated on the right. Ada holds one small round brass token in her right hand; the workbench surface between them is clear. Ada extends that hand. Bo reaches with his left hand and takes the same token. Ada releases it and draws her now-empty right hand back. Bo ends with the single token in his left hand above the workbench. Their other hands retain their established positions. Bo's seated posture and both characters' clothing remain consistent. The workshop stays quiet and the doorway remains empty. Breathing, blinking and the assigned hand movement provide natural motion.

This is useful only if it matches the approved action and actual reference frame. The compiler should not automatically add the reach, withdrawal, empty doorway or named hand when those details conflict with the user's direction. A dialogue-only turn would instead retain the current token holder throughout.

## Relevant research: what transfers to this application

### Direct H3 evidence: language control and temporal routing

**H3-World — 1 September 2026.** Chen and colleagues study the 33B MiniMax-H3 backbone itself. They observe coarse zero-shot language control, then separate character and camera commands and bind each instruction to its intended video-latent interval. Their stronger temporal control uses attention routing and trained LoRA adaptation: 8,000 gameplay samples, 10,000 optimization steps and 0.199% trainable parameters. This directly supports separating character motion from camera motion in the application, but the trained result is not obtained merely by writing more detailed prose. [19: H3-World paper](https://arxiv.org/abs/2609.01560)

The authors have released code and a checkpoint link. Their implementation requires a pinned, patched DiffSynth checkout and the H3 FL2VA path; it is not a normal ComfyUI prompt-node setting. Treat it as a separate renderer experiment after validating availability, terms and compatibility. The inspected repository root did not expose a license file, so this review does not assign its code a permissive license. [H3-World implementation](https://github.com/Danzer1xxxxChan/H3-World), [checkpoint page](https://huggingface.co/DANNY621/H3-World)

### Explicit state and visual memory

**StateAgent and StateBench — 3 September 2026.** Miao and colleagues distinguish remembered frames from the world state required after a new action. Their method maintains entities, updates their state, grounds the predicted endpoint in a future frame and then generates continuation video. The benchmark includes past-visible states, occluded processes and more complex transitions. The official repository contains code under MIT. This is the closest conceptual match to a game whose inventory and scene must survive across clips, but it is a very recent preprint and its reported improvement is not an H3 result. [5: StateAgent paper](https://arxiv.org/abs/2609.03673), [official implementation](https://github.com/AMAP-ML/StateAgent)

Application inference: retain separate records for **accepted state**, **the next proposed change**, and **uncertain visual observations**. A generated target frame can be helpful conditioning, but must itself be checked; otherwise a wrong token holder becomes a stronger wrong anchor. A cancelled candidate must not update location, character or prop memory. A past frame is valid history without necessarily being the correct current reference.

**CANVAS — 15 April 2026.** Mondal and colleagues plan character appearances, location identity and object-state changes, then retrieve visual anchors for each shot. Direct continuations reuse a previous frame; a different view of an existing location uses relevant location and character anchors. Candidate images receive targeted continuity questions before memory updates. This is primarily a storyboard framework, with additional video analysis, rather than a native H3 inference patch. Its gains should not be copied into this application's results. [6: CANVAS paper, sections 3.1–3.2](https://arxiv.org/html/2604.13452v1)

Application inference: key character, location and prop references by identity **and state version**. Revisiting an emptied display case should retrieve an emptied-case anchor, while revisiting a character after an approved clothing change should retrieve the current outfit. A still-image candidate selector is a possible later improvement, but several image candidates and extra VLM calls carry real latency. Do not introduce an unbounded multi-agent loop for every simple turn.

**World Scene Graph Generation — 13 March 2026.** Peddi and colleagues explicitly represent interacting objects that are currently unobserved, rather than discarding them after occlusion or camera motion. Their models operate on video understanding and world scene graphs; they are not a prompting technique for video synthesis. The transferable principle is object permanence in the application's memory. [7: WSGG paper](https://arxiv.org/abs/2603.13185)

**R4DSG — 11 August 2026; listed as ACM Multimedia 2026.** Ma and colleagues organize long-video memory around persistent objects, time, stable anchors and relative spatial changes. This supports queryable records rather than relying entirely on caption or transcript history. Its evaluation concerns egocentric question answering, so it provides architectural motivation, not evidence that such memory alone makes H3 obey a prompt. [8: R4DSG paper](https://arxiv.org/abs/2608.11017)

Application inference: “not visible in this crop” must not mean “deleted,” “dropped” or “holder unknown.” Preserve accepted possession through occlusion and mark visibility uncertainty separately. Conversely, do not use a logical holder as proof that the pixels show a successful transfer. Both errors can occur when a single VLM summary is treated as authoritative world state.

### Identity and shot planning methods with model-specific internals

**Story2Board — 13 August 2025.** Dinkevich and colleagues combine grounded panel prompts with latent anchoring and reciprocal attention value mixing to preserve identity while allowing composition and pose variation. The official MIT implementation uses FLUX.1-dev; it generates storyboard images. Grounded per-shot prompts and the distinction between identity consistency and frozen poses transfer as design ideas. The latent/attention operations require a model-specific implementation and are not a drop-in H3 prompt option. Its repository also notes that Windows was not officially tested. [9: Story2Board paper](https://arxiv.org/abs/2508.09983), [official code](https://github.com/DavidDinkevich/Story2Board)

**Long Context Tuning — 13 March 2025.** Guo and colleagues train video diffusion models to attend across shots, with positional and noise changes; a further context-causal configuration supports cached autoregressive inference. Increasing the planner's chat history is not this method. Transferring it would require model training and compatible video-model internals. Its relevance is evidence that cross-shot consistency can need more than a single-shot model receiving a longer text summary. [10: LCT paper](https://arxiv.org/abs/2503.10589)

**Stand-In — initial code/weights release 12 August 2025; CVPR 2026.** The official project provides trained identity control for Wan, with a later Wan2.2 version. It is useful evidence that identity conditioning can be strengthened through specialized learned components. Its Apache-2.0 repository does not make the adapter compatible with H3. A different checkpoint family, preprocessing path and any associated weight terms would need a separate integration. [11: official Stand-In implementation and release notes](https://github.com/WeChatCV/Stand-In)

These methods distinguish three separate goals: preserve identity, preserve state, and preserve spatial composition. A face-consistent clip can still show the wrong holder; a motionless background can preserve layout while making the scene look unnatural. Evaluate these properties separately.

## Current implementation and the next useful changes

The source inspected during this review already includes a scene-contract layer. [scene_contract.py](../backend/scene_contract.py) validates visible actor rows, prop identities and counts, and renders bounded staging text. [game_director.py](../backend/game_director.py) asks for shot staging while binding physical performance to approved beats. Generated contracts are distinguished from authored controls. [stories.py](../backend/stories.py) builds canonical start/end prop assignments from the world and approved effects. [compiler.py](../backend/compiler.py) handles H3 syntax and removes some exact action/performance repetition.

These are source-level capabilities, not a measured visual success rate. Exact-string deduplication also does not imply that all game prompts are free from repeated meaning: adding placement instructions around an action can make a formerly identical performance string different. Preserve authored nuance and prefer assembling distinct components to deleting prose heuristically.

The following priorities build on that implementation.

1. **Make the scene contract the single staging input.** Derive actor identities, relevant object instances and canonical assignment endpoints from approved data. Let the director propose bounded posture, position, camera and passive behavior only where compatible with that data. Keep unknown details optional. Validate contracts before expensive asset preparation or rendering. Retain provenance so an authored control cannot be silently replaced by a generated one.

2. **Compile each responsibility once.** Give the prompt an initial scene anchor, actor staging, one action sequence, exact speech, terminal state and camera/sound instructions. Avoid replaying the entire story or repeating the same action as story text, performance and shot action. Do not append old placement captions after canonical current placement. Token savings should come from removing redundancy, not removing the holder or passive actor behavior that resolves an ambiguity.

3. **Version continuity references with accepted state.** Keep appearance anchors separate from scene-layout anchors and accepted ending frames. Record which branch, turn, entity and state each represents. Reuse the last accepted frame for a continuous take; use appropriate location/identity references for a deliberate camera change. Generated references remain candidates until accepted. Rebuilding or cancelling a turn must invalidate only that candidate's artifacts and derived memory.

4. **Make important props observable.** Where the user has not locked framing, choose a scale and viewpoint that expose the relevant hand/object relation. A tiny token at low resolution may be impossible to count reliably. A closer view is an artistic choice that can improve inspectability, not a guarantee of correctness. Never change the requested action merely to make the inspector's task easier.

5. **Use targeted post-render evidence.** Inspect several moments around the relevant transition and retain the evidence behind findings. Prefer “one token is visible in Bo's left hand at the end” to a generic “looks consistent.” Treat blur, occlusion and crops as inconclusive. Show important disagreements for review and preserve the accepted branch until a choice is made. A bounded reroll should correct a specific failure rather than repeatedly adding unrelated prompt constraints.

No recommendation here requires replacing the current transactional story manager with an autonomous agent framework. Pure scene assembly and deterministic validation are the lowest-cost layer; optional visual selection should be added only after its benefit is measured.

## Speed: practical work before experimental accelerators

H3's ComfyUI text encoder consumes a raw prompt and multimodal labels through a Qwen3-VL-based encoding path; it is distinct from the application planner LLM. Changing the planner model does not replace this video text encoder. Prompt repetition therefore affects an additional stage beyond planning, although the actual latency effect depends on the runtime and cache state. [12: ComfyUI H3 text encoder source](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/text_encoders/minimax.py)

First measure the real pipeline: model preparation, planning stages, reference preparation, prompt encoding, sampling, VAE/audio decode, save, inspection and any retry. Report both cold and warm runs. A faster planner that causes more video rerolls can increase total time to an accepted clip. An optimization that reduces VRAM by moving work to CPU is not automatically a latency improvement.

Useful application experiments require no new model architecture:

- Preserve the existing deterministic route for known mechanical actions and cache only completed, validated planning stages. An uncertain network response is not permission to submit another expensive generation.
- Cache pure compilation and reference preprocessing by full input identity, including reference bytes, crop/resize policy, model version and relevant settings. Confirm whether ComfyUI already caches the corresponding node before adding a second cache. Do not reuse a changed scene's output merely because its story ID matches.
- Keep one explicit GPU owner when planner, image generator and video generator compete for memory. Measure the cost of each load/unload instead of assuming all models can remain resident.
- Benchmark useful prompt detail against redundant detail at identical render settings. Keep dialogue and action semantics fixed. Measure encoding latency, sampling latency and accepted-video rate separately.
- Keep fast draft settings visibly distinct from quality settings. A three-second low-resolution preview cannot validate the quality of a longer native-range final render, and tiny-object failures should be retested at a scale where the evidence is visible.

### Approximate caching and forecasting

**TeaCache — CVPR 2025 Highlight; original preprint 28 November 2024.** It estimates changes across diffusion timesteps to reuse computation. The official repository has several model-specific integrations and calibration guidance; H3 was not in its inspected supported-model list. Most code is Apache-2.0, with separate upstream components. It is background for a possible H3 port, not evidence that enabling an arbitrary TeaCache node will safely accelerate this workflow. [13: TeaCache repository](https://github.com/ali-vilab/TeaCache)

**Spectrum — 2 March 2026; CVPR 2026.** The paper fits diffusion feature trajectories with Chebyshev bases and ridge regression to forecast selected evaluations. Its reported FLUX/Wan speedups are not H3 measurements. [14: Spectrum paper](https://arxiv.org/abs/2603.01623)

A community **H3-native Spectrum integration** does exist. Its GPL-3.0 repository explicitly handles packed audio/video, supported samplers and fallback behavior. It also states that approximation changes outputs even with the same seed, documents audio and sampler-specific quality issues, and notes that outer sampler steps and actual model evaluations can differ. The inspected README identifies v0.2.23; the release-note view did not expose a calendar release date. This is the most directly relevant acceleration candidate found, but it should remain an isolated opt-in experiment until matched quality tests pass. [15: H3 Spectrum integration](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3)

For a later trial, pin the ComfyUI and extension revisions, start without other new model patches, and retain a native fallback. Test speech, passive motion, small props, ref conditioning and continuation separately. Record actual model evaluations, forecast counts, VRAM, audio artifacts and end-to-end time. At very low step counts, forecast warmup and final exact steps may leave little useful work to skip; that is a hypothesis to measure, not a promised speedup.

### Distillation and kernel optimizations that do not transfer directly

**TurboDiffusion — 18 December 2025.** Its large reported acceleration combines attention changes, timestep distillation, quantization and engineering around tested Wan models. It is not an attention-only speed claim and does not establish compatibility with H3. The official repository is Apache-2.0; checkpoint and dependency terms remain separate. [16: TurboDiffusion paper](https://arxiv.org/abs/2512.16093), [official code](https://github.com/thu-ml/TurboDiffusion)

**TurboT2VA — 25 August 2026.** This work targets LTX-2 joint video/audio generation through distillation and an architecture-aware inference stack. Its high-resolution speed figure is explicitly generator-only. The work illustrates why audio/video paths, text conditioning and quantization need coordinated treatment, but its LTX-2 kernels and distilled weights cannot simply be applied to H3. [17: TurboT2VA paper](https://arxiv.org/abs/2608.24674), [official inference code](https://github.com/thu-ml/TurboDiffusion/tree/main/turbot2va)

A trained Wan or LTX acceleration model is a different renderer integration. It may be worth a separate product experiment, but should not be presented as an H3 optimization or quietly substituted beneath existing reference, dialogue and continuation behavior.

## Tests that establish useful evidence

VBench-2.0, first submitted 27 March 2025 and revised 20 August 2025, evaluates human fidelity, controllability, creativity, physics and commonsense. It uses both generalist and specialized checks with human alignment. The relevant lesson is that attractive, temporally smooth output can still be physically or compositionally wrong. This project needs task-specific state checks alongside perceptual review, rather than a single aesthetic or generic VLM score. [18: VBench-2.0 paper](https://arxiv.org/abs/2503.21755), [official implementation](https://github.com/Vchitect/VBench/tree/master/VBench-2.0)

### Deterministic contract and compiler regressions

Exercise meaningful invariants without a model or GPU:

- Two references to one identity produce one actor; two distinct actors with similar descriptions remain separate. Duplicate contract IDs and unknown IDs fail before rendering.
- A point-of-view player does not add a second physical body. An offscreen speaker keeps the exact approved line without appearing in physical actor rows.
- A seated passive actor stays seated while another acts. Approved speech survives passive staging, and a silent actor receives no speaker marker.
- One held token remains one instance during a dialogue-only beat. A handoff moves its canonical holder; a handoff followed by a set-down ends unheld at the approved placement. Two deliberately separate tokens remain two.
- An accepted clothing change updates subsequent appearance, while camera changes preserve world location. Unknown appearance/position fields stay empty instead of becoming fabricated facts.
- A stale “on the ground” caption cannot override a current held assignment. Authored object placement that remains valid is not accidentally deleted by broad text cleanup.
- Editing, rerolling, branching or cancelling a candidate cannot overwrite another branch's accepted references, holder state or scene contract. Generated staging is rebuilt where necessary; authored staging survives unrelated edits.
- Exact redundancy is removed without deleting intentional nuance. The final H3 prompt still passes the actual compiler and all mode/reference/dialogue checks.

These tests establish that the application supplies consistent instructions. They cannot establish that H3 depicts the instructions correctly.

### A small controlled video suite

Use original synthetic fixtures with two fictional adults and a small set of visually distinct props. Begin with one variable at a time:

1. A dialogue turn while one actor already holds a token; another remains seated.
2. Pickup, hold, handoff and drop as separate clips, then as an accepted multi-turn chain.
3. Two similar props with different markings and different holders.
4. Temporary hand/object occlusion followed by reappearance.
5. A fixed-camera turn, a deliberate camera reversal and an explicit doorway entry.
6. A quiet two-person room versus an intentionally populated background.
7. An unchanged outfit versus an approved outfit change.
8. An offscreen factual answer and a short exact non-English quotation.

Compare the previous prompt assembly with the explicit contract at matched checkpoint, references, duration, resolution, sampler, schedule, seed and acceleration settings. Start with at least three paired seeds per fixture for a pilot and publish the sample count; expand before making broad reliability claims. Do not select only the best take. Keep draft and final-quality conditions separate and store the exact compiled prompts and source revisions.

Inspect the beginning, action contact, midpoint, end and any suspicious interval, using the full clip when a sampled frame is ambiguous. Record actor count, identity/appearance, posture/position, object count, holder/placement, action order and unrequested movement. Distinguish principal actors from authorized background figures. Review audio transcription and speaker timing separately from the video. A copied source line in a prompt is not evidence that the output pronounced it correctly.

Record each finding as **satisfied**, **violated** or **inconclusive**, with timestamped evidence and a reason. A VLM can help locate failures, but human review should adjudicate small-object ambiguity and important disagreements. Neither logical state nor a single vision-model answer should silently resolve an uncertain pixel observation.

Report successful renders, semantic failures, inconclusive checks, transport/compiler failures, retries and cancellations separately. Report total time to a reviewable candidate and, where measured, time to an accepted take. Include cold/warm preparation and inspection; do not relabel generator-only timings as end-to-end latency. A small pilot supports fixture-level conclusions, not “never fails.”

## Application findings and evaluation artifacts

The application now carries explicit actor and object contracts through planning and compilation, including canonical identity, appearance, passive posture, instance counts and intended endpoints. The director receives a bounded object registry with real entity IDs. Its output allowance scales with the requested shot/cast shape; a saved three-shot request that previously truncated is retained as an exact replay, with cached stages clearly separated from fresh inference. These are application controls, not an implementation of the papers' trained routing or attention mechanisms.

The [machine-readable evaluation results](scene-control-results.json) preserve historical failures and check versions, measured stage latency and tokens, source hashes, and manual observations. The scene harness uses real local model responses followed by production canonical assembly and compilation. Unit fixtures test the harness; they are not counted as live model successes. These planning results do not establish video or audio fidelity, and the individual video demo is not the controlled multi-seed experiment proposed above.

Observed limits include decorative environmental inventions, ambiguous camera wording, and occasional conflict between an all-visible roster and prose that describes progressively revealing characters. Canonical assembly can repair omitted rows without repairing every sentence. Multi-beat prompts also retain an aggregate action overview ahead of the timestamped beats and repeated identity/continuity wording. Removing redundant overview text is a candidate optimization requiring its own temporal-fidelity comparison; preserving per-shot identity and state anchors remains necessary.

## Availability, dates and licenses

This review recommends original application logic and links to upstream work. It does not vendor research repositories, checkpoints, example media or whole prompt guides. Repository licenses do not automatically cover associated weights, datasets, third-party code or hosted APIs.

- **MiniMax H3:** the official Community License identifies 2 August 2026 as its release/license date. It is a custom license, not MIT or Apache. Its territorial definition expressly excludes the European Union, United Kingdom, Republic of Korea and United States. Publication of this application's source must not represent H3 weights as permissively licensed or include them under the application's license. [Official H3 license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
- **ComfyUI:** GPL-3.0 license text; the native H3 integration is part of that repository. [ComfyUI license](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE)
- **StateAgent:** code was present in the official repository at review time, with MIT license text. [StateAgent license](https://github.com/AMAP-ML/StateAgent/blob/master/LICENSE)
- **Story2Board:** official code is MIT; its README identifies FLUX.1-dev as the base model, whose terms must be considered separately. [Story2Board repository](https://github.com/DavidDinkevich/Story2Board)
- **Stand-In:** official repository advertises Apache-2.0 and records the 2025/2026 releases noted above. The trained adapter is model-specific. [Stand-In repository](https://github.com/WeChatCV/Stand-In)
- **CANVAS:** the paper is available under CC BY 4.0. This review did not verify an official reusable code release/license through the inspected paper and project-page links; independently named reproductions are not the authors' official implementation. [CANVAS paper](https://arxiv.org/html/2604.13452v1), [authors' project page](https://ishani-mondal.github.io/canvas-project-page/)
- **H3 Spectrum:** GPL-3.0, distinct from the upstream Spectrum method and from H3's model license. [Integration license](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3/blob/main/LICENSE)
- **TeaCache, TurboDiffusion and VBench:** their official repositories identify Apache-2.0 for the relevant repository code, with upstream components and model/data terms requiring separate attention. [TeaCache](https://github.com/ali-vilab/TeaCache), [TurboDiffusion](https://github.com/thu-ml/TurboDiffusion), [VBench license](https://github.com/Vchitect/VBench/blob/master/LICENSE)

For LCT, WSGG and R4DSG, the recommendation here is architectural interpretation of the papers, not adoption of an audited implementation. No claim is made about a complete dependency or weight-license audit for those methods.

## Source index

All links were checked on 9 September 2026. Undated repository views are identified by the access date rather than assigned an invented release date.

1. MiniMax, *MiniMax-H3*, official system and repository documentation. [Repository](https://github.com/MiniMax-AI/MiniMax-H3).
2. MiniMax, *H3 base prompt-writing reference*, current official repository file. [Guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt).
3. MiniMax, *H3 Ref2VA prompt-writing reference*, current official repository file. [Guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt).
4. Comfy-Org, *Native MiniMax H3 nodes*, current implementation. [Source](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_minimax_h3.py).
5. Yingmao Miao et al., *Do Video Generators Track the World Across Segments? A Benchmark and Method for World-State Reasoning in Video Continuation*, 3 September 2026, arXiv preprint. [Paper](https://arxiv.org/abs/2609.03673), [code](https://github.com/AMAP-ML/StateAgent).
6. Ishani Mondal et al., *CANVAS: Continuity-Aware Narratives via Visual Agentic Storyboarding*, 15 April 2026, arXiv preprint. [Paper](https://arxiv.org/abs/2604.13452).
7. Rohith Peddi et al., *Towards Spatio-Temporal World Scene Graph Generation from Monocular Videos*, 13 March 2026. [Paper](https://arxiv.org/abs/2603.13185).
8. Ke Ma et al., *R4DSG: Relative 4D Scene Graph Memory for Object-Centric Question Answering in Long Egocentric Video*, 11 August 2026; ACM Multimedia 2026 listed in the paper record. [Paper](https://arxiv.org/abs/2608.11017).
9. David Dinkevich et al., *Story2Board: A Training-Free Approach for Expressive Storyboard Generation*, 13 August 2025. [Paper](https://arxiv.org/abs/2508.09983), [code](https://github.com/DavidDinkevich/Story2Board).
10. Yuwei Guo et al., *Long Context Tuning for Video Generation*, 13 March 2025. [Paper](https://arxiv.org/abs/2503.10589).
11. WeChatCV, *Stand-In: A Lightweight and Plug-and-Play Identity Control for Video Generation*, initial repository release 12 August 2025; CVPR 2026. [Repository](https://github.com/WeChatCV/Stand-In).
12. Comfy-Org, *MiniMax text encoder*, current implementation. [Source](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/text_encoders/minimax.py).
13. Feng Liu et al., *Timestep Embedding Tells: It's Time to Cache for Video Diffusion Model*, CVPR 2025 Highlight; original preprint 28 November 2024. [Official repository](https://github.com/ali-vilab/TeaCache).
14. Jiaqi Han et al., *Adaptive Spectral Feature Forecasting for Diffusion Sampling Acceleration*, 2 March 2026; CVPR 2026. [Paper](https://arxiv.org/abs/2603.01623).
15. xmarre, *ComfyUI Spectrum MiniMax H3*, community integration, README v0.2.23 inspected. [Repository](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3), [release notes](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3/blob/main/RELEASE_NOTES.md).
16. Jintao Zhang et al., *TurboDiffusion: Accelerating Video Diffusion Models by 100-200 Times*, 18 December 2025. [Paper](https://arxiv.org/abs/2512.16093), [code](https://github.com/thu-ml/TurboDiffusion).
17. Xiaoda Yang et al., *TurboT2VA: Fast Large-Scale Text-to-Video-Audio Generation via Score-Regularized Consistency Distillation*, 25 August 2026. [Paper](https://arxiv.org/abs/2608.24674), [code](https://github.com/thu-ml/TurboDiffusion/tree/main/turbot2va).
18. Dian Zheng et al., *VBench-2.0: Advancing Video Generation Benchmark Suite for Intrinsic Faithfulness*, 27 March 2025, revised 20 August 2025. [Paper](https://arxiv.org/abs/2503.21755), [code](https://github.com/Vchitect/VBench/tree/master/VBench-2.0).
19. Danze Chen et al., *H3-World: Turning Language Understanding into World Control*, 1 September 2026, arXiv preprint. [Paper](https://arxiv.org/abs/2609.01560), [code](https://github.com/Danzer1xxxxChan/H3-World).
