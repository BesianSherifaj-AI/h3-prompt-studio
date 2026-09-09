"""Ordinary directional movement needs rendering, not language-model inference.

The saved frame anchors appearance; these commands specify intent rather than
claiming measured geometry or verification of the newly rendered image.
"""
from __future__ import annotations

import copy

from .gameplay import mechanical_plan, mechanics_need_narrative


def _background_staging(scene, player_id, appearance, *, fresh, camera_moves):
    # Observed extras are visual constraints, not newly invented world identities.
    hold = ('Existing bystanders stay still, feet planted at the same ground contact, '
            'same posture and clothing. Nobody follows the player. ')
    if not camera_moves:
        hold += 'Keep their screen positions and sizes fixed. '
    labels = []
    for candidate in scene.get('candidates', []):
        if candidate.get('kind') != 'person' or candidate.get('known_id') == player_id:
            continue
        if candidate.get('description', '').strip() == appearance.strip():
            continue
        label = candidate.get('label', '').strip()[:65]
        if not label:
            continue
        position = candidate.get('position', '').strip()[:35] if fresh else ''
        entry = 'one ' + label + (' at ' + position if position else '')
        if len(hold + 'Hold: ' + '; '.join([*labels, entry]) + '.') <= 500:
            labels.append(entry)
    return hold + ('Hold: ' + '; '.join(labels) + '.' if labels else
                   'Retain all people and objects already in the source frame.')


def deterministic_movement(project, world, player_id, intent, duration, *, guides=(), observed_state=None):
    from .game_director import _author_instructions, _prepare_project
    if (not isinstance(intent, dict) or intent.get('kind') != 'move' or intent.get('target_id')
            or intent.get('presentation', 'continuous') != 'continuous'):
        return None
    instructions = _author_instructions(project)
    if mechanics_need_narrative(world, player_id, intent, guides=guides, user_instructions=instructions):
        return None
    # Fresh user-authored scene controls need the usual planner. A rendered
    # generated contract is historical staging, not a newly authored lock.
    if any(scene.get('director_locks') or (scene.get('scene_contract') and scene.get('scene_contract_source') != 'generated'
            and scene['id'] not in project.get('rendered_scene_contracts', {})) for scene in project.get('shots', [])):
        return None
    player = next((person for person in world['characters'] if person['id'] == player_id), None)
    if not player:
        raise ValueError('Choose your player character before moving.')
    pov = project.get('game_viewpoint') == 'pov'
    camera_only = intent.get('camera', 'player') == 'camera'
    appearance = player.get('state', {}).get('visual_anchor') or player.get('description', '')
    if not camera_only and not pov and not appearance.strip():
        raise ValueError('Choose your character to start moving.')
    observed = observed_state or {}
    remembered = observed.get('visible_scene') or observed.get('last_inspected_scene') or {}
    setting = remembered.get('setting') or next((scene.get('setting') for scene in reversed(project.get('shots', [])) if scene.get('setting')), '')
    plan = mechanical_plan(world, player_id, intent, duration, guides=guides,
                           user_instructions=instructions, current_setting=setting)
    if plan is None:
        return None
    prepared = _prepare_project(plan, project, duration)
    cast = {person['id']: person for person in world['characters']}
    visible = [person['id'] for person in plan['characters'] if not (pov and person['id'] == player_id)]
    offscreen = [person['id'] for person in plan['characters'] if person['id'] not in visible]
    direction = intent.get('direction', 'forward')
    motion = {'forward': 'dolly forward into the view', 'backward': 'dolly backward from the view',
              'left': 'truck left', 'right': 'truck right'}[direction]
    actor_rows = []
    for sid in visible:
        person = cast[sid]
        moving = sid == player_id and not camera_only
        description = person.get('state', {}).get('visual_anchor') or person.get('description', '')
        actor_rows.append({'subject_id': sid, 'activity': 'act' if moving else 'hold',
            'start': ('Match this same person in the source frame. ' + description)[:500],
            'action': plan['action'][:500] if moving else 'Keep the established posture, world position and appearance. No independent movement.',
            'end': plan['final_state'][:500] if moving else 'Same posture, world position and appearance as the source frame.'})
    objects = []
    for item in world['entities']:
        if len(objects) >= 24 or item.get('state', {}).get('hidden'):
            continue
        holder = item.get('holder_id') or item.get('worn_by_id')
        if (holder and holder not in visible) or (not holder and item.get('location_id') != player.get('location_id')):
            continue
        placement = (('Worn by ' if item.get('worn_by_id') else 'Held by ') + cast[holder]['name']) if holder else 'At its established position in the source frame.'
        count = item.get('state', {}).get('count', 1)
        count = count if type(count) is int and 1 <= count <= 100 else 1
        objects.append({'entity_id': item['id'], 'name': item['name'][:120], 'description': item.get('description', '')[:500],
                        'count': count, 'start': placement[:500], 'end': placement[:500]})
    shot = {'beat_id': plan['beats'][0]['id'], 'duration': duration,
            'camera': {'framing': 'first person' if pov else 'preserve source view and visible feet',
                       'movement': motion if camera_only or pov else 'static', 'height': 'preserve source height',
                       'speed': intent.get('speed', 'normal') if camera_only or pov else 'still',
                       'focus': 'preserve source orientation and stationary landmarks'},
            'performance': plan['action'], 'sound': '' if camera_only else 'Footsteps match the planted walking steps.',
            'visible_subject_ids': visible, 'offscreen_subject_ids': offscreen,
            'dialogue_indices': [], 'transition': 'continuous',
            'scene_contract': {'actors': actor_rows, 'objects': objects,
                'environment': (setting or plan['setting'])[:1000],
                'background_activity': _background_staging(remembered, player_id, appearance,
                    fresh=bool(observed.get('visible_scene')), camera_moves=camera_only or pov)},
            'scene_contract_source': 'generated'}
    plan['direction'] = {'shots': [shot]}
    plan['actor_actions'] = [{'subject_id': row['subject_id'], 'activity': row['activity'], 'action': row['action']} for row in actor_rows]
    plan['assistant_stages'] = [{'stage': 'deterministic_movement', 'actor_id': player_id}]
    # Use the same merge/validation as authored and model-directed plans.
    from .game_director import direct_plan
    direct_plan(plan, prepared, duration=duration)
    return plan


