"""Deterministic H3 prompt compilation. No IO, model calls or template execution."""
from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation
from typing import Any

MODES = {"ref2va", "fl2va", "i2va", "l2va", "t2va"}
PROFILES = {"official", "director", "concise", "custom"}
REFERENCE_ROLES = {"reference_image": "image", "reference_video": "video", "reference_audio": "audio"}
SEMANTICS = {"face", "character", "background", "object", "palette", "style", "wardrobe", "pose", "other"}
LANGUAGES = {"Arabic", "Chinese", "English", "French", "German", "Italian", "Japanese", "Korean", "Portuguese", "Russian", "Spanish"}
FIELDS = ("subject_definitions", "summary", "retention_analysis", "detailed_description", "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
FORGED_HEADER = re.compile(r"(?:^|\n)\s*(?:" + "|".join(FIELDS) + r")\s*:", re.I)
RESERVED_TAG = re.compile(r"</?d>|<\|?cutoff\|?>|<scenetrans>|<Speaker\s+\d+>", re.I)
EXPLICIT_BINDING = re.compile(r"<(Picture|Video|Audio|Subject)\s+(\d+)>")
PROMPT_TAG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
# A standalone @photo-tag is an authoring alias. Do not consume email addresses,
# URL path components, escaped literal handles, or half of an invalid alias.
TAG_MENTION = re.compile(r"(?<![\w@/.:+\\-])@([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)(?![\w@-])")


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sentence(value: str) -> str:
    value = value.strip()
    return value if not value or value[-1] in '.!?"' else value + "."


def _stamp(value: Decimal) -> str:
    milliseconds = int((value * 1000).quantize(Decimal(1)))
    minutes, rest = divmod(milliseconds, 60000)
    seconds, ms = divmod(rest, 1000)
    return f"{minutes:02d}:{seconds:02d}.{ms:03d}"


def compile_project(project: dict) -> dict:
    """Compile Project v1 without mutating it; invalid projects have no prompt."""
    issues: list[dict] = []
    references: list[dict] = []
    reference_tags: list[dict] = []
    timeline: list[dict] = []

    def issue(severity, code, path, message):
        issues.append({"severity": severity, "code": code, "path": path, "message": message})

    def result(prompt=""):
        valid = not any(i["severity"] == "error" for i in issues)
        return {"prompt": prompt if valid else "", "issues": issues, "references": references, "reference_tags": reference_tags, "timeline": timeline, "valid": valid}

    def string(value, path, required=False, dialogue=False):
        if not isinstance(value, str):
            issue("error", "invalid_text", path, "Expected text.")
            return ""
        if required and not value.strip():
            issue("error", "required_text", path, "This text cannot be empty.")
        if RESERVED_TAG.search(value) or (not dialogue and (FORGED_HEADER.search(value) or re.search(r"\[Shot\s+\d+\]", value))):
            issue("error", "reserved_prompt_syntax", path, "Use structured shot and dialogue fields instead of inserting compiler tags or section headers.")
        return value

    def collection(value, path):
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            issue("error", "invalid_collection", path, "Expected a list of objects.")
            return []
        return value

    def ids(items, path):
        found = {}
        for i, obj in enumerate(items):
            key = obj.get("id")
            if not isinstance(key, str) or not key.strip():
                issue("error", "missing_id", f"{path}[{i}].id", "Every item needs a stable non-empty ID.")
            elif key in found:
                issue("error", "duplicate_id", f"{path}[{i}].id", "IDs must be unique within their collection.")
            else:
                found[key] = obj
        return found

    if not isinstance(project, dict):
        issue("error", "invalid_project", "", "Expected a Project JSON object.")
        return result()
    # Resolve aliases in a private rendering copy; saved drafts retain their
    # readable tags and therefore continue to work after reference reordering.
    project = copy.deepcopy(project)
    if type(project.get("schema_version", 1)) is not int or project.get("schema_version", 1) != 1:
        issue("error", "schema_version", "schema_version", "Only Project schema version 1 is supported.")
    mode = project.get("mode")
    profile = project.get("profile", "director")
    if not isinstance(mode, str) or mode not in MODES:
        issue("error", "invalid_mode", "mode", "Choose ref2va, fl2va, i2va, l2va or t2va.")
        mode = ""
    if not isinstance(profile, str) or profile not in PROFILES:
        issue("error", "invalid_profile", "profile", "Choose official, director, concise or custom.")
        profile = ""
    duration = _number(project.get("duration"))
    if duration is None or duration != duration.to_integral_value() or not 4 <= duration <= 15:
        issue("error", "invalid_duration", "duration", "H3 authoring duration must be an integer from 4 through 15 seconds.")
        duration = Decimal(5)
    story = project.get("story", {})
    style = project.get("style", {})
    if not isinstance(story, dict) or not isinstance(style, dict):
        issue("error", "invalid_object", "story/style", "Story and style must be objects.")
        return result()
    story_text = string(story.get("text", ""), "story.text")
    for key in ("genre", "vibe", "lighting", "color", "notes"):
        string(style.get(key, ""), f"style.{key}")
    soundscape = string(project.get("soundscape", ""), "soundscape")
    music = string(project.get("music", ""), "music")
    custom = string(project.get("custom_instructions", ""), "custom_instructions")
    assets = collection(project.get("assets", []), "assets")
    subjects = collection(project.get("subjects", []), "subjects")
    shots = collection(project.get("shots", []), "shots")
    asset_map, subject_map = ids(assets, "assets"), ids(subjects, "subjects")
    ids(shots, "shots")
    if any(i["severity"] == "error" for i in issues):
        return result()
    if not shots:
        issue("error", "missing_shots", "shots", "Add at least one shot.")

    active = []
    for i, asset in enumerate(assets):
        path = f"assets[{i}]"
        for key in ("name", "description", "approved_observation"):
            string(asset.get(key, ""), path + "." + key)
        if "enabled" in asset and not isinstance(asset["enabled"], bool):
            issue("error", "invalid_enabled", path + ".enabled", "Enabled must be true or false.")
        if "audio_enabled" in asset and not isinstance(asset["audio_enabled"], bool):
            issue("error", "invalid_audio_enabled", path + ".audio_enabled", "Audio enabled must be true or false.")
        if not asset.get("enabled", True) or asset.get("role") == "context":
            continue
        role, media = asset.get("role"), asset.get("media_type")
        if not isinstance(role, str) or role not in {*REFERENCE_ROLES, "first_frame", "last_frame"}:
            issue("error", "invalid_asset_role", path + ".role", "Choose a conditioning role or context.")
            continue
        if media != REFERENCE_ROLES.get(role, "image"):
            issue("error", "media_role_mismatch", path, "The asset media type does not match its conditioning role.")
        if not isinstance(asset.get("semantic_role", "other"), str) or asset.get("semantic_role", "other") not in SEMANTICS:
            issue("error", "invalid_semantic_role", path + ".semantic_role", "Unknown semantic reference role.")
        owner = asset.get("simple_owner_id")
        if owner is not None and owner != "":
            if asset.get("semantic_role") != "object":
                issue("error", "object_owner_role", path + ".simple_owner_id", "Starts with is for objects. Clear this assignment, or change this photo's type to Object.")
            elif not isinstance(owner, str) or owner not in subject_map:
                issue("error", "unknown_object_owner", path + ".simple_owner_id", "Choose an existing character under Starts with, or clear the assignment. The previously selected character is no longer available.")
        if mode == "ref2va" and role not in REFERENCE_ROLES or mode != "ref2va" and role in REFERENCE_ROLES:
            if mode == "ref2va":
                message = "This project uses reference photos. Change this photo to a reference, or choose First frame only or First + last frame."
            else:
                mode_name = {"i2va": "First frame only", "fl2va": "First + last frame", "l2va": "Last frame only", "t2va": "Text only"}.get(mode, "This mode")
                message = f"{mode_name} does not use extra reference photos as video anchors. Change this photo to Context only so it can help the prompt, or choose Reference photos."
            issue("error", "mode_role_mismatch", path + ".role", message)
        active.append(asset)
        if media in ("video", "audio"):
            clip_duration = _number(asset.get("duration"))
            if clip_duration is None and asset.get("duration") is not None:
                issue("error", "invalid_media_duration", path + ".duration", "Clip duration must be a finite number or null when unknown.")
            elif clip_duration is None:
                issue("warning", "unknown_media_duration", path + ".duration", "Enter the clip duration to verify the documented 2–15 second media limits.")
            elif not 2 <= clip_duration <= 15:
                issue("error", "media_duration_limit", path + ".duration", "Reference video/audio clips must be 2–15 seconds under the official input profile.")
            elif media == "video" and clip_duration > duration:
                issue("warning", "comfy_reference_crop", path + ".duration", "The installed Comfy node crops reference videos longer than the generated clip.")
        if media == "image" and not _text(asset.get("description")) and not _text(asset.get("approved_observation")):
            issue("warning", "undescribed_reference", path, "Add a description or approve an observation; appearance details will not be invented.")

    counts = {role: sum(a.get("role") == role for a in active) for role in (*REFERENCE_ROLES, "first_frame", "last_frame")}
    if mode == "ref2va":
        if not active:
            issue("error", "missing_references", "assets", "Ref2VA needs at least one enabled reference asset.")
        for role, cap in (("reference_image", 9), ("reference_video", 3), ("reference_audio", 3)):
            if counts[role] > cap:
                issue("error", "reference_count_limit", "assets", f"At most {cap} enabled {role} assets are supported.")
        if len(active) > 12:
            issue("error", "reference_total_limit", "assets", "At most twelve enabled reference files are supported in total.")
        for role in ("reference_video", "reference_audio"):
            ds = [_number(a.get("duration")) for a in active if a.get("role") == role]
            if sum((d for d in ds if d is not None), Decimal(0)) > 15:
                issue("error", "reference_duration_total", "assets", f"Combined {role} duration exceeds the documented 15-second limit.")
    elif mode in MODES:
        needed = {"fl2va": (1, 1), "i2va": (1, 0), "l2va": (0, 1), "t2va": (0, 0)}[mode]
        if (counts["first_frame"], counts["last_frame"]) != needed:
            message = {
                "fl2va": "First + last frame needs one start photo and one end photo. If you only want a start photo, choose First frame only.",
                "i2va": "First frame only needs one start photo and no end photo. Set one photo as First frame; an ending photo is not required.",
                "l2va": "Last frame only needs one end photo and no start photo. Set one photo as Last frame.",
                "t2va": "Text only does not use start or end photos. Change these photos to Context only, or choose a mode that uses them.",
            }[mode]
            issue("error", "keyframe_count", "assets", message)

    # Match the installed node's presentation, including independent Audio numbering.
    audio_index = 0
    for role, label in (("reference_image", "Picture"), ("reference_video", "Video"), ("reference_audio", "Audio")):
        for ordinal, asset in enumerate((a for a in active if a.get("role") == role), 1):
            base = {"asset_id": asset.get("id"), "role": role, "semantic_role": asset.get("semantic_role", "other"), "name": _text(asset.get("name"))}
            if role == "reference_video" and asset.get("audio_enabled", False):
                audio_index += 1
                references.append({**base, "token": f"<Audio {audio_index}>", "role": "reference_audio", "source": "video_soundtrack"})
            if role == "reference_audio":
                audio_index += 1
                ordinal = audio_index
            references.append({**base, "token": f"<{label} {ordinal}>"})
    for ordinal, role in enumerate((r for r in ("first_frame", "last_frame") if counts[r]), 1):
        for asset in (a for a in active if a.get("role") == role):
            references.append({"asset_id": asset.get("id"), "token": f"<Picture {ordinal}>", "role": role, "semantic_role": asset.get("semantic_role", "other"), "name": _text(asset.get("name"))})

    tag_assets: dict[str, dict] = {}
    ambiguous_tags: set[str] = set()
    primary_tokens = {r["asset_id"]: r["token"] for r in references if r.get("source") != "video_soundtrack"}
    for i, asset in enumerate(assets):
        tag = asset.get("prompt_tag", "")
        if tag == "":
            continue
        if not isinstance(tag, str) or len(tag) > 64 or not PROMPT_TAG.fullmatch(tag):
            issue("error", "invalid_reference_tag", f"assets[{i}].prompt_tag", "Use a photo tag such as mira-face: start with a lowercase letter, then use lowercase letters, numbers and single hyphens (up to 64 characters).")
            continue
        if tag in tag_assets:
            ambiguous_tags.add(tag)
            issue("error", "duplicate_reference_tag", f"assets[{i}].prompt_tag", f"Two photos use @{tag}. Give each photo a different tag so the prompt selects the right one.")
        else:
            tag_assets[tag] = asset
        reference_tags.append({"id": asset["id"], "tag": tag, "label": _text(asset.get("name")),
                               "role": asset.get("role"), "enabled": asset.get("enabled", True),
                               "token": primary_tokens.get(asset["id"])})

    def resolve_tags(value, path):
        if not isinstance(value, str):
            return value

        def replace(match):
            tag = match.group(1).lower()
            if tag in ambiguous_tags:
                return match.group(0)
            asset = tag_assets.get(tag)
            if asset is None:
                issue("error", "unknown_reference_tag", path, f"@{tag} does not match a photo. Choose a tag from your photos, or add that photo first.")
                return match.group(0)
            if not asset.get("enabled", True):
                issue("error", "disabled_reference_tag", path, f"@{tag} is turned off. Turn that photo on, or remove its tag from this text.")
                return match.group(0)
            if asset.get("role") == "context":
                # Context is descriptive inspiration. Even a name that contains
                # angle brackets must never manufacture an H3 binding.
                label = _text(asset.get("name")) or tag.replace("-", " ")
                return label.replace("<", "").replace(">", "")
            token = primary_tokens.get(asset["id"])
            if token is None:
                issue("error", "inactive_reference_tag", path, f"@{tag} has no active photo input. Choose its image role or keep it as prompt inspiration.")
                return match.group(0)
            return token

        return TAG_MENTION.sub(replace, value)

    def resolve_fields(obj, fields, path):
        for key in fields:
            if key in obj:
                obj[key] = resolve_tags(obj[key], f"{path}.{key}" if path else key)

    resolve_fields(story, ("text",), "story")
    resolve_fields(style, tuple(style), "style")
    resolve_fields(project, ("soundscape", "music", "custom_instructions"), "")
    for i, asset in enumerate(assets):
        if asset.get("enabled", True) and asset.get("role") != "context":
            resolve_fields(asset, ("description", "approved_observation"), f"assets[{i}]")
    for i, subject in enumerate(subjects):
        resolve_fields(subject, ("description",), f"subjects[{i}]")
    for i, scene in enumerate(shots):
        resolve_fields(scene, ("action", "setting", "performance", "final_state", "sound", "transition"), f"shots[{i}]")
        if isinstance(scene.get("camera"), dict):
            resolve_fields(scene["camera"], tuple(scene["camera"]), f"shots[{i}].camera")
        if isinstance(scene.get("dialogue"), list):
            for j, line in enumerate(scene["dialogue"]):
                if isinstance(line, dict):
                    # Exact spoken words, language, and speaker ownership are
                    # protected. A delivery direction may reference a photo.
                    resolve_fields(line, ("delivery",), f"shots[{i}].dialogue[{j}]")
    story_text = story.get("text", "")
    soundscape, music, custom = (project.get(key, "") for key in ("soundscape", "music", "custom_instructions"))

    active_ids = {a.get("id") for a in active}
    bound: dict[str, list[str]] = {}
    for i, subject in enumerate(subjects):
        path = f"subjects[{i}]"
        string(subject.get("name", ""), path + ".name", required=True)
        string(subject.get("description", ""), path + ".description")
        bindings = subject.get("asset_ids", [])
        if not isinstance(bindings, list) or any(not isinstance(x, str) for x in bindings):
            issue("error", "invalid_binding", path + ".asset_ids", "Asset bindings must be a list of stable asset IDs.")
            bindings = []
        if len(set(bindings)) != len(bindings):
            issue("error", "duplicate_binding", path + ".asset_ids", "The same asset is bound more than once.")
        for aid in bindings:
            if aid not in asset_map:
                issue("error", "missing_binding", path + ".asset_ids", "A bound reference asset no longer exists.")
            elif aid not in active_ids:
                issue("warning", "inactive_binding", path + ".asset_ids", "A bound asset is disabled or context-only and will not condition H3.")
        bound[subject.get("id", "")] = [a for a in bindings if a in active_ids]

    clock = Decimal(0)
    speaker_ids: dict[str, str] = {}
    dialogue_ids: set[str] = set()
    for i, shot in enumerate(shots):
        path = f"shots[{i}]"
        sd = _number(shot.get("duration"))
        if sd is None or sd <= 0:
            issue("error", "invalid_shot_duration", path + ".duration", "Shot duration must be a positive finite number.")
            sd = Decimal(0)
        if sd < Decimal("0.001"):
            issue("error", "shot_time_precision", path + ".duration", "Shots must be at least one millisecond long.")
        for key, expected_value in (("start", clock), ("end", clock + sd)):
            if key in shot and (_number(shot[key]) is None or abs(_number(shot[key]) - expected_value) > Decimal("0.0005")):
                issue("error", "timeline_gap_or_overlap", path + "." + key, "Explicit shot times must agree with the continuous duration-based timeline.")
        timeline.append({"id": shot.get("id"), "start": float(clock), "end": float(clock + sd)})
        clock += sd
        for key in ("action", "setting", "performance", "final_state", "sound", "transition"):
            string(shot.get(key, ""), path + "." + key)
        if not _text(shot.get("action")):
            issue("warning", "missing_action", path + ".action", "Describe the visible action; the compiler will not invent one.")
        if profile == "director" and not _text(shot.get("final_state")):
            issue("warning", "missing_final_state", path + ".final_state", "A final state helps the next shot or ending remain continuous.")
        camera = shot.get("camera", {})
        if not isinstance(camera, dict):
            issue("error", "invalid_camera", path + ".camera", "Camera must be an object.")
        else:
            for key in ("framing", "movement", "height", "speed", "focus", "amplitude"):
                string(camera.get(key, ""), path + ".camera." + key)
        roster = {}
        for key in ("visible_subject_ids", "offscreen_subject_ids"):
            values = shot.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                issue("error", "invalid_subject_roster", path + "." + key, "Expected a list of subject IDs.")
                values = []
            roster[key] = values
            for sid in values:
                if sid not in subject_map:
                    issue("error", "unknown_subject", path + "." + key, "A shot refers to an unknown subject ID.")
            if len(set(values)) != len(values):
                issue("error", "duplicate_subject_roster", path + "." + key, "A subject is listed twice in the same roster.")
        if set(roster["visible_subject_ids"]) & set(roster["offscreen_subject_ids"]):
            issue("error", "conflicting_subject_roster", path, "A subject cannot be both visible and off-screen in the same shot roster.")
        dialogue = collection(shot.get("dialogue", []), path + ".dialogue")
        word_count = 0
        for j, line in enumerate(dialogue):
            dp = f"{path}.dialogue[{j}]"
            did = line.get("id")
            if not isinstance(did, str) or not did.strip() or did in dialogue_ids:
                issue("error", "invalid_dialogue_id", dp + ".id", "Every dialogue event needs a unique stable ID.")
            else:
                dialogue_ids.add(did)
            sid = line.get("speaker_id")
            if not isinstance(sid, str) or sid not in subject_map:
                issue("error", "unknown_speaker", dp + ".speaker_id", "Bind dialogue to an existing subject, including an off-screen narrator if needed.")
            else:
                speaker_ids.setdefault(sid, f"(S{len(speaker_ids) + 1})")
                if sid not in roster["visible_subject_ids"] + roster["offscreen_subject_ids"]:
                    issue("warning", "speaker_not_in_roster", dp + ".speaker_id", "Set whether this speaker is visible or off-screen in this shot.")
            text = string(line.get("text", ""), dp + ".text", required=True, dialogue=True)
            language = string(line.get("language", ""), dp + ".language", required=True)
            string(line.get("delivery", ""), dp + ".delivery")
            if any(ch in language for ch in "[]<>\r\n"):
                issue("error", "invalid_language_tag", dp + ".language", "Language must be plain text without brackets, tags or line breaks.")
            if language and language not in LANGUAGES:
                issue("warning", "language_quality_unverified", dp + ".language", "This language is outside the eleven officially stable dialogue languages; quality may vary.")
            word_count += len(text.split())
        if sd > 0 and word_count > float(sd) * 3.5:
            issue("warning", "dialogue_pacing", path + ".dialogue", "The dialogue may be too dense for this shot; this is an estimate, not a model limit.")
    if abs(clock - duration) > Decimal("0.0005"):
        issue("error", "timeline_total", "shots", f"Shot durations total {clock} seconds; they must total {duration} seconds.")
    if mode == "fl2va" and any(_text(s.get("transition", "continuous")).lower() not in ("", "continuous") for s in shots[1:]):
        issue("warning", "fl_multiple_shots", "shots", "FL2VA generally works best as one continuous path; cuts should be intentional.")
    if not soundscape.strip():
        issue("warning", "unspecified_soundscape", "soundscape", "No soundscape was specified; a neutral instruction will leave it unspecified rather than invent ambience or require silence.")
    if not story_text.strip() and not any(_text(s.get("action")) for s in shots):
        issue("warning", "empty_creative_brief", "story.text", "Provide a story or shot action for a meaningful generation request.")
    if profile == "custom" and not custom.strip():
        issue("warning", "empty_custom_directions", "custom_instructions", "Custom profile has no additional directions and uses official syntax.")
    from .video_timing import frame_budget
    try:
        timing = frame_budget(float(duration), project.get('comfy_render') or {})
        native_frames = timing['frames']
        detail = f" Includes motion context; adds {timing['new_seconds']:.3f}s of new footage." if timing['overlap_frames'] else ''
        issue("warning", "comfy_native_duration", "duration", f"The timeline targets {duration:.2f}s; local ComfyUI uses {native_frames} native frames ({native_frames / 24:.3f}s at 24fps).{detail} Prompt text does not trim the generated output.")
    except ValueError as exc:
        issue('error', 'continuation_duration', 'duration', str(exc))
    if any(i["severity"] == "error" for i in issues):
        return result()

    primary_ref = {r["asset_id"]: r for r in references if r.get("source") != "video_soundtrack"}
    subject_tokens: dict[str, str] = {}
    definitions: list[str] = []
    retention: list[str] = []
    used_assets: set[str] = set()
    visual_kinds = {"image", "video"}

    def details(asset):
        return " ".join(s for s in (_text(asset.get("description")), _text(asset.get("approved_observation"))) if s)

    def semantic(asset):
        return {"face": "facial identity", "character": "character identity", "background": "environment", "object": "object", "palette": "color palette", "style": "visual style", "wardrobe": "wardrobe", "pose": "pose", "other": "visible content"}.get(asset.get("semantic_role", "other"), "visible content")

    def visual_retention(label, aids):
        # Select retention from the explicit role enum. Captions remain intact;
        # their approved content is never filtered through keyword/NLP guesses.
        transfer_roles = {"style", "palette", "wardrobe", "pose"}
        scopes = {
            "face": "facial identity and specified face/hair features only",
            "character": "the specified character identity and appearance",
            "background": "the specified environment appearance and layout",
            "object": "the specified object appearance and form",
            "palette": "color palette only, excluding source objects and scene geometry",
            "style": "visual style attributes only, excluding source objects, identities and scene geometry",
            "wardrobe": "clothing and garment details only, excluding the source wearer's identity and backdrop",
            "pose": "pose and gesture only, excluding source identity, clothing and backdrop",
            "other": "the specified visible content",
        }
        roles = {asset_map[aid].get("semantic_role", "other") for aid in aids}
        if roles <= transfer_roles:
            level = "attribute_transfer"
        elif "face" in roles or len(roles) > 1:
            # Combined identity/wardrobe and other mixed sources describe a
            # composed target, not wholesale retention of each source image.
            level = "partially_preserved"
        else:
            level = "fully_preserved"
        parts = []
        for aid in aids:
            role = asset_map[aid].get("semantic_role", "other")
            verb = "transfer" if role in transfer_roles else "retain"
            parts.append(f"from {primary_ref[aid]['token']}, {verb} {scopes.get(role, scopes['other'])}")
        return f"{label}: {level} - " + "; ".join(parts) + ". Each source contributes only its assigned role."

    if mode == "ref2va":
        for subject in subjects:
            aids = [a for a in bound[subject["id"]] if asset_map[a].get("media_type") in visual_kinds]
            if not aids:
                continue
            label = f"<Subject {len(subject_tokens) + 1}>"
            subject_tokens[subject["id"]] = label
            source = "; ".join(f"{primary_ref[a]['token']} supplies {semantic(asset_map[a])}" for a in aids)
            desc = _text(subject.get("description"))
            observed = " ".join(details(asset_map[a]) for a in aids if details(asset_map[a]))
            definitions.append(_sentence(f"{label} is {subject['name']}. {source}. {desc} {observed}"))
            retention.append(visual_retention(label, aids))
            used_assets.update(aids)
        next_subject = len(subject_tokens) + 1
        for asset in active:
            aid = asset["id"]
            if asset.get("media_type") == "image" and aid not in used_assets:
                label = f"<Subject {next_subject}>"
                next_subject += 1
                definitions.append(_sentence(f"{label} is the {semantic(asset)} reference named {asset.get('name') or 'reference'}, supplied by {primary_ref[aid]['token']}. {details(asset)}"))
                retention.append(visual_retention(label, [aid]))
                used_assets.add(aid)
        for ref in references:
            if ref["token"].startswith("<Audio"):
                asset = asset_map[ref["asset_id"]]
                definition = details(asset) or "Audio reference; no transcript or listening analysis was supplied."
                if ref.get("source") == "video_soundtrack":
                    definition = "The explicitly enabled soundtrack of " + primary_ref[ref["asset_id"]]["token"] + ". " + definition
                elif asset.get("media_type") == "audio":
                    # A standalone audio binding is explicit. A visual binding to
                    # a video does not identify the speaker in its soundtrack.
                    assigned_speakers = []
                    for subject in subjects:
                        sid = subject["id"]
                        if sid in speaker_ids and asset["id"] in bound[sid]:
                            visual_label = subject_tokens.get(sid, "")
                            assigned_speakers.append(" ".join(v for v in (visual_label, subject["name"], speaker_ids[sid]) if v))
                    if assigned_speakers:
                        definition += " Voice reference assignment: " + "; ".join(assigned_speakers) + ". Use the supplied voice characteristics as a reference; do not add or replace the scripted words."
                definitions.append(_sentence(ref["token"] + " is an audio reference. " + definition))
                retention.append(f"{ref['token']}: reference - use only the described audible characteristics; exact signal copying is not guaranteed by a prompt.")
            elif ref["token"].startswith("<Video"):
                asset = asset_map[ref["asset_id"]]
                definitions.append(_sentence(f"{ref['token']} is the reference video named {asset.get('name') or 'video'}, supplying only the described content or temporal guidance. {details(asset)}"))
                retention.append(f"{ref['token']}: weak_reference - follow the specified content or temporal guidance without implying editing or continuation.")

    allowed_bindings = {r["token"] for r in references} | {m.group(0) for d in definitions for m in re.finditer(r"^<Subject \d+>", d)}
    # Explicit prose labels are permitted only when they match actual bindings.
    def check_prose(value, path):
        if isinstance(value, str):
            for match in EXPLICIT_BINDING.finditer(value):
                if match.group(0) not in allowed_bindings:
                    issue("error", "unresolved_prompt_reference", path, f"{match.group(0)} has no enabled binding in this project.")

    for value, path in ((story_text, "story.text"), (soundscape, "soundscape"), (music, "music"), (custom, "custom_instructions")):
        check_prose(value, path)
    for key, value in style.items():
        check_prose(value, "style." + str(key))
    for i, asset in enumerate(assets):
        if asset.get("id") in active_ids:
            for key in ("name", "description", "approved_observation"):
                check_prose(asset.get(key, ""), f"assets[{i}].{key}")
    for i, subject in enumerate(subjects):
        for key in ("name", "description"):
            check_prose(subject.get(key, ""), f"subjects[{i}].{key}")
    for i, shot in enumerate(shots):
        for key in ("action", "setting", "performance", "final_state", "sound"):
            check_prose(shot.get(key, ""), f"shots[{i}].{key}")
        for key, value in shot.get("camera", {}).items():
            check_prose(value, f"shots[{i}].camera.{key}")
        for j, line in enumerate(shot.get("dialogue", [])):
            check_prose(line.get("delivery", ""), f"shots[{i}].dialogue[{j}].delivery")
    if any(i["severity"] == "error" for i in issues):
        return result()

    def name(sid):
        s = subject_map[sid]
        return (subject_tokens[sid] + " " if sid in subject_tokens else "") + s["name"]

    style_parts = []
    for key, lead in (("genre", ""), ("vibe", ""), ("lighting", "Lighting: "), ("color", "Color: "), ("notes", "")):
        if _text(style.get(key)):
            style_parts.append(_sentence(lead + _text(style[key])))
    if profile == "custom" and custom.strip():
        style_parts.append(_sentence(custom))
    style_text = " ".join(style_parts)
    rendered_shots = []
    for i, shot in enumerate(shots):
        paragraphs = []
        if i == 0 and mode != "ref2va":
            paragraphs += [style_text, _sentence(story_text)]
        if i == 0:
            for asset in active:
                owner = asset.get("simple_owner_id")
                if asset.get("semantic_role") == "object" and owner:
                    # A prop's starting owner is a relationship, not a source
                    # for the character's identity or a permanent possession.
                    paragraphs.append(f"The object supplied by {primary_ref[asset['id']]['token']} starts with {name(owner)}; ownership may change through the described action.")
        camera = shot.get("camera", {})
        if _text(camera.get("framing")):
            paragraphs.append(_sentence("A " + camera["framing"] + " shot"))
        if _text(shot.get("setting")):
            paragraphs.append(_sentence(shot["setting"]))
        for sid in shot.get("visible_subject_ids", []):
            desc = _text(subject_map[sid].get("description"))
            paragraphs.append(_sentence(name(sid) + " is visible" + (": " + desc if desc else "")))
        for sid in shot.get("offscreen_subject_ids", []):
            paragraphs.append(_sentence(name(sid) + " remains off-screen"))
        movement = _text(camera.get("movement"))
        moving = {"static": "holds a static shot", "push_in": "pushes in", "pull_out": "pulls out", "pan_left": "pans left", "pan_right": "pans right", "truck_left": "trucks left", "truck_right": "trucks right", "tilt_up": "tilts up", "tilt_down": "tilts down", "arc": "arcs around the subject", "tracking": "tracks the subject", "zoom_in": "zooms in", "zoom_out": "zooms out"}
        if movement:
            motion = moving.get(movement, movement.replace("_", " "))
            parts = ["Camera stationary" if profile == "concise" and movement == "static" else "The camera " + motion]
            if movement != "static" and _text(camera.get("speed")):
                parts.append("at " + camera["speed"] + " speed")
            if _text(camera.get("amplitude")):
                parts.append("with " + camera["amplitude"] + " amplitude")
            if _text(camera.get("height")):
                parts.append("at " + camera["height"] + " height")
            paragraphs.append(_sentence(" ".join(parts)))
        if _text(camera.get("focus")):
            paragraphs.append(_sentence("Focus on " + camera["focus"]))
        paragraphs += [_sentence(_text(shot.get(key))) for key in ("action", "performance")]
        for line in shot.get("dialogue", []):
            sid = line["speaker_id"]
            delivery = _text(line.get("delivery"))
            voiceover = line.get("voiceover") is True
            offscreen = sid in shot.get("offscreen_subject_ids", [])
            verb = "says in an off-screen voiceover" if voiceover else ("speaks off-screen" if offscreen else "says")
            utterance = f"{name(sid)} {speaker_ids[sid]} {verb}"
            if delivery:
                utterance += ", " + delivery
            utterance += f": <d>[{line['language']}] {line['text']}</d>"
            if voiceover and sid in shot.get("visible_subject_ids", []):
                utterance += " while the character's lips remain completely closed."
            paragraphs.append(utterance)
        paragraphs += [_sentence(_text(shot.get("sound"))), _sentence(_text(shot.get("final_state")))]
        if profile == "director":
            paragraphs.append("Keep each subject separately identified and make the described changes in position and state explicit.")
        body = " ".join(p for p in paragraphs if p)
        if i == 0:
            rendered_shots.append("[Shot 1] " + body)
        else:
            transition = _text(shot.get("transition", "continuous")).lower()
            timestamp = _stamp(Decimal(str(timeline[i]['start'])))
            # A timeline row may describe another timed beat within the same
            # continuous take. Official [Shot N] markers introduce new shots.
            if transition in ("", "continuous"):
                rendered_shots[-1] += f"\nAt {timestamp}, within the same continuous shot, " + body
                continue
            if transition in ("cross-dissolve", "fade", "wipe"):
                body = f"the shot uses a {transition}. " + body
            else:
                body = "the camera cuts to the following composition. " + body
            rendered_shots.append(f"[Shot {len(rendered_shots) + 1}] At {timestamp}, " + body)
    body = "\n".join(rendered_shots)
    sound = soundscape.strip() or "No additional soundscape direction is specified."
    score = music.strip() or "N/A"
    if mode == "ref2va":
        summary = "[reference generation" + (" + audio reference" if any(r["token"].startswith("<Audio") for r in references) else "") + "] "
        summary += story_text.strip() or "Generate the target sequence from the defined references and shot timeline."
        prompt = "\n\n".join(("subject_definitions:\n" + "\n".join(definitions), "summary:\n" + summary, "retention_analysis:\n" + "\n".join(retention), "detailed_description:\n" + (style_text + "\n" if style_text else "") + body, "overall_soundscape:\n" + sound, "non_diegetic_music:\n" + score))
    else:
        preface = ""
        if mode == "i2va":
            preface = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
        elif mode == "fl2va":
            preface = f"How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot {len(rendered_shots)}) aligns with the {duration:.2f}-second mark of the target video."
        elif mode == "l2va":
            preface = f"How the reference pictures align with the target video — <Picture 1> (from [Shot {len(rendered_shots)}]) aligns with the {duration:.2f}-second mark of the target video."
        prompt = "\n\n".join(("integrated_multimodal_description: " + body, "overall_soundscape: " + sound, "non_diegetic_music: " + score))
        if preface:
            prompt = preface + "\n\n" + prompt
    return result(prompt)


def validate_project(project: dict) -> list[dict]:
    return compile_project(project)["issues"]
