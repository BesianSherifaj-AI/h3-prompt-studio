# Video, Studio and Game

Choose the workspace for the work you want to make. The navigation stays in the same app, while saved work and production logic stay separate.

## Video: one clip

Open `/video` for a video up to 15 seconds.

1. **My videos** lists saved work. Search by name or idea; sort by name or recent edits.
2. **New video** asks for a name. An idea is optional. Creating a video saves a separate project; it keeps previous work.
3. **Write** is the starting editor tab. Photos and detailed settings are optional. Duration and format are visible beside the idea.
4. **Prepare prompt** asks the selected Qwen 3.8 27B assistant to develop your direction. The prepared prompt appears before the video on small screens. Editing direction makes the previous prompt stale.
5. **Generate video** renders a take. Compare or reroll takes, then save a manual verdict and notes. AI frame review is advice; it does not approve the take or verify sound.
6. **My videos** returns to the library after saving. Select **Open** or **Resume** to return to that exact project.

Advanced authoring and continuation tools remain available under **More tools**. Opening an advanced tool is deliberate. The simple video editor does not start Game or assemble a longer film.

## Studio: a complete film

Open `/studio` for a film from 1 to 10 minutes.

1. **New film** asks for a name, target length and optional story idea. A minute requires four 15-second clips; ten minutes requires forty.
2. Write the storyboard yourself or select **Plan storyboard with AI**. AI planning saves direction without starting ComfyUI.
3. Edit each clip's action, place, opening, ending, camera, sound and exact speech. Reorder complete clips. Shared visual style, continuity notes and up to nine reference images guide the whole film.
4. **Create render queue** freezes a saved copy of the storyboard. Review it before pressing **Start batch**. Work runs serially through the existing local render manager.
5. Review each take. A successful render means a playable file, not creative approval. Stop the queue, retry a failed clip, or explicitly resume remaining work as necessary.
6. **Export film** joins completed clips; **Export clips ZIP** keeps them separate. Trim In/Out points to remove weak moments. Original takes remain available.

Studio tells a connected story through scene cuts and shared direction. It does not guarantee identical identity or continuous physical motion across separate generations. H3 frame rounding and export trims determine actual delivered runtime.

Film direction uses revision checks. An older browser cannot silently overwrite a newer saved storyboard. Planning and rendering use the revision you saved. A restarted queue waits for an explicit resume.

## Game: interactive play

Open `/game` to create or resume an independent playable story. Choose a character, describe a move, speak, and see the next scene. Game owns its character/world state, settings, history and accepted scenes. Navigating from Video or Studio does not adopt that workspace's current draft.

## Saving and moving work

- **Saving…** means the latest edits are still being written.
- **All changes saved** means those edits reached local storage.
- **Not saved** means keep the app open, resolve the connection/error, and retry. The editor retains the draft.
- **Duplicate video** creates a separate editable project.
- **Download backup** creates a portable project ZIP with reference images. It is different from downloading the rendered video.
- **Import backup** opens a separate saved project.

Video, films and games share the local Qwen 3.8 27B requirement and explicit GPU handoff. They do not share active projects. Old standalone Studio clip projects appear in Video without moving or deleting their files. Existing Game sessions and legacy continuation records remain readable.

Only source code and reviewed release inputs belong on GitHub. Local projects, reference uploads, videos, settings, service logs, model files and machine-specific GPU launch metadata are excluded.
