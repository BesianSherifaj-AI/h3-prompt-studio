# Local prompt writing and video review

After setup, double-click `Launch.cmd` and open <http://127.0.0.1:8766/studio>.
The launcher starts the editor and, when its installed CLI is available, LM Studio's
local server. Model files stay on this computer; nothing is downloaded automatically.

## Write a prompt

1. Open **Story & Dialogue** and enter your idea or open **Guided prompt writer**.
2. Describe the opening, one main action, ending, camera and things to keep consistent.
   Character moment, product showcase and prop handoff provide editable starting points.
3. Choose **Add direction to my idea**, then **Make my prompt**. You can also use
   **Build without AI** for a deterministic prompt.
4. Your direction fields and project save locally. Applying revised direction replaces
   its previously inserted block while keeping your other text and exact dialogue.
   **Undo last change** restores the preceding draft.

Writing checks identify missing endings, short scenes and crowded dialogue. They are
editing guidance, not a quality score for the generated video.

## Review a video

Open **Review videos** from either Studio view. Choose or drop MP4, WebM or MOV files,
up to **128 MB and 60 minutes** each. Imported review clips remain separate from H3
reference-length constraints. Existing local video assets also appear in the library.

Watch and listen to the whole clip. Set **Good · approved**, **Needs changes**, or
**Rejected**, mark the quality checklist and save your notes. Search and filter the
library to find unfinished reviews. Generated takes have the same review controls
under their player; combined films can be imported for whole-film review.

Open **Qwen 3.8 27B · review & repair prompt**, supply the original prompt and what to
check, then choose four, six or eight frame samples. **Analyze & improve prompt**
returns sampled frames, time-linked issues and an editable direction to use in the
writer. Click an examined frame or issue time to seek there. Copy, download or use the
repair prompt; using it opens the editor and does not start rendering.

AI recommendations never overwrite your verdict or checklist. The result reports
the exact model key and evidence limits. Changing the input prompt or review focus
marks the report as stale until you analyze again.

## Model and local data

The local launcher enforces Qwen **3.8 27B** for Studio, Game, review and other
assistant operations. This install uses `qwen3.8-27b@q4_k_s`. Saved smaller models,
CPU residency and old queued smaller-model requests cannot bypass the policy.
Each workspace can keep its own context length. GPU handoff lets Qwen and ComfyUI
take turns; a busy render prevents another assistant operation from taking the GPU.

Projects, imported files, notes and AI evidence live in the local `data` folder.
Reviews are under `data/reviews`; keep the entire data folder when backing up.

Frame review cannot verify continuous motion, speech, sound or lip sync. AI identity
checks compare the supplied text and sampled pictures, not the original reference
photos. Your complete playback review remains necessary before approval.

## Validation

The explicit live check `tests/run_local_review_smoke.py` exercises real local Qwen
prompt writing, four-frame review, exact model enforcement and evidence downloads;
it never queues a render. `frontend/tests/local-review-smoke.mjs` exercises a separate
QA project, video upload/playback, save/reload, frame seeking, repair direction and
desktop/390px/320px layout. Evidence is saved beneath `test-results/`.
