"""Build a decode-only F05 graph from an ordered, owned continuation chain.

No network, media reads, model loading or queue submissions. The caller resolves
successful run records; MMH3 validates immutable packet lineage at execution.
"""
from __future__ import annotations

import json
import uuid

from .comfy_transfer import TransferError, _materialize_ui_defaults, _ui_workflow, _validate_graph
from .mmh3_transfer import OVERLAPS, _source_selector
from .resources import local_url


class VideoJoinError(TransferError):
    pass


def _one(graph, kind):
    nodes = [(ident, node) for ident, node in graph.items() if node.get('class_type') == kind]
    if len(nodes) != 1:
        raise VideoJoinError(f'Combining clips requires one verified {kind} path in each saved workflow.')
    return nodes[0]


def _linked(graph, node, input_name, kind):
    value = node.get('inputs', {}).get(input_name)
    if (not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], str)
            or value[1] != 0 or graph.get(value[0], {}).get('class_type') != kind):
        raise VideoJoinError(f'The saved {input_name} connection no longer identifies the expected {kind}.')
    return graph[value[0]]


def _decode_settings(graph):
    _, save = _one(graph, 'SaveVideo')
    video = _linked(graph, save, 'video', 'CreateVideo')
    video_decode = _linked(graph, video, 'images', 'VAEDecode')
    audio_decode = _linked(graph, video, 'audio', 'VAEDecodeAudio')
    vaes = [_linked(graph, decoder, 'vae', 'VAELoader')['inputs'].get('vae_name')
            for decoder in (video_decode, audio_decode)]
    if not all(isinstance(name, str) and name for name in vaes) or video['inputs'].get('fps') != 24:
        raise VideoJoinError('The saved workflow must identify its video/audio VAEs and 24 fps directly.')
    return tuple(vaes), video['inputs'].get('bit_depth', 8), video['inputs'].get('color_space', 'sRGB')


def _segment_ports(schema, count, kind='MMH3H3LatentStitch'):
    definition = schema.get(kind, {})
    for field, spec in definition.get('input', {}).get('required', {}).items():
        if spec[0] != 'COMFY_AUTOGROW_V3':
            continue
        template = spec[1].get('template', {})
        child = list(template.get('input', {}).get('required', {}).values())
        names = template.get('names')
        if len(child) == 1 and child[0][0] == 'MMH3_MEDIA' and isinstance(names, list):
            if not max(2, template.get('min', 2)) <= count <= len(names):
                raise VideoJoinError(f'Choose between 2 and {len(names)} linked clips to combine.')
            return [f'{field}.{name}' for name in names[:count]]
    raise VideoJoinError(f'The installed {kind} inputs are unavailable or changed. Refresh the H3 installation.')


def _audio_phase_preflight(facts):
    """Mirror installed H3's independently rounded 40Hz audio lengths."""
    absolute_frames = audio_ticks = 0
    boundaries = []
    for index, fact in enumerate(facts):
        frames, overlap = fact['frames'], fact['overlap_frames']
        available = round(frames * 40 / 24) - overlap * 40 // 24
        absolute_frames += frames - overlap
        end = round(absolute_frames * 40 / 24)
        needed = end - audio_ticks
        boundaries.append({'segment_index': index, 'available_audio_ticks': available,
                           'needed_audio_ticks': needed, 'shortfall_ticks': max(0, needed - available)})
        audio_ticks = end
    return {'latent_safe': all(b['shortfall_ticks'] == 0 for b in boundaries), 'boundaries': boundaries}


def _require_saved_decoded_av(entry):
    graph = entry['transfer']['prompt']
    ident, pack = _one(graph, 'MMH3PackH3Result')
    recorded = entry['transfer']['manifest']['mmh3'].get('pack_node_id')
    _, save = _one(graph, 'SaveVideo')
    create = _linked(graph, save, 'video', 'CreateVideo')
    if (recorded != ident or pack['inputs'].get('video') != save['inputs'].get('video')
            or pack['inputs'].get('audio') != create['inputs'].get('audio')):
        raise VideoJoinError('This chain needs its saved decoded video and audio. An older clip did not archive the exact displayed video/audio output.')


