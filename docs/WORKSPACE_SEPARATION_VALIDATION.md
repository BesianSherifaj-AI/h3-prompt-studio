# Studio and Game workspace validation — 1.5.0

The release source was validated in the public checkout after integration. Model inference and GPU rendering were mocked in automated tests. The configured live ComfyUI service was unavailable during this update, so no new end-to-end H3 render or GPU performance claim is made. Earlier published demo videos remain evidence for their original versions.

## Results

- Backend: **1,742 tests passed**, with two existing dependency deprecation warnings from Starlette/httpx and anyio.
- Frontend: **426 tests passed across 35 files**.
- ComfyUI browser bridge: **40 tests passed** in the public source checkout.
- Production TypeScript/Vite build: passed. The output contains a separate Game JavaScript and CSS bundle, loaded when Game is first opened.
- Four built-application browser scenarios passed with no page errors: normal Game actions and lost-acknowledgement handling; legacy player-picker recovery; Studio scene continuity; and desktop/mobile workspace Scenes navigation.
- Source whitespace check: passed. Added changes were checked for local private-user paths and obvious key patterns, with no matches. Runtime data, environment files, model weights and local logs are excluded from publication.

The public checkout's count differs from the development folder because it retains the previously authored high-context regression coverage and excludes local-only live/demo scripts. These are the counts for the reviewed release source.

## Behavior covered

Backend tests cover separate Studio and Game catalogs, legacy story-linked project classification, independent last-project settings, workspace-aware import/export and generated turns, route reloads, and serialized bootstrap/settings writes. Imports deliberately detach Game session ownership when opened as a Studio project.

Frontend tests cover workspace route resolution and browser links, save status, accessible editor tabs, Game Scenes controls, saved-game selection, transcript formatting, history actions, visible targets and media-error recovery.

The workspace browser scenario checks the legacy Game link, Back/Forward and reload navigation, desktop/mobile Scenes controls, film navigation, transcript download, unavailable-video retry, readable mobile target labels and history playback. It performs **zero story mutations**. Film export controls are exercised against mocked endpoints; this is not a new rendered-film or encoding benchmark.

The Studio continuity browser scenario checks that opening scene controls makes no mutation, authored actor/prop direction survives save and reload, copied scenes do not replay prior direction, and the advanced editor remains reachable. It makes no model or video-generation request. Its static test server serves the new `/studio` and `/game` routes so reload behavior matches the application.

Input context validation still accepts integers from 1,024 to 262,144 tokens, with larger context choices in the GUI. Stage output limits stay independently bounded.

## Reproduce

From the repository root, after installing the locked dependencies:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm test --prefix frontend
node --test comfy_extension/tests/*.test.mjs
npm run build --prefix frontend
node tools/browser_game_smoke.mjs
node tools/browser_game_smoke.mjs --player-picker-recovery
node tools/browser_game_smoke.mjs --workspace-scenes
node tools/browser_scene_continuity_smoke.mjs
.\.venv\Scripts\python.exe tools/package_release.py --version 1.5.0 --output-dir release
```

The browser scripts use isolated synthetic projects, a local static server and intercepted API calls. Their screenshots are test fixtures, not generated-video evidence. Packaging uses the built frontend, produces a per-file SHA-256 manifest and an archive checksum sidecar, and refuses to overwrite an existing archive. Release verification extracts the archive and checks each manifest entry's size and hash against the packaged bytes.

## Remaining limits

Generated video can still drift in identity, object count, motion or continuity. Saved media whose original ComfyUI output is unavailable requires that service or its original file to be restored; the new player error and retry controls make this state visible. Opening Connections or retrying playback does not regenerate a missing video.

The automated checks do not establish fresh GPU generation, unavailable-service recovery after a real restart, or visual correctness of a new H3 output. Saved local user data and private media are outside the published application package.
