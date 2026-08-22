from __future__ import annotations
import json
import subprocess
from pathlib import Path
from app.core.config import settings


def _duration(path: Path) -> float:
    cmd=[settings.ffmpeg_bin.replace('ffmpeg','ffprobe'),'-v','error','-show_entries','format=duration','-of','json',str(path)]
    try:
        result=subprocess.run(cmd,check=True,capture_output=True,text=True)
        return float(json.loads(result.stdout)['format']['duration'])
    except Exception:
        return 0.0


def _atempo_chain(speed: float) -> str:
    parts=[]
    while speed > 2.0:
        parts.append('atempo=2.0'); speed/=2.0
    while speed < 0.5:
        parts.append('atempo=0.5'); speed/=0.5
    parts.append(f'atempo={speed:.5f}')
    return ','.join(parts)


def prepare_child_voice(source: Path, output_wav: Path, max_seconds: float | None = None) -> Path:
    """Clean speech and optionally fit a cartoon line into max_seconds without pitch shift."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    filters = [
        'highpass=f=80', 'afftdn=nf=-25',
        'acompressor=threshold=-20dB:ratio=3:attack=10:release=120:makeup=3',
        'alimiter=limit=0.891', 'loudnorm=I=-16:TP=-1:LRA=7'
    ]
    duration=_duration(source)
    if max_seconds and duration > max_seconds and duration <= max_seconds * 2.5:
        filters.append(_atempo_chain(duration / max_seconds))
    cmd=[settings.ffmpeg_bin,'-y','-i',str(source),'-vn','-ac','1','-ar','48000','-af',','.join(filters),str(output_wav)]
    subprocess.run(cmd,check=True,capture_output=True)
    return output_wav