def build_join_transfer(runs_with_transfer, schema):
    """Accept [{run: successful_record, transfer: stored_transfer}, ...].

    run.continuation_source is that run's saved output; the source used to render
    it is transfer.manifest.mmh3.source. Reroll parent IDs are not lineage proof.
    """
    if not isinstance(runs_with_transfer, (list, tuple)) or len(runs_with_transfer) < 2:
        raise VideoJoinError('Combine needs at least two successful clips generated in sequence.')
    ports = _segment_ports(schema, len(runs_with_transfer))
    needed = {'MMH3Load', 'MMH3H3LatentStitch', 'VAELoader', 'VAEDecode', 'VAEDecodeAudio', 'CreateVideo', 'SaveVideo'}
    if not needed.issubset(schema):
        raise VideoJoinError('MMH3 Latent Stitch and the H3 video/audio decoders must be available before combining.')
    ids, selectors, facts = set(), [], []
    base = geometry = decode = None
    total_frames, previous_frames = 0, 0
    for index, entry in enumerate(runs_with_transfer):
        if not isinstance(entry, dict) or not isinstance(entry.get('run'), dict) or not isinstance(entry.get('transfer'), dict):
            raise VideoJoinError('A saved run record or its immutable workflow is missing.')
        run, transfer = entry['run'], entry['transfer']
        manifest, graph = transfer.get('manifest'), transfer.get('prompt')
        if not isinstance(manifest, dict) or not isinstance(graph, dict):
            raise VideoJoinError('A clip is missing its original prepared workflow.')
        if not all(isinstance(node, dict) and isinstance(node.get('inputs'), dict) for node in graph.values()):
            raise VideoJoinError('A saved workflow contains an unreadable node.')
        ident = run.get('id')
        if (not isinstance(ident, str) or not ident or ident in ids or run.get('status') != 'succeeded'
                or run.get('operation') in ('combine', 'join') or manifest.get('operation') in ('combine', 'join')):
            raise VideoJoinError('Choose distinct successful generation clips; a combined video is not a continuation source.')
        if not run.get('project_id') or manifest.get('project_id') != run['project_id']:
            raise VideoJoinError('A saved workflow does not belong to its recorded project.')
        ids.add(ident)
        try:
            current_base = local_url(transfer.get('comfy_url', ''))
            selector = _source_selector(run.get('continuation_source'), schema)
        except (ValueError, TypeError) as exc:
            raise VideoJoinError(str(exc)) from exc
        if not selector or selector in selectors:
            raise VideoJoinError('Every clip needs its own available .mmh3 working file.')
        if manifest.get('comfy_url', current_base) != current_base or (base and current_base != base):
            raise VideoJoinError('All clips must come from the same ComfyUI installation.')
        width, height, frames, fps = (manifest.get(key) for key in ('width', 'height', 'frames', 'fps'))
        if (any(type(value) is not int for value in (width, height, frames)) or width < 32 or height < 32
                or width % 32 or height % 32 or frames < 5 or (frames - 5) % 17 or fps != 24):
            raise VideoJoinError('Every clip must have recorded H3 frame-grid geometry at 24 fps.')
        if geometry and geometry != (width, height):
            raise VideoJoinError('All linked clips must have the same resolution to combine in latent space.')
        current_decode = _decode_settings(graph)
        if decode and decode != current_decode:
            raise VideoJoinError('Linked clips must use the same video/audio VAEs, bit depth and color space.')
        media = manifest.get('mmh3', {})
        if not isinstance(media, dict) or media.get('latent_origin') != 'sampler_output':
            raise VideoJoinError('A direct H3 sampler state is required for every combined segment.')
        overlap = 0
        if index:
            try:
                parent = _source_selector(media.get('source'), schema)
            except (ValueError, TypeError) as exc:
                raise VideoJoinError('The next clip no longer identifies its exact saved parent.') from exc
            if not media.get('continuation') or parent != selectors[-1]:
                raise VideoJoinError('The clips are not in direct continuation order. Independent clips or skipped parents cannot be latent-joined.')
            overlap = media.get('overlap_frames')
            if type(overlap) is not int or overlap not in OVERLAPS or overlap >= frames or overlap > previous_frames:
                raise VideoJoinError('The saved continuation overlap is missing or does not fit both clips.')
            _, handover = _one(graph, 'MMH3H3ContinuationHandover')
            hand = handover['inputs']
            if (hand.get('video_handover_frames') != overlap or hand.get('audio_handover_frames') not in (0, overlap)
                    or hand.get('audio_feather_frames') != 0):
                raise VideoJoinError('Latent combining requires equal video/audio context with no audio feathering.')
            loader = _linked(graph, handover, 'packet', 'MMH3Load')
            if loader['inputs'].get('file') != parent or loader['inputs'].get('path_override', ''):
                raise VideoJoinError('The recorded continuation source differs from the source loaded by its workflow.')
        start = total_frames
        total_frames += frames - overlap
        facts.append({'run_id': ident, 'project_id': run['project_id'], 'source': selector.removeprefix('output::'),
                      'frames': frames, 'overlap_frames': overlap, 'appended_frames': frames - overlap,
                      'output_frame_start': start, 'output_frame_end': total_frames})
        selectors.append(selector)
        base, geometry, decode, previous_frames = current_base, (width, height), current_decode, frames

    transfer_id = str(uuid.uuid4())
    audio_phase = _audio_phase_preflight(facts)
    decoded = not audio_phase['latent_safe']
    if decoded:
        needed = {'MMH3Unpack', 'Video Slice', 'GetVideoComponents', 'TrimAudioDuration', 'MMH3PackH3Result', 'MMH3VideoStitch'}
        if not needed.issubset(schema):
            raise VideoJoinError('These clip lengths need the decoded audio-safe join. Update MMH3/ComfyUI to provide Video Stitch, Video Slice, Get Video Components and audio trimming.')
        ports = _segment_ports(schema, len(facts), 'MMH3VideoStitch')
        for entry in runs_with_transfer:
            _require_saved_decoded_av(entry)
    graph = {}
    def add(kind, inputs, title, group):
        ident = str(len(graph) + 1)
        graph[ident] = {'class_type': kind, 'inputs': inputs, '_meta': {'title': title, 'benchmark_group': group}}
        return ident
    def output(ident, name, dtype):
        definition = schema[graph[ident]['class_type']]
        names = definition.get('output_name', definition.get('output', []))
        if name not in names or definition['output'][names.index(name)] != dtype:
            raise VideoJoinError(f'The installed {graph[ident]["class_type"]} {name} output changed.')
        return [ident, names.index(name)]
    inputs = {}
    for index, (port, selector) in enumerate(zip(ports, selectors)):
        ident = add('MMH3Load', {'file': selector, 'verify': 'full', 'path_override': ''}, f'Clip {index + 1} · exact saved state', 'Inputs')
        overlap = facts[index]['overlap_frames']
        if decoded and overlap:
            unpack = add('MMH3Unpack', {'packet': output(ident, 'packet', 'MMH3_MEDIA')},
                         f'Clip {index + 1} · original decoded picture and sound', 'Inputs')
            seconds = (facts[index]['frames'] - overlap) / 24
            # MP4 container duration is truncated to microseconds. Keep the exact
            # frame window, but allow its end to reach EOF a fraction early.
            # duration=0 is unsuitable: native frame-count estimation uses it
            # literally and may report one frame for a trimmed file.
            video = add('Video Slice', {'video': output(unpack, 'video', 'VIDEO'),
                         'start_time': overlap / 24, 'duration': seconds, 'strict_duration': False},
                        f'Remove {overlap} repeated video frames', 'Conditioning')
            # MMH3's streaming adapter decodes get_stream_source() directly,
            # which is still the original file for Video Slice. Materialize
            # the exact trimmed frames so that stream and metadata agree.
            components = add('GetVideoComponents', {'video': output(video, 'VIDEO', 'VIDEO')},
                             f'Clip {index + 1} · extract only the new frames', 'Conditioning')
            video = add('CreateVideo', {'images': output(components, 'images', 'IMAGE'),
                        'fps': 24, 'bit_depth': decode[1], 'color_space': decode[2]},
                        f'Clip {index + 1} · materialized trimmed video', 'Conditioning')
            audio = add('TrimAudioDuration', {'audio': output(unpack, 'audio', 'AUDIO'),
                         'start_index': overlap / 24, 'duration': seconds},
                        f'Remove the same {overlap / 24:g}s from the sound', 'Conditioning')
            ident = add('MMH3PackH3Result', {'packet': output(ident, 'packet', 'MMH3_MEDIA'),
                        'video': output(video, 'VIDEO', 'VIDEO'), 'audio': output(audio, 'AUDIO', 'AUDIO'),
                        'operation': 'trim_continuation_prefix', 'mode': 'decoded_video_join', 'status': '',
                        'process_info_json': json.dumps({'source_run_id': facts[index]['run_id'], 'removed_context_frames': overlap}),
                        'latent_origin': 'derived'}, f'Clip {index + 1} · new footage only', 'Conditioning')
        inputs[port] = output(ident, 'packet', 'MMH3_MEDIA')
    if decoded:
        inputs.update(transition='Cut', transition_frames=0, color_match='Off',
                      color_match_strength=0.0, color_match_decay_frames=0)
        stitch = add('MMH3VideoStitch', inputs, 'Join decoded clips · align sound to the absolute timeline', 'Conditioning')
        save_input = output(stitch, 'video', 'VIDEO')
    else:
        stitch = add('MMH3H3LatentStitch', inputs, 'Join continuation chain · remove duplicated motion and sound', 'Conditioning')
        video_vae = add('VAELoader', {'vae_name': decode[0][0]}, 'Original video VAE', 'Models')
        audio_vae = add('VAELoader', {'vae_name': decode[0][1]}, 'Original audio VAE', 'Models')
        video = add('VAEDecode', {'samples': output(stitch, 'latent', 'LATENT'), 'vae': output(video_vae, 'VAE', 'VAE')}, 'Decode joined video once', 'Output')
        audio = add('VAEDecodeAudio', {'samples': output(stitch, 'latent', 'LATENT'), 'vae': output(audio_vae, 'VAE', 'VAE')}, 'Decode joined audio once', 'Output')
        create = add('CreateVideo', {'images': output(video, 'IMAGE', 'IMAGE'), 'audio': output(audio, 'AUDIO', 'AUDIO'),
                                   'fps': 24, 'bit_depth': decode[1], 'color_space': decode[2]},
                     f'Combined film · {total_frames / 24:.3f}s · {geometry[0]}×{geometry[1]}', 'Output')
        save_input = output(create, 'VIDEO', 'VIDEO')
    prefix = 'h3_prompt_studio/combined/' + transfer_id
    save = add('SaveVideo', {'video': save_input, 'filename_prefix': prefix,
                            'format': 'mp4', 'format.codec': 'h264'}, 'Save combined video', 'Output')
    _materialize_ui_defaults(graph, schema)
    _validate_graph(graph, schema, set())
    title = f'Combined continuation · {len(facts)} clips · {total_frames / 24:.3f}s'
    workflow = _ui_workflow(graph, schema, transfer_id, title)
    manifest = {'transfer_id': transfer_id, 'title': title, 'operation': 'combine', 'mode': 'joined',
        'project_id': facts[-1]['project_id'], 'comfy_url': base, 'width': geometry[0], 'height': geometry[1],
        'frames': total_frames, 'fps': 24, 'duration': total_frames / 24, 'actual_duration': total_frames / 24,
        'megapixels': geometry[0] * geometry[1] / 1_000_000, 'steps': 0, 'seed': None, 'images': [],
        'output_prefix': prefix, 'video_save_node_id': save, 'stitch_node_id': stitch, 'source_runs': facts,
        'queued': False, 'no_diffusion': True, 'continuation_source': None, 'supports_continuation': False,
        'assembly_mode': 'streaming_decoded' if decoded else 'latent', 'audio_boundary_preflight': audio_phase,
        'audio_normalization': ('Decoded PCM is trimmed or time-conformed by at most one video frame per segment to absolute32kHz boundaries; no audio latent padding.' if decoded else 'Original40Hz latent audio, trimmed at absolute timeline boundaries.'),
        'lineage_validation': ('Recorded exact source order and saved AV connections verified; archives are fully verified when loaded.' if decoded else 'Recorded source order verified; MMH3 checks immutable packet/resource/revision lineage at execution.'),
        'duration_note': f'{len(facts)} clips joined into {total_frames} frames / {total_frames / 24:.3f}s, with repeated continuation context removed.',
        'mmh3': {'available': True, 'save_enabled': False, 'saved': False, 'continuation': False, 'latent_origin': 'derived'}}
    return {'id': transfer_id, 'prompt': graph, 'workflow': workflow, 'manifest': manifest, 'comfy_url': base}
