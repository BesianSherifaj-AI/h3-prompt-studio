# H3 Prompt Studio

A local workspace for turning reference photos and a plain-language idea into MiniMax H3 video. **Studio** gives you direct control of shots, references and dialogue. **Game** lets you play a character: describe what you do or say, let the local assistant respond, and watch that response as the next video scene.

Version **1.3.1** fixes text-only Game startup: ordinary actions no longer require an extra image generator by default, and unavailable ComfyUI servers are reported separately from missing model components. Scene continuity controls describe who acts, who stays seated, which objects exist, their appearance and count, and where each starts and ends. Game keeps accepted world state between turns; Studio lets you edit the same controls directly. A reported visual mismatch pauses automatic progression for review. LM Studio handles vision and writing; ComfyUI renders video.

**[Scene continuity guide](docs/SCENE_CONTINUITY.md)** · **[Research: H3 and 2025–2026 methods](docs/H3_SCENE_CONTROL_RESEARCH.md)** · **[Validation](docs/SCENE_CONTINUITY_VALIDATION.md)** · **[Changelog](CHANGELOG.md)**

The new [Coin Safety Inspector demonstration](demo/scene-continuity/README.md) exercises a seated bystander and a single prop across a continuous sequence. The demo record separates authored direction, generated video and visual review. The model can still make mistakes; prompt instructions are not a guarantee of physical consistency.

[![The Coin Safety Inspector — actual H3 frame](demo/scene-continuity/coin-poster.png)](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/download/v1.3.0/continuity-30s.mp4)

**[Watch the 30-second demo](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/download/v1.3.0/continuity-30s.mp4)** · **[Chase, one-minute parody and Studio images](demo/scene-continuity/README.md)** · **[Download v1.3.1](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/tag/v1.3.1)**

The earlier **v1.0** [The Message demo](demo/README.md) remains available: three actual 0.3 MP scenes, ten shared reference images, exact dialogue, measured render times, and portable projects. Its showreel and measurements describe that release. The visual review includes observed model mistakes.

[![Watch the H3 Prompt Studio showreel](demo/showreel-poster.jpg)](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/download/v1.0.0/H3-Prompt-Studio-showreel.mp4)

**[Watch the v1.0 showreel](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/download/v1.0.0/H3-Prompt-Studio-showreel.mp4)** · **[App releases](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases)** · **[Published example projects](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/tag/v1.0.0)**

The showreel combines clearly labeled captures of the working app with real generated videos played at normal speed. It follows reference assignment, shot and dialogue direction, a completed take, seed comparison, three AI story choices, and a combined continuation. See [the test record](VERIFICATION.md) for what was verified.

## What you can do

- Assign faces, clothes, props, places, palettes, and styles to named characters. Reorder or replace images while keeping their tags and assignments.
- Write exact dialogue by speaker. Give each scene its own duration, framing, camera movement, transition, and ending.
- Direct each visible character's starting pose, action or hold, and ending. Track important props by stable identity, quantity, appearance and placement. Opening the continuity editor and inventory requires no model call.
- Keep the Studio editor beside the video, with **Photos**, **Story & Dialogue**, and **Settings** tabs.
- Generate a fresh video or **Try another take** from an existing take's exact prompt and settings.
- **Continue from this ending** uses the active story endpoint, its saved motion, actual final frame and completed history. Browsing an older take does not move that endpoint; choose **Branch from this preview** to start another path.
- In Game, write a move or choose one of three suggested player actions. The assistant writes the other characters' actions and speaker-bound dialogue. Responses render automatically by default; optional review lets you edit them first.
- Generate needed character, outfit, prop or location references with an installed Z-Image-Turbo model. Existing identities and reference tags are reused; new visual elements use an explicit scene cut.
- Switch between the latest scene and the whole accepted story. Sequential playback needs no ComfyUI join job. Save the active branch as one film; compatible legacy continuation chains can still use **Combine clips**.
- Use named takes, favorites, side-by-side comparison, saved setups, and portable project ZIPs with references.
- Choose the installed LM Studio model. Automatic GPU hand-off lets larger assistants and H3 take turns. An optional verified 0.8B assistant can stay in system memory alongside H3.
- Sketch a guide or plan simple movement, then add it as a reference or scene instruction.

