"""Compact authoring tests use synthetic people/images and a fake completion client."""
import base64
import copy
import io
import json

import pytest
from PIL import Image

from backend.continuation_suggestions import (
    ENDING_FACTS_SCHEMA, ENDING_FACTS_SYSTEM, ENDING_IMAGE_EDGE, MAX_CONTEXT_CHARS,
    MAX_LARGE_CONTEXT_CHARS, MAX_SMALL_WRITER_CONTEXT_CHARS, MAX_STORY_HISTORY_CHARS,
    MAX_SUGGESTION_CONTEXT_CHARS, PLAN_SYSTEM,
    SMALL_WRITER_SYSTEM, SUGGESTIONS_SCHEMA, SUGGESTIONS_SYSTEM, compact_plan,
    continuation_context, large_continuation_context, small_writer_context, suggest_continuations,
)
from backend.lmstudio import LMStudioError
from backend.projects import merge_plan, new_project, shot


def project():
    p = new_project()
    p.update(title="Two friends in an atelier", duration=5, simple={"directed": True})
    p["story"]["text"] = "Mira gives Nora the closed gift box, then they look toward the window."
    p["subjects"] = [
        {"id": "mira", "name": "Mira", "asset_ids": ["face1", "dress1"], "description": "Short dark hair."},
        {"id": "nora", "name": "Nora", "asset_ids": ["face2", "dress2"], "description": "Long brown hair."},
    ]
    data = [("face1", "Mira face", "face", "Mira's face only"),
            ("face2", "Nora face", "face", "Nora's face only"),
            ("dress1", "Blue coat", "wardrobe", "Mira wears a blue coat"),
            ("dress2", "Green dress", "wardrobe", "Nora wears a green dress"),
            ("box", "Closed gift box", "object", "Closed red gift box"),
            ("room", "Atelier", "background", "Small atelier with one window"),
            ("look", "Warm style", "style", "Warm daylight and soft contrast")]
    p["assets"] = [{"id": key, "name": name, "semantic_role": role, "description": caption,
                    "approved_observation": caption, "observation": "UNAPPROVED_CAPTION",
                    "filename": "PRIVATE_PATH.png", "media_type": "image", "role": "reference_image",
                    "prompt_tag": key, "enabled": True} for key, name, role, caption in data]
    p["assets"][4]["simple_owner_id"] = "mira"
    p["simple"]["person_actions"] = {"nora": "Receive the box carefully."}
    p["shots"] = [shot(1.5), shot(1.5), shot(2)]
    for i, scene in enumerate(p["shots"]):
        scene.update(action=["Mira lifts @box.", "Mira hands the box to Nora.", "Nora holds the box and looks toward the window."][i],
                     setting="Atelier", final_state="Nora holds the closed box." if i == 2 else "Mira holds the box.",
                     visible_subject_ids=["mira", "nora"])
        scene["camera"]["framing"] = ["wide", "close-up", "medium"][i]
        scene["director_locks"] = ["camera.framing", "camera.movement", "setting", "visible_subject_ids"]
    p["shots"][0]["dialogue"] = [{"id": "line1", "speaker_id": "mira", "text": "This is for you.", "locked": True}]
    p["shots"][1]["dialogue"] = [{"id": "line2", "speaker_id": "nora", "text": "Thank you, Mira!", "locked": True}]
    return p


def frame(width=960, height=540):
    image = Image.new("RGB", (width, height), (20, 180, 60))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def choices():
    return {"suggestions": [
        {"title": "Hold it closer", "idea": "Nora carefully draws the closed box closer to her body without changing its holder."},
        {"title": "Let hands settle", "idea": "Mira lowers her empty hands to her sides while Nora keeps holding the box."},
        {"title": "One careful step", "idea": "Nora takes one small step toward the window while keeping both hands around the closed box."},
    ]}


class Client:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = copy.deepcopy(replies if replies is not None else [choices()])
        self.last_completion_info = {"model": "exact-loaded-instance", "attempts": 1, "usage": {"completion_tokens": 95}}

    def complete_json(self, model, system, content, schema, **options):
        self.calls.append((model, system, copy.deepcopy(content), copy.deepcopy(schema), options))
        return self.replies.pop(0)


