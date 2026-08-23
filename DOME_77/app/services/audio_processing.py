from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from app.core.config import settings


@dataclass(frozen=True)
class VoiceActivity:
    duration_seconds: float
    speech_seconds: float
    speech_ratio: float
    mean_volume_db: float | None
    max_volume_db: float | None
    has_speech: bool
    reason: str


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


def analyze_voice_activity(path: Path) -> VoiceActivity:
    """Reject silence before ASR so a recognizer cannot hallucinate an answer.

    The gate intentionally accepts short *words* once there is real acoustic
    speech: PRE_A1 children are allowed to answer with one word. It rejects
    recordings that are too short, almost entirely silent, or below the
    microphone noise floor.
    """
    duration = _duration(path)
    cmd = [
        settings.ffmpeg_bin, '-hide_banner', '-nostats', '-i', str(path),
        '-af', 'silencedetect=noise=-42dB:d=0.20,volumedetect', '-f', 'null', '-',
    ]
    stderr = ''
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        stderr = result.stderr or ''
    except Exception:
        # Fail closed: an unreadable take must not become a correct answer.
        return VoiceActivity(duration, 0.0, 0.0, None, None, False, 'ANALYSIS_FAILED')

    silence = sum(float(value) for value in re.findall(r'silence_duration:\s*([0-9.]+)', stderr))
    speech = max(0.0, duration - min(duration, silence))
    ratio = speech / duration if duration > 0 else 0.0
    mean_match = re.search(r'mean_volume:\s*(-?(?:inf|[0-9.]+))\s*dB', stderr, re.IGNORECASE)
    max_match = re.search(r'max_volume:\s*(-?(?:inf|[0-9.]+))\s*dB', stderr, re.IGNORECASE)

    def volume(match) -> float | None:
        if not match or match.group(1).lower() == '-inf':
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    mean_db, max_db = volume(mean_match), volume(max_match)
    reason = 'SPEECH'
    if duration < 0.55:
        reason = 'TOO_SHORT'
    elif speech < 0.35 or ratio < 0.10:
        reason = 'INSUFFICIENT_SPEECH'
    elif max_db is None or max_db < -38.0 or (mean_db is not None and mean_db < -52.0):
        reason = 'TOO_QUIET'
    return VoiceActivity(duration, speech, ratio, mean_db, max_db, reason == 'SPEECH', reason)
