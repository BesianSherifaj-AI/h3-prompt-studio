"""Native H3 frame grid budgets distinguish new footage from preserved context."""
import math

import pytest

from backend.video_timing import frame_budget


def test_legacy_five_seconds_keeps_the_existing_total_frame_grid():
    assert frame_budget(5, {}) == {'frames': 124, 'overlap_frames': 0, 'new_frames': 124,
                                    'new_seconds': 124 / 24, 'duration_basis': 'total'}
    continuation = frame_budget(5, {'continuation_source': 'mmh3/source.mmh3'})
    assert continuation['frames'] == 124 and continuation['new_frames'] == 85
    assert continuation['new_seconds'] == pytest.approx(3.5416666667)


def test_five_new_seconds_adds_the_preserved_context_before_rounding():
    result = frame_budget(5, {'continuation_source': 'mmh3/source.mmh3', 'duration_basis': 'new_footage'})
    assert result['frames'] == 175 and result['overlap_frames'] == 39 and result['new_frames'] == 136
    assert result['new_seconds'] == pytest.approx(5.6666666667)


def test_a_new_scene_has_no_overlap_even_if_a_previous_overlap_setting_remains():
    result = frame_budget(5, {'duration_basis': 'new_footage', 'continuation_overlap_frames': 39})
    assert result['frames'] == result['new_frames'] == 124 and result['overlap_frames'] == 0


@pytest.mark.parametrize('seconds', range(4, 14))
def test_supported_new_footage_lengths_fit_the_native_grid_without_underdelivery(seconds):
    result = frame_budget(seconds, {'duration_basis': 'new_footage', 'continuation_source': 'mmh3/source.mmh3'})
    assert result['frames'] <= 362 and (result['frames'] - 5) % 17 == 0
    assert seconds <= result['new_seconds'] < seconds + 17 / 24
    assert result['new_frames'] + result['overlap_frames'] == result['frames']


def test_thirteen_new_seconds_is_supported_but_fourteen_and_fifteen_exceed_the_clip_limit():
    settings = {'duration_basis': 'new_footage', 'continuation_source': 'mmh3/source.mmh3'}
    assert frame_budget(13, settings)['frames'] == 362
    for seconds in (14, 15):
        with pytest.raises(ValueError, match='too long'):
            frame_budget(seconds, settings)
    assert frame_budget(15, {})['frames'] == 362


@pytest.mark.parametrize('duration', [None, True, '5', 3, 16, 4.25, math.nan, math.inf])
def test_invalid_duration_is_rejected_before_rendering(duration):
    with pytest.raises(ValueError, match='whole clip length'):
        frame_budget(duration, {})


@pytest.mark.parametrize('overlap', [-1, True, '39', None])
def test_invalid_overlap_is_rejected(overlap):
    with pytest.raises(ValueError, match='context length'):
        frame_budget(5, {'continuation_source': 'mmh3/source.mmh3', 'continuation_overlap_frames': overlap})


def test_unknown_duration_basis_is_rejected():
    with pytest.raises(ValueError, match='duration basis'):
        frame_budget(5, {'duration_basis': 'guess'})
