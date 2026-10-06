"""Workspace-specific assistant choices, with an offline legacy migration."""
from __future__ import annotations

import copy
import re

WORKSPACES = ('studio', 'game')
PROFILE_FIELDS = ('model', 'context_length', 'ai_memory_mode')
MIN_CONTEXT_TOKENS, MAX_CONTEXT_TOKENS = 1024, 262_144
DEFAULT_PROFILE = {'model': '', 'context_length': 8192, 'ai_memory_mode': 'exclusive'}
QWEN_MODEL_POLICY = 'qwen3.8-27b'
DEFAULT_QWEN_MODEL = 'qwen3.8-27b@q4_k_s'


def is_qwen_27b_model(model):
    """Match installed family keys, including publishers and quantized variants."""
    return isinstance(model, str) and bool(re.search(
        r'(?<![a-z0-9])qwen[._-]?3[._-]?8[._-]+27b(?:$|[._@/-])', model, re.IGNORECASE))


def model_policy(settings):
    policy = settings.get('assistant_model_policy', '')
    if policy not in ('', QWEN_MODEL_POLICY):
        raise ValueError('Unknown assistant model policy. Use qwen3.8-27b for the local model lock.')
    return policy


def enforce_model_policy(settings, profile):
    result = validate_profile(profile)
    if model_policy(settings) == QWEN_MODEL_POLICY:
        if not is_qwen_27b_model(result['model']):
            raise ValueError('This local app is locked to Qwen 3.8 27B. Select an installed 27B variant; smaller models are never substituted.')
        if result['ai_memory_mode'] != 'exclusive':
            raise ValueError('Qwen 3.8 27B requires GPU mode with automatic H3 handoff. CPU residency is disabled for this local app.')
    return result


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) - set(PROFILE_FIELDS):
        raise ValueError('Assistant profiles contain only model, context_length and ai_memory_mode.')
    result = {**DEFAULT_PROFILE, **profile}
    model = result['model']
    if not isinstance(model, str) or len(model) > 500 or (model and not model.strip()):
        raise ValueError('Choose a valid installed LM Studio model.')
    context = result['context_length']
    if type(context) is not int or not MIN_CONTEXT_TOKENS <= context <= MAX_CONTEXT_TOKENS:
        raise ValueError(f'Choose a context length between {MIN_CONTEXT_TOKENS} and {MAX_CONTEXT_TOKENS} tokens.')
    if result['ai_memory_mode'] not in ('exclusive', 'resident_cpu', 'resident_small'):
        raise ValueError('Choose GPU with automatic H3 handoff or a CPU assistant.')
    return result


def migrate_profiles(settings):
    """Copy existing choices into both workspaces without probing/changing models."""
    migrated = copy.deepcopy(settings)
    legacy = validate_profile({key: settings[key] for key in PROFILE_FIELDS if key in settings})
    stored = settings.get('assistant_profiles', {})
    if not isinstance(stored, dict) or set(stored) - set(WORKSPACES):
        raise ValueError('Choose a Studio or Game assistant profile.')
    profiles = {}
    for workspace in WORKSPACES:
        supplied = stored.get(workspace, {})
        if not isinstance(supplied, dict):
            raise ValueError('Each assistant profile must be an object.')
        profiles[workspace] = validate_profile({**legacy, **supplied})
    if model_policy(settings) == QWEN_MODEL_POLICY:
        preferred = next((profiles[workspace]['model'] for workspace in WORKSPACES
                          if is_qwen_27b_model(profiles[workspace]['model'])), DEFAULT_QWEN_MODEL)
        for profile in profiles.values():
            if not is_qwen_27b_model(profile['model']):
                profile['model'] = preferred
            profile['ai_memory_mode'] = 'exclusive'
    migrated['assistant_profiles'] = profiles
    if model_policy(settings):
        migrated.update(profiles['studio'])
    return migrated


def resolve_profile(settings, workspace='studio'):
    if workspace == 'video':
        workspace = 'studio'
    if workspace not in WORKSPACES:
        raise ValueError('Choose the Studio or Game workspace.')
    return migrate_profiles(settings)['assistant_profiles'][workspace]


def merge_profile_settings(settings, updates):
    """Nested edits affect one workspace; legacy flat edits retain global scope."""
    result = migrate_profiles(settings)
    legacy_patch = {key: updates[key] for key in PROFILE_FIELDS if key in updates}
    nested = updates.get('assistant_profiles', {})
    if not isinstance(nested, dict) or set(nested) - set(WORKSPACES):
        raise ValueError('Choose a Studio or Game assistant profile.')
    for workspace in WORKSPACES:
        patch = nested.get(workspace, {})
        if not isinstance(patch, dict):
            raise ValueError('Each assistant profile must be an object.')
        result['assistant_profiles'][workspace] = enforce_model_policy(result, {
            **result['assistant_profiles'][workspace], **legacy_patch, **patch})
    # Older Studio clients still read the original top-level fields.
    result.update(result['assistant_profiles']['studio'])
    return result
