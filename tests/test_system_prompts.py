"""Standalone chat exports must remain separate from the JSON planner API."""
import pytest

from backend.prompts import PERSONAS, export_system_prompt, persona_instruction


@pytest.mark.parametrize('mode', ['ref2va', 'fl2va'])
@pytest.mark.parametrize('persona', [item['id'] for item in PERSONAS])
def test_all_manual_profiles_include_exact_persona_and_protected_inputs(mode, persona):
    result = export_system_prompt(persona, mode)
    assert persona_instruction(persona) in result
    assert mode.upper() in result
    assert 'exact user text</d>' in result and '(S1)' in result
    assert 'required image' in result and 'locked' in result
    assert 'Return only the requested JSON' not in result
    assert 'additionalProperties' not in result and 'json_schema' not in result
    if mode == 'ref2va':
        headings = ['subject_definitions:', 'summary:', 'retention_analysis:', 'detailed_description:', 'overall_soundscape:', 'non_diegetic_music:']
    else:
        headings = ['integrated_multimodal_description:', 'overall_soundscape:', 'non_diegetic_music:']
        assert 'Picture 2 (from Shot FINAL_SHOT)' in result
        assert 'ask for the last image or permission to use I2VA' in result
    assert [result.index(item) for item in headings] == sorted(result.index(item) for item in headings)


@pytest.mark.parametrize('mode', ['i2va', 'l2va', 't2va'])
def test_additional_modes_keep_their_distinct_alignment_rules(mode):
    result = export_system_prompt(mode=mode)
    assert 'integrated_multimodal_description:' in result
    if mode == 'i2va':
        assert 'For the target video, at 0.00 seconds' in result
    elif mode == 'l2va':
        assert '<Picture 1> (from [Shot FINAL_SHOT])' in result
    else:
        assert 'Add no image alignment preface' in result


def test_custom_suffix_is_bounded_and_invalid_inputs_rejected():
    assert 'Use soft light.' in export_system_prompt('cinematic: Use soft light.')
    with pytest.raises(ValueError): export_system_prompt(mode='unknown')
    with pytest.raises(ValueError): export_system_prompt(persona='unknown')
    with pytest.raises(ValueError): export_system_prompt(persona='custom:' + 'x' * 2001)