def test_one_actual_resized_frame_exact_instance_and_bounded_three_choice_schema():
    p = project(); original = copy.deepcopy(p); image = frame(); client = Client()
    result = suggest_continuations(client, "exact-loaded-instance", p, 5, image, "Keep the box closed.")
    assert p == original
    assert result["suggestions"] == choices()["suggestions"]
    assert len(client.calls) == 1
    model, system, content, schema, options = client.calls[0]
    assert model == "exact-loaded-instance"
    assert system == SUGGESTIONS_SYSTEM
    assert schema == SUGGESTIONS_SCHEMA
    assert options == {"max_tokens": 650, "temperature": 0.5}
    assert [part["type"] for part in content] == ["text", "image_url"]
    assert content[1]["image_url"]["detail"] == "low"
    encoded = content[1]["image_url"]["url"]
    with Image.open(io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))) as resized:
        assert resized.size == (ENDING_IMAGE_EDGE, 288)
        assert resized.getpixel((10, 10))[1] > 170
    assert result["ending_image"] == {"width": 512, "height": 288, "actual_ending_frame": True}
    assert result["context_chars"] == len(content[0]["text"]) <= MAX_CONTEXT_CHARS
    client.last_completion_info["usage"]["completion_tokens"] = 500
    assert result["completion_info"]["usage"]["completion_tokens"] == 95


def test_context_prioritizes_current_ending_and_keeps_only_compact_role_bindings():
    text = continuation_context(project(), 5)
    data = json.loads(text)
    assert [p["name"] for p in data["people"]] == ["Mira", "Nora"]
    assert list(data)[:3] == ["ending_note", "direction", "new_action_seconds"]
    assert "Receive the box carefully." not in text
    refs = {r["tag"]: r for r in data["references"]}
    assert refs["@dress1"]["bound_to"] == ["Mira"]
    assert refs["@dress2"]["bound_to"] == ["Nora"]
    assert refs["@box"] == {"role": "object", "tag": "@box"}
    assert "historical_start_owner" not in text and "approved_caption" not in text
    assert "Mira wears a blue coat" not in text
    assert data["ending_note"] == "Nora holds the closed box."
    assert data["camera"]["framing"] == "medium"
    assert data["previous_speech_must_not_replay"] is True
    assert "UNAPPROVED_CAPTION" not in text and "PRIVATE_PATH" not in text
    assert "This is for you." not in text and "Thank you, Mira!" not in text
    assert data["completed_events_do_not_repeat"][-1] == "Nora holds the box and looks toward the window."
    assert len(text) < 1700
    assert data["new_action_seconds"] == 3.542
    assert data["preserved_overlap_seconds"] == 1.625


@pytest.mark.parametrize("duration,frames,new_seconds", [(4, 107, 2.833), (5, 124, 3.542), (15, 362, 13.458)])
def test_short_beat_uses_actual_new_time_after_h3_overlap(duration, frames, new_seconds):
    data = json.loads(continuation_context(project(), duration))
    assert data["generated_seconds"] == round(frames / 24, 3)
    assert data["new_action_seconds"] == new_seconds
    assert "new_action_seconds" in SUGGESTIONS_SYSTEM


def test_long_context_bounded_without_dropping_names_bindings_or_direction():
    p = project()
    p["story"]["text"] = "A detailed existing story. " * 400
    for a in p["assets"]:
        a["description"] *= 150
        a["approved_observation"] *= 150
    text = continuation_context(p, 7, "Keep the box closed.")
    assert len(text) <= MAX_SUGGESTION_CONTEXT_CHARS
    data = json.loads(text)
    assert len(data["references"]) == 7 and len(data["people"]) == 2
    assert data["direction"] == "Keep the box closed."
    assert "…" in text


def test_stale_owners_requests_and_ref_appearance_cannot_override_current_ending():
    p = project()
    del p["assets"][4]["simple_owner_id"]
    p["assets"][4]["role"] = "context"
    p["simple"]["continuation"] = {"previous_ending": "Mira has the box.", "request": "Pass it to Nora.",
                                      "previous_object_owners": [{"asset_id": "box", "person_id": "mira"}]}
    data = json.loads(continuation_context(p, 5))
    ref = next(r for r in data["references"] if r["tag"] == "@box")
    assert ref == {"role": "object", "tag": "@box"}
    assert "Mira has the box." not in json.dumps(data)
    assert "Pass it to Nora." not in json.dumps(data)
    assert "continuation_request" not in data and "previous_ending" not in data
    assert data["ending_note"] == "Nora holds the closed box."