def movement_observation(previous, plan, source_run_id):
    """Keep old evidence labelled as old; never claim an uninspected new frame."""
    previous = previous or {}
    scene = previous.get('visible_scene') or previous.get('last_inspected_scene')
    result = {'observed_state': '', 'uncertainties': 'This movement used the saved frame and deterministic direction. Its new ending has not been visually inspected.',
              'choices': copy.deepcopy(plan.get('choices', [])), 'continuity_checks': [], 'visible_effects': [],
              'inspection_status': 'not_run', 'cached_scene_run_id': previous.get('cached_scene_run_id') or source_run_id}
    if scene:
        result['last_inspected_scene'] = copy.deepcopy(scene)
    return result


def preserve_assigned_ending_references(project):
    """A user-bound photo remains a design asset even if it came from a video.

    Automatic continuity images have no subject binding. Keep provenance under
    a separate key so replacing those automatic frames cannot delete a photo
    the user has explicitly assigned to their character.
    """
    assigned = {aid for subject in project.get('subjects', []) for aid in subject.get('asset_ids', [])}
    for asset in project.get('assets', []):
        if asset.get('media_type') == 'image' and asset.get('video_run_ending') and asset.get('id') in assigned:
            origin = asset.pop('video_run_ending')
            asset['reference_from_video_ending'] = origin


def apply_movement_frames(story, turn, project, ending, get_ending, *, current_frame=False):
    """Use saved images as actual native keyframes, never restore an old world."""
    from .navigation import navigation_return
    if not ending or not turn.get('parent_run_id'):
        return
    preserve_assigned_ending_references(project)
    trial = {**turn, 'project': project}
    return_run = navigation_return(story, trial)
    if not current_frame and not return_run:
        return
    # Explicit authored keyframes must not silently become a different task.
    if any(asset.get('enabled', True) and not asset.get('video_run_ending')
           and asset.get('role') in ('first_frame', 'last_frame') for asset in project['assets']):
        if current_frame:
            raise ValueError('This movement has explicit frame controls. Remove those controls or choose a directed turn.')
        return
    destination = get_ending(return_run) if return_run else None
    images = [asset for asset in project['assets'] if not asset.get('video_run_ending')]
    # Identity assets stay saved as context: the current actual frame already
    # contains their established appearance. Native exact frames use FL2VA,
    # whose interface is separate from arbitrary reference-image conditioning.
    for asset in images:
        if asset.get('media_type') == 'image' and asset.get('enabled', True):
            if asset.get('role') == 'reference_image':
                # This is an H3 task-format change, not a changed design.
                # Preserve it for safe waypoint comparison and future editing.
                asset['movement_reference_role'] = 'reference_image'
            asset['role'] = 'context'
    first = {**copy.deepcopy(ending), 'enabled': True, 'role': 'first_frame',
             'prompt_tag': 'movement-start', 'video_run_ending': True}
    images.append(first)
    if destination:
        if destination['id'] == first['id']:
            raise ValueError('The saved destination frame is also the movement start. Choose a different viewpoint.')
        images.append({**copy.deepcopy(destination), 'enabled': True, 'role': 'last_frame',
                       'prompt_tag': 'movement-return', 'video_run_ending': True})
    project['assets'] = images
    project['mode'] = 'fl2va' if destination else 'i2va'
    for key in ('continuation_source', 'duration_basis', 'continuation_overlap_frames'):
        project['comfy_render'].pop(key, None)
    project.get('simple', {}).pop('continuation', None)
    project['custom_instructions'] = project['custom_instructions'].replace(' Scene timing describes NEW footage after the preserved motion context.', '')
    if destination:
        project['custom_instructions'] += ' Return to the saved destination viewpoint shown by the final frame. Preserve current identities and object state throughout the movement.'
    turn['plan']['transition'] = 'cut'
    turn['movement_conditioning'] = {'mode': project['mode'], 'start_run_id': turn['parent_run_id'],
                                    'start_asset_id': first['id'], 'return_run_id': return_run,
                                    'return_asset_id': destination['id'] if destination else None}
