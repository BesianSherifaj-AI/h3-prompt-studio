"""Add saved joint AV state and real MMH3 continuations to a prepared H3 graph.

No model is loaded and no graph is queued here. MMH3 remains an optional
ComfyUI node dependency; ordinary transfers work without it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from pathlib import PurePosixPath

import httpx


class MMH3TransferError(ValueError):
    pass


SAVE_NODES = frozenset({'MMH3Create', 'MMH3Inspect', 'MMH3PackH3Result', 'MMH3Save'})
CONTINUATION_NODES = frozenset({'MMH3Create', 'MMH3Inspect', 'MMH3Load', 'MMH3H3GenerationSettings',
                              'MMH3H3ContinuationHandover', 'MMH3H3ContinuationCondition'})
OVERLAPS = (39, 90, 141, 192, 243, 294, 345)


def _choices(spec):
    if not isinstance(spec, (list, tuple)) or not spec:
        return []
    if isinstance(spec[0], list):
        return spec[0]
    return spec[1].get('options', []) if len(spec) > 1 and isinstance(spec[1], dict) else []


def _source_selector(value, schema):
    if not isinstance(value, str):
        raise MMH3TransferError('Choose a saved .mmh3 file from the ComfyUI output list.')
    if not value.strip():
        return None
    value = value.strip().replace('\\', '/')
    relative = value.removeprefix('output::')
    path = PurePosixPath(relative)
    if (path.is_absolute() or ':' in relative or '\x00' in relative or
            any(part in ('', '.', '..') for part in relative.split('/')) or
            path.suffix.lower() != '.mmh3'):
        raise MMH3TransferError('Choose a saved .mmh3 file from the ComfyUI output list.')
    selector = 'output::' + relative
    spec = schema.get('MMH3Load', {}).get('input', {}).get('required', {}).get('file')
    if selector not in _choices(spec):
        raise MMH3TransferError('That saved video state is no longer in ComfyUI’s output list. Refresh the connection and choose it again.')
    return selector


def mmh3_options(schema):
    available = SAVE_NODES.issubset(schema)
    spec = schema.get('MMH3Load', {}).get('input', {}).get('required', {}).get('file')
    sources = []
    for value in _choices(spec):
        if not isinstance(value, str) or not value.startswith('output::'):
            continue
        try:
            selector = _source_selector(value, schema)
        except MMH3TransferError:
            continue
        relative = selector.removeprefix('output::')
        sources.append({'value': relative, 'selector': selector, 'label': relative})
    return {
        'mmh3_available': available,
        'mmh3_save_available': available,
        'mmh3_continuation_available': CONTINUATION_NODES.issubset(schema),
        'mmh3_sources': sources,
        'mmh3_overlap_frames': list(OVERLAPS),
        'mmh3_default_overlap_frames': 39,
        'mmh3_note': ('Save the working .mmh3 file beside the video to continue its actual motion and audio later.'
                      if available else 'MMH3 saving becomes available after its nodes are installed and ComfyUI is restarted.'),
    }


def read_mmh3_source(client, base, settings, schema):
    """Inspect one selected archive's manifest over the local ComfyUI API."""
    source = settings.get('continuation_source')
    if not source:
        return None
    if not CONTINUATION_NODES.issubset(schema):
        raise MMH3TransferError('Saved-state continuation needs the MMH3 nodes. Restart the H3 ComfyUI installation after installing them.')
    selector = _source_selector(source, schema)
    try:
        response = client.get(base + '/mmh3_media/file_info', params={'file': selector}, timeout=20)
        response.raise_for_status()
        card = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise MMH3TransferError('The saved video state could not be inspected. Refresh ComfyUI and choose the .mmh3 file again.') from exc
    geometry = card.get('geometry') if isinstance(card, dict) else None
    if not isinstance(geometry, dict) or not card.get('has', {}).get('latent'):
        raise MMH3TransferError('This file has no saved H3 latent state. Generate once with “Save continuation state” enabled, then select that .mmh3 file.')
    width, height, frames, fps = (geometry.get(key) for key in ('width', 'height', 'frames', 'fps'))
    if (any(type(v) is not int for v in (width, height, frames)) or
            width < 32 or height < 32 or width % 32 or height % 32 or
            frames < 5 or (frames - 5) % 17 or fps != 24):
        raise MMH3TransferError('This saved state does not have the expected H3 frame grid, resolution and 24 fps. Use a direct H3 sampler result.')
    return {'selector': selector, 'id': card.get('id'), 'name': card.get('name'),
            'revision': card.get('revision'), 'geometry': copy.deepcopy(geometry)}