No cloud account is required by this app. Reference photos, prompt drafts, stories and projects remain in the configured local data folder; the app sends them to the local LM Studio and ComfyUI services you configure.

## Install on Windows

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), [Node.js 22.12 or newer](https://nodejs.org/), and [FFmpeg with ffprobe](https://ffmpeg.org/download.html). Both FFmpeg executables must be on `PATH`.

Download or clone this repository into a writable folder, then run in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\Setup.ps1
powershell -ExecutionPolicy Bypass -File .\Launch.ps1
```

Setup creates a separate Python 3.12 environment and builds the frontend. Launch opens [localhost:8766](http://127.0.0.1:8766); it does not start a render or load a model. Keep the folder after setup: it also holds your private project data.

1. Start the local server in [LM Studio](https://lmstudio.ai/). In **Settings → Prompt assistant**, refresh and choose an installed model. Choose a model marked **Reads photos** for image analysis. No particular prompt model is required or downloaded automatically.
2. Open ComfyUI with the H3 runtime described in [ComfyUI setup](COMFY_BRIDGE_SETUP.md). In **Connection**, keep only the local ComfyUI addresses you intend to use. The defaults cover standard ComfyUI (`8188`), Desktop (`8000`), and an alternate instance (`8010`). The app does not install ComfyUI, H3 models, LoRAs, or attention kernels.
3. Add a few photos, assign their roles, write your idea, and start with **0.3 MP / 5 seconds**. Select **Build without AI** for a deterministic prompt using your own descriptions, or **Make my prompt** to have the selected assistant improve it.

The application also uses ordinary Python and Node tooling on other platforms, but the supplied launchers and GPU hand-off have been tested on Windows with an NVIDIA RTX 4090. A fresh macOS/Linux GPU installation has not been verified.

## Use Studio

1. Add photos and name each person. Use **Clothes → Worn by** and **Object → Starts with** to make ownership explicit.
2. Enter a short action. Use **Scenes & spoken words** to add cuts, camera choices, and exact speech.
3. Select **Generate video**. The assistant prepares the prompt if needed, then H3 renders. Review the result in **Video**.
4. Select **Try another take** for another version, or **Continue from this ending** for the next event. Choose an unchanged suggestion to render it, or write your own direction for the assistant to develop into actions and dialogue.
5. Use **Whole story** to play the accepted scenes in order. Preview older takes freely; **Branch from this preview** deliberately changes the story path. The separate-scene planner remains an Advanced authoring tool.

The 0.3 MP preset is 736 × 416 in landscape. Quick drafts use a compatible four-step recipe; the quality preset uses eight steps. Those labels are practical comparison presets, not promises of fidelity. Actual timing depends on your hardware, prompt, reference count, model cache, and duration. See [workflow details](COMFY_FLOW.md) and [creative tools](CREATIVE_TOOLS.md).

## Play a story in Game

1. Open **Game**, enter a premise and choose the character you play. Start from a Studio ending with **Play from here**, or begin a new story. Reference photos are optional: a text-only premise can establish a location, nearby objects and new characters directly. **Create extra reference images** is off by default; enable it when you want the assistant to request separate reference images. That option requires an available image generator and adds a generation step. Existing references remain usable with it off.
2. New games default to **2D pixel art / 0.2 MP / three seconds / eight steps**. Landscape is608×320; a fresh clip has73frames (3.04 seconds). Change style, quality, duration and viewpoint freely in the editor. Existing games retain their saved settings. Turn on **Review before rendering** to inspect the response before rendering.
3. Write a move such as `I look at the note and ask what it means.` Put exact spoken words in double quotes, for example `I say "Who sent this?"`. A response that changes quoted player speech is rejected before rendering.
4. The game resolves known item and movement actions, while the assistant directs their presentation. Dialogue, combat and new situations use the creative planner. The scene renders, its ending is inspected, and accepted actions update the saved world. You can edit the response, try another take, branch from an older clip, or type your own next action.
5. Watch **Latest scene** or **Whole story**, and save the active branch as a film. A reroll replaces the current take within that turn; it does not append the same event twice.

The vision assistant distinguishes intended actions from what it sees in the final frame and records uncertainties. It cannot verify speech or lip sync from an image. Object ownership, handoffs and character consistency can still drift; review the video before building a long story on a mistaken result.

Open **Inventory** or type `open inventory` to see held and worn items immediately. Pick up, drop, give, open, close, inspect and move controls use the saved location and ownership rules. A locked door stays locked, unavailable characters cannot respond, and inspecting an item does not silently pick it up. **Quick item and movement actions** is enabled by default; turn it off when you want the creative planner to include reactions to every interaction. Authored rules, guides and unusual conditions already defer to creative resolution so shortcuts cannot bypass them. Waiting, conversation and combat remain creative turns.

The assistant uses bounded corrections for eligible malformed responses, retains exact request receipts after a lost connection, and validates edited plans before rendering. Failed or cancelled turns do not advance accepted inventory or history. See the [local assistant comparison](docs/ASSISTANT_EVALUATION.md) for the tested model choice, measured latency and evaluation limits.

Use **Play** to act or speak; use **Guide** to change the next response or persistent story behavior. The side editor remains available during play: add/remove references, choose who wears/holds each item, edit personalities, choose aspect ratio and ordered LoRAs. Edits during rendering apply next turn. **What ran** exposes the compiled prompt, actual reference connections, seed and ComfyUI receipt.

**Supervised test assistant** pauses for an external tester to complete the saved role request. It is a diagnostic provider, not an autonomous assistant. Choose **LM Studio** for normal play. Small models can produce schema-valid but semantically invalid responses; a validation error must be fixed before video is queued.

Optional microphone input records first, transcribes into an editable message and never automatically sends a move. Original recordings can separately be selected as H3 audio references. Soundtrack mixing creates a separate preview. See [audio setup](AUDIO_SETUP.md).

**UPSCALE** buttons in Studio and Game open an existing installation's GUI, optionally adding the selected scene. It does not start processing. Install that app separately; set `H3_STUDIO_UPSCALE` to its folder if it is not in `Desktop/UPSCALE` or `OneDrive/Desktop/UPSCALE`. It supports video upscaling/interpolation, not still-image files.

The player shows live operation progress through a local event relay when ComfyUI sends it. The current H3 runtime supplies the playable clip after decoding; intermediate image previews are not shown.

## Optional image generation

**H3 still frame (experimental)** uses the installed FL2VA model, compatible four-step LoRA and five-frame generation, then saves the middle frame. This is a video-model workaround, not a dedicated image model or identity-preserving editor. A local 608 × 320 pixel courtyard completed in 50.1 seconds including loading. Keep **Z-Image-Turbo** selected when preferred; no fair head-to-head speed claim is made.

The configured baseline is **`z_image_turbo_bf16.safetensors`**. **`z_image_turbo_fp8_e4m3fn.safetensors`** is an optional alternative when installed and reported compatible; its availability is not a speed or quality guarantee. Initial BF16 and FP8 checks used different cache conditions, so they do not establish a fair speed comparison or justify changing the default. Both use ComfyUI's native image workflow with:

```text
models/diffusion_models/z_image_turbo_bf16.safetensors
models/text_encoders/qwen_3_4b.safetensors
models/vae/ae.safetensors
```

The optional FP8 file belongs in `models/diffusion_models/` too. The generator checks that the model, encoder, VAE and required nodes exist together on a configured ComfyUI instance. New reference images default to 512 × 512, eight steps and CFG 1. Missing requirements appear in Game's **New scene images** settings. The app does not download these model files.

The assistant, image model and H3 use the existing automatic GPU hand-off. Larger assistants are unloaded before ComfyUI rendering, and ComfyUI releases its models when the assistant needs the GPU. A selected small resident assistant is a separate memory option. Opening settings or reading saved story state does not start generation.

## Continue, recover and measure duration

**New Studio continuations and Game scene length mean new action.** A saved-motion continuation adds its context before rounding to H3's frame grid. With the standard 39-frame context, a five-second new-action request generates 175 frames: about 7.292 seconds total, including 1.625 seconds of context and 5.667 seconds of new footage. The UI offers 4, 5, 7, 10 or 13 seconds of new action; source dimensions stay fixed during a continuous extension.

Legacy clips keep their original total-duration budget. A five-second legacy continuation is 124 frames: about 5.167 seconds total and 3.542 seconds new after its 39-frame context. Scene playback removes only the recorded context; original outputs remain available in clip details. Fresh scenes without a motion source have no context to remove. Rounding can add up to one frame-grid interval to the requested duration.

Turns and request IDs are saved before work starts. If a connection is interrupted, use the displayed **Resume** or **Retry** control to recover that same request. An uncertain submission is checked rather than blindly sent again. Reloading does not start a fresh turn. Failure or cancellation leaves the last completed story ending active; once the app confirms failure, an explicit retry can create a new render attempt.

## Storage and configuration

- Projects, reference images, drafts, and private run snapshots: `data/`.
- Stories, branches and turn records: `data/stories/`; generated-image jobs: `data/asset_runs/`.
- Cached exports of the active story branch: `data/story_films/`.
- Project backups exported with images: `data/exports/`.
- Local playback copies: `data/video_runs/<run ID>/playback.mp4` once a result is watched.
- Original video: the connected ComfyUI's `output/h3_prompt_studio/runs/<run ID>/`.
- Continuation state: ComfyUI's `output/mmh3/h3_prompt_studio/runs/<run ID>/`.

The **Outputs** button lists local folders. To enable shortcuts to the original ComfyUI files, set `H3_STUDIO_COMFY_OUTPUT` to your installation's actual `output` directory before launching. Set `H3_STUDIO_DATA` if you want Studio's private data elsewhere. These values are local configuration and should not be committed.

```powershell
$env:H3_STUDIO_COMFY_OUTPUT = 'D:\AI\ComfyUI\output'
$env:H3_STUDIO_DATA = 'D:\AI\H3StudioData'
.\Launch.ps1
```

Use **Export with images** for a portable project backup. Back up the complete data directory to retain story branches and turn records. Original ComfyUI videos and `.mmh3` continuation states are separate from a project export, so retain those too when you need to continue an old render.

Before upgrading, stop the existing Studio server and back up those files. Keep your data directory when replacing the application files, then launch the new version. The launcher reuses a compatible server already running on port 8766, so leaving an older server open can show the previous interface.

## Runtime requirements and limits

The bundled render recipe requires native MiniMax H3 ComfyUI nodes, MMH3 Media for saved-state continuation, the tested SLA attention node, compatible model files, and FFmpeg. Studio validates the installed node schema and reports missing components before submission. This is a local H3 workflow editor, not an implementation of MiniMax's hosted Context-IR service.

H3 uses 24 fps on its `17k + 5` frame grid, with a maximum 362 generated frames for this recipe. Initial Studio clips and legacy projects use their original total-duration budget; new continuations and Game turns leave space for motion context. The direct reference-image workflow supports up to nine conditioned images. Other photos can remain inspiration; direct audio/video reference conditioning is not implemented. The prompt assistant does not listen to audio. Supply exact speech and sound instructions in the editor.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test comfy_extension/tests/*.test.mjs
cd frontend
npm test
npm run build
```

The included tests use neutral fixtures and mocked model/GPU calls. They do not render your projects. `data`, `logs`, models, credentials, user exports, and local benchmark records are excluded from version control. The separate `demo/` folder contains the explicitly selected public example. See [third-party notices](THIRD_PARTY_NOTICES.md) for external components and model licensing boundaries.

After testing and building a reviewed release checkout, create its portable archive from the repository root:

```powershell
.\.venv\Scripts\python.exe tools/package_release.py --version 1.3.1 --output-dir release
```

The package includes the prebuilt `dist/` frontend, source, launchers, dependency locks, notices and selected public demo. It excludes runtime data, environments, logs, credentials and model files. The script writes a file-hash manifest and an archive SHA-256 sidecar, uses fixed ZIP metadata, and refuses to overwrite an existing release. Rebuilding or packaging never uploads anything.

This is an independent community tool and is not affiliated with MiniMax, ComfyUI, or LM Studio.

See [v1.1 live validation and visual limitations](VERIFICATION_V1.1.md) for the measured Studio/Game, image-generation, playback and recovery checks.
