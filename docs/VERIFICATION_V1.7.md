# Version 1.7.0 verification

Validated on Windows on 6 October 2026. The local runtime and reviewed GitHub checkout use the same application source. Private data, service metadata, workflows and GPU launcher helpers remain outside the public release.

## Automated checks

- 1,979 backend tests passed, including direct-start Qwen policy, persistence, workspace separation, film planning, revision conflicts, request recovery, production and exports.
- 505 frontend tests passed across 45 files; the TypeScript and Vite production build passed.
- 44 ComfyUI bridge tests passed.
- Windows launcher syntax, native exit handling, version/source fingerprint and local LM configuration checks passed.
- The release packager includes the Windows double-click launcher and excludes private runtime folders and model files.

## Actual local workflow

- Started version 1.7.0 through Launch.ps1 on loopback port 8766. Verified its workspace and backend source fingerprint.
- Reopened the existing single-video project with saved direction, prepared prompt and playable takes. Only Video was visible; Studio and Game retained independent state.
- Created and reopened a one-minute film, The Paper Boat, with four storyboard clips.
- Qwen 3.8 27B, exact installed key `qwen3.8-27b@q4_k_s`, planned all four clips. Creating the render queue left it paused; an explicit Start batch ran the four clips serially.
- All four H3 clips completed. Original clips contain 362 video frames at 24 fps, approximately 15.083 seconds each.
- Export film delivered 1,440 video frames: exactly 60.000 seconds of video, H.264 at 736 × 416, with AAC audio. AAC padding gives the MP4 container a 60.021-second duration. FFmpeg decoded the complete export without errors.
- Saved a manual review and notes. A real four-frame Qwen 27B analysis completed in 27.032 seconds, saved timestamped evidence and a repair prompt, and preserved the manual verdict.
- The generated film remains draft footage. Sampled review identified placement and continuity concerns; reference-free scene generations also changed courtyard geometry. Export success does not establish creative approval, sound quality or continuity between every cut.
- Video, Studio and Game fit 390-pixel and 320-pixel viewport tests without horizontal overflow. Opening Game showed its own blank setup and did not adopt the current film or single-video cast.
- Verified that attempting to select a smaller model returned HTTP 400 and did not alter saved settings. Fresh direct backend starts and legacy settings also default to the 27B lock; the GUI cannot disable it.

## Reproducible browser checks

GitHub CI runs the built application with isolated data and mocked external services. Browser checks cover the three workspaces, Game recovery/scenes, film create/save/reopen/duplicate/storyboard import, frozen queue history and lost-response recovery, scene continuity controls, assistant settings, Advanced Video production and keyframe controls.

These browser checks verify controls and persistence contracts. They do not generate footage or load a model. The real model/render evidence above comes from the Windows runtime.

## Remaining practical limits

A ten-minute plan contains forty clips. Planning/count boundaries and recovery are covered automatically; the actual GPU smoke rendered one minute. Review each take and transition before scaling a film. Phone tests emulate viewport sizes; physical Android and iPhone hardware were not used. LM Studio, the selected installed 27B model, ComfyUI/H3 and FFmpeg are required for local production.