def _one(graph, kinds, label):
    found = [(key, node) for key, node in graph.items() if node.get('class_type') in kinds]
    if len(found) != 1:
        raise MMH3TransferError(f'The H3 recipe must contain one {label} to save and continue it safely.')
    return found[0]


def _port(schema, kind, name, expected):
    definition = schema.get(kind, {})
    names = definition.get('output_name', definition.get('output', []))
    types = definition.get('output', [])
    matches = [index for index, label in enumerate(names) if label == name]
    if not matches:
        matches = [index for index, dtype in enumerate(types) if dtype == expected]
    if len(matches) != 1 or types[matches[0]] != expected:
        raise MMH3TransferError(f'The installed {kind} output {name} changed. Refresh or update the MMH3 nodes.')
    return matches[0]


def _reference_image_prefix(schema, media_type='IMAGE'):
    task = schema.get('MMH3Create', {}).get('input', {}).get('required', {}).get('task')
    choice = next((item for item in _choices(task) if isinstance(item, dict) and item.get('key') == 'References to video'), None)
    if not choice:
        raise MMH3TransferError('The installed MMH3 Create node has no reference-video task.')
    matches = []
    for group in ('required', 'optional'):
        for name, spec in choice.get('inputs', {}).get(group, {}).items():
            if not isinstance(spec, list) or spec[0] != 'COMFY_AUTOGROW_V3':
                continue
            options = spec[1]
            template = options.get('template', {})
            fields = template.get('input', {})
            child = list(fields.get('required', {}).values()) + list(fields.get('optional', {}).values())
            prefix = template.get('prefix', options.get('prefix'))
            if len(child) == 1 and child[0][0] == media_type and isinstance(prefix, str):
                matches.append('task.' + name + '.' + prefix)
    if len(matches) != 1:
        raise MMH3TransferError(f'The MMH3 reference-{media_type.lower()} inputs changed. Refresh the installed node schema before sending.')
    return matches[0]


def _ordered_loras(graph, sampler):
    """Follow the model chain backwards, then record adapters in application order."""
    guider = graph.get(sampler['inputs'].get('guider', [''])[0], {})
    link = guider.get('inputs', {}).get('model')
    chain, visited = [], set()
    while isinstance(link, list) and len(link) == 2 and link[0] in graph:
        key = link[0]
        if key in visited:
            raise MMH3TransferError('The model chain has a circular connection.')
        visited.add(key)
        node = graph[key]
        inputs = node.get('inputs', {})
        if node['class_type'] == 'LoraLoaderModelOnly':
            name = inputs.get('lora_name', '')
            strength = inputs.get('strength_model')
            if not isinstance(name, str) or type(strength) not in (int, float) or not math.isfinite(strength):
                raise MMH3TransferError('The LoRA stack could not be recorded accurately for this saved state.')
            chain.append({'name': name, 'strength_model': strength, 'strength_clip': None,
                          'sha256': None, 'purpose': 'acceleration' if 'turbo' in name.lower() else 'unknown',
                          'loader': 'LoraLoaderModelOnly', 'reapply_for_high_sigma': True})
        link = inputs.get('model')
    return list(reversed(chain))