def test_ending_note_not_compacted_behind_long_old_story_or_wardrobe_captions():
    p = project()
    ending = "Mira, in green, stands on the left with empty hands. Nora, in orange, stands on the right holding the closed box."
    p["shots"][-1]["final_state"] = ending
    p["story"]["text"] = "Old warm smiles and turns. " * 300
    for asset in p["assets"]:
        asset["approved_observation"] = "INCIDENTAL_OTHER_HOLDER_AND_OUTFIT " * 100
    context = continuation_context(p, 4, "Keep the box closed and move the scene forward.")
    data = json.loads(context)
    assert data["ending_note"] == ending
    assert data["direction"] == "Keep the box closed and move the scene forward."
    assert "INCIDENTAL_OTHER_HOLDER_AND_OUTFIT" not in context
    assert len(context) <= MAX_SUGGESTION_CONTEXT_CHARS


def test_titles_are_short_unnumbered_labels_and_ideas_have_no_list_prefix():
    reply = choices()
    reply["suggestions"][0]["title"] = "1. Nora draws the closed box a little closer"
    reply["suggestions"][0]["idea"] = "Choice 1: " + reply["suggestions"][0]["idea"]
    result = suggest_continuations(Client([reply]), "instance", project(), 5, frame())
    assert result["suggestions"][0]["title"] == "Nora draws the closed box"
    assert len(result["suggestions"][0]["title"].split()) <= 6
    assert result["suggestions"][0]["idea"] == choices()["suggestions"][0]["idea"]


def test_suggestion_rules_require_different_new_beats_and_current_holders():
    assert "Those outrank past story and reference labels" in SUGGESTIONS_SYSTEM
    assert "held object stays with its current holder" in SUGGESTIONS_SYSTEM
    assert "different main actions and visible outcomes" in SUGGESTIONS_SYSTEM
    assert "completed_events_do_not_repeat" in SUGGESTIONS_SYSTEM


@pytest.mark.parametrize("duration", [True, 3, 16, 5.5, "5"])
def test_invalid_duration_never_calls_model(duration):
    client = Client()
    with pytest.raises(ValueError, match="4 to 15"):
        suggest_continuations(client, "instance", project(), duration, frame())
    assert not client.calls


@pytest.mark.parametrize("image", [None, "https://example.com/fake.png", "data:image/png;base64,YmFk"])
def test_real_ending_image_required_before_completion(image):
    client = Client()
    with pytest.raises(LMStudioError):
        suggest_continuations(client, "instance", project(), 5, image)
    assert not client.calls


@pytest.mark.parametrize("overlap", [True, 0, 40, 141, 345])
def test_overlap_must_fit_requested_clip(overlap):
    with pytest.raises(ValueError, match="overlap"):
        continuation_context(project(), 4, overlap_frames=overlap)


@pytest.mark.parametrize("reply", [
    {}, {"suggestions": []}, {"suggestions": choices()["suggestions"][:2]},
    {"suggestions": [{"title": "One", "idea": "Only one complete long enough sentence."}] * 4},
    {"suggestions": [{"title": "One", "idea": "No"}] * 3},
    {"suggestions": [{"title": "One", "idea": "A sufficiently long action idea.", "dialogue": "New words"}] * 3},
])
def test_missing_invalid_or_extra_reply_fields_rejected(reply):
    with pytest.raises(LMStudioError) as failure:
        suggest_continuations(Client([reply]), "instance", project(), 5, frame())
    assert failure.value.code == "invalid_suggestions"


def test_repeated_ideas_do_not_become_three_fake_choices():
    reply = choices()
    reply["suggestions"][2]["idea"] = reply["suggestions"][0]["idea"].upper()
    with pytest.raises(LMStudioError) as failure:
        suggest_continuations(Client([reply]), "instance", project(), 5, frame())
    assert failure.value.code == "duplicate_suggestions"


def test_excess_named_refs_raise_instead_of_silently_dropping_assignments():
    p = project()
    p["assets"] *= 28
    with pytest.raises(ValueError, match="too many named references"):
        continuation_context(p, 5)


def scene_replies():
    return [
        {"action": "Mira carefully lifts @box while Nora watches.", "final_state": "Mira holds the closed box steady."},
        {"action": "Mira gently passes the closed box into Nora's hands.", "final_state": "Nora holds the closed box."},
        {"action": "Nora keeps the closed box steady and looks toward the window.", "final_state": "Nora looks toward the window, holding the box."},
    ]


