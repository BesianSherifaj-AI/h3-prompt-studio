# H3 Prompt Studio

A local workspace for turning reference photos and a plain-language idea into MiniMax H3 video. Connect LM Studio for vision-assisted writing and ComfyUI for rendering, then watch, compare, continue, and combine your clips without leaving the browser.

Try [The Message demo](demo/README.md): three actual 0.3 MP scenes, ten shared reference images, exact dialogue, measured render times, and portable projects. The visual review includes the model's observed mistakes as well as the successful parts.

[![Watch the H3 Prompt Studio showreel](demo/showreel-poster.jpg)](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/download/v1.0.0/H3-Prompt-Studio-showreel.mp4)

**[Watch the 88-second showreel](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/download/v1.0.0/H3-Prompt-Studio-showreel.mp4)** · **[Download the app](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/download/v1.0.0/H3-Prompt-Studio-v1.zip)** · **[Example projects and release notes](https://github.com/BesianSherifaj-AI/h3-prompt-studio/releases/tag/v1.0.0)**

The showreel combines clearly labeled captures of the working app with real generated videos played at normal speed. It follows reference assignment, shot and dialogue direction, a completed take, seed comparison, three AI story choices, and a combined continuation. See [the test record](VERIFICATION.md) for what was verified.

## What you can do

- Assign faces, clothes, props, places, palettes, and styles to named characters. Reorder or replace images while keeping their tags and assignments.
- Write exact dialogue by speaker. Give each scene its own duration, framing, camera movement, transition, and ending.
- Generate a fresh video or try another seed from an existing take's exact prompt and settings.
- Continue the story from a finished take. The vision assistant sees the real ending frame and saved story history, offers three editable choices, and passes the selected direction into the next clip.
- Combine a compatible continuation chain, watch it inline, and continue from its final clip.
- Use named takes, favorites, side-by-side comparison, saved setups, and portable project ZIPs with references.
- Choose the installed LM Studio model. Automatic GPU hand-off lets larger assistants and H3 take turns. An optional verified 0.8B assistant can stay in system memory alongside H3.
- Sketch a guide or plan simple movement, then add it as a reference or scene instruction.

No cloud account is required by this app. Reference photos, prompt drafts, and projects remain in the local app folder; the app sends them to the local LM Studio and ComfyUI services you configure.

## Install on Windows

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), [Node.js 22.12 or newer](https://nodejs.org/), and [FFmpeg with ffprobe](https://ffmpeg.org/download.html). Both FFmpeg executables must be on `PATH`.

Download or clone this repository into a writable folder, then run in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\Setup.ps1
powershell -ExecutionPolicy Bypass -File .\Launch.ps1
```

Setup creates a separate Python 3.12 environment and builds the frontend. Launch opens [localhost:8766](http://127.0.0.1:8766); it does not start a render or load a model. Keep the folder after setup: it also holds your private project data.

1. Start the local server in [LM Studio](https://lmstudio.ai/). In Studio's **Prompt assistant**, refresh and choose one of your installed models. Choose a model marked **Reads photos** for image analysis. No particular prompt model is required or downloaded automatically.
2. Open ComfyUI with the H3 runtime described in [ComfyUI setup](COMFY_BRIDGE_SETUP.md). In **Connection**, keep only the local ComfyUI addresses you intend to use. The defaults cover standard ComfyUI (`8188`), Desktop (`8000`), and an alternate instance (`8010`). The app does not install ComfyUI, H3 models, LoRAs, or attention kernels.
3. Add a few photos, assign their roles, write your idea, and start with **0.3 MP / 5 seconds**. Select **Build without AI** for a deterministic prompt using your own descriptions, or **Make my prompt** to have the selected assistant improve it.

The application also uses ordinary Python and Node tooling on other platforms, but the supplied launchers and GPU hand-off have been tested on Windows with an NVIDIA RTX 4090. A fresh macOS/Linux GPU installation has not been verified.

## Make your first story

1. Add photos and name each person. Use **Clothes → Worn by** and **Object → Starts with** to make ownership explicit.
2. Enter a short action. Use **Scenes & spoken words** to add cuts, camera choices, and exact speech.
3. Select **Generate video**. The assistant prepares the prompt if needed, then H3 renders. Review the result in **Video**.
4. Select **Try another seed** for another version, or **Continue video** for the next event. Review one of the three story suggestions or type your own.
5. After a continuation finishes, choose **Combine clips** to watch the linked story together. Save the video from the player.

The 0.3 MP preset is 736 × 416 in landscape. Quick drafts use a compatible four-step recipe; the quality preset uses eight steps. Those labels are practical comparison presets, not promises of fidelity. Actual timing depends on your hardware, prompt, reference count, model cache, and duration. See [workflow details](COMFY_FLOW.md) and [creative tools](CREATIVE_TOOLS.md).

## Storage and configuration

- Projects, reference images, drafts, and private run snapshots: `data/`.
- Project backups exported with images: `data/exports/`.
- Local playback copies: `data/video_runs/<run ID>/playback.mp4` once a result is watched.
- Original video: the connected ComfyUI's `output/h3_prompt_studio/runs/<run ID>/`.
- Continuation state: ComfyUI's `output/mmh3/h3_prompt_studio/runs/<run ID>/`.

The **Files & outputs** button lists local folders. To enable shortcuts to the original ComfyUI files, set `H3_STUDIO_COMFY_OUTPUT` to your installation's actual `output` directory before launching. Set `H3_STUDIO_DATA` if you want Studio's private data elsewhere. These values are local configuration and should not be committed.

```powershell
$env:H3_STUDIO_COMFY_OUTPUT = 'D:\AI\ComfyUI\output'
$env:H3_STUDIO_DATA = 'D:\AI\H3StudioData'
.\Launch.ps1
```

Use **Export with images** for a portable backup. Original ComfyUI videos and `.mmh3` continuation states are separate from a project export, so retain those too when you need to continue an old render.

## Runtime requirements and limits

The bundled render recipe requires native MiniMax H3 ComfyUI nodes, MMH3 Media for saved-state continuation, the tested SLA attention node, compatible model files, and FFmpeg. Studio validates the installed node schema and reports missing components before submission. This is a local H3 workflow editor, not an implementation of MiniMax's hosted Context-IR service.

Each generated segment is 4–15 authoring seconds at 24 fps on H3's `17k + 5` frame grid. A five-second target therefore becomes 124 frames (about 5.167 seconds). Continuation context is removed when combining a chain. The direct reference-image workflow supports up to nine conditioned images; audio/video can be kept as descriptive context, but direct audio/video reference conditioning is not implemented by this release. The prompt assistant does not listen to audio. Supply exact speech and sound instructions in the editor.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test comfy_extension/tests/*.test.mjs
cd frontend
npm test
npm run build
```

The included tests use neutral fixtures and mocked model/GPU calls. They do not render your projects. `data`, `logs`, models, credentials, user exports, and local benchmark records are excluded from version control. The separate `demo/` folder contains the explicitly selected public example. See [third-party notices](THIRD_PARTY_NOTICES.md) for external components and model licensing boundaries.

This is an independent community tool and is not affiliated with MiniMax, ComfyUI, or LM Studio.
