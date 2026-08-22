from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from .kling_provider import KlingProvider, KlingError, enabled as kling_enabled
from .character_motion_library import CharacterMotionLibrary, signature
from .quality_check import animation_ok

log=logging.getLogger('dome.character_animation')


def prepare_character_animation(character_png:Path, segment:dict, work_root:Path, audio_path:Path|None=None, *, allow_generate:bool=True) -> Path|None:
    """Return cached/generated green-screen character clip or None for PNG fallback."""
    if not kling_enabled():
        return None
    block=segment.get('character_animation') or {}
    description=str(block.get('description_ru') or '').strip()
    if not description:
        return None
    duration=max(.5,float(segment.get('end',1))-float(segment.get('visible_start',0)))
    view=str(block.get('view') or segment.get('view') or 'front')
    speaking=bool(block.get('speaking', segment.get('talk_start') is not None))
    reuse=bool(block.get('reuse',True))
    lib=CharacterMotionLibrary(settings.storage_root,character_png)
    sig=signature(description,speaking=speaking,view=view,duration=duration)
    provider=KlingProvider()
    body_path=None
    body_url=''
    if reuse:
        cached=lib.find(sig)
        if cached:
            log.info('Reusing animation %s for %s',sig,description)
            body_path=cached
            # Current library keeps the local reusable body motion. A fresh lip-sync is attempted
            # only when a public Kling URL was created in this render; otherwise the body motion
            # is still reused and FFmpeg mixes the new child voice as a safe fallback.
    if body_path is None and not allow_generate:
        return None
    if body_path is None:
        tries=max(1,int(settings.character_animation_max_retries)+1)
        for attempt in range(tries):
            tmp=work_root/f'kling_{sig}_{attempt+1}.mp4'
            try:
                generated, body_url=provider.generate(character_png=character_png,description_ru=description,duration=duration,speaking=speaking,view=view,output_path=tmp)
                if animation_ok(generated,character_png,duration,description):
                    body_path=lib.register(sig,generated,description_ru=description,speaking=speaking,view=view,duration=duration)
                    break
                log.warning('Kling QC rejected %s attempt %s/%s',sig,attempt+1,tries)
            except Exception as exc:
                log.warning('Kling animation attempt failed %s/%s: %s',attempt+1,tries,exc)
    if body_path is None:
        return None
    if speaking and audio_path and body_url:
        try:
            synced=work_root/f'kling_{sig}_lipsync.mp4'
            provider.lip_sync(video_url=body_url,audio_path=audio_path,output_path=synced)
            if animation_ok(synced,character_png,duration,description):
                return synced
        except Exception as exc:
            log.warning('Kling lip-sync failed; using reusable body motion: %s',exc)
    return body_path
