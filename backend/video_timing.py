"""Explicit new-footage budgets; legacy projects keep their original grid."""
import math


def frame_budget(duration, settings):
    minimum = 3 if settings.get('experimental_preview') is True else 4
    if type(duration) not in (int, float) or not math.isfinite(duration) or int(duration) != duration or not minimum <= duration <= 15:
        raise ValueError(f'Choose a whole clip length from {minimum} to 15 seconds. Three-second clips require experimental preview.')
    basis = settings.get('duration_basis', 'total')
    if basis not in ('total', 'new_footage'):
        raise ValueError('Unknown video duration basis.')
    overlap = settings.get('continuation_overlap_frames', 39) if settings.get('continuation_source') else 0
    if type(overlap) is not int or overlap < 0:
        raise ValueError('Invalid continuation context length.')
    frames = math.ceil((int(duration) * 24 + (overlap if basis == 'new_footage' else 0) - 5) / 17) * 17 + 5
    if frames > 362:
        raise ValueError('This continuation is too long including its motion context. Choose up to 13 seconds of new footage.')
    return {'frames': frames, 'overlap_frames': overlap, 'new_frames': max(0, frames - overlap),
            'new_seconds': max(0, frames - overlap) / 24, 'duration_basis': basis}