def test_compact_plan_three_scenes_exact_cameras_dialogue_style_bindings_and_ids_survive_merge():
    p = project(); original = copy.deepcopy(p); client = Client(scene_replies())
    proposal = compact_plan(client, "exact-instance", p, "Keep the closed box intact.", "concise")
    merged = merge_plan(p, proposal)
    assert p == original
    assert len(client.calls) == len(proposal["shots"]) == len(merged["shots"]) == 3
    for index, scene in enumerate(merged["shots"]):
        for key in ("id", "duration", "camera", "dialogue", "setting", "visible_subject_ids", "offscreen_subject_ids", "transition"):
            assert scene[key] == p["shots"][index][key]
        assert scene["action"] == scene_replies()[index]["action"]
        assert scene["final_state"] == scene_replies()[index]["final_state"]
    for key in ("assets", "subjects", "style", "story", "soundscape", "music", "id"):
        assert merged[key] == p[key]
    for model, system, content, schema, options in client.calls:
        assert model == "exact-instance" and system == PLAN_SYSTEM
        assert isinstance(content, str) and len(content) <= MAX_CONTEXT_CHARS
        assert set(schema["properties"]) == {"action", "final_state"}
        assert schema["additionalProperties"] is False
        assert options["max_tokens"] == 400
        assert "UNAPPROVED_CAPTION" not in content
        assert "This is for you." not in content and "Thank you, Mira!" not in content
    assert json.loads(client.calls[0][2])["scene"]["speech"] == [{"speaker": "Mira", "words": 4}]
    assert "Nora holds the closed box." in json.loads(client.calls[2][2])["preceding_ending"]


def test_locked_ending_is_not_requested_or_replaced():
    p = project(); p["shots"][0]["director_locks"].append("final_state")
    replies = scene_replies(); del replies[0]["final_state"]
    client = Client(replies)
    proposal = compact_plan(client, "instance", p)
    assert client.calls[0][3]["required"] == ["action"]
    assert proposal["shots"][0]["final_state"] == p["shots"][0]["final_state"]


def test_compact_plan_cannot_return_extra_camera_or_dialogue_replacements():
    p = project(); original = copy.deepcopy(p)
    replies = scene_replies(); replies[0]["camera"] = {"framing": "changed"}
    with pytest.raises(LMStudioError):
        compact_plan(Client(replies), "instance", p)
    assert p == original


def test_compact_plan_rejects_dropped_existing_reference_tag():
    replies = scene_replies(); replies[0]["action"] = "Mira lifts a box."
    with pytest.raises(LMStudioError, match="dropped a named reference"):
        compact_plan(Client(replies), "instance", project())


def test_compact_plan_invalid_scene_timing_fails_before_model():
    p = project(); p["shots"][0]["duration"] = 4; client = Client(scene_replies())
    with pytest.raises(ValueError, match="add up"):
        compact_plan(client, "instance", p)
    assert not client.calls


def observation():
    return {"current_appearance": "Mira left in green; Nora right in orange.",
            "current_holders": "Nora holds the closed box; Mira's hands are empty."}


def test_small_mode_observes_once_then_writes_text_only_with_exact_same_instance():
    p = project(); original = copy.deepcopy(p)

    class InfoClient(Client):
        def complete_json(self, *args, **kwargs):
            reply = super().complete_json(*args, **kwargs)
            self.last_completion_info = {"model": args[0], "pass": len(self.calls), "usage": {"completion_tokens": 45 * len(self.calls)}}
            return reply

    client = InfoClient([observation(), choices()])
    result = suggest_continuations(client, "same-loaded-instance", p, 4, frame(), "Keep the box closed.", small_model=True)
    assert p == original
    assert len(client.calls) == 2
    vision_model, vision_system, vision_content, vision_schema, vision_options = client.calls[0]
    writer_model, writer_system, writer_content, writer_schema, writer_options = client.calls[1]
    assert vision_model == writer_model == "same-loaded-instance"
    assert vision_system == ENDING_FACTS_SYSTEM and vision_schema == ENDING_FACTS_SCHEMA
    assert vision_options == {"max_tokens": 120, "temperature": 0.0}
    assert [part["type"] for part in vision_content] == ["text", "image_url"]
    assert json.loads(vision_content[0]["text"])["cast"] == ["Mira", "Nora"]
    assert "references" not in vision_content[0]["text"]
    assert writer_system == SMALL_WRITER_SYSTEM
    assert writer_options == {"max_tokens": 420, "temperature": 0.3}
    assert isinstance(writer_content, str) and "data:image" not in writer_content
    assert len(writer_content) <= MAX_SMALL_WRITER_CONTEXT_CHARS
    writer_data = json.loads(writer_content)
    assert list(writer_data)[0] == "authoritative_ending_note"
    assert writer_data["authoritative_ending_note"] == "Nora holds the closed box."
    assert writer_data["direction"] == "Keep the box closed."
    assert writer_data["new_action_seconds"] == 2.833
    assert "references" not in writer_data and "past_story_context_only" not in writer_data
    assert writer_schema["properties"]["suggestions"]["items"]["properties"]["idea"]["maxLength"] == 240
    assert SUGGESTIONS_SCHEMA["properties"]["suggestions"]["items"]["properties"]["idea"]["maxLength"] == 320
    assert result["suggestion_method"] == "vision_then_text"
    assert result["ending_frame_observation"] == observation()
    assert result["vision_completion_info"]["pass"] == 1
    assert result["completion_info"]["pass"] == 2
    client.last_completion_info["usage"]["completion_tokens"] = 999
    assert result["vision_completion_info"]["usage"]["completion_tokens"] == 45
    assert result["completion_info"]["usage"]["completion_tokens"] == 90


