# H3 Prompt Studio implementation contract

Local React/TypeScript UI built with Vite; Python FastAPI backend serves built UI on http://127.0.0.1:8766. Project JSON is the source of truth. The compiler is deterministic Python. LM Studio outputs validated proposals; applying a proposal preserves locked story, dialogue, and reference bindings. Video submission is a separate explicit user action, with a persistent run record and no automatic retry of an uncertain request.

Backend modules use relative package imports. Storage and HTTP coordination live in `backend/app.py`; compilation, LM Studio, GPU lifecycle, workflow preparation, continuation suggestions, joining, and persistent run management are separate modules. The optional ComfyUI bridge imports no model or GPU packages.

Project v1 shape (plain JSON dictionaries; unknown fields may be preserved but never executed):

```json
{
  "schema_version": 1,
  "id": "uuid", "title": "Untitled film",
  "mode": "ref2va", "duration": 5, "aspect_ratio": "16:9",
  "profile": "director", "authoring_mode": "assisted",
  "story": {"text": "User's exact story/instruction", "locked": true},
  "style": {"genre": "cinematic", "vibe": "", "lighting": "", "color": "", "notes": ""},
  "assets": [{"id": "uuid", "name": "Subject reference", "media_type": "image", "role": "reference_image", "semantic_role": "face", "enabled": true, "locked_order": false, "description": "user facts", "observation": "unapproved VLM observation", "approved_observation": "", "duration": null}],
  "subjects": [{"id": "uuid", "name": "Lead", "asset_ids": ["uuid"], "description": ""}],
  "shots": [{"id": "uuid", "duration": 5, "action": "", "setting": "", "camera": {"framing": "medium", "movement": "static", "height": "eye level", "speed": "slow", "focus": ""}, "performance": "", "final_state": "", "visible_subject_ids": [], "offscreen_subject_ids": [], "dialogue": [{"id": "uuid", "speaker_id": "uuid", "language": "English", "text": "Exact dialogue", "delivery": "", "locked": true}], "sound": "", "transition": "continuous"}],
  "soundscape": "", "music": "", "custom_instructions": ""
}
```

Modes: ref2va, fl2va(first+last), i2va(first), l2va(last), t2va(no conditioned references). Asset roles: reference_image/reference_video/reference_audio/first_frame/last_frame/context. Semantic roles: face, character, background, object, palette, style, wardrobe, pose, other. Array order of enabled conditioning assets is authoritative; IDs stay stable after reorder. Context/disabled library assets never get Picture tokens or H3 conditioning. Unlimited library images within sensible storage limits; Ref2VA enabled caps9images/3videos/3audio/12total. Backend image files are addressed by asset UUID, no arbitrary external paths.

Profile IDs: official (official syntax); director (official syntax plus physical/camera/continuity discipline, default); concise (same correct syntax, compact prose); custom (same valid syntax plus user directions). The attachment's original Strict Director formatter was not actually supplied: do not claim exact compliance with missing rules.

Compiler contract `compile_project(project: dict) -> dict`: returns `{prompt: str, issues: [{severity:'error'|'warning',code,path,message}], references:[{asset_id,token,role,semantic_role,name}], timeline:[{id,start,end}], valid:bool}`. Return no runnable prompt if mode/identity/timing validation errors. `validate_project(project)` may return same issue list. Never include unapproved observations in prompt. Exact dialogue bytes/language preserved; official tags follow verified guide. No arbitrary code/template execution.

LM client contract: `LMStudioClient(base_url='http://127.0.0.1:1234/v1', api_key='', timeout=180)` with sync methods `models() -> list`, `health() -> dict`, `complete_json(model, system, content, schema, max_tokens=1800, temperature=0.3) -> dict` (content accepts OpenAI string or multimodal array; extract/validate structured output, bounded retry, clear errors), `analyse_image(model, data_url, asset) -> dict` (objective observation, suggested_role, suggested_name, uncertainties; no audio hearing claims), `propose_plan(model, project, instructions='', persona='universal') -> dict`, `assist(model, project, shot_id, field, instructions='', persona='universal') -> dict`. Prompt module exports selectable `PERSONAS` list/dict + small task prompts. Keep calls compact for small VLM. `propose_plan` returns proposed SHOTS/style/soundscape/music and notes, never authoritative replacement of IDs/story/assets/dialogue. Assist returns `{field,value,reason}` for narrowly allowed field. Root applies validation and immutable locks. LM native load/unload helper interfaces coordinated after local API inspection.

Comfy bridge: explicit send/apply actions in selected H3 node context menu or toolbar. postMessage protocol agreed with root before implementation. Exact origin + source window + unpredictable session validation; no remote origins or implicit media transfer. Imported media is validated/re-encoded by backend. Preserve existing graph/unsaved work. No automatic generation. App has explicit Prepare H3 action to unload LM; Prepare AI checks Comfy is idle then frees H3 before loading LM. Root owns GPU coordination and app endpoints.

Tests: real official reference ordering, modes, inactive assets, missing references, timeline gaps/overruns, verbatim dialogue, proposal locks, malicious/untrusted model output, bridge wrong origin/session, and GPU busy refusal. Mock tests plus real LM image and plan smoke tests. Browser UI interaction tests and visual inspection before delivery.