def repair_studio_controls(graph, schema, manifest):
    """Return a repaired copy of a verified Studio graph; never execute it.

    The visible Create node is authoritative. Prompt/image contents are neither
    interpreted nor returned separately from the caller's own graph.
    """
    if not isinstance(graph, dict) or not isinstance(manifest, dict):
        raise MMH3TransferError('Select an exported Prompt Studio workflow to repair.')
    try:
        uuid.UUID(str(manifest.get('transfer_id', '')))
    except ValueError as exc:
        raise MMH3TransferError('This workflow has no valid Prompt Studio transfer identity.') from exc
    prefix = manifest.get('output_prefix')
    info = manifest.get('mmh3')
    if (not isinstance(prefix, str) or not prefix.startswith('h3_prompt_studio/') or
            not isinstance(info, dict) or not info.get('available')):
        raise MMH3TransferError('This is not a saved-state Prompt Studio workflow.')
    if 'MMH3Inspect' not in schema:
        raise MMH3TransferError('The installed MMH3 nodes need MMH3 Inspect to share the prompt and seed.')
    work, updated = copy.deepcopy(graph), copy.deepcopy(manifest)
    packet_id, packet = _one(work, {'MMH3Create'}, 'shared prompt and seed control')
    if str(info.get('reference_packet_node_id')) != packet_id:
        raise MMH3TransferError('The shared controls do not match this workflow’s Studio metadata.')
    controls = packet.get('inputs', {})
    if (not isinstance(controls.get('prompt'), str) or type(controls.get('seed')) is not int or
            not 0 <= controls['seed'] <= 0xFFFFFFFFFFFFFFFF):
        raise MMH3TransferError('The visible prompt and seed must be local MMH3 Create controls before repair.')
    sampler_id, sampler = _one(work, {'SamplerCustomAdvanced'}, 'joint video and audio sampler')
    noise_id, noise = _one(work, {'RandomNoise'}, 'sampling noise source')
    guider_id, guider = _one(work, {'BasicGuider'}, 'H3 conditioning guider')
    if sampler['inputs'].get('noise') != [noise_id, 0] or sampler['inputs'].get('guider') != [guider_id, 0]:
        raise MMH3TransferError('The sampler has a different noise or conditioning branch. Repair it manually.')
    cond_id, condition = _one(work, {'MiniMaxH3ReferenceToVideo', 'MiniMaxH3ImageToVideo',
                                    'MMH3H3ContinuationCondition'}, 'H3 prompt node')
    if str(manifest.get('conditioning_node_id')) != cond_id or guider['inputs'].get('conditioning') != [cond_id, 0]:
        raise MMH3TransferError('The prompt node does not match this workflow’s sampling branch.')
    continuation = condition['class_type'] == 'MMH3H3ContinuationCondition'
    if bool(info.get('continuation')) != continuation:
        raise MMH3TransferError('The continuation metadata does not match the actual workflow.')
    if continuation:
        hand_id, _ = _one(work, {'MMH3H3ContinuationHandover'}, 'saved video handover')
        if (str(info.get('handover_node_id')) != hand_id or
                sampler['inputs'].get('latent_image') != [hand_id, _port(schema, 'MMH3H3ContinuationHandover', 'latent', 'LATENT')] or
                condition['inputs'].get('packet') != [packet_id, 0]):
            raise MMH3TransferError('The continuation has a different source or packet branch.')
    elif sampler['inputs'].get('latent_image') != [cond_id, 1]:
        raise MMH3TransferError('The native H3 sampler has a different latent branch.')
    _, video_saver = _one(work, {'SaveVideo'}, 'video output')
    if video_saver['inputs'].get('filename_prefix') != prefix:
        raise MMH3TransferError('The video output does not match the exported Studio workflow.')
    if info.get('save_enabled'):
        pack_id, pack = _one(work, {'MMH3PackH3Result'}, 'saved H3 result')
        _, save = _one(work, {'MMH3Save'}, 'saved continuation state')
        packet_source = [cond_id, _port(schema, condition['class_type'], 'packet', 'MMH3_MEDIA')] if continuation else [packet_id, 0]
        if (pack['inputs'].get('latent') != [sampler_id, 0] or pack['inputs'].get('packet') != packet_source or
                pack['inputs'].get('video') != video_saver['inputs'].get('video') or
                save['inputs'].get('packet') != [pack_id, 0] or
                save['inputs'].get('filename_prefix') != 'mmh3/' + prefix):
            raise MMH3TransferError('The saved state has a different sampling or archive branch.')
    inspectors = [(key, node) for key, node in work.items() if node['class_type'] == 'MMH3Inspect']
    if len(inspectors) > 1 or (inspectors and inspectors[0][1]['inputs'].get('packet') != [packet_id, 0]):
        raise MMH3TransferError('There are ambiguous metadata readers. Repair this edited workflow manually.')
    if inspectors:
        inspect_id = inspectors[0][0]
    else:
        if not all(isinstance(key, str) and key.isdigit() for key in work):
            raise MMH3TransferError('The workflow uses nonstandard node identifiers. Export a new Studio workflow.')
        inspect_id = str(max(map(int, work)) + 1)
        work[inspect_id] = {'class_type': 'MMH3Inspect', 'inputs': {'packet': [packet_id, 0]},
                            '_meta': {'title': 'Shared prompt and seed · automatic', 'benchmark_group': 'Conditioning'}}
    prompt_link = [inspect_id, _port(schema, 'MMH3Inspect', 'prompt', 'STRING')]
    seed_link = [inspect_id, _port(schema, 'MMH3Inspect', 'seed', 'INT')]
    previous_noise = noise['inputs'].get('noise_seed')
    resolved_seed = [cond_id, _port(schema, condition['class_type'], 'seed', 'INT')] if continuation else seed_link
    if type(previous_noise) is not int and previous_noise not in (seed_link, resolved_seed):
        raise MMH3TransferError('The sampling seed has another input connection. Repair this edited workflow manually.')
    prompt_name = 'prompt_override' if continuation else 'prompt'
    if not isinstance(condition['inputs'].get(prompt_name), str) and condition['inputs'].get(prompt_name) != prompt_link:
        raise MMH3TransferError('The prompt has another input connection. Repair this edited workflow manually.')
    if continuation:
        override = condition['inputs'].get('seed_override')
        if type(override) is not int and override != seed_link:
            raise MMH3TransferError('The continuation seed has another input connection. Repair it manually.')
        condition['inputs']['seed_override'] = seed_link
    condition['inputs'][prompt_name] = prompt_link
    noise['inputs']['noise_seed'] = resolved_seed
    packet.setdefault('_meta', {}).update(title='Edit prompt and seed · used by generation', benchmark_group='Conditioning')
    updated.update(prompt_control_node_id=packet_id, seed_control_node_id=packet_id, seed=controls['seed'],
                   prompt_sha256=hashlib.sha256(controls['prompt'].encode('utf-8')).hexdigest())
    updated['mmh3'].update(shared_controls_node_id=packet_id, inspect_node_id=inspect_id)
    return {'prompt': work, 'manifest': updated}


