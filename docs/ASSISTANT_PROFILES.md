# Studio and Game assistant profiles

Each workspace saves its own installed model key and context length. Changing workspaces does not load a model. Prepare the assistant or request an AI action to load the selected profile. Changing a choice does not modify a turn already queued: its full profile remains attached to its saved request. An explicit retry without an accepted plan can use the newly selected profile.

The Windows launcher always enables the **Qwen 3.8 27B** policy for prompt writing, Studio, Game and video review. The policy is stored as `assistant_model_policy: "qwen3.8-27b"` in `data/settings.json`, including when a fresh installation first starts. Existing valid Qwen keys and both workspace contexts are retained. Older smaller-model profiles migrate to the valid saved Studio key, or `qwen3.8-27b@q4_k_s` when none exists. Model files and private projects are retained.

The assistant bar offers installed family variants and displays their exact keys. An unavailable saved key produces a recovery message; the app never picks another model or downloads one automatically. The browser cannot disable the lock, select a smaller model, or enable CPU residency. The resource coordinator also checks saved queued profiles before loading, and the LM Studio client checks the canonical model behind a loaded instance. An older queued request frozen to a smaller model is blocked; explicitly retry it to capture the current 27B profile.

## CPU or GPU

- **GPU / H3 handoff:** the assistant uses the GPU. The application releases its own assistant before video generation and prepares the saved assistant again when the next AI operation needs it. Other applications' model instances are not unloaded.
- **Local 27B placement:** GPU mode with automatic H3 handoff is required. The 27B assistant is not kept resident alongside H3. Other applications' model instances remain protected by the coordinator's ownership checks.
- Full Game planning, ending inspection and video frame review require a variant reported as capable of reading photos. A text-only variant is not silently used as a replacement.

Context changes are saved separately for each workspace. The application verifies the loaded context and reloads an owned instance when necessary. A large advertised model context does not mean that context will fit this machine's available memory.

## Connection and recovery

Double-click `Launch.cmd` after setup. If LM Studio's local server is offline and its CLI is installed, the launcher starts it bound to this computer. It never loads a model at startup. Use `Launch.ps1 -SkipLMStudio` to manage that server yourself. The default endpoint is `http://127.0.0.1:1234/v1`. Editing prompts and playing cached videos work without a connected assistant.

Connections edits remain drafts until Save succeeds; Cancel leaves the saved profile unchanged. Offline errors preserve selections. A definite load rejection allows a corrected retry. An uncertain load response requires inspection of the named instance before another load is submitted, preventing accidental duplicates.

Model status distinguishes a saved selection from an actually prepared instance. Generation and inspection can still fail even after loading succeeds. Keep the recorded error and use the explicit recovery action; do not treat a settings save or a valid JSON response as evidence of correct story behavior.

## Local data and validation

Profiles are stored in `data/settings.json` under `assistant_profiles.studio` and `.game`; legacy flat fields remain compatible with older clients. Story turns retain an `assistant_profile` snapshot. Keep a backup of the whole data folder before replacing runtime code.

For a direct Python server start in a fresh folder, set `H3_STUDIO_MODEL_POLICY=qwen3.8-27b` once; startup saves the lock. Library integrations without that environment setting or a saved policy retain the older configurable profiles for compatibility. Normal `Launch.cmd` and `Launch.ps1` runs always enforce the local policy.

Regression tests cover profile isolation, context reloads, legacy CPU placement, settings failure handling, queued-turn snapshots, family filtering, browser lock enforcement and refusal of smaller queued models or loaded aliases. Live inference and generated-media review must be reported separately from these automated tests.
