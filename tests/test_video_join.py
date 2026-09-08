"""Decode-only combine preflight; synthetic records, no network or GPU."""
import copy

import pytest

from backend.video_join import VideoJoinError, build_join_transfer
from test_comfy_transfer import assert_ui_api_parity, node_schema, schema as original_schema


@pytest.fixture
def schema():
    result = original_schema.__wrapped__()
    result['MMH3Load'] = {**node_schema({
        'file': [['(none)', *[f'output::mmh3/clip-{n}.mmh3' for n in range(1, 5)]]],
        'verify': [['on_access', 'manifest', 'full']], 'path_override': ['STRING', {}]}, ['MMH3_MEDIA']), 'output_name': ['packet']}
    result['MMH3H3LatentStitch'] = {**node_schema({'segments': ['COMFY_AUTOGROW_V3', {'template': {
        'input': {'required': {'segment': ['MMH3_MEDIA', {}]}}, 'names': [f'segment_{n}' for n in range(1, 67)], 'min': 2}}]},
        ['MMH3_MEDIA', 'LATENT', 'STRING', 'STRING']), 'output_name': ['packet', 'latent', 'summary', 'assembly_report_json']}
    return result


def records(count=2):
    result = []
    for index in range(1, count + 1):
        graph = {
            '1': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'minimax_h3_video_vae_fp16.safetensors'}},
            '2': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'minimax_h3_audio_vae_fp32.safetensors'}},
            '3': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sampler', 0], 'vae': ['1', 0]}},
            '4': {'class_type': 'VAEDecodeAudio', 'inputs': {'samples': ['sampler', 0], 'vae': ['2', 0]}},
            '5': {'class_type': 'CreateVideo', 'inputs': {'images': ['3', 0], 'audio': ['4', 0], 'fps': 24, 'bit_depth': 8, 'color_space': 'sRGB'}},
            '6': {'class_type': 'SaveVideo', 'inputs': {'video': ['5', 0]}},
        }
        media = {'latent_origin': 'sampler_output', 'continuation': index > 1, 'save_enabled': True}
        if index > 1:
            parent = f'mmh3/clip-{index-1}.mmh3'
            media.update(source=parent, overlap_frames=39)
            graph.update({
                '7': {'class_type': 'MMH3Load', 'inputs': {'file': 'output::' + parent, 'path_override': ''}},
                '8': {'class_type': 'MMH3H3ContinuationHandover', 'inputs': {'packet': ['7', 0], 'video_handover_frames': 39,
                    'audio_handover_frames': 0, 'audio_feather_frames': 0}},
            })
        result.append({'run': {'id': f'run-{index}', 'project_id': f'project-{index}', 'status': 'succeeded',
            'continuation_source': f'mmh3/clip-{index}.mmh3', 'parent_run_id': 'reroll-pointer-is-not-proof'},
            'transfer': {'id': f'transfer-{index}', 'comfy_url': 'http://127.0.0.1:8010', 'prompt': graph,
                'manifest': {'project_id': f'project-{index}', 'mode': 'ref2va', 'width': 736, 'height': 416, 'frames': 124, 'fps': 24, 'mmh3': media}}})
    return result


def node(graph, kind):
    return next((key, value) for key, value in graph.items() if value['class_type'] == kind)


def test_build_two_clip_f05_one_av_decode_no_diffusion_or_continuable_state(schema):
    entries = records(); original = copy.deepcopy(entries)
    result = build_join_transfer(entries, schema)
    graph, manifest = result['prompt'], result['manifest']
    assert entries == original
    assert manifest['frames'] == 209
    assert manifest['duration'] == 209 / 24
    assert manifest['source_runs'][1]['appended_frames'] == 85
    assert manifest['project_id'] == 'project-2'
    assert manifest['operation'] == 'combine'
    assert manifest['supports_continuation'] is False
    assert manifest['continuation_source'] is None
    assert manifest['mmh3']['save_enabled'] is False
    assert manifest['no_diffusion'] is True
    assert set(n['class_type'] for n in graph.values()) == {
        'MMH3Load', 'MMH3H3LatentStitch', 'VAELoader', 'VAEDecode', 'VAEDecodeAudio', 'CreateVideo', 'SaveVideo'}
    stitch_id, stitch = node(graph, 'MMH3H3LatentStitch')
    assert stitch['inputs'] == {'segments.segment_1': ['1', 0], 'segments.segment_2': ['2', 0]}
    assert node(graph, 'VAEDecode')[1]['inputs']['samples'] == [stitch_id, 1]
    assert node(graph, 'VAEDecodeAudio')[1]['inputs']['samples'] == [stitch_id, 1]
    assert [n['inputs']['verify'] for n in graph.values() if n['class_type'] == 'MMH3Load'] == ['full', 'full']
    assert_ui_api_parity(result, schema)