@pytest.mark.parametrize("facts", [{}, {"current_appearance": "Visible people."},
                                   {"current_appearance": " ", "current_holders": " "},
                                   {**observation(), "next_action": "Pass the box."}])
def test_incomplete_small_vision_pass_stops_before_writing(facts):
    client = Client([facts, choices()]); p = project(); original = copy.deepcopy(p)
    with pytest.raises(LMStudioError):
        suggest_continuations(client, "instance", p, 5, frame(), small_model=True)
    assert len(client.calls) == 1
    assert p == original


def test_small_text_pass_keeps_note_separate_from_potentially_wrong_ai_observation():
    p = project()
    wrong_observation = {"current_appearance": "Two people in coats.", "current_holders": "Mira holds the box."}
    result = json.loads(small_writer_context(continuation_context(p, 5), wrong_observation))
    assert result["authoritative_ending_note"] == "Nora holds the closed box."
    assert result["visual_description"]["current_holders"] == "Mira holds the box."
    assert "wins over the visual description" in SMALL_WRITER_SYSTEM
    assert "historical_start_owner" not in json.dumps(result)


def test_small_writer_long_completed_actions_still_fit_compact_budget():
    p = project()
    for scene in p["shots"]:
        scene["action"] *= 100
    context = small_writer_context(continuation_context(p, 5, "Keep the box closed."), observation())
    assert len(context) <= MAX_SMALL_WRITER_CONTEXT_CHARS
    assert json.loads(context)["people"] == ["Mira", "Nora"]


@pytest.mark.parametrize("mode", ["true", 1, None])
def test_small_mode_flag_must_be_boolean_before_model_calls(mode):
    client = Client()
    with pytest.raises(ValueError, match="true or false"):
        suggest_continuations(client, "instance", project(), 5, frame(), small_model=mode)
    assert not client.calls


def memory_project(earlier_count=2):
    p = project()
    p["subjects"][0]["description"] = "Mira is the person in emerald green on the left."
    p["subjects"][1]["description"] = "Nora is the person in terracotta orange on the right."
    p["simple"]["continuation"] = {"previous_story": {
        "opening_story": "Mira brings Nora a gift after their atelier exhibition.",
        "earlier_clips": [{"brief": f"Earlier clip {i}: the friends celebrate their exhibition.",
                            "events": [{"action": f"Completed action {i}: Mira approaches the window.",
                                        "final_state": "Mira held the gift before handing it over.",
                                        "dialogue": [{"speaker_id": "mira", "text": "We did it!"}]}]} for i in range(earlier_count)],
        "brief": "Mira gives Nora the gift.",
        "shots": [{"action": "Mira hands over the closed box.", "final_state": "Nora now holds it.",
                   "dialogue": [{"speaker_id": "mira", "text": "This is for you."}]}],
    }}
    return p


