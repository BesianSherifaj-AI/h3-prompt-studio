# Studio and Game assistant profiles

Each workspace saves its own installed model, context length, and execution mode. Changing workspaces does not load a model. Prepare the assistant or request an AI action to load the selected profile. Changing a choice does not modify a turn already queued: its full profile remains attached to its saved request. An explicit retry without an accepted plan can use the newly selected profile.

The local deployment uses Qwen 3.8 27B Q4_K_S with 8,192 context for Studio and Qwen3.5 4B Q4_K_M with 8,192 context on CPU for Game. These are configurable starting choices, not quality guarantees. No model downloads or cloud inference are required. Existing installations migrate their previous choices into both profiles without changing model files.

## CPU or GPU

- **GPU / H3 handoff:** the assistant uses the GPU. The application releases its own assistant before video generation and prepares the saved assistant again when the next AI operation needs it. Other applications' model instances are not unloaded.
- **CPU:** an installed vision model with a reported size of at most 8 GB runs with verified GPU offload disabled and its KV cache in system memory. It can remain loaded while H3 renders. Actual inference and resource handoffs still follow the application's job coordination. CPU replies may be slow; select another compatible model or GPU mode explicitly if needed.
- Full Game planning and ending inspection require a model reported as capable of reading photos. A text-only model is not silently used as a replacement. The old 0.8B / 4,096-context mode remains readable for compatibility.

Context changes are saved separately for each workspace. The application verifies the loaded context and reloads an owned instance when necessary. A large advertised model context does not mean that context will fit this machine's available memory.

## Connection and recovery

Start LM Studio's local server, then use Reconnect in the assistant bar. On this Windows installation it can also be started with `%USERPROFILE%\.lmstudio\bin\lms.exe server start`. The default endpoint is `http://127.0.0.1:1234/v1`.

Connections edits remain drafts until Save succeeds; Cancel leaves the saved profile unchanged. Offline errors preserve selections. A definite load rejection allows a corrected retry. An uncertain load response requires inspection of the named instance before another load is submitted, preventing accidental duplicates.

Model status distinguishes a saved selection from an actually prepared instance. Generation and inspection can still fail even after loading succeeds. Keep the recorded error and use the explicit recovery action; do not treat a settings save or a valid JSON response as evidence of correct story behavior.

## Local data and validation

Profiles are stored in `data/settings.json` under `assistant_profiles.studio` and `.game`; legacy flat fields remain compatible with older clients. Story turns retain an `assistant_profile` snapshot. Keep a backup of the whole data folder before replacing runtime code.

Regression tests cover profile isolation, context reloads, CPU placement, settings failure handling, and queued-turn snapshots. Live inference and generated-media review must be reported separately from these automated tests.