def apply_mmh3(graph, config, project, settings, schema):
    """Mutate a prepared API graph atomically; return metadata for its manifest."""
    availability = mmh3_options(schema)
    source = settings.get('continuation_source')
    requested_save = settings.get('save_mmh3', availability['mmh3_save_available'])
    if type(requested_save) is not bool:
        raise MMH3TransferError('Save continuation state must be switched on or off.')
    if settings.get('save_mmh3') is True and not availability['mmh3_save_available']:
        raise MMH3TransferError('Saving continuation state needs the MMH3 nodes. Restart ComfyUI after installing them, or switch off “Save continuation state” for this transfer.')
    save = requested_save and availability['mmh3_save_available']
    if source and not availability['mmh3_continuation_available']:
        raise MMH3TransferError('Saved-state continuation needs the MMH3 nodes. Restart the H3 ComfyUI installation after installing them.')
    if not save and not source:
        return {'mmh3': {'available': availability['mmh3_save_available'], 'save_enabled': False, 'saved': False, 'continuation': False,
                         'note': availability['mmh3_note'] if requested_save else 'Continuation state saving is off.'}}
    work = copy.deepcopy(graph)
    cond_id, native = _one(work, {'MiniMaxH3ImageToVideo', 'MiniMaxH3ReferenceToVideo'}, 'H3 prompt node')
    sampler_id, sampler = _one(work, {'SamplerCustomAdvanced'}, 'joint video and audio sampler')
    noise_id, noise = _one(work, {'RandomNoise'}, 'sampling noise source')
    if sampler['inputs'].get('noise') != [noise_id, 0]:
        raise MMH3TransferError('The H3 sampler must use the shared seed noise source to save its actual generation settings.')
    _, saver = _one(work, {'SaveVideo'}, 'video output')
    mode = config['mode']
    if source and mode not in ('ref2va', 't2va'):
        raise MMH3TransferError('For saved-state continuation, choose Reference photos or Text only. The saved video tail supplies the starting motion; a new first-frame photo would describe a different start.')
    counter = max(int(key) for key in work) + 1

    def add(kind, inputs, title, group='Output'):
        nonlocal counter
        key, counter = str(counter), counter + 1
        work[key] = {'class_type': kind, 'inputs': inputs,
                     '_meta': {'title': title, 'benchmark_group': group}}
        return key

    def output(key, name, dtype):
        return [key, _port(schema, work[key]['class_type'], name, dtype)]

    old = native['inputs']
    packet_inputs = {'prompt': old['prompt'], 'task': 'References to video' if mode == 'ref2va' else 'Video (optional frames)',
                     'seed': config['seed'], 'name': str(project.get('title') or 'Prompt Studio film'),
                     'notes': 'Created by H3 Prompt Studio. Photo order follows the compiled prompt.'}
    if mode == 'ref2va':
        image_prefix = _reference_image_prefix(schema)
        for name, value in old.items():
            if name.startswith('ref_images.ref_image_'):
                packet_inputs[image_prefix + name.rsplit('_', 1)[-1]] = copy.deepcopy(value)
            elif name.startswith('ref_videos.ref_video_'):
                components = work.get(value[0], {})
                video_link = components.get('inputs', {}).get('video')
                if components.get('class_type') != 'GetVideoComponents' or not video_link:
                    raise MMH3TransferError('Reference video must preserve its native VIDEO source for saved-state continuation.')
                packet_inputs[_reference_image_prefix(schema, 'VIDEO') + name.rsplit('_', 1)[-1]] = copy.deepcopy(video_link)
            elif name.startswith('ref_audios.ref_audio_'):
                packet_inputs[_reference_image_prefix(schema, 'AUDIO') + name.rsplit('_', 1)[-1]] = copy.deepcopy(value)
    else:
        for name in ('first_frame', 'last_frame'):
            if name in old:
                packet_inputs[name] = copy.deepcopy(old[name])
    packet_id = add('MMH3Create', packet_inputs, 'Edit prompt and seed · used by generation', 'Conditioning')
    packet_link = output(packet_id, 'packet', 'MMH3_MEDIA')
    # The archive controls must also drive the sampler. Independent literals
    # leave metadata editable while Comfy reuses a cached video unchanged.
    inspect_id = add('MMH3Inspect', {'packet': packet_link}, 'Shared prompt and seed · automatic', 'Conditioning')
    shared_prompt = output(inspect_id, 'prompt', 'STRING')
    shared_seed = output(inspect_id, 'seed', 'INT')
    native['inputs']['prompt'] = shared_prompt
    noise['inputs']['noise_seed'] = shared_seed
    pack_inputs = {'packet': packet_link, 'latent': [sampler_id, 0],
                   'video': copy.deepcopy(saver['inputs']['video']), 'operation': 'generate',
                   'mode': mode, 'status': 'Generated with the Prompt Studio H3 recipe.',
                   'process_info_json': '', 'latent_origin': 'sampler_output',
                   'applied_loras_json': json.dumps(_ordered_loras(work, sampler), ensure_ascii=False)}
    video_node = work.get(saver['inputs']['video'][0], {})
    if 'audio' in video_node.get('inputs', {}):
        pack_inputs['audio'] = copy.deepcopy(video_node['inputs']['audio'])
    metadata = {'available': availability['mmh3_save_available'], 'save_enabled': save, 'saved': False, 'continuation': bool(source),
                'latent_origin': 'sampler_output', 'source_node_id': None,
                'reference_packet_node_id': packet_id,
                'shared_controls_node_id': packet_id, 'inspect_node_id': inspect_id,
                'note': 'The .mmh3 working file saves motion, audio state, references and the applied LoRA order.'}
    manifest = {'prompt_control_node_id': packet_id, 'seed_control_node_id': packet_id}
    if source:
        selector = _source_selector(source, schema)
        source_info = config.get('_mmh3_source_info')
        if not isinstance(source_info, dict) or source_info.get('selector') != selector:
            raise MMH3TransferError('Inspect the selected saved video state before building its continuation.')
        source_geometry = source_info['geometry']
        overlap = settings.get('continuation_overlap_frames', 39)
        if type(overlap) is not int or overlap not in OVERLAPS:
            raise MMH3TransferError('Choose an exact video/audio context length: 39, 90, 141, 192, 243, 294 or 345 frames.')
        if overlap >= config['frames'] or overlap > source_geometry['frames']:
            raise MMH3TransferError('The continuation context must fit the source and leave room for new action. Choose a shorter context or a longer next clip.')
        load_id = add('MMH3Load', {'file': selector, 'verify': 'on_access', 'path_override': ''}, 'Continue from saved motion and audio', 'Inputs')
        settings_id = add('MMH3H3GenerationSettings', {'packet': output(load_id, 'packet', 'MMH3_MEDIA'),
                           'resolution': 'Source', 'duration_seconds': config['frames'] / 24}, 'Keep source size · generated clip length', 'Conditioning')
        dimensions = {name: output(settings_id, name, 'INT') for name in ('width', 'height', 'frames')}
        handover_id = add('MMH3H3ContinuationHandover', {'packet': output(load_id, 'packet', 'MMH3_MEDIA'),
                            'target_frames': dimensions['frames'], 'video_handover_frames': overlap,
                            'audio_handover_frames': 0, 'audio_feather_frames': 0,
                            'target_width': dimensions['width'], 'target_height': dimensions['height']},
                           f'Preserve {overlap / 24:g}s of motion and audio', 'Conditioning')
        continue_inputs = {'packet': packet_link, 'handover_info_json': output(handover_id, 'info_json', 'STRING'),
                           'task_family': 'ref2va' if mode == 'ref2va' else 'fl2va', 'prompt_override': shared_prompt,
                           'seed_override': shared_seed, 'clip': copy.deepcopy(old['clip']), 'video_vae': copy.deepcopy(old['vae']),
                           'width_override': dimensions['width'], 'height_override': dimensions['height'], 'frames_override': dimensions['frames']}
        _, audio_decoder = _one(work, {'VAEDecodeAudio'}, 'audio VAE decoder')
        continue_inputs['audio_vae'] = copy.deepcopy(audio_decoder['inputs']['vae'])
        continuation_id = add('MMH3H3ContinuationCondition', continue_inputs, 'Next action · preserved video tail', 'Conditioning')
        noise['inputs']['noise_seed'] = output(continuation_id, 'seed', 'INT')
        # Positive conditioning and masked joint AV target have different sources.
        for node in work.values():
            for name, value in list(node['inputs'].items()):
                if value == [cond_id, 0]:
                    node['inputs'][name] = output(continuation_id, 'positive', 'CONDITIONING')
                elif value == [cond_id, 1]:
                    node['inputs'][name] = output(handover_id, 'latent', 'LATENT')
        del work[cond_id]
        pack_inputs.update(packet=output(continuation_id, 'packet', 'MMH3_MEDIA'), operation='continuation',
                           mode=output(continuation_id, 'mode', 'STRING'), status=output(continuation_id, 'status', 'STRING'),
                           process_info_json=output(continuation_id, 'info_json', 'STRING'),
                           generation_settings_json=output(settings_id, 'settings_json', 'STRING'))
        metadata.update(source=selector.removeprefix('output::'), source_node_id=load_id,
                        source_revision=source_info.get('revision'), source_packet_id=source_info.get('id'),
                        uses_source_resolution=True, overlap_frames=overlap, context_seconds=overlap / 24,
                        generated_seconds=config['frames'] / 24, new_frames=config['frames'] - overlap,
                        new_seconds=(config['frames'] - overlap) / 24,
                        future_family=continue_inputs['task_family'], handover_node_id=handover_id,
                        note='The generated clip includes copied context. New footage is shorter; use MMH3 Latent Stitch to remove the overlap in a linked chain.')
        manifest.update(conditioning_node_id=continuation_id, requested_width=config['width'], requested_height=config['height'],
                        requested_resolution=config.get('resolution'), requested_aspect_ratio=config.get('aspect_ratio'),
                        width=source_geometry['width'], height=source_geometry['height'],
                        megapixels=source_geometry['width'] * source_geometry['height'] / 1_000_000,
                        resolution='source', aspect_ratio='source',
                        duration_note=f'{config["frames"] / 24:.3f}s generated = {overlap / 24:.3f}s preserved context + {(config["frames"] - overlap) / 24:.3f}s new footage. Source resolution is preserved.')
    if save:
        pack_id = add('MMH3PackH3Result', pack_inputs, 'Save sampled motion, sound and references')
        prefix = 'mmh3/' + saver['inputs']['filename_prefix']
        save_id = add('MMH3Save', {'packet': output(pack_id, 'packet', 'MMH3_MEDIA'), 'filename_prefix': prefix,
                                    'target': 'output', 'overwrite': False}, 'Save continuation state · output/' + prefix)
        metadata.update(output_prefix=prefix, save_node_id=save_id, pack_node_id=pack_id)
    graph.clear()
    graph.update(work)
    manifest['mmh3'] = metadata
    return manifest