def test_three_clips_keep_exact_order_and_total_h3_frame_grid(schema):
    result = build_join_transfer(records(3), schema)
    assert result['manifest']['frames'] == 294
    assert result['manifest']['source_runs'][-1]['output_frame_start'] == 209
    assert result['manifest']['source_runs'][-1]['output_frame_end'] == 294
    assert list(node(result['prompt'], 'MMH3H3LatentStitch')[1]['inputs']) == ['segments.segment_1', 'segments.segment_2', 'segments.segment_3']
    assert_ui_api_parity(result, schema)


def test_unique_output_prefix_for_each_combine_click(schema):
    one, two = [build_join_transfer(records(), schema) for _ in range(2)]
    assert one['id'] != two['id']
    assert one['manifest']['output_prefix'] != two['manifest']['output_prefix']


@pytest.mark.parametrize('change,message', [
    (lambda r: r[1]['run'].update(status='running'), 'successful'),
    (lambda r: r[1]['run'].update(id='run-1'), 'distinct'),
    (lambda r: r[1]['run'].update(continuation_source=None), 'saved'),
    (lambda r: r[1]['run'].update(continuation_source='mmh3/deleted.mmh3'), 'output list'),
    (lambda r: r[1]['run'].update(continuation_source='mmh3/clip-1.mmh3'), 'own available'),
    (lambda r: r[1]['run'].update(project_id='wrong'), 'recorded project'),
    (lambda r: r[1]['transfer'].update(comfy_url='http://remote.example:8010'), 'computer'),
    (lambda r: r[1]['transfer'].update(comfy_url='http://127.0.0.1:8020'), 'same ComfyUI'),
    (lambda r: r[1]['transfer']['manifest'].update(width=960), 'same resolution'),
    (lambda r: r[1]['transfer']['manifest'].update(frames=120), 'frame-grid'),
    (lambda r: r[1]['transfer']['manifest'].update(fps=30), '24 fps'),
    (lambda r: r[1]['transfer']['manifest']['mmh3'].update(source='mmh3/clip-3.mmh3'), 'direct continuation'),
    (lambda r: r[1]['transfer']['manifest']['mmh3'].update(continuation=False), 'Independent'),
    (lambda r: r[1]['transfer']['manifest']['mmh3'].update(latent_origin='derived'), 'sampler state'),
    (lambda r: r[1]['transfer']['manifest']['mmh3'].update(overlap_frames=40), 'overlap'),
    (lambda r: r[1]['transfer']['manifest']['mmh3'].update(overlap_frames=141), 'overlap'),
    (lambda r: r[1]['transfer']['prompt']['8']['inputs'].update(audio_handover_frames=90), 'equal video/audio'),
    (lambda r: r[1]['transfer']['prompt']['8']['inputs'].update(audio_feather_frames=1), 'feathering'),
    (lambda r: r[1]['transfer']['prompt']['7']['inputs'].update(file='output::mmh3/clip-3.mmh3'), 'source loaded'),
    (lambda r: r[1]['transfer']['prompt']['7']['inputs'].update(path_override='C:/different.mmh3'), 'source loaded'),
    (lambda r: r[1]['transfer']['prompt']['1']['inputs'].update(vae_name='different.safetensors'), 'same video/audio VAEs'),
    (lambda r: r[1]['transfer']['prompt']['5']['inputs'].update(bit_depth=10), 'bit depth'),
])
def test_rejects_unrelated_incomplete_ambiguous_or_incompatible_runs(schema, change, message):
    entries = records(); change(entries)
    with pytest.raises(VideoJoinError, match=message):
        build_join_transfer(entries, schema)


def test_missing_node_or_unsupported_count_fails_without_mutating_inputs(schema):
    entries = records(); original = copy.deepcopy(entries)
    del schema['MMH3H3LatentStitch']
    with pytest.raises(VideoJoinError, match='unavailable or changed'):
        build_join_transfer(entries, schema)
    assert entries == original
    with pytest.raises(VideoJoinError, match='at least two'):
        build_join_transfer(entries[:1], schema)


