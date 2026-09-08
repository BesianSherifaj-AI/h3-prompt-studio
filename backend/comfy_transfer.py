"""Prepare a new, reviewable H3 workflow with the Studio project's real images.

This module never queues work, edits an open graph, or touches GPU/model state.
Its only ComfyUI write is a non-overwriting image upload into a new transfer folder.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from .resources import local_url


class TransferError(ValueError):
    pass


HERETIC = 'qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors'
REF_LORA = 'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors'
FL_LORA = 'minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors'
RESOLUTIONS = {
    '0.3': (736, 416), '0.5': (960, 544),
    '0.7': (1152, 640), '1.0': (1344, 768),
}
MODE_LABELS = {'ref2va': 'Reference photos', 'i2va': 'First frame only',
               'fl2va': 'First + last frame', 'l2va': 'Last frame only', 't2va': 'Text only'}
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def transfer_options():
    """Stable, bounded choices: the UI cannot request arbitrary models or paths."""
    return {
        'resolutions': [{'value': key, 'label': f'{key} MP', 'width': w, 'height': h}
                        for key, (w, h) in RESOLUTIONS.items()],
        'qualities': [
            {'value': 'fast', 'label': 'Fast tested recipe', 'ref_steps': 8, 'frame_steps': 4},
            {'value': 'detailed', 'label': 'Double steps to compare', 'ref_steps': 16, 'frame_steps': 8},
        ],
        'steps': [4, 8, 16], 'aspect_ratios': ['16:9', '9:16', '1:1', '4:3', '3:4'],
        'modes': [{'value': key, 'label': label} for key, label in MODE_LABELS.items()],
        'min_duration': 4, 'max_duration': 15, 'fps': 24,
        'text_encoder': HERETIC,
        'default_loras': {mode: [{'name': REF_LORA if mode == 'ref2va' else FL_LORA, 'strength': 1.0, 'enabled': True}]
                          for mode in MODE_LABELS},
        'lora_strength_min': -4, 'lora_strength_max': 4, 'max_loras': 8,
        'note': 'Fast uses the measured Ref 8-step or frame 4-step recipe. Double steps is a comparison option; better quality is not guaranteed.',
    }


def _settings(project, settings):
    mode = project.get('mode')
    if mode not in MODE_LABELS:
        raise TransferError('Choose a supported H3 mode before sending to ComfyUI.')
    duration = project.get('duration')
    if type(duration) not in (int, float) or not math.isfinite(duration) or int(duration) != duration or not 4 <= duration <= 15:
        raise TransferError('Each H3 clip must be 4–15 whole seconds. Continue with another clip for a longer film.')
    resolution = str(settings.get('resolution', '0.3'))
    resolution = '1.0' if resolution == '1' else resolution
    if resolution not in RESOLUTIONS:
        raise TransferError('Choose 0.3, 0.5, 0.7 or 1.0 MP for the ComfyUI output.')
    aspect = settings.get('aspect_ratio', project.get('aspect_ratio', '16:9'))
    if aspect not in transfer_options()['aspect_ratios']:
        raise TransferError('Choose landscape, portrait, square, 4:3 or 3:4 for the output shape.')
    width, height = RESOLUTIONS[resolution]
    if aspect == '9:16':
        width, height = height, width
    elif aspect != '16:9':
        # Stay on the native 32-pixel grid while matching the chosen pixel budget.
        ratio = {'1:1': 1, '4:3': 4 / 3, '3:4': 3 / 4}[aspect]
        pixels = width * height
        width = max(32, round(math.sqrt(pixels * ratio) / 32) * 32)
        height = max(32, round(math.sqrt(pixels / ratio) / 32) * 32)
    quality = settings.get('quality', 'fast')
    if quality not in ('fast', 'detailed'):
        raise TransferError('Choose Fast tested recipe or Double steps to compare.')
    defaults = (8, 16) if mode == 'ref2va' else (4, 8)
    steps = settings.get('steps')
    if steps is None or steps == 'auto':
        steps = defaults[quality == 'detailed']
    if type(steps) is not int or steps not in (4, 8, 16):
        raise TransferError('Choose 4, 8 or 16 sampling steps, or use the recipe default.')
    seed = settings.get('seed', 9072026)
    if type(seed) is not int or not 0 <= seed <= 2**53 - 1:
        raise TransferError('The seed must be a whole number from 0 through 9007199254740991.')
    from .video_timing import frame_budget
    try:
        timing = frame_budget(duration, settings)
    except ValueError as exc:
        raise TransferError(str(exc)) from exc
    frames = timing['frames']
    return {'mode': mode, 'duration': int(duration), 'resolution': resolution, 'aspect_ratio': aspect,
            'width': width, 'height': height, 'megapixels': width * height / 1_000_000,
            'frames': frames, 'fps': 24, 'actual_duration': frames / 24,
            'quality': quality, 'steps': steps, 'seed': seed,
            'lora': REF_LORA if mode == 'ref2va' else FL_LORA,
            'lora_training_steps': 8 if mode == 'ref2va' else 4,
            'shift_video': 12.0 if mode == 'ref2va' else 6.0, 'shift_audio': 3.0,
            'attention': 'H3 SLA · Kitchen · 85% sparsity', 'reference_image_size': 'match'}


def _active_images(project, mode):
    assets = project.get('assets', [])
    if not isinstance(assets, list) or any(not isinstance(a, dict) for a in assets):
        raise TransferError('The photo list is invalid. Reopen the saved project and try again.')
    active = [a for a in assets if a.get('enabled', True) and a.get('role') != 'context']
    if any(a.get('media_type') != 'image' for a in active):
        raise TransferError('Direct ComfyUI transfer currently supports photos. Set video/audio references to Prompt inspiration only, or use an existing Comfy workflow for those inputs.')
    ids = [a.get('id') for a in active]
    if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)):
        raise TransferError('Each active photo needs a unique library ID. Add the affected photo again.')
    if mode == 'ref2va':
        if not 1 <= len(active) <= 9 or any(a.get('role') != 'reference_image' for a in active):
            raise TransferError('Reference photos needs 1–9 reference images. Put other photos in Prompt inspiration only.')
        return active
    needed = {'i2va': ['first_frame'], 'fl2va': ['first_frame', 'last_frame'],
              'l2va': ['last_frame'], 't2va': []}[mode]
    if sorted(a.get('role', '') for a in active) != sorted(needed):
        if mode == 'i2va':
            raise TransferError('First frame only needs one starting photo and no ending photo. Extra photos can stay as prompt inspiration.')
        raise TransferError(f'{MODE_LABELS[mode]} needs ' +
                            ('one start photo and one end photo.' if mode == 'fl2va' else
                             'one end photo.' if mode == 'l2va' else 'all photos set to prompt inspiration.'))
    return [next(a for a in active if a['role'] == role) for role in needed]


def _lora_compatibility(name):
    # Availability in the global LoRA loader is not evidence of H3 compatibility.
    # Only the two exact measured adapters can be certified by this app.
    modes = ['ref2va'] if name == REF_LORA else ['fl2va', 'i2va', 'l2va', 't2va'] if name == FL_LORA else []
    return {'compatibility': 'tested' if modes else 'unverified', 'compatible_modes': modes}


def _lora_selections(settings, mode):
    selected = settings.get('loras')
    if selected is None:
        selected = transfer_options()['default_loras'][mode]
    if not isinstance(selected, list) or not 1 <= len(selected) <= 8:
        raise TransferError('Choose 1–8 LoRAs for this workflow. Start with the tested speed LoRA, then add any extras.')
    active = []
    for entry in selected:
        if not isinstance(entry, dict) or ('enabled' in entry and type(entry['enabled']) is not bool):
            raise TransferError('Each LoRA needs a name, strength and enabled switch.')
        if entry.get('enabled', True) is False:
            continue
        name, strength = entry.get('name'), entry.get('strength', 1.0)
        if not isinstance(name, str) or not name.strip() or len(name) > 512:
            raise TransferError('Choose each LoRA from the installed ComfyUI list.')
        if type(strength) not in (int, float) or not math.isfinite(strength) or not -4 <= strength <= 4:
            raise TransferError('Each LoRA strength must be a number from -4 through 4.')
        compatibility = _lora_compatibility(name)
        if compatibility['compatible_modes'] and mode not in compatibility['compatible_modes']:
            raise TransferError(f'{name} belongs to a different H3 mode. Use the default speed LoRA for {MODE_LABELS[mode]}.')
        active.append({'name': name, 'strength': float(strength), **compatibility})
    if not active:
        raise TransferError('Enable at least one LoRA. The tested speed recipe needs its trained H3 adapter.')
    return active


def _apply_loras(graph, loras):
    existing = [(ident, node) for ident, node in graph.items() if node['class_type'] == 'LoraLoaderModelOnly']
    if len(existing) != 1:
        raise TransferError('The transfer recipe must start with one speed LoRA before adding your stack.')
    first_id, first_node = existing[0]
    source = first_node['inputs']['model']
    next_id = max(int(ident) for ident in graph) + 1
    chain_ids = set()
    for index, selected in enumerate(loras):
        ident = first_id if index == 0 else str(next_id + index - 1)
        graph[ident] = {'class_type': 'LoraLoaderModelOnly',
                        'inputs': {'model': copy.deepcopy(source), 'lora_name': selected['name'], 'strength_model': selected['strength']},
                        '_meta': {'title': f'LoRA {index + 1} · {selected["name"]} · {selected["strength"]:g}', 'benchmark_group': 'Acceleration'}}
        chain_ids.add(ident)
        source = [ident, 0]
    for ident, node in graph.items():
        if ident in chain_ids:
            continue
        for key, value in node['inputs'].items():
            if value == [first_id, 0]:
                node['inputs'][key] = copy.deepcopy(source)


def _read_image(asset, resolver):
    """The app resolver, not a project-supplied filename, chooses the local file."""
    try:
        path = Path(resolver(asset)).resolve(strict=True)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            raise TransferError('A reference file is empty or larger than 64 MB. Add a smaller image.')
        data = path.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            raise TransferError('A reference file is larger than 64 MB. Add a smaller image.')
        with Image.open(io.BytesIO(data)) as image:
            fmt, dimensions = image.format, list(image.size)
            image.verify()
        mime, ext = {'PNG': ('image/png', '.png'), 'JPEG': ('image/jpeg', '.jpg'),
                     'WEBP': ('image/webp', '.webp'), 'BMP': ('image/bmp', '.bmp')}[fmt]
    except TransferError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise TransferError(f'The photo {asset.get("name", "Reference")} cannot be read. Add it again before sending.') from exc
    digest = hashlib.sha256(data).hexdigest()
    if asset.get('sha256') and asset['sha256'] != digest:
        raise TransferError(f'The photo {asset.get("name", "Reference")} changed in the library. Reopen or replace it before sending.')
    return {'asset': asset, 'data': data, 'mime': mime, 'extension': ext,
            'sha256': digest, 'dimensions': dimensions}


def _template(mode, template_dir=None):
    """Read the measured templates when present; keep the same recipe portable."""
    stem = '01_Ref2VA_Balanced_0p3_to_0p7' if mode == 'ref2va' else '03_FL2VA_Balanced_0p3_to_0p7'
    folder = Path(template_dir) if template_dir else Path(__file__).resolve().parents[1] / 'workflows'
    path = folder / (stem + '.api.json')
    if path.is_file():
        graph = json.loads(path.read_text(encoding='utf-8-sig'))
        return graph, stem + '.api.json'
    # Exact model/LoRA/attention defaults from the measured workflow, embedded so
    # a packaged standalone Studio still prepares the same calculation.
    graph = {}

    def add(kind, inputs, title, group):
        ident = str(len(graph) + 1)
        graph[ident] = {'class_type': kind, 'inputs': inputs,
                        '_meta': {'title': title, 'benchmark_group': group}}
        return [ident, 0]

    ref = mode == 'ref2va'
    model = add('UNETLoader', {'unet_name': f'minimax_h3_{"ref2va" if ref else "fl2va"}_pruned_int8_convrot.safetensors', 'weight_dtype': 'default'}, 'H3 diffusion model', 'Models')
    clip = add('CLIPLoader', {'clip_name': HERETIC, 'type': 'minimax', 'device': 'default'}, 'H3 encoder · Qwen3-VL 32B Heretic', 'Models')
    vae = add('VAELoader', {'vae_name': 'minimax_h3_video_vae_fp16.safetensors'}, 'Video VAE', 'Models')
    audio_vae = add('VAELoader', {'vae_name': 'minimax_h3_audio_vae_fp32.safetensors'}, 'Audio VAE', 'Models')
    model = add('LoraLoaderModelOnly', {'model': model, 'lora_name': REF_LORA if ref else FL_LORA, 'strength_model': 1.0}, 'Turbo LoRA', 'Acceleration')
    model = add('MiniMaxH3SigmaShift', {'model': model, 'shift_video': 12.0 if ref else 6.0, 'shift_audio': 3.0}, 'Video and audio shifts', 'Acceleration')
    model = add('H3SLAAttention', {'model': model, 'sparsity_ratio': 0.85, 'block_size': '32', 'min_seq_len': 8192, 'dense_last_steps': 0,
                                 'protect_audio': True, 'enabled': True, 'dense_steps': '0', 'dense_backend': 'comfy_kitchen',
                                 'disable_fp16_accum': True, 'stabilize_motion': False, 'reference_protection': 'Heavy Enforcement',
                                 'tail_correction': True, 'use_int8_qk': False, 'engine': 'comfy_kitchen'}, 'H3 SLA · Kitchen · 85%', 'Acceleration')
    inputs = {'clip': clip, 'vae': vae, 'prompt': '', 'width': 736, 'height': 416, 'length': 124}
    if ref:
        inputs.update(audio_vae=audio_vae, ref_image_size='match')
    cond = add('MiniMaxH3ReferenceToVideo' if ref else 'MiniMaxH3ImageToVideo', inputs, 'Prompt and clip settings', 'Conditioning')
    guider = add('BasicGuider', {'model': model, 'conditioning': cond}, 'Basic guider · CFG 1', 'Sampling')
    noise = add('RandomNoise', {'noise_seed': 9072026}, 'Seed · fixed for comparisons', 'Sampling')
    sampler = add('KSamplerSelect', {'sampler_name': 'euler'}, 'Euler sampler', 'Sampling')
    sigmas = add('BasicScheduler', {'model': model, 'scheduler': 'simple', 'steps': 8 if ref else 4, 'denoise': 1.0}, 'Steps', 'Sampling')
    sampled = add('SamplerCustomAdvanced', {'noise': noise, 'guider': guider, 'sampler': sampler, 'sigmas': sigmas, 'latent_image': [cond[0], 1]}, 'Generate video and sound', 'Sampling')
    decoded = add('VAEDecode', {'samples': sampled, 'vae': vae}, 'Decode video', 'Output')
    audio = add('VAEDecodeAudio', {'samples': sampled, 'vae': audio_vae}, 'Decode audio', 'Output')
    video = add('CreateVideo', {'images': decoded, 'audio': audio, 'fps': 24.0, 'bit_depth': 8, 'color_space': 'sRGB'}, 'Video · 24 fps with sound', 'Output')
    add('SaveVideo', {'video': video, 'filename_prefix': 'h3_prompt_studio', 'format': 'mp4', 'format.codec': 'h264', 'format.codec.encoding': 'auto'}, 'Save MP4 with sound', 'Output')
    return graph, stem + ' (bundled calculation)'


def _type(spec):
    return 'COMBO' if isinstance(spec[0], list) else spec[0]


def _choices(spec):
    return spec[0] if isinstance(spec[0], list) else spec[1].get('options', []) if len(spec) > 1 else []


def _default(spec):
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if 'default' in opts:
        return copy.deepcopy(opts['default'])
    choices = _choices(spec)
    if choices:
        return choices[0]['key'] if isinstance(choices[0], dict) else choices[0]
    return {'STRING': '', 'INT': 0, 'FLOAT': 0.0, 'BOOLEAN': False}.get(_type(spec))


def _widget(spec):
    return _type(spec) in {'STRING', 'INT', 'FLOAT', 'BOOLEAN', 'COMBO', 'COMFY_DYNAMICCOMBO_V3'} and not (len(spec) > 1 and spec[1].get('forceInput'))


def _link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int


def _fields(schema, inputs):
    fields = {}

    def visit(definitions, prefix=''):
        for kind in ('required', 'optional'):
            for name, spec in definitions.get(kind, {}).items():
                key = prefix + name
                if _type(spec) == 'COMFY_AUTOGROW_V3':
                    opts = spec[1]
                    template = opts['template']['input']
                    child = next(iter({**template.get('required', {}), **template.get('optional', {})}.values()))
                    # Current V3 puts prefix/min/max inside template; earlier
                    # installed schemas exposed these beside template instead.
                    limits = {**opts, **opts['template']}
                    names = limits.get('names')
                    if isinstance(names, list):
                        # TemplateNames (for example ordered continuation
                        # segments) defines explicit socket names and order.
                        matched = [key + '.' + name for name in names if key + '.' + name in inputs]
                        maximum = limits.get('max', len(names))
                    else:
                        pattern = re.compile(re.escape(key + '.' + limits.get('prefix', '')) + r'\d+$')
                        matched = sorted((k for k in inputs if pattern.fullmatch(k)), key=lambda x: int(x.rsplit('_', 1)[-1]))
                        maximum = limits.get('max', 9999)
                    minimum = limits.get('min', 0)
                    if not minimum <= len(matched) <= maximum:
                        raise TransferError(f'The installed {key} input needs between {minimum} and {maximum} connections.')
                    for k in matched:
                        fields[k] = child, False
                    continue
                fields[key] = spec, kind == 'required'
                if _type(spec) == 'COMFY_DYNAMICCOMBO_V3':
                    selected = inputs.get(key, _default(spec))
                    choice = next((x for x in _choices(spec) if x['key'] == selected), None)
                    if choice:
                        visit(choice.get('inputs', {}), key + '.')
    visit(schema['input'])
    return fields


def _validate_graph(graph, schema, image_names):
    for ident, node in graph.items():
        kind = node['class_type']
        if kind not in schema:
            raise TransferError(f'ComfyUI is missing {kind}. Open the H3 Desktop installation with the tested nodes.')
        inputs = node['inputs']
        fields = _fields(schema[kind], inputs)
        unknown = inputs.keys() - fields.keys()
        if unknown:
            raise TransferError(f'The installed {kind} inputs changed: {", ".join(sorted(unknown))}. Update the H3 recipe before sending.')
        for name, (spec, required) in fields.items():
            if name not in inputs:
                if required:
                    raise TransferError(f'The installed {kind} now requires {name}. Update the H3 recipe before sending.')
                continue
            value, dtype = inputs[name], _type(spec)
            if _link(value):
                source, slot = value
                outputs = schema.get(graph.get(source, {}).get('class_type'), {}).get('output', [])
                if not 0 <= slot < len(outputs) or outputs[slot] not in (dtype, '*'):
                    raise TransferError(f'The {kind} {name} connection is incompatible with the installed nodes.')
                continue
            choices = [v['key'] if isinstance(v, dict) else v for v in _choices(spec)]
            if dtype in ('COMBO', 'COMFY_DYNAMICCOMBO_V3') and value not in choices:
                if not (kind == 'LoadImage' and name == 'image' and value in image_names):
                    raise TransferError(f'ComfyUI does not have the required {name}: {value}. Select the H3 Desktop installation used for the tested workflows.')
            if not _widget(spec):
                raise TransferError(f'{kind}.{name} must be connected to its source node.')
            if ((dtype == 'INT' and type(value) is not int) or
                (dtype == 'FLOAT' and (type(value) not in (int, float) or not math.isfinite(value))) or
                (dtype == 'BOOLEAN' and type(value) is not bool) or
                (dtype == 'STRING' and not isinstance(value, str))):
                raise TransferError(f'The {kind} {name} setting has the wrong type.')
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if type(value) in (int, float) and (value < opts.get('min', -math.inf) or value > opts.get('max', math.inf)):
                raise TransferError(f'The {kind} {name} setting exceeds the installed node limits.')
    if not any(schema[n['class_type']].get('output_node') for n in graph.values()):
        raise TransferError('The prepared workflow has no video output node.')
    visiting, visited = set(), set()

    def visit(ident):
        if ident in visiting:
            raise TransferError('The workflow has a circular connection.')
        if ident in visited:
            return
        visiting.add(ident)
        for value in graph[ident]['inputs'].values():
            if _link(value):
                visit(value[0])
        visiting.remove(ident)
        visited.add(ident)
    for ident in graph:
        visit(ident)


def _materialize_ui_defaults(graph, schema):
    """Keep the API document equal to what the installed frontend serializes.

    Comfy includes optional widgets even when a saved API graph omits them,
    including hidden compatibility widgets. Required inputs still fail normal
    preflight if absent; connections are never invented.
    """
    for node in graph.values():
        definition = schema.get(node['class_type'])
        if not definition:
            continue
        for _ in range(8):
            changed = False
            for name, (spec, required) in _fields(definition, node['inputs']).items():
                if required or name in node['inputs'] or not _widget(spec):
                    continue
                default = _default(spec)
                if default is not None:
                    node['inputs'][name] = default
                    changed = True
            if not changed:
                break


def _ui_workflow(graph, schema, transfer_id, title):
    """Expand live V3 widgets and image slots into an editable LiteGraph document."""
    group_order = ['Inputs', 'Conditioning', 'Models', 'Acceleration', 'Sampling', 'Output']
    nodes, links, slots, groups = [], [], {}, defaultdict(list)
    for order, (ident, node) in enumerate(graph.items()):
        definition = schema[node['class_type']]
        fields = _fields(definition, node['inputs'])
        inputs, widgets = [], []
        field_order = [k for k, (s, _) in fields.items() if not _widget(s)] + [k for k, (s, _) in fields.items() if _widget(s)]
        for key in field_order:
            spec, required = fields[key]
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if opts.get('hidden') and key not in node['inputs']:
                continue
            inp = {'name': key, 'type': _type(spec), 'link': None}
            if not required:
                inp['shape'] = 7
            if _widget(spec):
                inp['widget'] = {'name': key}
                value = node['inputs'].get(key)
                widgets.append(_default(spec) if value is None or _link(value) else value)
                if opts.get('control_after_generate'):
                    widgets.append('fixed')
            slots[(ident, key)] = len(inputs)
            inputs.append(inp)
        group = node.get('_meta', {}).get('benchmark_group', 'Models')
        if group not in group_order:
            group_order.append(group)
        prompt_node = 'prompt' in fields
        size = [720 if prompt_node else 360, max(120, 65 + len(inputs) * 28) + (300 if prompt_node else 220 if node['class_type'] == 'LoadImage' else 0)]
        x = group_order.index(group) * 420 + (360 if group_order.index(group) >= 2 else 0)
        y = 390 + sum(n['size'][1] + 40 for n in groups[group])
        ui_node = {'id': int(ident), 'type': node['class_type'], 'pos': [x, y], 'size': size,
                   'flags': {'collapsed': True} if node['class_type'] == 'MMH3Inspect' else {},
                   'order': order, 'mode': 0, 'inputs': inputs,
                   'outputs': [{'name': definition.get('output_name', definition['output'])[i], 'type': dtype, 'links': [], 'slot_index': i}
                               for i, dtype in enumerate(definition['output'])],
                   'properties': {'Node name for S&R': node['class_type']}, 'widgets_values': widgets,
                   'title': node.get('_meta', {}).get('title', node['class_type'])}
        nodes.append(ui_node)
        groups[group].append(ui_node)
    by_id = {str(n['id']): n for n in nodes}
    for ident, node in graph.items():
        for name, value in node['inputs'].items():
            if not _link(value):
                continue
            source, out_slot = value
            in_slot, link_id = slots[(ident, name)], len(links) + 1
            dtype = by_id[source]['outputs'][out_slot]['type']
            links.append([link_id, int(source), out_slot, int(ident), in_slot, dtype])
            by_id[source]['outputs'][out_slot]['links'].append(link_id)
            by_id[ident]['inputs'][in_slot]['link'] = link_id
    ui_groups = []
    labels = {'Inputs': '1 · Your photos, in prompt order', 'Conditioning': '2 · Prompt and clip settings',
              'Models': 'H3 models', 'Acceleration': 'Speed recipe', 'Sampling': '3 · Generate', 'Output': '4 · Save video with sound'}
    for group, members in groups.items():
        x = min(n['pos'][0] for n in members) - 20
        bottom = max(n['pos'][1] + n['size'][1] for n in members)
        ui_groups.append({'id': len(ui_groups) + 1, 'title': labels.get(group, group),
                          'bounding': [x, 340, max(n['size'][0] for n in members) + 40, bottom - 320],
                          'color': '#3f789e', 'font_size': 24, 'flags': {}})
    return {'id': transfer_id, 'revision': 0, 'last_node_id': max(n['id'] for n in nodes),
            'last_link_id': len(links), 'nodes': nodes, 'links': links, 'groups': ui_groups, 'config': {},
            'extra': {'ds': {'scale': 0.48, 'offset': [45, 45]}, 'workflow_title': title}, 'version': 0.4}


def _connect(settings, client):
    urls = settings.get('comfy_urls', ['http://127.0.0.1:8188', 'http://127.0.0.1:8000', 'http://127.0.0.1:8010'])
    if not isinstance(urls, list) or not 1 <= len(urls) <= 4:
        raise TransferError('Set a ComfyUI address in Connections first.')
    selected = settings.get('comfy_url')
    if selected is not None:
        if selected not in urls:
            raise TransferError('Choose one of the ComfyUI addresses saved in Connections.')
        urls = [selected]
    endpoints = []
    for url in urls:
        try:
            base = local_url(url)
            if urlparse(base).path not in ('', '/'):
                raise ValueError('Unexpected endpoint path')
        except (ValueError, TypeError) as exc:
            raise TransferError('Use a local ComfyUI address such as http://127.0.0.1:8010.') from exc
        endpoints.append(base)
    for base in endpoints:
        try:
            response = client.get(base + '/object_info', timeout=20)
            response.raise_for_status()
            schema = response.json()
            if not isinstance(schema, dict) or not isinstance(schema.get('LoadImage'), dict):
                continue
            return base, schema
        except (httpx.HTTPError, ValueError):
            continue
    raise TransferError('ComfyUI is not reachable. Open ComfyUI Desktop, wait for it to start, then send again. Your Studio draft is saved separately.')


def installed_transfer_options(settings, *, client=None):
    """Read installed LoRA names without loading models or inspecting user graphs."""
    from .mmh3_transfer import mmh3_options
    result = {**transfer_options(), 'available_loras': [], 'online': False}
    own_client = client is None
    if own_client:
        client = httpx.Client(trust_env=False, follow_redirects=False)
    try:
        base, schema = _connect(settings, client)
        spec = schema.get('LoraLoaderModelOnly', {}).get('input', {}).get('required', {}).get('lora_name')
        if not spec:
            raise TransferError('ComfyUI is online, but the model-only LoRA loader is unavailable.')
        names = _choices(spec)
        result.update(mmh3_options(schema))
        result.update(online=True, comfy_url=base, available_loras=[
            {'name': name, **_lora_compatibility(name)} for name in names if isinstance(name, str)
        ])
        return result
    except (TransferError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        result['error'] = str(exc)
        return result
    finally:
        if own_client:
            client.close()


def build_transfer(project, compiled_prompt, settings, asset_path_resolver, *, client=None, template_dir=None):
    """Return {id, workflow, prompt, manifest, comfy_url}; never submit /prompt.

    The caller compiles and validates the complete project first. For portability,
    asset_path_resolver(asset) must locate a trusted library file using asset.id.
    A supplied httpx-compatible client supports isolated tests without network IO.
    """
    if not isinstance(compiled_prompt, str) or not compiled_prompt.strip() or len(compiled_prompt) > 100_000:
        raise TransferError('Build a valid prompt before sending this project to ComfyUI.')
    config = _settings(project, settings)
    loras = _lora_selections(settings, config['mode'])
    assets = _active_images(project, config['mode'])
    images = [_read_image(asset, asset_path_resolver) for asset in assets]
    transfer_id = str(uuid.uuid4())
    subfolder = 'h3_prompt_studio/transfers/' + transfer_id
    for index, image in enumerate(images, 1):
        image['filename'] = f'picture-{index:02d}-{image["sha256"][:12]}' + image['extension']
        image['input_name'] = subfolder + '/' + image['filename']
    graph, template_name = _template(config['mode'], template_dir)
    graph = copy.deepcopy(graph)
    # No template/demo photos survive this transfer, including unused loaders.
    for ident in [i for i, n in graph.items() if n['class_type'] == 'LoadImage']:
        del graph[ident]
    conditioning = [(i, n) for i, n in graph.items() if n['class_type'] in ('MiniMaxH3ReferenceToVideo', 'MiniMaxH3ImageToVideo')]
    if len(conditioning) != 1:
        raise TransferError('The installed transfer template must have exactly one H3 prompt node.')
    cond_id, cond = conditioning[0]
    for key in list(cond['inputs']):
        if key in ('first_frame', 'last_frame') or key.startswith(('ref_images.', 'ref_videos.', 'ref_video_audios.', 'ref_audios.')):
            del cond['inputs'][key]
    cond['inputs'].update(prompt=compiled_prompt, width=config['width'], height=config['height'], length=config['frames'])
    cond['_meta']['title'] = f'{MODE_LABELS[config["mode"]]} · {config["width"]}×{config["height"]} · {config["actual_duration"]:.3f}s'
    if config['mode'] == 'ref2va':
        cond['inputs']['ref_image_size'] = 'match'
    next_id = max(int(i) for i in graph) + 1
    reference_map = []
    for index, image in enumerate(images):
        ident = str(next_id + index)
        asset = image['asset']
        label = f'<Picture {index + 1}> · {asset.get("name", "Photo")}'
        if asset.get('prompt_tag'):
            label += ' · @' + asset['prompt_tag']
        graph[ident] = {'class_type': 'LoadImage', 'inputs': {'image': image['input_name']},
                        '_meta': {'title': label, 'benchmark_group': 'Inputs'}}
        input_slot = f'ref_images.ref_image_{index}' if config['mode'] == 'ref2va' else asset['role']
        cond['inputs'][input_slot] = [ident, 0]
        reference_map.append({'asset_id': asset['id'], 'name': asset.get('name', ''), 'tag': asset.get('prompt_tag', ''),
                              'token': f'<Picture {index + 1}>', 'role': asset['role'], 'semantic_role': asset.get('semantic_role', 'other'),
                              'input_slot': input_slot, 'node_id': ident, 'comfy_image': image['input_name'],
                              'sha256': image['sha256'], 'bytes': len(image['data']), 'dimensions': image['dimensions']})
    title_slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', project.get('title', 'film')).strip('_')[:60] or 'film'
    prefix = f'h3_prompt_studio/{title_slug}/{transfer_id[:8]}'
    for node in graph.values():
        kind, inputs = node['class_type'], node['inputs']
        if kind == 'MiniMaxH3SigmaShift':
            inputs.update(shift_video=config['shift_video'], shift_audio=config['shift_audio'])
        elif kind == 'RandomNoise':
            inputs['noise_seed'] = config['seed']
        elif kind == 'BasicScheduler':
            inputs['steps'] = config['steps']
        elif kind == 'SaveVideo':
            inputs['filename_prefix'] = prefix
            node['_meta']['title'] = 'Save video · output/' + prefix
    _apply_loras(graph, loras)
    own_client = client is None
    if own_client:
        client = httpx.Client(trust_env=False, follow_redirects=False)
    try:
        base, schema = _connect(settings, client)
        from .mmh3_transfer import apply_mmh3, read_mmh3_source, MMH3TransferError
        try:
            source_info = read_mmh3_source(client, base, settings, schema)
            if source_info is not None:
                config['_mmh3_source_info'] = source_info
            media_metadata = apply_mmh3(graph, config, project, settings, schema)
        except MMH3TransferError as exc:
            raise TransferError(str(exc)) from exc
        # Continuations replace the native conditioning node with a packet-based
        # node. Keep the review map pointed at the actual image input sockets.
        image_target_id = cond_id
        if cond_id not in graph:
            image_target_id = media_metadata.get('mmh3', {}).get('reference_packet_node_id')
        image_inputs = graph.get(image_target_id, {}).get('inputs', {})
        for reference in reference_map:
            sockets = [name for name, value in image_inputs.items() if value == [reference['node_id'], 0]]
            if len(sockets) != 1:
                raise TransferError('The final workflow image connections could not be verified. No images were uploaded.')
            reference['input_slot'] = sockets[0]
            reference['conditioning_input_node_id'] = image_target_id
        _materialize_ui_defaults(graph, schema)
        # All types/models/settings/links are checked before the first upload.
        _validate_graph(graph, schema, {image['input_name'] for image in images})
        if config['mode'] in ('i2va', 'l2va', 't2va'):
            optional = schema.get('MiniMaxH3ImageToVideo', {}).get('input', {}).get('optional', {})
            if not all(key in optional for key in ('first_frame', 'last_frame')):
                raise TransferError('This ComfyUI H3 node does not support optional start/end photos. Update it before using this mode.')
        workflow = _ui_workflow(graph, schema, transfer_id, 'Prompt Studio · ' + str(project.get('title', 'Film')))
        for image in images:
            response = client.post(base + '/upload/image', files={'image': (image['filename'], image['data'], image['mime'])},
                                   data={'type': 'input', 'subfolder': subfolder, 'overwrite': 'false'}, timeout=30)
            response.raise_for_status()
            returned = response.json()
            if (returned.get('name') != image['filename'] or returned.get('subfolder', '').replace('\\', '/') != subfolder or returned.get('type') != 'input'):
                raise TransferError('ComfyUI saved a photo at an unexpected location. The workflow was not sent; retry with a fresh transfer.')
            # Read the original file back. This verifies bytes, not merely a
            # successful upload response or a potentially resized thumbnail.
            verified = client.get(base + '/view', params={'filename': image['filename'], 'subfolder': subfolder, 'type': 'input'}, timeout=30)
            verified.raise_for_status()
            if hashlib.sha256(verified.content).hexdigest() != image['sha256']:
                raise TransferError('A photo changed during transfer to ComfyUI. The workflow was not sent; add the photo again and retry.')
    except TransferError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise TransferError('The ComfyUI transfer did not finish. Your project is unchanged and no video was queued. Check ComfyUI and retry.') from exc
    finally:
        if own_client:
            client.close()
    manifest = {**{key: value for key, value in config.items() if not key.startswith('_')}, 'transfer_id': transfer_id, 'project_id': project.get('id'),
                'template': template_name, 'comfy_url': base, 'conditioning_node_id': cond_id,
                'conditioning_input_node_id': image_target_id,
                'text_encoder': HERETIC, 'images': reference_map, 'output_prefix': prefix,
                'prompt_sha256': hashlib.sha256(compiled_prompt.encode('utf-8')).hexdigest(),
                'image_bytes_verified': True, 'queued': False,
                'duration_note': f'{config["duration"]}s requested → {config["frames"]} frames / {config["actual_duration"]:.3f}s at 24 fps on the H3 frame grid.',
                'quality_note': transfer_options()['note'], 'loras': loras,
                'lora': loras[0]['name'],
                'recipe_modified': len(loras) != 1 or loras[0]['name'] != config['lora'] or loras[0]['strength'] != 1.0,
                'lora_warnings': [f'{item["name"]}: installed, but H3 compatibility and its effect with this stack have not been verified.'
                                  for item in loras if item['compatibility'] == 'unverified'],
                **media_metadata}
    if not any(item['name'] == config['lora'] and item['strength'] != 0 for item in loras):
        manifest['lora_warnings'].append('The tested speed adapter is absent or has zero strength. These low sampling-step settings are no longer the measured speed recipe.')
    workflow['extra']['h3_prompt_studio'] = copy.deepcopy(manifest)
    return {'id': transfer_id, 'workflow': workflow, 'prompt': graph, 'manifest': manifest, 'comfy_url': base}