def test_large_path_retains_opening_earlier_previous_latest_and_exact_old_speaker_lines():
    p = memory_project(); before = copy.deepcopy(p); client = Client()
    result = suggest_continuations(client, "large-instance", p, 5, frame())
    context = json.loads(client.calls[0][2][0]["text"])
    history = context["completed_story_history"]
    assert p == before
    assert list(context)[0] == "ending_note"
    assert context["ending_note"] == "Nora holds the closed box."
    assert history["opening_story"] == "Mira brings Nora a gift after their atelier exhibition."
    assert len(history["earlier_clips"]) == 2
    assert history["earlier_clips"][0]["brief"].startswith("Earlier clip 0:")
    assert history["previous_clip"]["brief"] == "Mira gives Nora the gift."
    assert history["previous_clip"]["already_spoken"] == [{"speaker": "Mira", "exact_old_line": "This is for you."}]
    assert history["latest_completed_clip"]["already_spoken"][1] == {"speaker": "Nora", "exact_old_line": "Thank you, Mira!"}
    assert "ALL ALREADY HAPPENED" in history["status"]
    assert "never replay its actions or exact old speech" in SUGGESTIONS_SYSTEM
    assert result["history_clip_count"] == 4
    assert result["history_context_chars"] <= MAX_STORY_HISTORY_CHARS


def test_large_nominal_appearance_maps_names_without_old_object_owner_or_unapproved_captions():
    p = memory_project()
    context, _ = large_continuation_context(continuation_context(p, 5), p)
    data = json.loads(context)
    mira, nora = data["people"]
    assert mira["description"] == "Mira is the person in emerald green on the left."
    assert nora["description"] == "Nora is the person in terracotta orange on the right."
    assert {a["role"] for a in mira["nominal_identity_and_wardrobe"]} == {"face", "wardrobe"}
    outfit = next(a for a in mira["nominal_identity_and_wardrobe"] if a["role"] == "wardrobe")
    assert outfit["tag"] == "@dress1"
    assert outfit["approved_observation"] == "Mira wears a blue coat"
    assert "UNAPPROVED_CAPTION" not in context
    assert "simple_owner_id" not in context and "historical_start_owner" not in context
    assert "current ending image and new direction win if appearance changed" in SUGGESTIONS_SYSTEM


def test_twenty_recent_earlier_clips_plus_opening_survive_bounded_long_memory():
    p = memory_project(27)
    previous = p["simple"]["continuation"]["previous_story"]
    for entry in previous["earlier_clips"]:
        entry["brief"] += " Lengthy completed story context." * 100
        entry["events"][0]["action"] += " Elaborate past movement." * 150
        entry["events"][0]["dialogue"][0]["text"] = "OLD_LONG_LINE " * 700
    for person in p["subjects"]:
        person["description"] *= 40
    for asset in p["assets"]:
        asset["description"] *= 100
        asset["approved_observation"] *= 100
    before = copy.deepcopy(p)
    context, info = large_continuation_context(continuation_context(p, 5), p)
    data = json.loads(context); history = data["completed_story_history"]
    assert p == before
    assert len(context) <= MAX_LARGE_CONTEXT_CHARS
    assert info["history_context_chars"] <= MAX_STORY_HISTORY_CHARS
    assert info["history_clip_count"] == 22
    assert len(history["earlier_clips"]) == 20
    assert history["earlier_clips"][0]["brief"].startswith("Earlier clip 7:")
    assert history["earlier_clips"][-1]["brief"].startswith("Earlier clip 26:")
    assert history["opening_story"] == "Mira brings Nora a gift after their atelier exhibition."
    assert "OLD_LONG_LINE" not in context
    assert history["earlier_clips"][0]["other_already_spoken_word_counts"] == {"Mira": 700}
    for person in data["people"]:
        assert len(person["description"]) <= 300
        for anchor in person.get("nominal_identity_and_wardrobe", []):
            for key in ("description", "approved_observation"):
                assert len(anchor.get(key, "")) <= 200


def test_small_two_pass_never_receives_large_story_memory_or_nominal_appearance_anchors():
    p = memory_project(20); client = Client([observation(), choices()])
    result = suggest_continuations(client, "small-instance", p, 5, frame(), small_model=True)
    assert "history_clip_count" not in result
    for _, _, content, _, _ in client.calls:
        text = content[0]["text"] if isinstance(content, list) else content
        assert "completed_story_history" not in text and "atelier exhibition" not in text
        assert "emerald green" not in text and "nominal_identity_and_wardrobe" not in text
    assert result["context_chars"] <= MAX_SMALL_WRITER_CONTEXT_CHARS


def test_legacy_previous_story_without_earlier_clips_is_still_remembered():
    p = memory_project(); previous = p["simple"]["continuation"]["previous_story"]
    del previous["earlier_clips"]; del previous["opening_story"]
    context, info = large_continuation_context(continuation_context(p, 5), p)
    history = json.loads(context)["completed_story_history"]
    assert history["opening_story"] == "Mira gives Nora the gift."
    assert history["earlier_clips"] == []
    assert info["history_clip_count"] == 2