@pytest.fixture
def decoded_schema(schema):
    result = copy.deepcopy(schema)
    # Current native CreateVideo permits a picture-only intermediate stream.
    create_inputs = result['CreateVideo']['input']
    create_inputs['optional']['audio'] = create_inputs['required'].pop('audio')
    result['MMH3Unpack'] = {**node_schema({'packet': ['MMH3_MEDIA', {}]}, ['LATENT', 'VIDEO', 'AUDIO']),
                           'output_name': ['latent', 'video', 'audio']}
    result['Video Slice'] = {**node_schema({'video': ['VIDEO', {}], 'start_time': ['FLOAT', {}],
                            'duration': ['FLOAT', {}], 'strict_duration': ['BOOLEAN', {}]}, ['VIDEO']), 'output_name': ['VIDEO']}
    result['GetVideoComponents'] = {**node_schema({'video': ['VIDEO', {}]}, ['IMAGE', 'AUDIO', 'FLOAT', 'COMBO', 'COMBO']),
                                    'output_name': ['images', 'audio', 'fps', 'bit_depth', 'color_space']}
    result['TrimAudioDuration'] = {**node_schema({'audio': ['AUDIO', {}], 'start_index': ['FLOAT', {}],
                                    'duration': ['FLOAT', {}]}, ['AUDIO']), 'output_name': ['AUDIO']}
    result['MMH3PackH3Result'] = {**node_schema({'packet': ['MMH3_MEDIA', {}], 'video': ['VIDEO', {}],
        'audio': ['AUDIO', {}], 'operation': ['STRING', {}], 'mode': ['STRING', {}], 'status': ['STRING', {}],
        'process_info_json': ['STRING', {}], 'latent_origin': [['derived', 'sampler_output']]}, ['MMH3_MEDIA']), 'output_name': ['packet']}
    fields = copy.deepcopy(result['MMH3H3LatentStitch']['input']['required'])
    fields.update(transition=[['Cut', 'Crossfade', 'Auto Seamless']], transition_frames=['INT', {}],
                  color_match=[['Off', 'Auto', 'Always']], color_match_strength=['FLOAT', {}], color_match_decay_frames=['INT', {}])
    result['MMH3VideoStitch'] = {**node_schema(fields, ['MMH3_MEDIA', 'VIDEO', 'AUDIO', 'STRING', 'STRING']),
                               'output_name': ['packet', 'video', 'audio', 'summary', 'assembly_report_json']}
    return result


def mixed_records():
    entries = records(4)
    for entry, frames in zip(entries, (124, 124, 107, 107)):
        entry['transfer']['manifest']['frames'] = frames
        entry['transfer']['manifest']['mmh3']['pack_node_id'] = '9'
        entry['transfer']['prompt']['9'] = {'class_type': 'MMH3PackH3Result',
                                            'inputs': {'video': ['5', 0], 'audio': ['4', 0]}}
    return entries


def test_mixed_four_clip_audio_phase_uses_decoded_av_without_vaes(decoded_schema):
    entries = mixed_records(); original = copy.deepcopy(entries)
    result = build_join_transfer(entries, decoded_schema)
    graph, manifest = result['prompt'], result['manifest']
    assert entries == original
    assert manifest['frames'] == 345 and manifest['actual_duration'] == 14.375
    assert manifest['assembly_mode'] == 'streaming_decoded' and manifest['no_diffusion']
    assert manifest['audio_boundary_preflight']['boundaries'][2] == {
        'segment_index': 2, 'available_audio_ticks': 113, 'needed_audio_ticks': 114, 'shortfall_ticks': 1}
    assert not manifest['audio_boundary_preflight']['latent_safe']
    assert not any(n['class_type'] in ('MMH3H3LatentStitch', 'VAELoader', 'VAEDecode', 'VAEDecodeAudio', 'SamplerCustomAdvanced') for n in graph.values())
    trims = [n['inputs'] for n in graph.values() if n['class_type'] == 'Video Slice']
    assert [n['start_time'] for n in trims] == [39/24] * 3
    assert [n['duration'] for n in trims] == [85/24, 68/24, 68/24]
    assert all(n['strict_duration'] is False for n in trims)
    audio_trims = [n['inputs'] for n in graph.values() if n['class_type'] == 'TrimAudioDuration']
    assert [n['start_index'] for n in audio_trims] == [39/24] * 3
    assert [n['duration'] for n in audio_trims] == [85/24, 68/24, 68/24]
    stitch_id, stitch = node(graph, 'MMH3VideoStitch')
    assert stitch['inputs']['transition'] == 'Cut' and stitch['inputs']['transition_frames'] == 0
    assert stitch['inputs']['color_match'] == 'Off'
    assert node(graph, 'SaveVideo')[1]['inputs']['video'] == [stitch_id, 1]
    assert [n['inputs']['file'] for n in graph.values() if n['class_type'] == 'MMH3Load'] == [f'output::mmh3/clip-{n}.mmh3' for n in range(1,5)]
    assert manifest['source_runs'][-1]['run_id'] == 'run-4'
    assert manifest['project_id'] == 'project-4' and manifest['continuation_source'] is None
    assert manifest['supports_continuation'] is False and not manifest['mmh3']['save_enabled']
    assert_ui_api_parity(result, decoded_schema)


