# Public release verification

Verified 8 September 2026. This file describes checks run against the standalone public-source copy. Hardware video demonstrations are documented separately with the published examples.

## Clean installation

Ran `Setup.ps1` in the standalone directory with no Python environment or Node dependencies present. It created its own Python 3.12 environment, installed all 34 pinned Python packages, installed the frontend dependency lockfile, and produced the Vite production build. No parent-workspace Python modules or workflow files were needed.

The test uncovered and fixed a launcher bug on computers with multiple Node.js paths: executable discovery now selects one application path rather than passing an array to PowerShell. Setup also checks that FFmpeg and ffprobe are available before proceeding.

## Automated checks

- Python backend: **632 passed**. Integration fixtures use isolated temporary data, mocked model/ComfyUI clients, and no live GPU calls. Where available, the focused ending-frame test uses FFmpeg on generated neutral color frames.
- Frontend unit tests: **222 passed** across 17 files.
- Optional ComfyUI bridge: **40 passed**, none skipped. Tests cover origin/window/session checks, ordered references, multiple LoRAs, exact source-result selection, transfer verification, and queue guard failure handling.
- PowerShell launcher checks passed: syntax, arguments with spaces, failure propagation, and server-response identity.
- Production frontend build passed. The generated ZIP passed the ZIP integrity check.

The Simple mode reference-count fix also passed its focused 28-test suite in both the running app and standalone copy, and both production builds passed. The header distinguishes eight conditioned video references from two inspiration images; the test confirms that inspiration images do not trigger the nine-reference limit. The full backend, frontend, and bridge counts above were rechecked after the final reference-count and public folder-location changes.

The test suite reported two upstream FastAPI/Starlette deprecation warnings concerning their test-client transport. They did not fail these checks; the lockfile records the tested versions.

## Portability changes

The public build starts with no selected prompt model; users choose an installed LM Studio model. Saving connection settings before choosing a model is supported, while attempting AI generation without a selection returns a clear error before loading hardware.

Standard ComfyUI port 8188 is included alongside Desktop 8000 and alternate 8010. Added checks verify that 8188 has the same restricted bridge access as the other ports and cannot read a transfer intended for another instance.

Native ComfyUI output-folder shortcuts use optional `H3_STUDIO_COMFY_OUTPUT`. Private workspace paths and references to earlier local test directories have been removed. FFmpeg/ffprobe resolve through `PATH`. Workflow fallback is self-contained; any deliberate local template override lives in this repository's `workflows/` directory.

The public source and release are assembled from an allowlist. Private `data`, logs, original live test scripts, raw benchmark reports, credentials, model files, runtime environments, and unrelated downloaded images are excluded. Public demonstration media is added separately after review.

## The Message public example

Three independently authored Ref2VA clips were actually rendered at 736 × 416, 24 fps, 243 frames, eight steps, with eight conditioned references per clip. Each clip is 10.125 seconds. Recorded ComfyUI execution times were 100.658, 90.061, and 88.228 seconds. The three MP4s passed full audio/video decoding; copied public files match the original verification hashes.

The public [demo](demo/README.md) includes explicit sampled-frame limitations and independent CPU speech-recognition results. All 37 intended words matched the recognized words after punctuation/case normalization; this does not verify lip sync, speaker identity, intonation, or sound effects.

The three project ZIPs were fetched using Studio's normal project-export endpoint. Their story, references, shots, dialogue, and render settings were checked against the actual tested snapshots. The public copies retain those immutable snapshots and exact API-exported image bytes, excluding later UI-only caches and timestamps. Each ZIP contains exactly 31 expected entries: one project JSON and ten image/thumbnail/metadata sets. All ZIP entries, JSON, image hashes, and image metadata were checked for unexpected files, path traversal, private paths, and credential-like values. The ten shared full-resolution PNGs are stored once in `demo/references`; ZIPs are separate release downloads.

Each final public ZIP was then imported through the standalone backend in an isolated temporary data directory with model/network/GPU operations disabled. All three imports succeeded, preserved ten images, reference roles and tags, exact dialogue and shots, and produced an identical compiled prompt. The imported image files matched their saved hashes. See [portable import verification](demo/verification/portable-import.json).

The public **Files & outputs → Published examples** location now resolves to `demo/`. Its focused nine-test folder-opening suite passed, including unknown paths, session requirements, missing directories, and the configured ComfyUI output locations.

## Reroll, story choices, and linked playback

The actual café reroll took **87.263 seconds** with an unchanged saved prompt hash, a different seed, and different decoded video pixels. Café Take 2 was then selected for Continue. Qwen3.8-27B used automatic GPU unload/load and produced three next-event choices from the actual ending image and saved story context. A subsequent live UI reload restored those three choices, the selected second idea, five-second duration, and eight-step quality; the GUI displayed its restored-draft status. This restoration was observed in the live UI and reviewed capture, not established by an automated test.

The selected key-reveal continuation actually rendered in **77.741 seconds** at 0.3 MP / eight steps. It has **124 frames**, including 39 copied context frames and 85 new frames. The correct **Café Take 2 + game continuation** join took **20.096 seconds**, removed exactly 39 repeated frames, and produced **328 frames / about 13.667 seconds**. It used native latent joining without diffusion and correctly resolves future Continue to the final individual game take. The first café take was not duplicated in the combined chain.

Both additional public clips passed full audio/video decoding. Their original embedded prompt metadata contained absolute local paths in MMH3 cache fields. The public copies were therefore remuxed with global/stream metadata removed using FFmpeg stream copy, without re-encoding. The complete decoded video and audio SHA-256 values match the untouched originals. Public container hashes and original hashes are recorded separately in [interactive-story verification](demo/verification/interactive-story.json).

Sampled-frame review found consistent identities, outfits, setting, and a visible key reveal. The generated ending transfers the key to Elira without a shown handoff and drops the note from view; this departs from the selected suggestion and remains visible in the combined film. A separate existing CPU Whisper small transcription of the game clip returned no text, consistent with its empty intended dialogue. That check does not guarantee absence of all speech or assess sound effects. No independent wording/voice assessment is claimed for the reroll or combined branch. These limitations are documented alongside the [published clips](demo/README.md).

## Scope of verification

This verifies the standalone app installation and automated behavior on Windows. It does not establish that every GPU, model quantization, custom-node revision, or third-party LoRA is compatible. The render recipe requires the exact runtime described in [ComfyUI setup](COMFY_BRIDGE_SETUP.md). First-run hardware installation on an unrelated computer and macOS/Linux GPU operation have not been tested.
