# Create, vary, continue, combine

## Studio

Add references and assign roles. Name people first, then attach wardrobe and objects to the right person. An object's **Starts with** assignment describes the opening setup; a later action may transfer it. Use scene cards to define camera changes and exact dialogue. A continuous shot may have several timed beats with **Keep filming · no cut**.

**Make my prompt** gives the selected LM Studio assistant the reference descriptions, allowed edits, and protected dialogue. The app validates the response and compiles H3 reference tags in actual image order. **Build without AI** compiles your existing text immediately. AI changes can be undone; named templates and saved prompt versions let you compare approaches.

**Generate video** compiles when needed and creates a new, immutable run snapshot. The player shows the selected take's seed, resolution, steps, and elapsed render time. **Try another seed** starts from that snapshot, so reference bindings, LoRA order, and prompt remain consistent. A reroll is an alternative take rather than an automatic continuation.

Multiple enabled LoRAs are applied in the selected order with individual strengths. The app verifies installed filenames and distinguishes its tested adapters from unverified additions. A globally available LoRA is not necessarily H3-compatible.

## Interactive story

Select a completed take and click **Continue video**. The app extracts its actual last frame and gives the prompt assistant that image plus saved story history, named characters, and previous exact dialogue. The three proposed events are editable. Pick one, type a custom idea, or request another set. Only Generate submits the next clip.

The new project preserves the selected take's references and render settings. Its saved MMH3 state supplies the last 39 frames as overlap for motion continuation. The original project and take remain available. Longer assistants use automatic GPU unloading; the optional supported 0.8B vision assistant uses system memory and is better suited to short edits than complex continuity.

The **Quick draft** option applies 0.3 MP and four steps to the new continuation. It selects the known four-step Ref2VA Turbo adapter only when replacing the recognized default adapter; it preserves custom LoRA stacks. **Quality** uses the corresponding eight-step preset. Inspect the result before using a draft as the basis of a long film.

## Combined playback

**Combine clips** follows the selected take's verified continuation chain. It removes repeated context and uses native latent joining when the video/audio boundaries align. When audio-grid rounding makes that unsafe, it joins the already-decoded clips with exact video-frame trimming. Source clips and state archives remain unchanged. Joining does not run diffusion again.

The combined film appears as another take. **Continue video** on that film selects the final individual clip's saved motion state and the full accumulated story. Side-by-side comparison, favorites, take names, downloads, and saved choices are available in the same workspace.

## Recovering an interrupted run

Studio records the project, graph, and exact request ID before submission. **Check & unlock** checks that request and recovers a finished result when available. It does not automatically resubmit an uncertain render. Keep ComfyUI's original output and `.mmh3` state files if you want to reopen or extend old results.

## Timing

H3 uses 24 fps and a `17k + 5` frame grid. A five-second authoring target is 124 native frames, approximately 5.167 seconds. A four-second target is 107 native frames. Continuation overlaps are context, so the combined film's duration is the sum of the first clip and each subsequent clip's new frames, not the sum of the untrimmed source durations.

The app preserves exact dialogue text in its prompt, but the generated audio may mispronounce, omit, or retime words. A final frame cannot reveal everything that happened or was heard earlier. Review the generated film and story choices; suggested actions and technical settings are not guarantees of visual fidelity.