def test_decoded_trim_accepts_truncated_mp4_duration_without_losing_frame(decoded_schema):
    result = build_join_transfer(mixed_records(), decoded_schema)
    trim = [n['inputs'] for n in result['prompt'].values() if n['class_type'] == 'Video Slice'][1]
    # Real native VideoFromFile semantics: container.duration is microseconds,
    # whereas stream.duration * time_base retains the exact 107/24 seconds.
    truncated_container_duration = 4.458333
    exact_stream_duration = 107 / 24
    actual_duration = min(trim['duration'], truncated_container_duration - trim['start_time'])
    assert actual_duration < trim['duration']  # strict mode rejected this valid clip
    assert not (trim['strict_duration'] and actual_duration < trim['duration'])
    estimated_frames = round(min(trim['duration'], exact_stream_duration - trim['start_time']) * 24)
    assert estimated_frames == 68
    assert trim['start_time'] == 39 / 24 and trim['duration'] == 68 / 24


def test_decoded_trim_materializes_frames_before_streaming_source_access(decoded_schema):
    result = build_join_transfer(mixed_records(), decoded_schema)
    graph = result['prompt']
    packs = [n for n in graph.values() if n['class_type'] == 'MMH3PackH3Result']
    assert len(packs) == 3
    for pack in packs:
        video_id, slot = pack['inputs']['video']
        video = graph[video_id]
        assert slot == 0 and video['class_type'] == 'CreateVideo'
        assert video['inputs']['fps'] == 24
        # Audio stays in its separate precisely trimmed PCM packet. The
        # intermediate stream needs only the images, avoiding extra AAC work.
        assert 'audio' not in video['inputs']
        components_id, slot = video['inputs']['images']
        assert slot == 0 and graph[components_id]['class_type'] == 'GetVideoComponents'
        slice_id, slot = graph[components_id]['inputs']['video']
        assert slot == 0 and graph[slice_id]['class_type'] == 'Video Slice'
        assert graph[slice_id]['inputs']['start_time'] == 39 / 24
        assert graph[slice_id]['inputs']['strict_duration'] is False
    assert_ui_api_parity(result, decoded_schema)


def test_exact_phase_clips_keep_original_latent_path_with_decoded_nodes_available(decoded_schema):
    result = build_join_transfer(records(3), decoded_schema)
    assert result['manifest']['assembly_mode'] == 'latent'
    assert result['manifest']['audio_boundary_preflight']['latent_safe']
    assert any(n['class_type'] == 'MMH3H3LatentStitch' for n in result['prompt'].values())


@pytest.mark.parametrize('change', [
    lambda r: r[2]['transfer']['manifest']['mmh3'].update(pack_node_id='wrong'),
    lambda r: r[2]['transfer']['prompt']['9']['inputs'].update(audio=['other', 0]),
    lambda r: r[2]['transfer']['prompt']['9']['inputs'].pop('video'),
])
def test_decoded_fallback_requires_exact_archived_video_audio(decoded_schema, change):
    entries = mixed_records(); change(entries)
    with pytest.raises(VideoJoinError, match='exact displayed video/audio'):
        build_join_transfer(entries, decoded_schema)


def test_decoded_fallback_never_bypasses_lineage_or_missing_node_check(decoded_schema):
    entries = mixed_records()
    entries[2]['transfer']['manifest']['mmh3']['source'] = 'mmh3/clip-1.mmh3'
    with pytest.raises(VideoJoinError, match='direct continuation'):
        build_join_transfer(entries, decoded_schema)
    del decoded_schema['Video Slice']
    with pytest.raises(VideoJoinError, match='decoded audio-safe join'):
        build_join_transfer(mixed_records(), decoded_schema)
