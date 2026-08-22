from __future__ import annotations
import subprocess
from pathlib import Path
from app.core.config import settings

class FreeTopicCartoonError(RuntimeError):
    pass


def _run(cmd:list[str], timeout:int=420):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        tail=(exc.stderr or exc.stdout or '')[-1200:]
        raise FreeTopicCartoonError(f'FFmpeg render failed: {tail}') from exc
    except Exception as exc:
        raise FreeTopicCartoonError(f'Render failed: {exc}') from exc


def _make_background_video(images:list[Path], work:Path, duration:int)->Path:
    per=max(4.0, duration/max(1,len(images)))
    segments=[]
    for i,p in enumerate(images):
        seg=work/f'bg_{i:02d}.mp4'
        # Still images are converted one-by-one first. This is much more robust than
        # concat-demuxing stills with zoompan in one command (the v38 failure path).
        _run([
            settings.ffmpeg_bin,'-y','-loop','1','-framerate','30','-i',str(p),
            '-t',f'{per:.3f}','-vf',
            'scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1,fps=30',
            '-an','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(seg)
        ],240)
        segments.append(seg)
    concat=work/'video_concat.txt'
    concat.write_text('\n'.join(f"file '{p.resolve().as_posix()}'" for p in segments),encoding='utf-8')
    bg=work/'background.mp4'
    _run([settings.ffmpeg_bin,'-y','-f','concat','-safe','0','-i',str(concat),'-t',str(duration),'-c','copy',str(bg)],240)
    return bg


def build_free_topic_cartoon(
    backgrounds:list[Path], character_png:Path, child_voice_files:list[Path], companion_voice_files:list[Path],
    output:Path, duration:int=75, companion_png:Path|None=None, first_child_scene_seconds:int=9,
)->Path:
    if not character_png.exists():
        raise FreeTopicCartoonError('Не найден герой ребёнка.')
    bgs=[Path(p) for p in backgrounds if p and Path(p).exists()]
    if not bgs:
        raise FreeTopicCartoonError('Нет изображений для мультфильма.')
    duration=max(60,min(90,int(duration)))
    first_child_scene_seconds=max(9,int(first_child_scene_seconds or 9))
    output.parent.mkdir(parents=True,exist_ok=True)
    work=output.parent/(output.stem+'_work'); work.mkdir(exist_ok=True)
    bgvideo=_make_background_video(bgs[:8],work,duration)

    cmd=[settings.ffmpeg_bin,'-y','-i',str(bgvideo),'-loop','1','-framerate','30','-i',str(character_png)]
    companion_input=None
    if companion_png and Path(companion_png).exists():
        companion_input=2
        cmd += ['-loop','1','-framerate','30','-i',str(companion_png)]
    voices=[]
    for p in list(child_voice_files)+list(companion_voice_files):
        if p and Path(p).exists() and Path(p).stat().st_size>0:
            cmd += ['-i',str(p)]; voices.append(Path(p))

    # Reusable lightweight motion for flat characters. Full arm/leg articulation still requires a segmented/rigged character asset.
    # Standing scenes face camera with subtle idle/talk motion; travel scenes use horizontal walk + body bob.
    filt=[
        "[1:v]format=rgba,scale=-1:330,rotate='0.010*sin(3*t)':ow=rotw(iw):oh=roth(ih):c=none[hero]",
        f"[0:v][hero]overlay=x='if(lt(t,{first_child_scene_seconds}),120,120+70*sin(0.16*t))':y='360+if(lt(t,{first_child_scene_seconds}),3*sin(2.4*t),8*abs(sin(4*t)))':enable='between(t,0,{duration})'[v0]",
    ]
    vlabel='v0'
    if companion_input is not None:
        filt.append(f"[{companion_input}:v]format=rgba,scale=-1:250,rotate='-0.008*sin(2.6*t)':ow=rotw(iw):oh=roth(ih):c=none[friend]")
        # Companion appears in several dialogue windows instead of occupying the whole film.
        filt.append(f"[{vlabel}][friend]overlay=x='930+30*sin(0.5*t)':y='405+5*abs(sin(4.5*t))':enable='between(t,12,25)+between(t,38,52)+between(t,62,{duration})'[v1]")
        vlabel='v1'

    if voices:
        labels=[]; spacing=duration/(len(voices)+1)
        base_index=3 if companion_input is not None else 2
        for i,_ in enumerate(voices):
            delay=int((i+1)*spacing*1000); inp=base_index+i; lab=f'a{i}'
            filt.append(f'[{inp}:a]atrim=0:6,asetpts=PTS-STARTPTS,adelay={delay}|{delay}[{lab}]')
            labels.append(f'[{lab}]')
        filt.append(''.join(labels)+f'amix=inputs={len(labels)}:normalize=0,alimiter=limit=0.9[a]')

    cmd += ['-filter_complex',';'.join(filt),'-map',f'[{vlabel}]']
    if voices:
        cmd += ['-map','[a]']
    cmd += ['-t',str(duration),'-c:v','libx264','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p']
    if voices:
        cmd += ['-c:a','aac','-b:a','160k']
    cmd += ['-movflags','+faststart',str(output)]
    _run(cmd,420)
    if not output.exists() or output.stat().st_size<10000:
        raise FreeTopicCartoonError('Мультфильм не создан.')
    return output
